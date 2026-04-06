#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════
  PERFORMANCE TEST METHODOLOGY — decky-romm-sync
═══════════════════════════════════════════════════════════════════════

PURPOSE
  Measure the real-world performance of the sync pipeline by fetching
  ROM metadata from the live RomM server.  The instrumented plugin
  (PerfCollector in py_modules/lib/perf.py) records every production
  sync automatically — this script is for ad-hoc baseline captures
  without needing Game Mode.

HOW PERF DATA IS CAPTURED IN PRODUCTION
  Every sync the plugin runs in Game Mode automatically:
    1. Logs a formatted report to Decky logs (journalctl -u plugin_loader)
    2. Writes perf_report.json to the plugin directory
    3. Exposes get_perf_report() as an RPC method for the frontend

REPRESENTATIVE TEST PLATFORMS (5 platforms, ~3,200 ROMs, ~2 min)
  Chosen to cover three size tiers and different content profiles:

  ┌─────────────┬────────┬──────────────────────────────────────┐
  │ Platform    │  ROMs  │ Why included                         │
  ├─────────────┼────────┼──────────────────────────────────────┤
  │ dc          │    362 │ SMALL — minimal pagination (8 pages) │
  │ snes        │    828 │ MEDIUM — moderate pagination         │
  │ gba         │  1,057 │ MEDIUM-LARGE — tests steady state    │
  │ psx         │  1,980 │ LARGE — heaviest pagination (40 pgs) │
  │ switch      │  3,526 │ EXTRA-LARGE — stress test (71 pages) │
  └─────────────┴────────┴──────────────────────────────────────┘

  Total: ~7,753 ROMs across 5 platforms ≈ 2-3 minutes on LAN.

  These 5 platforms exercise:
    - API pagination at varying depths (8 to 71 pages)
    - Mixed content types (disc-based, cartridge, modern)
    - The RomM server's ability to handle back-to-back queries

REPRESENTATIVE COLLECTIONS (checked but not ROM-fetched)
  Collections are fetched as a single list (~101 items, <1s).
  No per-collection ROM fetching is done in this test.

WHAT THIS DOES NOT TEST
  - Actual ROM file downloads (network I/O for multi-GB files)
  - Steam shortcut creation (requires Steam running)
  - Artwork download at scale (only 3 sample covers)
  - Concurrent/parallel fetching (sequential by design)

RUNNING THIS SCRIPT
  From deployed plugin on Deck::

    cd ~/homebrew/plugins/decky-romm-sync
    python3 scripts/deck_perf_test.py

  From a temp copy on Deck::

    scp -r decky-romm-sync/ deck@192.168.0.84:/tmp/perf-test/
    ssh deck@192.168.0.84 "cd /tmp/perf-test && python3 scripts/deck_perf_test.py"

  Environment variable overrides::

    ROMM_URL=http://host:8098  ROMM_USER=user  ROMM_PASS=pass
    PERF_OUTPUT=/path/to/output.json

BRANCH LAYOUT
  main                             — upstream base (v0.15.0)
  feat/perf-instrumentation-v2     — Feature 1 only, PUBLISHED to fork
  feat/concurrent-sync-performance — LOCAL ONLY, 51 commits, broken
  feat/perf-instrumentation        — LOCAL ONLY, old perf attempt (superseded by v2)

  Only feat/perf-instrumentation-v2 is pushed to the remote fork.
  The other two feature branches exist locally only and must not be pushed.

═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time

# Set up sys.path to find py_modules
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
_py_modules = os.path.join(_project_root, "py_modules")
sys.path.insert(0, _py_modules)

from adapters.romm.http import RommHttpAdapter
from domain.shortcut_data import build_shortcuts_data
from lib.perf import PerfCollector

# ── Configuration ────────────────────────────────────────────

ROMM_URL = os.environ.get("ROMM_URL", "http://theblaze-aorus:8098")
ROMM_USER = os.environ.get("ROMM_USER", "cblaisdell")
ROMM_PASS = os.environ.get("ROMM_PASS", "comcast")
OUTPUT_PATH = os.environ.get("PERF_OUTPUT", "/tmp/perf_baseline.json")

# See methodology above for rationale
TARGET_PLATFORM_SLUGS = {"dc", "snes", "gba", "psx", "switch"}

TOTAL_TIMEOUT_SEC = 300  # 5 minute hard ceiling (expect ~2-3 min)

# ── Logging ──────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("perf_test")


