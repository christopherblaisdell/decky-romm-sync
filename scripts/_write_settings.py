#!/usr/bin/env python3
"""Write plugin settings.json on the Deck. Run via: python3 /tmp/_write_settings.py"""
import json, os
d = {
    "romm_url": "http://theblaze-aorus:8098",
    "romm_user": "cblaisdell",
    "romm_pass": "comcast",
    "enabled_platforms": {},
    "enabled_collections": {},
    "collection_create_platform_groups": False,
    "steam_input_mode": "default",
    "steamgriddb_api_key": "",
    "romm_allow_insecure_ssl": False,
    "log_level": "info",
    "version": 1
}
p = os.path.expanduser("~/homebrew/settings/decky-romm-sync/settings.json")
with open(p, "w") as f:
    json.dump(d, f, indent=2)
print(json.dumps(json.load(open(p)), indent=2))
