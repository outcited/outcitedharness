import json, time, sys, urllib.request, urllib.error
sys.path.insert(0, "/Users/samkim/Harnessv1")
from harness.gateway.qwen_tools import parse_qwen_tool_text

BASE = "http://100.68.133.1:8900"
MODEL = "qwen3.8-27b-nvfp4"

def post(path, body, timeout=180):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))

# --- readiness ---
t0 = time.time()
while True:
    try:
        urllib.request.urlopen(BASE + "/v1/models", timeout=5); break
    except Exception:
        if time.time() - t0 > 900: print("SERVER NEVER READY"); sys.exit(1)
        time.sleep(10)
print(f"[ready in {time.time()-t0:.0f}s]")

# --- 1. SPEED (thinking off, same method as the canon 8.3 baseline) ---
body = {"model": MODEL, "prompt": "List ten short operational status codes for a data pipeline, one per line, plain text.",
        "max_tokens": 200, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}
t0 = time.time(); r = post("/v1/completions", body); dt = time.time() - t0
u = r.get("usage", {})
print(f"[speed] elapsed={dt:.1f}s tokens={u.get('completion_tokens')} rate={u.get('completion_tokens',0)/dt:.1f} tok/s")
print(f"[speed] sample: {r['choices'][0].get('text','')[:100]!r}")

# --- 2. TOOL CALLS (orchestration-style, parsed with the qwen parser) ---
tools = [{"type": "function", "function": {
    "name": "fleet_action",
    "description": "Execute an infrastructure orchestration action",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["restart_service", "scale_workers", "reroute_traffic", "page_oncall"]},
        "target": {"type": "string", "description": "box or service name"},
        "reason": {"type": "string"}}, "required": ["action", "target", "reason"]}}}]
scenarios = [
    "The extraction queue on mbp-intel has been stalled for 40 minutes with 200 docs pending. Decide the action.",
    "Canon verdict latency on asus4 just spiked to 90 seconds. Handle it.",
    "We need 3 more extraction workers for tonight's facet run. Decide.",
    "dgx1 overflow endpoint is returning connection refused. Handle it.",
    "Disk on asus2 is at 91 percent. Decide the action.",
]
ok = 0
for i, s in enumerate(scenarios):
    body = {"model": MODEL, "messages": [{"role": "system", "content": "You are the fleet orchestrator. Use the fleet_action tool. Respond with the tool call only."},
                                          {"role": "user", "content": s}],
            "tools": tools, "tool_choice": "auto", "temperature": 0, "max_tokens": 400,
            "chat_template_kwargs": {"enable_thinking": False}}
    t0 = time.time(); r = post("/v1/chat/completions", body); dt = time.time() - t0
    msg = r["choices"][0]["message"]
    content = msg.get("content") or ""
    native = msg.get("tool_calls")
    parsed = parse_qwen_tool_text(content) if content else []
    calls = native or parsed
    if calls:
        c = calls[0]
        fn = c.get("function", {})
        args = fn.get("arguments") or fn.get("parameters", {})
        if isinstance(args, str):
            args = json.loads(args)  # must parse cleanly as JSON
        print(f"[tool {i+1}] {dt:.1f}s OK action={args.get('action')} target={args.get('target')} reason={str(args.get('reason'))[:40]!r}")
        ok += 1
    else:
        print(f"[tool {i+1}] {dt:.1f}s FAIL content={content[:120]!r}")
print(f"[tools] {ok}/{len(scenarios)} clean")
