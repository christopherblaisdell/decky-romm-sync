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

These 5 platforms were selected to cover three size tiers and exercise different pagination depths against the RomM API:

| Platform | ROMs  | Tier         | Why Included                          |
|----------|-------|--------------|---------------------------------------|
| dc       | 362   | Small        | Minimal pagination (~8 pages)         |
| snes     | 828   | Medium       | Moderate pagination                   |
| gba      | 1,057 | Medium-Large | Tests steady-state throughput         |
| psx      | 1,980 | Large        | Heavy pagination (~40 pages)          |
| switch   | 3,526 | Extra-Large  | Stress test (~71 pages)               |

**Total: ~7,753 ROMs across 5 platforms.**

These platforms exercise:
- API pagination at varying depths (8 to 71 pages)
- Mixed content types (disc-based, cartridge, modern)
- The RomM server's ability to handle back-to-back queries

## Representative Collections

These 8 collections were selected to cover the full range of sizes. Collections trigger per-collection ROM fetches, so larger collections add real pagination load.

| Collection                 | ROMs | Tier         | Why Included                                  |
|----------------------------|------|--------------|-----------------------------------------------|
| Best of Metroid            | 11   | Tiny         | Single-page fetch, franchise collection       |
| Best of Castlevania        | 23   | Small        | Small multi-franchise, quick pagination       |
| Best of Nintendo 64        | 42   | Small-Medium | Single-platform "best of"                     |
| Best of SNES               | 101  | Medium       | Moderate pagination (~2 pages)                |
| Best of Wii                | 115  | Medium       | Disc-based platform collection                |
| Best of PS2                | 185  | Large        | Heaviest single-platform collection           |
| Best of RPGs               | 248  | Large        | Cross-platform genre, heavy pagination (~5 pages) |
| Best of Xbox 360           | 384  | Extra-Large  | Largest collection, stress test (~8 pages)    |

**Total: ~1,109 ROMs across 8 collections** (some overlap with platform ROMs is expected).

These collections exercise:
- Per-collection ROM fetching at varying depths (1 to 8 pages)
- Single-platform vs. cross-platform genre collections
- Overlap deduplication (ROMs already seen via platform sync)

## Test Procedure

1. Open Game Mode on Steam Deck
2. Press `...` (QAM) → decky-romm-sync
3. Enable the 5 representative platforms and 8 representative collections
4. Run a sync
5. After the sync completes, retrieve perf data:
   - **From Decky logs:** `ssh deck@192.168.0.84 "echo comcast | sudo -S journalctl -u plugin_loader.service --no-pager -n 50"`
   - **From saved JSON:** `scp deck@192.168.0.84:~/homebrew/plugins/decky-romm-sync/perf_report.json .`
   - **Via RPC:** Call `get_perf_report()` from the frontend

## What This Measures

- Platform and ROM metadata fetch time (API latency + pagination)
- Per-collection ROM fetch time and pagination depth
- HTTP request count and total bytes transferred
- Per-phase timing breakdown (fetch_platforms, fetch_roms, fetch_collections, prepare_shortcuts, cache_metadata, artwork_download)
- Error and retry counts

## What This Does NOT Measure

- Actual ROM file downloads (multi-GB network I/O)
- Steam shortcut creation performance
- Artwork download at scale
- Concurrent/parallel fetching (currently sequential by design)

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
