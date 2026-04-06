# Performance Test Plan — decky-romm-sync

## Overview

Performance testing is done **live in production** using the actual decky-romm-sync plugin running in Game Mode on the Steam Deck. There are no standalone scripts or synthetic benchmarks — we measure real sync operations against the real RomM server.

## How Performance Data Is Captured

Every sync the plugin runs automatically:

1. Logs a formatted report to Decky logs (`journalctl -u plugin_loader`)
2. Writes `perf_report.json` to the plugin directory
3. Exposes `get_perf_report()` as an RPC method for the frontend

The instrumentation lives in `py_modules/lib/perf.py` (PerfCollector + ETAEstimator).

## Representative Test Platforms

These 3 platforms were selected to cover different size tiers while keeping total sync time under ~5 minutes:

| Platform       | ROMs  | Tier   | Why Included                          |
|----------------|-------|--------|---------------------------------------|
| dc (Dreamcast) | 362   | Small  | Minimal pagination (~8 pages)         |
| snes           | 828   | Medium | Moderate pagination, cartridge-based  |
| psx            | 1,980 | Large  | Heavy pagination (~40 pages), disc-based |

**Total: ~3,170 ROMs across 3 platforms.**

These platforms exercise:
- API pagination at varying depths (8 to 40 pages)
- Mixed content types (disc-based, cartridge)
- The RomM server's ability to handle back-to-back queries

## Representative Collections

These 4 collections were selected to cover different sizes while keeping sync time manageable:

| Collection                 | ROMs | Tier   | Why Included                                  |
|----------------------------|------|--------|-----------------------------------------------|
| Best of Metroid            | 11   | Tiny   | Single-page fetch, franchise collection       |
| Best of Castlevania        | 23   | Small  | Small multi-franchise, quick pagination       |
| Best of Nintendo 64        | 42   | Medium | Single-platform "best of"                     |
| Best of SNES               | 101  | Large  | Moderate pagination (~2 pages)                |

**Total: ~177 ROMs across 4 collections** (some overlap with platform ROMs is expected).

These collections exercise:
- Per-collection ROM fetching at varying depths (1 to 2 pages)
- Franchise vs. platform-based collections
- Overlap deduplication (ROMs already seen via platform sync)

## Test Procedure

1. Open Game Mode on Steam Deck
2. Press `...` (QAM) → decky-romm-sync
3. Enable the 3 representative platforms and 4 representative collections
4. Run a sync
5. After the sync completes, retrieve perf data:
   - **From Decky logs:** `ssh deck@192.168.0.84 "echo comcast | sudo -S journalctl -u plugin_loader.service --no-pager -n 50"`
   - **From saved JSON:** `scp deck@192.168.0.84:~/homebrew/plugins/decky-romm-sync/perf_report.json .`
   - **Via RPC:** Call `get_perf_report()` from the frontend

## What This Measures

- Platform and ROM metadata fetch time (API latency + pagination)
- Per-platform ROM fetch timing (logged individually)
- Per-collection ROM fetch time and pagination depth
- Artwork download progress (downloaded/skipped/failed counts, logged every ~10%)
- HTTP request count and total bytes transferred
- Per-phase timing breakdown (fetch_platforms, fetch_roms, fetch_collections, prepare_shortcuts, cache_metadata, artwork_download)
- Shortcut and stale ROM gauges
- Error and retry counts

## What This Does NOT Measure

- Actual ROM file downloads (multi-GB network I/O)
- Steam shortcut creation time (happens in JS frontend, not instrumented)
- Concurrent/parallel fetching (currently sequential by design)

## Sync Pipeline Architecture (for future refactoring)

The sync runs in 5 sequential phases. Understanding the pipeline is critical for future optimization work.

### Phase 1: Metadata Fetch (Python backend — fast)
- Fetches platform list, then ROMs per platform (paginated, 50/page)
- Fetches collection ROMs (paginated per collection)
- **~48s for 3,241 ROMs** — acceptable, could benefit from concurrent platform fetching

### Phase 2: Prepare Shortcuts (Python backend — fast)
- Builds shortcut data structures from ROM metadata
- Sub-second

### Phase 3: Artwork Download (Python backend — moderate)
- Downloads cover images sequentially from RomM server, one per ROM
- Skips ROMs that already have covers (staging or final)
- **~6 min for 3,139 covers** — major optimization target for concurrent downloads
- **Current issue:** Zero progress logging in stock code (fixed in our instrumented version)

### Phase 4: Shortcut Application (JS frontend — SLOWEST)
- Backend emits `sync_apply` event with all 3,241 shortcuts at once
- Frontend calls `SteamClient.Apps.AddShortcut()` sequentially per ROM
- Renames cover art from `romm_{rom_id}_cover.png` to `{app_id}p.png`
- **~30+ min for 3,241 shortcuts** — dominant bottleneck
- **Current issues:**
  - Zero backend logging during this phase (entirely JS)
  - All-or-nothing delivery: no games are visible with artwork until the entire batch completes
  - Cover art only renamed after `report_sync_results` callback
  - No incremental value — user sees title stubs appearing but with no artwork

### Phase 5: Finalization (Python backend — fast)
- `report_sync_results` callback from frontend
- Updates shortcut registry, renames artwork to final paths
- Writes perf_report.json
- Sub-second

### Observed Timing (3 platforms + 4 collections, 3,241 ROMs, first sync)

| Phase | Duration | % of Total |
|-------|----------|------------|
| Metadata fetch | ~48s | ~2% |
| Collection fetch | ~5s | <1% |
| Artwork download | ~6 min | ~15% |
| **Shortcut application** | **~30+ min** | **~80%** |
| Finalization | <1s | <1% |
| **Total** | **~40+ min** | |

### Future Refactoring Targets

1. **Concurrent artwork downloads** — fetch multiple covers in parallel (e.g., 4-8 concurrent requests). Could reduce artwork phase from 6 min to ~1 min.
2. **Incremental shortcut delivery** — emit shortcuts in batches (e.g., 50 at a time) so games appear with artwork progressively instead of all-or-nothing at the end.
3. **Frontend shortcut creation optimization** — investigate if `AddShortcut()` can be batched or if Steam has a bulk API.
4. **Progress reporting from JS** — add periodic callbacks from the frontend during shortcut creation so the backend can log and instrument this phase.
5. **Collection-as-Steam-category creation** — currently gated by `collection_create_platform_groups` setting (default: False). Needs to be enabled for collections to appear in Steam library.

## Baseline (April 5, 2026)

Captured from a full metadata sync of all 6,756 ROMs across 90 platforms:

| Metric             | Value     |
|--------------------|-----------|
| Wall time          | 83.1s     |
| HTTP requests      | 144       |
| Bytes transferred  | 23.3 MB   |
| Errors             | 0         |
| Dominant phase     | fetch_roms (98% of time) |

## Branch Layout

| Branch | Status | Notes |
|--------|--------|-------|
| `main` | Base | upstream v0.15.0 |
| `feat/perf-instrumentation-v2` | **Published** to fork | Feature 1 — performance instrumentation |
| `feat/concurrent-sync-performance` | Local only | 51 commits, broken — do not push |
