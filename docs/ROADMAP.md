# decky-romm-sync Feature Roadmap

> **Created:** April 5, 2026
> **Status:** Draft — features identified, ordering TBD
> **Baseline:** v0.15.0 (stock) + Feature 1 perf instrumentation (on branch `feat/perf-instrumentation-v2`)

---

## Completed

### Feature 1: Performance Instrumentation ✅

PerfCollector + ETAEstimator — phase timing, HTTP tracking, counters, gauges, auto-save JSON report. 34 unit tests. Deployed to Deck. Baseline data collected from live 3,241-ROM sync.

**Design doc:** `docs/perf-test-plan.md`

---

## Planned

### Feature 2: Per-Unit Sync Pipeline

**Problem:** Current sync processes all platforms/collections through each phase before advancing. User waits 40+ minutes, then everything appears at once. Crashes lose all progress.

**Solution:** Fetch only the platform+collection list upfront, then run the full pipeline (fetch ROMs → build shortcuts → download artwork → apply shortcuts → update registry) for one platform/collection at a time. Each completed unit is immediately visible and playable in Steam.

**Key wins:**
- Time to first visible game: ~40 min → ~2–4 min
- Crash safety: all completed units preserved
- Artwork double-download eliminated
- 50ms inter-shortcut delay reduced

**Design doc:** `docs/per-unit-pipeline.md`

---

### Feature 3: Hide Synced Shortcuts from Non-Steam Games View

**Problem:** Syncing 3,000+ ROMs floods the "Non-Steam Games" section, making it unusable for actual non-Steam PC games (e.g., GOG, Epic, standalone). Users want ROM shortcuts to appear ONLY under platform/RomM collections in the Collections tab.

**Current state:** No solution exists in decky-romm-sync or any known Decky plugin. Steam's "Non-Steam Games" is a system view with no known API to selectively hide apps from it.

**Research needed:**
1. Test Steam's built-in "Hidden" system collection — does adding an app there hide it from Non-Steam Games but keep it launchable from user collections? Or does it hide from everywhere?
2. Investigate `appStore.GetAppOverviewByAppID(appId)` properties — is there a visibility/hidden flag that can be set programmatically?
3. Check `localconfig.vdf` and `shortcuts.vdf` for per-app hidden flags
4. Research what happens with `SteamClient.Apps` — are there undocumented methods like `SetAppHidden`?
5. Check if other Decky plugins (MoonDeck, BoilR integration, Playnite) have solved this

**Risk:** High — may not be possible with current Steam client APIs. May require a creative workaround or waiting for Valve to add the capability.

**Fallback options if hiding isn't possible:**
- Add a naming prefix (e.g., `🎮 `) to all ROM shortcuts so they sort together and are visually distinct from real non-Steam games
- Create a "Real Non-Steam Games" collection that users manually curate, and recommend using the Collections tab as the primary view

**Design doc:** None yet — research phase

---

### Feature 4: Sync UX Overhaul — Per-Platform Accordion

**Problem:** The current sync UI shows an abstract 4-phase stepper with an indeterminate spinner during the fetch phase (60–80% of sync time). Users have no idea which platform is syncing, how far along it is, or how long remains.

**Solution:** Replace the stepper with a per-platform accordion that shows real-time progress per platform. Each platform row transitions through `○ pending → ⟳ active → ✓ done`. Only the active platform is expanded, showing a progress bar, current game title, cover art thumbnail, and per-platform ETA.

**Dependency:** Feature 2 (Per-Unit Sync Pipeline) — the accordion needs per-platform events (`sync_plan`, `sync_apply_platform`) which Feature 2 provides.

**Wireframe:**

```
┌─────────────────────────────────────────┐
│  ✓  Dreamcast               362/362    │  ← completed (collapsed)
│  ⟳  PlayStation             142/1978   │  ← active (EXPANDED)
│     ████████░░░░░░░░░░░░░░             │
│     ┌──────┐                            │
│     │cover │  Crash Bandicoot 3         │
│     │ art  │  ~4m remaining             │
│     └──────┘                            │
│  ○  SNES                     0/830     │  ← pending (collapsed)
│  ○  Best of Metroid           0/11     │  ← pending (collapsed)
│                                         │
│  2 of 4 units · ~12m remaining          │
│  [ Cancel Sync ]                        │
└─────────────────────────────────────────┘
```

**Key states:**
- **Connecting** (~1s): Indeterminate spinner + "Connecting to RomM..."
- **Fetching Library** (5–20s): All platforms listed as `○` pending, compact header with overall fetch progress
- **Applying Per-Platform** (main state): One platform expanded, cover art cycling, per-platform ETA, footer with global ETA
- **Collections Phase**: After all platforms `✓`, separate section: "Building collections... 5/8"
- **Complete**: All rows `✓`, summary: "✓ Sync complete — 5,320 games · 14 platforms · 8 collections · 6m12s elapsed"
- **Cancelled**: Active platform shows partial with `✗`, unprocessed stay `○`
- **Truncation (14+ platforms)**: Show active + 2 nearest completed + 2 nearest pending, collapse rest into summary rows

**Scope:**
- `SyncAccordion.tsx` — new React component (~400 lines)
- `syncAccordion.ts` — state management (~150 lines)
- `MainPage.tsx` — swap progress section for accordion
- `types/index.ts` — SyncPlan type, SyncAccordionState
- `library.py` — emit `sync_plan` event (may already exist from Feature 2)

**Risk:** Low-moderate — frontend-only rendering change, but substantial new component

**PR size:** ~700 lines

**Origin:** Consolidated from archived Features 10 (fetch progress bar), 11 (per-platform accordion), and 13 (interleaved artwork) from the April 5, 2026 archived roadmap.
