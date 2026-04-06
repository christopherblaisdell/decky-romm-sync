#!/usr/bin/env python3
"""Check sync progress by counting artwork files and checking state."""
import glob
import os
import time

# Find grid dir
userdata = os.path.expanduser("~/.steam/steam/userdata")
grid = None
for d in os.listdir(userdata):
    candidate = os.path.join(userdata, d, "config", "grid")
    if os.path.isdir(candidate):
        grid = candidate
        break

if not grid:
    print("ERROR: No grid dir found")
    exit(1)

# Count files
staging = glob.glob(os.path.join(grid, "romm_*_cover.png"))
final = glob.glob(os.path.join(grid, "*p.png"))

print(f"Grid dir: {grid}")
print(f"Staging covers (romm_*_cover.png): {len(staging)}")
print(f"Final covers (*p.png): {len(final)}")
print(f"Total artwork files: {len(staging) + len(final)}")

# Check perf report
perf = os.path.expanduser("~/homebrew/plugins/decky-romm-sync/perf_report.json")
if os.path.exists(perf):
    print(f"\nPerf report EXISTS — sync complete!")
    import json
    with open(perf) as f:
        print(json.dumps(json.load(f), indent=2))
else:
    print(f"\nNo perf_report.json — sync still running")

# Check TCP connections to RomM
import subprocess
result = subprocess.run(["ss", "-tn"], capture_output=True, text=True)
romm_conns = [l for l in result.stdout.splitlines() if "8098" in l]
print(f"Active connections to RomM: {len(romm_conns)}")
for c in romm_conns:
    print(f"  {c.strip()}")
