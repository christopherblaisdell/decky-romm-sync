#!/usr/bin/env python3
"""Query Decky plugin RPC methods for sync progress and perf data."""
import json
import http.client

def call_rpc(method, args=None):
    conn = http.client.HTTPConnection("localhost", 1337)
    body = json.dumps({"method": method, "args": args or {}})
    conn.request("POST", "/methods/call", body=body, headers={"Content-Type": "application/json"})
    r = conn.getresponse()
    data = r.read().decode()
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return {"status": r.status, "raw": data}

print("=== SYNC PROGRESS ===")
progress = call_rpc("get_sync_progress")
print(json.dumps(progress, indent=2))

print("\n=== PERF REPORT ===")
perf = call_rpc("get_perf_report")
print(json.dumps(perf, indent=2))
