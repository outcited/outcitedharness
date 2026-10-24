#!/bin/bash
# asus2 wave orchestrator — runs ON the M5 (user session), lanes execute on
# asus2 (GX10 20c/121G, GPU 0% — free since v2 training completed) over ssh.
# Waves come from the MIDDLE of the todo list: MBP grinds the front, M5 the
# tail, asus2 works inward from the center. Ledger is shared on M5.
#
# Corpus: rsync'd once from M5 to ~/corpus-vault on asus2 (cas/ + landing/).
# Partial corpus is fine — undelivered docs fall to missing-docs.log and
# retry later; only verified-delivered + lane-completed docs enter done.txt.
set -u
cd /Users/samkim/Harnessv1
BOX=samkimasus2@100.68.133.1
WAVE=200
STATE=results/mbp-worker-20261014
BOX_ROOT=/home/samkimasus2/corpus-vault
CANON_ROOT=/Volumes/M5_4TB/vault
mkdir -p "$STATE"
touch "$STATE/done.txt" "$STATE/parked.txt"

scp -q scripts/mbp_run_lanes.sh "$BOX:mbp_run_lanes.sh"

while true; do
  python3 - "$WAVE" "$STATE" <<'PYEOF'
import json, sys
wave_n, state = int(sys.argv[1]), sys.argv[2]
paths = json.load(open("/tmp/vault-paths.json"))
done = set(l.strip() for l in open(f"{state}/done.txt") if l.strip())
parked = set(l.strip() for l in open(f"{state}/parked.txt") if l.strip())
todo = [p for p in paths if p not in done and p not in parked]
half = wave_n // 2
mid = len(todo) // 2
sel = todo[max(0, mid - half): mid + half] or todo[:wave_n]
json.dump(sel, open("/tmp/asus2-wave-current.json", "w"))
print(len(todo), len(sel))
PYEOF
  TODO=$(python3 -c "import json;print(len(json.load(open('/tmp/asus2-wave-current.json'))))")
  if [ "$TODO" = "0" ]; then echo "$(date -u +%FT%TZ) ASUS2 VAULT COMPLETE"; break; fi

  echo "$(date -u +%FT%TZ) asus2-wave: $TODO docs (middle)"
  scp -q /tmp/asus2-wave-current.json "$BOX:/tmp/wave-current.json"
  DELIVERED=$(ssh -o BatchMode=yes "$BOX" 'python3 -c "
import json, os
paths = json.load(open(\"/tmp/wave-current.json\"))
docs, missing = [], []
for p in paths:
    if \"/vault\" not in p:
        continue
    mp = \"/home/samkimasus2/corpus-vault\" + p.split(\"/vault\", 1)[1]
    if os.path.exists(mp):
        docs.append({\"source_path\": mp, \"part_number\": p.rsplit(\"/\",1)[-1][:-4][:96]})
    else:
        missing.append(p)
json.dump({\"documents\": docs}, open(\"/tmp/mbp-queue.json\", \"w\"))
json.dump(missing, open(\"/tmp/mbp-missing.json\", \"w\"))
print(len(docs))"')
  scp -q "$BOX:/tmp/mbp-missing.json" "$STATE/missing-wave.json" 2>/dev/null
  python3 - "$STATE" <<'PYEOF'
import json, sys
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

  if [ "$DELIVERED" = "0" ]; then
    echo "$(date -u +%FT%TZ) asus2-wave: nothing delivered yet (corpus still landing), sleeping 120"
    sleep 120
    continue
  fi

  echo "$(date -u +%FT%TZ) asus2-wave: lanes on asus2 ($DELIVERED deliverable)"
  ssh -o BatchMode=yes "$BOX" 'CORPUS_ROOT=/home/samkimasus2/corpus-vault bash mbp_run_lanes.sh' 2>&1 | tail -2
  LANES_OK=$(ssh -o BatchMode=yes "$BOX" 'test -f lane-out/lanes-ok && echo yes || echo no')

  for f in thermal-w.jsonl mode-w-m0.jsonl mode-w-m1.jsonl mode-w-m2.jsonl mode-w-m3.jsonl mode-w-m4.jsonl mode-w-m5.jsonl; do
    rsync -a -e ssh "$BOX:lane-out/$f" "$STATE/" 2>/dev/null
  done

  python3 - "$STATE" "$BOX_ROOT" "$CANON_ROOT" "$LANES_OK" <<'PYEOF'
import json, sys, os
state, box_root, canon_root, lanes_ok = sys.argv[1:5]
ok = lanes_ok == "yes"

files = ["thermal-w.jsonl"] + [f"mode-w-m{i}.jsonl" for i in range(6)]
for src in files:
    s = os.path.join(state, src)
    if not os.path.exists(s):
        continue
    d = os.path.join(state, "thermal-mbp.jsonl" if src == "thermal-w.jsonl" else "mode-mbp.jsonl")
    with open(d, "a") as out:
        for l in open(s):
            try:
                r = json.loads(l)
            except Exception:
                continue
            sp = r.get("source_path") or ""
            r["source_path"] = sp.replace(box_root, canon_root)
            out.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    os.remove(s)

wave = json.load(open("/tmp/asus2-wave-current.json"))
try:
    missing = set(json.load(open(f"{state}/missing-wave.json")))
except Exception:
    missing = set()
wave = [p for p in wave if p not in missing]
if ok:
    with open(f"{state}/done.txt", "a") as fh:
        for p in wave:
            fh.write(p + "\n")
    print(f"asus2-wave merged: done={len(wave)}")
else:
    att_path = f"{state}/attempts-asus2.json"
    att = json.load(open(att_path)) if os.path.exists(att_path) else {}
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
    print(f"asus2-wave FAILED: 0 done, {len(parked_now)} parked")
PYEOF

  ssh -o BatchMode=yes "$BOX" 'rm -f lane-out/*.jsonl lane-out/lanes-ok /tmp/mbp-queue*.json /tmp/mbp-missing.json' 2>/dev/null
  echo "$(date -u +%FT%TZ) asus2-wave: complete"
done
