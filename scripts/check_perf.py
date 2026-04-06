#!/usr/bin/env python3
"""Check sync progress by inspecting plugin state directly."""
import json
import os
import sys

# Check for perf_report.json
perf_path = os.path.expanduser("~/homebrew/plugins/decky-romm-sync/perf_report.json")
if os.path.exists(perf_path):
    with open(perf_path) as f:
        data = json.load(f)
    print("=== PERF REPORT (from file) ===")
    print(json.dumps(data, indent=2))
else:
    print(f"No perf_report.json yet at {perf_path}")

# Check settings for sync state
settings_path = os.path.expanduser("~/homebrew/settings/decky-romm-sync/settings.json")
if os.path.exists(settings_path):
    with open(settings_path) as f:
        settings = json.load(f)
    print("\n=== SETTINGS (sync-related) ===")
    for k in sorted(settings.keys()):
        if "sync" in k.lower() or "platform" in k.lower() or "collection" in k.lower():
            v = settings[k]
            if isinstance(v, str) and len(v) > 200:
                v = v[:200] + "..."
            print(f"  {k}: {v}")

# Check state file
state_path = os.path.expanduser("~/homebrew/settings/decky-romm-sync/state.json")
if os.path.exists(state_path):
    with open(state_path) as f:
        state = json.load(f)
    print(f"\n=== STATE (keys: {list(state.keys())}) ===")
    # Show sync-related keys
    for k in state:
        if "sync" in k.lower() or "progress" in k.lower():
            print(f"  {k}: {state[k]}")
