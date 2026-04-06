#!/usr/bin/env python3
import json, sys
cols = json.load(sys.stdin)
targets = {94, 90, 10, 6}
for c in cols:
    if c["id"] in targets:
        print(c["id"], c["rom_count"], c["name"])
