import sys, os, json, urllib.request

sys.path.insert(0, "/home/samsonkim/seven-director/app06")
TOKEN = open("/home/samsonkim/seven-director/app06/.gci_token").read().strip()

# 1. GCI search
req = urllib.request.Request(
    "http://100.100.116.82:8810/search-exact",
    data=json.dumps({"query": "watcher_health"}).encode(),
    headers={"Content-Type": "application/json",
             "Authorization": "Bearer " + TOKEN})
with urllib.request.urlopen(req, timeout=20) as r:
    hits = json.loads(r.read()).get("hits", [])
print("GCI hits for 'watcher_health':", len(hits))
for h in hits[:2]:
    print("  ", h.get("path","?")[:70], "| line", h.get("start_line"))

# 2. ASUS2 reasoning
req2 = urllib.request.Request(
    "http://100.68.133.1:8900/v1/chat/completions",
    data=json.dumps({
        "model": "qwen3.8-27b-nvfp4",
        "messages": [{"role": "user", "content":
            "The sentinel watcher has been stale for 249 hours. "
            "What code module handles sentinel watcher health and "
            "what should be checked first?"}],
        "max_tokens": 200, "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False}
    }).encode(),
    headers={"Content-Type": "application/json"})
import time
t0 = time.time()
with urllib.request.urlopen(req2, timeout=60) as r:
    resp = json.loads(r.read())
lat = time.time() - t0
text = resp["choices"][0]["message"]["content"]
print()
print("=== ASUS2 response (latency %.1fs) ===" % lat)
print(text[:500])
print()
print("tokens:", resp.get("usage", {}).get("total_tokens"))