# ── Helpers ──────────────────────────────────────────────────

def create_http_adapter(perf: PerfCollector) -> RommHttpAdapter:
    """Create a configured HTTP adapter pointed at the live RomM server."""
    settings = {
        "romm_url": ROMM_URL,
        "romm_user": ROMM_USER,
        "romm_pass": ROMM_PASS,
        "romm_allow_insecure_ssl": False,
    }
    adapter = RommHttpAdapter(
        settings=settings,
        plugin_dir=_project_root,
        logger=log,
    )
    adapter.set_perf_collector(perf)
    return adapter


def fetch_all_roms_paginated(adapter: RommHttpAdapter, platform_id: int, limit: int = 50) -> list[dict]:
    """Paginate through /api/roms for a single platform."""
    offset = 0
    roms: list[dict] = []
    while True:
        result = adapter.request(f"/api/roms?platform_ids={platform_id}&limit={limit}&offset={offset}")
        batch = result.get("items", []) if isinstance(result, dict) else result
        roms.extend(batch)
        total = result.get("total", len(batch)) if isinstance(result, dict) else len(batch)
        if len(roms) >= total or len(batch) < limit:
            break
        offset += limit
    return roms


# ── Test phases ──────────────────────────────────────────────

def phase_1_platforms(adapter: RommHttpAdapter, perf: PerfCollector) -> list[dict]:
    """Phase 1: Fetch and filter platforms. Returns target platforms."""
    log.info("Phase 1: Fetching platforms...")
    with perf.time_phase("fetch_platforms"):
        all_platforms = adapter.request("/api/platforms")

    if not isinstance(all_platforms, list):
        log.error(f"Unexpected response type: {type(all_platforms)}")
        return []

    target = [p for p in all_platforms if p.get("slug") in TARGET_PLATFORM_SLUGS and p.get("rom_count", 0) > 0]
    perf.set_gauge("total_platforms_available", len(all_platforms))
    perf.set_gauge("target_platforms", len(target))

    log.info(f"  Found {len(all_platforms)} total platforms, {len(target)} target platforms")
    for p in target:
        log.info(f"    {p['slug']:12s}  {p.get('rom_count', 0):5d} ROMs")
    return target


