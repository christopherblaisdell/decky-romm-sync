#!/usr/bin/env python3
"""Fetch platform and collection inventory from RomM."""
import sys, json, logging
sys.path.insert(0, "py_modules")
from adapters.romm.http import RommHttpAdapter

a = RommHttpAdapter(
    settings={"romm_url": "http://theblaze-aorus:8098", "romm_user": "cblaisdell",
              "romm_pass": "comcast", "romm_allow_insecure_ssl": False},
    plugin_dir=".", logger=logging.getLogger(),
)
platforms = a.request("/api/platforms")
platforms.sort(key=lambda p: p.get("rom_count", 0), reverse=True)
print("=== TOP 20 PLATFORMS BY ROM COUNT ===")
for p in platforms[:20]:
    print(f"  {p['slug']:20s} {p.get('rom_count',0):6d} ROMs  id={p['id']}")
total_roms = sum(p.get("rom_count", 0) for p in platforms)
print(f"\n=== TOTAL: {len(platforms)} platforms, {total_roms} ROMs ===")

# Size buckets
small = [p for p in platforms if 0 < p.get("rom_count", 0) <= 200]
medium = [p for p in platforms if 200 < p.get("rom_count", 0) <= 800]
large = [p for p in platforms if p.get("rom_count", 0) > 800]
print(f"\nSmall (1-200): {len(small)} platforms")
print(f"Medium (201-800): {len(medium)} platforms")
print(f"Large (800+): {len(large)} platforms")

colls = a.request("/api/collections")
print(f"\n=== COLLECTIONS ({len(colls)}) ===")
for c in sorted(colls, key=lambda c: c.get("rom_count", 0), reverse=True)[:20]:
    print(f"  {c.get('name','?'):45s} id={c['id']:4d}  roms={c.get('rom_count', '?')}")
if len(colls) > 20:
    print(f"  ... and {len(colls)-20} more")
