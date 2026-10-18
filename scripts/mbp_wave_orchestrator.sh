#!/bin/bash
# MBP wave orchestrator — runs ON the M5 (user session).
#
# The corpus is LOCAL on the MBP at ~/corpus-vault (copied 2026-10-15 from
# MACMOBILE / the M5 vault). No pdfs are shipped per wave — only queues out,
# rows back.
#
# Ledger law:
#   - done.txt gets a doc ONLY when lanes-ok lands (all stages completed)
#     AND the doc's file existed at queue-build time.
#   - missing files -> missing-docs.log, NOT done (they may land later).
#   - a failed/timed-out wave marks NOTHING; docs accumulate attempts and
#     park after 2 failures so poison PDFs cannot eat the night.
set -u
cd /Users/samkim/Harnessv1
MBP=samsonkim@100.109.86.30
WAVE=200
STATE=results/mbp-worker-20261014
MBP_ROOT=/Users/samsonkim/corpus-vault
CANON_ROOT=/Volumes/M5_4TB/vault
mkdir -p "$STATE"
touch "$STATE/done.txt" "$STATE/parked.txt"

scp -q scripts/mbp_run_lanes.sh "$MBP:mbp_run_lanes.sh"

while true; do
  python3 - "$WAVE" "$STATE" <<'PYEOF'
import json, sys
wave_n, state = int(sys.argv[1]), sys.argv[2]
paths = json.load(open("/tmp/vault-paths.json"))
done = set(l.strip() for l in open(f"{state}/done.txt") if l.strip())
parked = set(l.strip() for l in open(f"{state}/parked.txt") if l.strip())
todo = [p for p in paths if p not in done and p not in parked]
json.dump(todo[:wave_n], open("/tmp/wave-current.json", "w"))
print(len(todo), min(wave_n, len(todo)))
PYEOF
  TODO=$(python3 -c "import json;print(len(json.load(open('/tmp/wave-current.json'))))")
  if [ "$TODO" = "0" ]; then echo "$(date -u +%FT%TZ) VAULT COMPLETE"; break; fi

  echo "$(date -u +%FT%TZ) wave: scheduling $TODO docs"
  scp -q /tmp/wave-current.json "$MBP:/tmp/wave-current.json"
  DELIVERED=$(ssh -o BatchMode=yes "$MBP" 'python3 -c "
import json, os
paths = json.load(open(\"/tmp/wave-current.json\"))
docs, missing = [], []
for p in paths:
    if \"/vault\" not in p:
        continue
    mp = \"/Users/samsonkim/corpus-vault\" + p.split(\"/vault\", 1)[1]
    if os.path.exists(mp):
        docs.append({\"source_path\": mp, \"part_number\": p.rsplit(\"/\",1)[-1][:-4][:96]})
    else:
        missing.append(p)
json.dump({\"documents\": docs}, open(\"/tmp/mbp-queue.json\", \"w\"))
json.dump(missing, open(\"/tmp/mbp-missing.json\", \"w\"))
print(len(docs))"')
  scp -q "$MBP:/tmp/mbp-missing.json" "$STATE/missing-wave.json" 2>/dev/null
  python3 - "$STATE" <<'PYEOF'
import json, sys, os
state = sys.argv[1]
try:
    missing = json.load(open(f"{state}/missing-wave.json"))
except Exception:
    missing = []
if missing:
    with open(f"{state}/missing-docs.log", "a") as fh:
        for p in missing:
            fh.write(p + "\n")
print(f"missing-files: {len(missing)}")
PYEOF

  echo "$(date -u +%FT%TZ) wave: lanes on MBP ($DELIVERED deliverable)"
  ssh -o BatchMode=yes "$MBP" 'bash mbp_run_lanes.sh' 2>&1 | tail -2
  LANES_OK=$(ssh -o BatchMode=yes "$MBP" 'test -f lane-out/lanes-ok && echo yes || echo no')

  for f in thermal-w.jsonl mode-w-m0.jsonl mode-w-m1.jsonl mode-w-m2.jsonl mode-w-m3.jsonl mode-w-m4.jsonl mode-w-m5.jsonl; do
    rsync -a -e ssh "$MBP:lane-out/$f" "$STATE/" 2>/dev/null
  done

  python3 - "$STATE" "$MBP_ROOT" "$CANON_ROOT" "$LANES_OK" <<'PYEOF'
import json, sys, os
state, mbp_root, canon_root, lanes_ok = sys.argv[1:5]
ok = lanes_ok == "yes"

files = ["thermal-w.jsonl"] + [f"mode-w-m{i}.jsonl" for i in range(6)]
for src in files:
    s, d = os.path.join(state, src), os.path.join(state, src.replace("-w", "-mbp", 1) if src == "thermal-w.jsonl" else "mode-mbp.jsonl")
    if not os.path.exists(s):
        continue
    with open(d, "a") as out:
        for l in open(s):
            try:
                r = json.loads(l)
            except Exception:
                continue
            sp = r.get("source_path") or ""
            r["source_path"] = sp.replace(mbp_root, canon_root)
            out.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    os.remove(s)

wave = json.load(open("/tmp/wave-current.json"))
try:
    missing = set(json.load(open(f"{state}/missing-wave.json")))
except Exception:
    missing = set()
wave = [p for p in wave if p not in missing]
if ok:
    with open(f"{state}/done.txt", "a") as fh:
        for p in wave:
            fh.write(p + "\n")
    print(f"wave merged: done={len(wave)}")
else:
    # attempts accounting; park docs that failed twice
    att_path = f"{state}/attempts.json"
    att = {}
    if os.path.exists(att_path):
        att = json.load(open(att_path))
    parked_now = []
    for p in wave:
        att[p] = att.get(p, 0) + 1
        if att[p] >= 2:
            parked_now.append(p)
    json.dump(att, open(att_path, "w"))
    if parked_now:
        with open(f"{state}/parked.txt", "a") as fh:
            for p in parked_now:
                fh.write(p + "\n")
    print(f"wave FAILED: 0 done, {len(parked_now)} parked after 2 attempts")
PYEOF

  ssh -o BatchMode=yes "$MBP" 'rm -f lane-out/*.jsonl lane-out/lanes-ok /tmp/mbp-queue*.json /tmp/mbp-missing.json' 2>/dev/null
  echo "$(date -u +%FT%TZ) wave: complete"
done