def phase_2_roms(adapter: RommHttpAdapter, perf: PerfCollector, platforms: list[dict]) -> list[dict]:
    """Phase 2: Fetch ROMs for all target platforms (sequential, paginated)."""
    log.info("Phase 2: Fetching ROMs per platform...")
    all_roms: list[dict] = []
    platform_stats: list[dict] = []

    with perf.time_phase("fetch_roms"):
        for i, p in enumerate(platforms, 1):
            slug = p.get("slug", "?")
            pid = p["id"]
            log.info(f"  [{i}/{len(platforms)}] Fetching {slug}...")

            t0 = time.monotonic()
            roms = fetch_all_roms_paginated(adapter, pid)
            elapsed = time.monotonic() - t0

            all_roms.extend(roms)
            perf.increment("platforms_fetched")

            stat = {
                "slug": slug,
                "rom_count": len(roms),
                "expected": p.get("rom_count", 0),
                "time_sec": round(elapsed, 3),
                "requests": (len(roms) // 50) + (1 if len(roms) % 50 else 0) or 1,
            }
            platform_stats.append(stat)
            log.info(f"    → {len(roms)} ROMs in {elapsed:.1f}s")

    perf.set_gauge("total_roms", len(all_roms))
    log.info(f"  Total: {len(all_roms)} ROMs from {len(platforms)} platforms")
    return all_roms


def phase_3_collections(adapter: RommHttpAdapter, perf: PerfCollector) -> None:
    """Phase 3: Fetch collection listings (not ROMs within them)."""
    log.info("Phase 3: Fetching collections...")
    with perf.time_phase("fetch_collections"):
        try:
            colls = adapter.request("/api/collections")
            coll_count = len(colls) if isinstance(colls, list) else 0
        except Exception as e:
            log.warning(f"  Collections fetch failed: {e}")
            coll_count = 0

    perf.set_gauge("collections", coll_count)
    log.info(f"  Collections: {coll_count}")


def phase_4_prepare_shortcuts(perf: PerfCollector, all_roms: list[dict]) -> list[dict]:
    """Phase 4: Build shortcut data (CPU-bound, no I/O)."""
    log.info("Phase 4: Preparing shortcut data...")
    with perf.time_phase("prepare_shortcuts"):
        shortcuts = build_shortcuts_data(all_roms, _project_root)
    perf.set_gauge("shortcuts_prepared", len(shortcuts))
    log.info(f"  Prepared {len(shortcuts)} shortcuts")
    return shortcuts


def phase_5_artwork_sample(adapter: RommHttpAdapter, perf: PerfCollector, all_roms: list[dict]) -> None:
    """Phase 5: Download 3 sample cover images to measure per-file latency."""
    log.info("Phase 5: Downloading sample artwork (3 covers)...")
    covers_to_test = [
        r for r in all_roms
        if r.get("path_cover_small") or r.get("path_cover_large")
    ][:3]

    if not covers_to_test:
        log.warning("  No ROMs with cover art found — skipping")
        return

    with perf.time_phase("download_artwork_sample"):
        for rom in covers_to_test:
            cover_url = rom.get("path_cover_small") or rom.get("path_cover_large")
            rom_name = rom.get("name", "Unknown")
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                dest = tmp.name
            try:
                t0 = time.monotonic()
                adapter.download(f"/{cover_url}", dest)
                elapsed = time.monotonic() - t0
                size = os.path.getsize(dest)
                log.info(f"    {rom_name}: {size:,} bytes in {elapsed:.2f}s")
                perf.increment("covers_downloaded")
            except Exception as e:
                log.warning(f"    {rom_name}: FAILED — {e}")
            finally:
                if os.path.exists(dest):
                    os.unlink(dest)


# ── Main ─────────────────────────────────────────────────────

def run_perf_test() -> dict:
    """Execute all phases and return the structured report."""
    perf = PerfCollector()
    adapter = create_http_adapter(perf)

    # Quick connectivity check
    log.info(f"Testing connectivity to {ROMM_URL}...")
    try:
        heartbeat = adapter.request("/api/heartbeat")
        log.info(f"  Server version: {heartbeat.get('version', 'unknown')}")
    except Exception as e:
        log.error(f"  Cannot reach RomM server: {e}")
        return {"success": False, "error": str(e)}

    perf.start_sync()

    # Run all phases
    platforms = phase_1_platforms(adapter, perf)
    if not platforms:
        perf.end_sync()
        return {"success": False, "error": "No target platforms found"}

    all_roms = phase_2_roms(adapter, perf, platforms)
    phase_3_collections(adapter, perf)
    phase_4_prepare_shortcuts(perf, all_roms)
    phase_5_artwork_sample(adapter, perf, all_roms)

    perf.end_sync()

    # Build report
    report = perf.generate_report()
    formatted = perf.format_report()

    result = {
        "success": True,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "romm_url": ROMM_URL,
        "target_platforms": sorted(TARGET_PLATFORM_SLUGS),
        "report": report,
        "formatted": formatted,
    }

    return result


def main():
    log.info("=" * 60)
    log.info("decky-romm-sync Performance Baseline Test")
    log.info(f"RomM server: {ROMM_URL}")
    log.info(f"Target platforms: {sorted(TARGET_PLATFORM_SLUGS)}")
    log.info(f"Timeout: {TOTAL_TIMEOUT_SEC}s")
    log.info("=" * 60)

    t0 = time.monotonic()
    result = run_perf_test()
    total = time.monotonic() - t0

    if total > TOTAL_TIMEOUT_SEC:
        log.error(f"Test exceeded {TOTAL_TIMEOUT_SEC}s timeout ({total:.0f}s)")

    # Print summary
    print()
    print("=" * 60)
    if result.get("success"):
        print(result["formatted"])
        print()
        report = result["report"]
        print(f"  Total wall time:   {report['wall_time']:.1f}s")
        print(f"  HTTP requests:     {report['http']['total_requests']}")
        print(f"  Data transferred:  {report['http']['total_bytes'] / (1024 * 1024):.1f} MB")
        print(f"  HTTP errors:       {report['http']['errors']}")
        if report["phases"]:
            print("\n  Phase breakdown:")
            for name, secs in report["phases"].items():
                pct = (secs / report["wall_time"] * 100) if report["wall_time"] > 0 else 0
                print(f"    {name:30s}  {secs:7.1f}s  ({pct:.0f}%)")
    else:
        print(f"TEST FAILED: {result.get('error', 'unknown')}")
    print("=" * 60)

    # Save to file
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"\nReport saved to: {OUTPUT_PATH}")

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
