#!/usr/bin/env python3
"""Parallel OpenRouter worker for power extraction. Direct calls, no batch."""
import json, os, sys, time, urllib.request, urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
OR_API = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-v4.1-flash"
MAX_CHARS = 28000
SUBSTRATES = ROOT / "results/substrates"
OUTPUT = ROOT / "results/production-power-lane/output-cloud"

def _key():
    for l in (ROOT / ".env").read_text().splitlines():
        if l.startswith("OPENROUTER_API_KEY="):
            return l.split("=", 1)[1].strip()
    raise SystemExit("no key")

PROMPT = (
    "You are a datasheet parametric extraction engine. Extract parametric table rows. "
    "Every value MUST carry a quote copied character-for-character from the text. "
    "Output ONLY JSON: [{\"symbol\": str, \"value\": number, \"unit\": str, "
    "\"qualifier\": str|null, \"condition\": str|null, \"quote\": str}]"
)

def extract(stem: str, key: str) -> dict:
    sub = SUBSTRATES / f"{stem}.json"
    if not sub.exists():
        return {"stem": stem, "skip": "no_substrate"}
    d = json.loads(sub.read_text())
    text = ""
    for p in d.get("pages", []):
        text += (p.get("text", "") or "") + "\n"
    text = text[:MAX_CHARS]
    if len(text.strip()) < 100:
        return {"stem": stem, "skip": "short_text"}

    body = json.dumps({
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 16000,
        "reasoning": {"effort": "low"},
        "messages": [{"role": "user", "content": f"{PROMPT}\n\n{text}"}],
    }).encode()
    req = urllib.request.Request(OR_API, data=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost/harness",
        "X-Title": "power-lane",
    })
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            result = json.loads(resp.read())
        content = result["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content
        if content.endswith("```"):
            content = content.rsplit("```", 1)[0]
        start = content.find("[")
        claims = json.loads(content[start:content.rfind("]") + 1]) if start >= 0 else []
        return {"stem": stem, "tier": "cloud-v41", "claims": claims,
                "surviving": len(claims), "p0": 0}
    except Exception as e:
        return {"stem": stem, "skip": f"error:{str(e)[:50]}"}

def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    key = _key()
    OUTPUT.mkdir(parents=True, exist_ok=True)

    queue_path = ROOT / "results/production-power-lane/batch-queue.jsonl"
    items = [json.loads(l) for l in queue_path.read_text().splitlines() if l.strip()]

    # skip already done
    done = {f.stem for f in OUTPUT.glob("*.json")}
    todo = [i["stem"] for i in items if i["stem"] not in done]
    print(f"todo: {len(todo)} docs, workers: {workers}")

    t0 = time.time()
    completed = 0
    with ThreadPoolExecutor(workers) as ex:
        futures = {ex.submit(extract, stem, key): stem for stem in todo}
        for future in as_completed(futures):
            result = future.result()
            out = OUTPUT / f"{result['stem']}.json"
            out.write_text(json.dumps(result, ensure_ascii=False))
            completed += 1
            if completed % 10 == 0:
                elapsed = time.time() - t0
                rate = completed / elapsed * 3600
                print(f"  {completed}/{len(todo)} ({rate:.0f} docs/hr, ${elapsed:.0f}s)", flush=True)

    print(f"done: {completed} docs in {time.time()-t0:.0f}s")

if __name__ == "__main__":
    sys.exit(main() or 0)
