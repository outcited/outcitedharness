#!/bin/bash
# MBP wave orchestrator — runs ON the M5 (launchd).
#
# Pushes vault PDFs in waves to the old MacBook Pro (samsonkim@100.109.86.30),
# runs thermal + mode lanes there (16 local threads; no mounts, no keys),
# collects rows back with M5-canonical paths, frees the wave, repeats.
# Done-tracking: plain path list in done.txt; rows append to *-mbp.jsonl.
set -u
cd /Users/samkim/Harnessv1
MBP=samsonkim@100.109.86.30
WAVE=2000
STATE=results/mbp-worker-20261014
mkdir -p "$STATE"
touch "$STATE/done.txt"

while true; do
  # next wave = vault paths not in done.txt
  python3 - "$WAVE" "$STATE" <<'PYEOF'
import json, sys
wave_n, state = int(sys.argv[1]), sys.argv[2]
paths = json.load(open("/tmp/vault-paths.json"))
done = set(l.strip() for l in open(f"{state}/done.txt") if l.strip())
todo = [p for p in paths if p not in done]
json.dump(todo[:wave_n], open("/tmp/wave-current.json", "w"))
print(len(todo), min(wave_n, len(todo)))
PYEOF
  TODO=$(python3 -c "import json;print(len(json.load(open('/tmp/wave-current.json'))))")
  if [ "$TODO" = "0" ]; then echo "$(date -u +%FT%TZ) VAULT COMPLETE"; break; fi

  echo "$(date -u +%FT%TZ) wave: pushing $TODO PDFs"
  python3 -c "import json;[print(p) for p in json.load(open('/tmp/wave-current.json'))]" > /tmp/wave-list.txt
  rsync -a --files-from=/tmp/wave-list.txt --relative -e ssh /Volumes/M5_4TB/ "$MBP:corpus-wave/" 2>&1 | tail -1
  scp -q /tmp/wave-current.json "$MBP:/tmp/wave-current.json"
  ssh -o BatchMode=yes "$MBP" 'python3 -c "
import json
paths=json.load(open(\"/tmp/wave-current.json\"))
docs=[{\"source_path\": \"/Users/samsonkim/corpus-wave/vault\"+p.split(\"/vault\",1)[1], \"part_number\": p.rsplit(\"/\",1)[-1][:-4][:96]} for p in paths]
json.dump({\"documents\": docs}, open(\"/tmp/mbp-queue.json\",\"w\"))
print(\"queue:\", len(docs))"'

  echo "$(date -u +%FT%TZ) wave: lanes on MBP"
  ssh -o BatchMode=yes "$MBP" 'cd ~/Harnessv1 && \
    ~/harness-venv/bin/python scripts/extract_thermal_metrics.py --queue /tmp/mbp-queue.json --pdf-root /Users/samsonkim/corpus-wave --out lane-out/thermal-w.jsonl --workers 8 --doc-timeout 45 ; \
    ~/harness-venv/bin/python scripts/extract_mode_currents.py --queue /tmp/mbp-queue.json --out lane-out/mode-w.jsonl' 2>&1 | tail -3

  rsync -a -e ssh "$MBP:lane-out/thermal-w.jsonl" "$STATE/" 2>/dev/null
  rsync -a -e ssh "$MBP:lane-out/mode-w.jsonl" "$STATE/" 2>/dev/null

  python3 - "$STATE" <<'PYEOF'
import json, sys, os
state = sys.argv[1]
for src, dst in (("thermal-w.jsonl", "thermal-mbp.jsonl"), ("mode-w.jsonl", "mode-mbp.jsonl")):
    s, d = f"{state}/{src}", f"{state}/{dst}"
    if not os.path.exists(s):
        continue
    with open(d, "a") as out:
        for l in open(s):
            try:
                r = json.loads(l)
            except Exception:
                continue
            sp = r.get("source_path") or ""
            r["source_path"] = sp.replace("/Users/samsonkim/corpus-wave", "/Volumes/M5_4TB")
            out.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    os.remove(s)
with open(f"{state}/done.txt", "a") as fh:
    for p in json.load(open("/tmp/wave-current.json")):
        fh.write(p + "\n")
print("wave merged + done recorded")
PYEOF

  ssh -o BatchMode=yes "$MBP" 'rm -rf corpus-wave/* lane-out/*-w.jsonl /tmp/mbp-queue.json' 2>/dev/null
  echo "$(date -u +%FT%TZ) wave: complete"
done