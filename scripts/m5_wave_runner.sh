#!/bin/bash
# M5 local facet runner — same ledger as the MBP orchestrator, but lanes run
# HERE (M5 Max 18c/128G, corpus is canonical on /Volumes/M5_4TB — user
# session, not launchd: TCC). Waves come from the TAIL of the todo list so
# M5 and the MBP grind toward the middle and never double-work.
#
# Measured 2026-10-15 (same 2 mcu-manual docs): M5 is ~5x the MBP —
# thermal 16.7s vs 89s, mode 12.4s vs 58s.
set -u
cd /Users/samkim/Harnessv1
WAVE=400
STATE=results/mbp-worker-20261014
CANON=/Volumes/M5_4TB
mkdir -p "$STATE"
touch "$STATE/done.txt" "$STATE/parked.txt"

while true; do
  python3 - "$WAVE" "$STATE" <<'PYEOF'
import json, sys
wave_n, state = int(sys.argv[1]), sys.argv[2]
paths = json.load(open("/tmp/vault-paths.json"))
done = set(l.strip() for l in open(f"{state}/done.txt") if l.strip())
parked = set(l.strip() for l in open(f"{state}/parked.txt") if l.strip())
todo = [p for p in paths if p not in done and p not in parked]
json.dump(todo[-wave_n:], open("/tmp/m5-wave-current.json", "w"))
print(len(todo), min(wave_n, len(todo)))
PYEOF
  TODO=$(python3 -c "import json;print(len(json.load(open('/tmp/m5-wave-current.json'))))")
  if [ "$TODO" = "0" ]; then echo "$(date -u +%FT%TZ) M5 VAULT COMPLETE"; break; fi

  echo "$(date -u +%FT%TZ) m5-wave: $TODO docs (tail)"
  python3 - <<'PYEOF'
import json, os
paths = json.load(open("/tmp/m5-wave-current.json"))
docs, missing = [], []
for p in paths:
    if os.path.exists(p):
        docs.append({"source_path": p, "part_number": p.rsplit("/", 1)[-1][:-4][:96]})
    else:
        missing.append(p)
json.dump({"documents": docs}, open("/tmp/m5-queue.json", "w"))
# ledger law: only deliverable docs may enter done.txt later — shrink the
# wave file itself so the merge step cannot mark missing files done.
json.dump([d["source_path"] for d in docs], open("/tmp/m5-wave-current.json", "w"))
if missing:
    with open("results/mbp-worker-20261014/missing-docs.log", "a") as fh:
        for p in missing:
            fh.write(p + "\n")
print(f"deliverable={len(docs)} missing={len(missing)}")
PYEOF

  OUT="$STATE/m5-lane-out"
  mkdir -p "$OUT"
  rm -f "$OUT/lanes-ok"

  perl -e 'alarm 3300; exec @ARGV' .venv/bin/python \
    scripts/extract_thermal_metrics.py --queue /tmp/m5-queue.json \
    --pdf-root "$CANON" --out "$OUT/thermal-w.jsonl" --workers 12 --doc-timeout 45
  T_RC=$?

  python3 - <<'PYEOF'
import json
docs = json.load(open("/tmp/m5-queue.json"))["documents"]
for i in range(8):
    json.dump({"documents": docs[i::8]}, open(f"/tmp/m5-queue-m{i}.json", "w"))
PYEOF
  pids=()
  for i in 0 1 2 3 4 5 6 7; do
    perl -e 'alarm 3300; exec @ARGV' .venv/bin/python \
      scripts/extract_mode_currents.py --queue "/tmp/m5-queue-m$i.json" \
      --out "$OUT/mode-w-m$i.jsonl" &
    pids+=($!)
  done
  M_RC=0
  for p in "${pids[@]}"; do wait "$p" || M_RC=1; done

  python3 - "$STATE" "$OUT" "$T_RC" "$M_RC" <<'PYEOF'
import json, sys, os
state, out, t_rc, m_rc = sys.argv[1:5]
ok = (t_rc == "0" and m_rc == "0")

files = ["thermal-w.jsonl"] + [f"mode-w-m{i}.jsonl" for i in range(8)]
for src in files:
    s = os.path.join(out, src)
    if not os.path.exists(s):
        continue
    d = os.path.join(state, "thermal-mbp.jsonl" if src == "thermal-w.jsonl" else "mode-mbp.jsonl")
    with open(d, "a") as dst:
        for l in open(s):
            try:
                r = json.loads(l)
            except Exception:
                continue
            dst.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    os.remove(s)

wave = json.load(open("/tmp/m5-wave-current.json"))
if ok:
    with open(f"{state}/done.txt", "a") as fh:
        for p in wave:
            fh.write(p + "\n")
    print(f"m5-wave merged: done={len(wave)}")
else:
    att_path = f"{state}/attempts-m5.json"
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
    print(f"m5-wave FAILED t={t_rc} m={m_rc}: 0 done, {len(parked_now)} parked")
PYEOF

  rm -f /tmp/m5-queue*.json
  echo "$(date -u +%FT%TZ) m5-wave: complete"
done
