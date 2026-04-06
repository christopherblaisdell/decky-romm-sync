# Feature: Per-Unit Sync Pipeline

> **Status:** Design — not yet implemented
> **Created:** April 5, 2026
> **Branch:** TBD
> **Replaces:** The current all-at-once sync architecture

---

## Problem Statement

The current sync pipeline processes **all platforms and collections through each phase before advancing to the next phase**. This means:

1. **All** ROMs are fetched before **any** artwork is downloaded.
2. **All** artwork is downloaded before **any** shortcuts are created.
3. **All** shortcuts are created before **any** become visible in Steam.
4. The user waits 40+ minutes seeing only a spinner, then everything appears simultaneously.
5. If sync is cancelled or crashes mid-way, **zero** value is preserved — no shortcuts, no artwork, nothing.

### Observed Timing (3,241 ROMs across 3 platforms + 4 collections)

| Phase | Time | % of total |
|---|---|---|
| Metadata fetch (all platforms + collections) | ~53s | ~3% |
| Artwork download (all ROMs, sequential) | ~6 min | ~15% |
| Shortcut application (all ROMs, 50ms delay each) | ~30+ min | ~80% |
| Registry update + collection creation | ~1 min | ~2% |
| **Total** | **~40 min** | |

### Additional Waste

- **Double artwork download:** Python downloads covers to `romm_{rom_id}_cover.png` staging files (step 6). Then after JS creates each shortcut and obtains the `appId`, JS calls `getArtworkBase64(romId)` which makes Python re-read the same file, base64-encode it, and send it back. JS then calls `SetCustomArtworkForApp()`. The cover is transferred twice.
- **Pure sleep overhead:** Each shortcut has a 50ms `await delay(50)` between creations = 3,241 × 50ms = **2.7 minutes of doing nothing**.
- **Giant single event:** `sync_apply` sends all 3,241 shortcuts in one event payload, which must be fully received and parsed before any processing begins.

---

## Solution: Per-Unit Pipeline

### Core Architecture Change

Instead of running each phase across all data, run **all phases for one unit** before moving to the next unit. A "unit" is one platform or one collection.

```
BEFORE (all-at-once):
  fetch ALL ROMs → download ALL artwork → create ALL shortcuts → done

AFTER (per-unit):
  for each platform:
    fetch ROMs → download artwork → create shortcuts → VISIBLE
  for each collection:
    fetch ROMs → download artwork → create shortcuts → VISIBLE
  cleanup stale → done
```

### Why This Works

The user sees games appear platform-by-platform. Dreamcast (362 ROMs) completes in ~4 minutes and is fully playable with artwork while PlayStation (1,978 ROMs) is still syncing. A cancelled sync preserves all completed platforms.

---

## Detailed Design

### Phase 0: Build the Work Queue

**What:** Fetch the list of enabled platforms and enabled collections. Do NOT fetch any ROMs yet.

**Why fetch only the list:**
- It's fast (~1 API call for platforms, ~2 for collections).
- It gives us the complete work queue with ROM counts per unit, enabling accurate progress reporting ("Platform 2 of 5, ~362 ROMs").
- It avoids the current approach of fetching all ROM data upfront, which delays the start of useful work.
- It decouples "what do we need to sync?" from "sync this specific thing" — the outer loop is just a dispatcher.

**Output:** An ordered list of work units:

```python
work_queue = [
    {"type": "platform", "id": 4,  "name": "Dreamcast",   "rom_count": 362},
    {"type": "platform", "id": 19, "name": "PlayStation",  "rom_count": 1978},
    {"type": "platform", "id": 11, "name": "SNES",         "rom_count": 830},
    {"type": "collection", "id": 94, "name": "Castlevania", "rom_count": 23},
    {"type": "collection", "id": 90, "name": "Metroid",     "rom_count": 11},
]
```

**Emit:** `sync_plan` event to frontend with the full work queue. This enables the frontend to render a per-platform progress view (future UX feature).

### Phase 1–N: Per-Unit Sync (one iteration per unit)

For each unit in the work queue, execute all steps to completion:

#### Step 1: Fetch ROMs

- **Platform unit:** Call existing `_full_fetch_platform_roms()` (or `_try_incremental_skip()` for unchanged platforms). Already works for a single platform — no change needed.
- **Collection unit:** Call existing `_fetch_single_collection_roms()`. Already works for a single collection — no change needed.
- **Deduplication:** Maintain a running `synced_rom_ids: set[int]` across units. Collection units skip ROMs already synced via a platform unit. This is the same dedup logic that exists today in `_fetch_collection_roms()`, just applied per-collection instead of in a batch.

#### Step 2: Build Shortcut Data

- Call existing `_build_shortcuts_data(unit_roms)` with only this unit's ROMs.
- No change to the function itself — it already accepts any ROM list.

#### Step 3: Cache Metadata

- Extract and cache metadata for this unit's ROMs only.
- Same existing logic, smaller input.

#### Step 4: Download Artwork

- Call existing `artwork.download_artwork(unit_roms, ...)` with only this unit's ROMs.
- No change to the function — it already accepts any ROM list and handles skip-if-exists.

#### Step 5: Emit `sync_apply_unit`

New event (replaces the monolithic `sync_apply`):

```python
await self._emit("sync_apply_unit", {
    "unit_type": "platform",        # or "collection"
    "unit_name": "Dreamcast",
    "unit_index": 0,                # 0-based index in work queue
    "total_units": 5,
    "shortcuts": shortcuts_data,    # only this unit's shortcuts
    "remove_rom_ids": [],           # empty — stale cleanup deferred to end
})
```

The payload is much smaller (362 shortcuts vs 3,241), parsed faster, and processed immediately.

#### Step 6: Frontend Applies Shortcuts

`syncManager.ts` processes `sync_apply_unit`:

1. For each shortcut in the unit:
   - `AddShortcut()` or update existing → get `appId`
   - Read artwork from staging path, `SetCustomArtworkForApp()` — **no base64 round-trip**
   - Record `rom_id → appId` mapping
2. Report unit results back to backend: `reportUnitResults(rom_id_to_app_id)`

**At this point, this platform's games are visible, have artwork, and are playable.**

#### Step 7: Backend Updates Registry

- `report_unit_results()` (new, simpler version of `report_sync_results`):
  - Renames staging artwork to `{appId}p.png`
  - Updates `shortcut_registry` for this unit's ROMs
  - Saves state to disk — **crash-safe checkpoint**
  - Does NOT build collections yet (deferred)

#### Step 8: Collection Grouping (collection units only)

- After a collection unit's shortcuts are applied, create/update the Steam collection for that collection.
- Platform collections are created after all platform units complete (needs the full set of platform appIds).

### Final Phase: Stale Cleanup

After all units are processed:

1. **Build the complete set of synced ROM IDs** from the accumulated results across all units.
2. **Compare against the shortcut registry** to find stale entries (ROMs in registry but not in any synced unit).
3. **Emit stale removal** to frontend.
4. **Build platform collections** from the registry (needs all platform appIds).
5. **Save final state** with `last_sync` timestamp and synced platform/collection lists.

Stale cleanup MUST happen at the end because we can't know what's stale until we've seen every unit's ROM IDs.

---

## Artwork Unification

### Current Flow (double-download)

```
Python: download cover → save to romm_{rom_id}_cover.png (disk)
Python: emit sync_apply with cover_path
JS: AddShortcut() → get appId
JS: getArtworkBase64(romId) → Python reads file, base64-encodes → JS receives
JS: SetCustomArtworkForApp(appId, base64, "png", 0)
Python (later): rename romm_{rom_id}_cover.png → {appId}p.png
```

The cover bytes travel: RomM server → Python → disk → Python → base64 → JS → Steam

### New Flow (single pass)

```
Python: download cover → save to romm_{rom_id}_cover.png (disk)
Python: emit sync_apply_unit with cover_path per shortcut
JS: AddShortcut() → get appId
JS: reportUnitResults({rom_id: appId, ...})
Python: rename romm_{rom_id}_cover.png → {appId}p.png
Python: SetCustomArtworkForApp via SteamClient? 
```

**Open question:** Can Python set Steam artwork directly, or must JS do it? If JS must call `SetCustomArtworkForApp`, we still need to transfer the image. Options:

- **Option A (file path):** JS reads the staging file directly from the grid directory using Node.js `fs` (if accessible in Decky's CEF context). Avoids base64 encoding/decoding overhead.
- **Option B (keep base64 but per-unit):** Still do the base64 round-trip, but only for one unit's worth of ROMs at a time. Less impactful because the unit is small.
- **Option C (defer artwork to post-shortcut):** Create the shortcut, get the appId, report back to Python, Python renames the file to `{appId}p.png`. Steam may pick it up automatically from the grid directory without needing `SetCustomArtworkForApp` at all — the file naming convention `{appId}p.png` is what Steam's grid system looks for.

**Recommended: Option C.** Steam's grid system reads artwork from `{appId}p.png` in the grid directory. If we rename the staging file to the correct name, Steam should display it without any explicit API call. This eliminates the base64 transfer entirely. Needs verification on Deck.

---

## Inter-Unit State

State that accumulates across units:

| State | Purpose | When used |
|---|---|---|
| `synced_rom_ids: set[int]` | Deduplication — collections skip ROMs already synced via platforms | Collection unit fetch step |
| `all_rom_id_to_app_id: dict[str, int]` | Complete mapping for stale detection and platform collections | Final phase |
| `platform_app_ids: dict[str, list[int]]` | Platform name → appIds for Steam platform collections | Final phase |
| `collection_memberships: dict[str, list[int]]` | Collection name → ROM IDs for Steam collection grouping | Final phase (or per-collection-unit) |

All of these are simple accumulators that grow as each unit completes. No complex merging required.

---

## Event Protocol Changes

### New Events

| Event | Direction | When | Payload |
|---|---|---|---|
| `sync_plan` | Python → JS | After Phase 0 | `{units: [{type, name, rom_count}], total_roms: int}` |
| `sync_apply_unit` | Python → JS | Per unit | `{unit_type, unit_name, unit_index, total_units, shortcuts: [...]}` |
| `sync_unit_complete` | JS → Python (RPC) | Per unit | `{rom_id_to_app_id: {...}}` |
| `sync_stale` | Python → JS | Final phase | `{remove_rom_ids: [...]}` |
| `sync_collections` | Python → JS | Final phase | `{platform_app_ids: {...}, romm_collection_app_ids: {...}}` |

### Removed Events

| Event | Why |
|---|---|
| `sync_apply` (monolithic) | Replaced by per-unit `sync_apply_unit` |

### Unchanged Events

| Event | Why unchanged |
|---|---|
| `sync_progress` | Still emitted throughout, now with unit-level context |
| `sync_complete` | Still emitted once at the very end |

---

## 50ms Delay Reduction

The current 50ms `await delay(50)` between each shortcut creation exists to "yield to the event loop." With per-unit processing:

- Units are smaller (362 shortcuts for Dreamcast vs 3,241 total).
- We can **reduce the delay to 5–10ms** or switch to yielding every N shortcuts instead of every single one.
- For 362 shortcuts at 10ms: 3.6s of delay vs 18s at 50ms. For 1,978 (PlayStation): 20s vs 99s.
- **Conservative approach:** Start at 20ms, measure, reduce further if no crashes.

This alone saves ~2 minutes on the current 3,241-shortcut sync.

---

## Crash Safety

### Current State

If the plugin crashes or the user kills it mid-sync, `report_sync_results()` never runs. The shortcut registry is not updated. All newly created shortcuts become orphans — present in Steam but unknown to the plugin.

### Per-Unit Improvement

After each unit completes, `report_unit_results()` persists the registry. If the plugin crashes between unit 3 and unit 4:

- Units 1–3 are fully persisted in the registry.
- Unit 4's shortcuts may be orphaned (the usual risk), but it's at most one platform's worth.
- On next sync, incremental skip can detect that units 1–3 are unchanged and skip them.

This is a significant durability improvement with no additional code — it's a natural consequence of per-unit state saves.

---

## Changes Required by File

### Python (backend)

| File | Change | Scope |
|---|---|---|
| `library.py` | Restructure `_do_sync()` to iterate per-unit | Major — this is the main change |
| `library.py` | New `_sync_one_platform()` method | Extract from existing code |
| `library.py` | New `_sync_one_collection()` method | Extract from existing code |
| `library.py` | New `report_unit_results()` RPC method | Small — subset of `report_sync_results` |
| `library.py` | Stale detection moved to final phase | Move, not rewrite |
| `main.py` | Expose `report_unit_results` callable | 1 line |
| `artwork.py` | No changes | ✅ |
| `http.py` | No changes | ✅ |
| `perf.py` | No changes (phases still timed) | ✅ |

### TypeScript (frontend)

| File | Change | Scope |
|---|---|---|
| `syncManager.ts` | Handle `sync_apply_unit` instead of `sync_apply` | Moderate — same processing logic, different event shape |
| `syncManager.ts` | Call `reportUnitResults()` after each unit | Small |
| `syncManager.ts` | Handle `sync_stale` event for removals | Small |
| `backend.ts` | New `reportUnitResults()` API call | 1 function |
| `index.tsx` | Register new event listeners | Small |
| `types/index.ts` | New event types | Small |

### Files NOT changed

- `artwork.py` — already accepts a ROM list of any size
- `http.py` — HTTP adapter is unit-agnostic
- `perf.py` — phase timing still works (phases are just smaller/repeated)
- `steamShortcuts.ts` — `addShortcut()`/`removeShortcut()` unchanged
- `collections.ts` — collection creation logic unchanged
- `domain/shortcut_data.py` — unchanged
- `domain/sync_state.py` — unchanged

---

## Testing Strategy

### Unit Tests

- `_sync_one_platform()` with mocked API returns correct shortcuts data
- `_sync_one_collection()` deduplicates against `synced_rom_ids`
- `report_unit_results()` updates registry and saves state
- Stale detection with partial sync (some units complete, some not)
- Work queue ordering (platforms before collections)

### Integration Tests (on Deck)

1. **Small sync (1 platform, 0 collections):** Verify single-unit pipeline works end to end.
2. **Multi-platform sync:** Verify platforms appear one at a time in Steam Library.
3. **Platform + collection sync:** Verify collection-only ROMs are handled after platforms.
4. **Cancel mid-unit:** Verify completed units are preserved, in-progress unit is partially applied.
5. **Cancel between units:** Verify all completed units are fully persisted.

### Performance Measurement

Compare against the Feature 1 baseline:

| Metric | Baseline (all-at-once) | Target (per-unit) |
|---|---|---|
| Time to first visible game | ~40 min | ~2–4 min |
| Total sync time | ~40 min | ~35 min (reduced delay + no double-artwork) |
| Data transferred (artwork) | 2× (disk + base64) | 1× (disk only, if Option C works) |
| Crash recovery | 0 games preserved | All completed units preserved |

---

## Open Questions

1. **Does Steam auto-detect `{appId}p.png` in the grid directory?** If yes, Option C for artwork eliminates base64 entirely. If no, we fall back to Option B (per-unit base64, smaller batches). **Must verify on Deck before implementing.**

2. **Should we parallelize artwork within a unit?** The current sequential download takes ~1s per cover. For a 362-ROM platform, that's ~6 minutes of artwork alone. Adding 4–8 concurrent downloads per unit would reduce this to ~1 min per platform. This is an orthogonal optimization that can be added later without changing the per-unit architecture.

3. **How should `sync_preview` (delta mode) work?** Delta preview currently fetches everything to compute the diff. Per-unit pipeline could either: (a) keep preview as-is (fetch-all for diff summary), or (b) skip preview entirely in full-sync mode and only use it for delta syncs. **Recommend deferring this decision.**

4. **Platform collection timing:** Platform Steam collections need all appIds for a platform. Since we process one platform at a time, we can create each platform's collection immediately after that unit completes. This is simpler than the current approach of building all collections at the end.
