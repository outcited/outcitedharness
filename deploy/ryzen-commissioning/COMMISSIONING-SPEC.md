# ============================================================================
# COMMISSIONING SPEC — new extraction box (Machine A/B)
# Kit: Harnessv1/deploy/ryzen-commissioning/     (tested paths, real args)
#
# WHAT THIS BOX BECOMES: a self-feeding extraction lane worker that emits
# harness.extraction-telemetry.v1 so the Director (seven) can see it.
#
# Total operator time: ~15 min + one unattended build (~10 min).
# Everything privileged is in steps 1 and 6. Everything else is samsonkim.
# ============================================================================

## 0. Prereqs (operator, once)
#  - Ubuntu 24.04 installed, box on Tailscale, SSH key auth working
#  - code source reachable: rsync from M5 or happy (same layout)

## 1. Base system (sudo)
sudo apt-get update && sudo apt-get install -y \
    build-essential python3.12-venv python3-pip sqlite3 curl git rsync

## 2. Code + venv (samsonkim)
mkdir -p ~/Harnessv1-powerdata01
rsync -avz M5:~/Harnessv1/scripts/  ~/Harnessv1/scripts/
rsync -avz M5:~/Harnessv1/harness/  ~/Harnessv1/harness/
rsync -avz M5:~/Harnessv1/config/   ~/Harnessv1/config/
rsync -avz M5:~/Harnessv1/deploy/   ~/Harnessv1/deploy/
rsync -avz M5:~/Harnessv1-powerdata01/designwins/ ~/Harnessv1-powerdata01/designwins/
python3 -m venv ~/Harnessv1/.venv
~/Harnessv1/.venv/bin/pip install --upgrade pip
~/Harnessv1/.venv/bin/pip install pymupdf requests pydantic pyyaml httpx
#   NOTE vs old kit: + pyyaml + httpx. httpx is required by the queue-based
#   curve tooling and the telemetry emitter; without it those die on import.

## 3. Rust bridge (the truth-provenance lane)
cd ~/Harnessv1-powerdata01/designwins && cargo build --release
bash ~/Harnessv1/deploy/ryzen-commissioning/canary-test.sh
#   PASS = dw-bridge prints verification rates > 0%. FAIL = stop, do not
#   enqueue work against a broken bridge.

## 4. Work population (what this box will chew)
#  Option A (recommended): its own shard so boxes never double-process:
mkdir -p ~/lane-out ~/corpus-vault
rsync -avz happy:/home/samsonkim/corpus-vault/cas/  ~/corpus-vault/cas/
python3 - <<'PY'
# split the vault queue into a box-local shard (id-stable)
import json, hashlib
docs = json.load(open('/tmp/vault-queue.json'))['documents']
box = json.load(open('/etc/hostname')) if False else None
me = open('/etc/hostname').read().strip()
n = 4  # boxes: happy + machine-a + machine-b + spare
shard = [d for d in docs
         if int(hashlib.sha256(d['source_path'].encode()).hexdigest(), 16) % n
         in ({'machine-a': 1, 'machine-b': 2}.get(me, 3),)]
json.dump({'documents': shard}, open('/tmp/vault-queue.json', 'w'))
print('shard size:', len(shard))
PY

## 5. Feeder (the fix that matters)
install -m 755 ~/Harnessv1/deploy/ryzen-commissioning/work_feeder.py \
               ~/Harnessv1/scripts/work_feeder.py
#   v2 fixes: PYTHONPATH per launch (v1's `import harness` failures), REAL
#   argparse contracts per script (v1 had 7 of 9 lanes wrong), skip-with-
#   reason for lanes lacking inputs, thrash quarantine instead of ~720
#   respawn-failures/hour. Verify dry:
~/Harnessv1/.venv/bin/python ~/Harnessv1/scripts/work_feeder.py &
sleep 35 && grep -c "launched\|SKIP" /tmp/feeder-*.log 2>/dev/null; kill %1
#   EXPECT: thermal/aec/mode/parametrics/power_desc/power_v2 launched or
#   SKIP-with-reason for tables/vector_curves/family_matrix. NO repeat spam.

## 6. Services (sudo)
sudo cp ~/Harnessv1/deploy/ryzen-commissioning/work-feeder.service /etc/systemd/system/
sudo cp ~/Harnessv1/deploy/ryzen-commissioning/extract-*.service /etc/systemd/system/ 2>/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now work-feeder.service
#   unit now points at ~/Harnessv1/scripts/work_feeder.py (v1 kit pointed at
#   a path that does not exist — boxes came up unfed)
systemctl is-active work-feeder.service   # -> active

## 7. Telemetry (so seven can SEE this box)
install -m 755 ~/Harnessv1/deploy/emit-extraction-telemetry.py ~/Harnessv1/scripts/
~/Harnessv1/.venv/bin/python ~/Harnessv1/scripts/emit-extraction-telemetry.py \
    --machine-id $(hostname) --role extraction
#   then a 60s timer (sudo):
sudo cp ~/Harnessv1/deploy/ryzen-commissioning/extraction-telemetry.service \
     /etc/systemd/system/ && sudo cp ~/Harnessv1/deploy/ryzen-commissioning/extraction-telemetry.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now extraction-telemetry.timer
#   verify: cat ~/extraction-telemetry/*.json | python3 -m json.tool | head
#   seven consumes it via the mirror (M5 cron) or /var/lib/extraction-telemetry

## 8. Acceptance (all must pass before the box is "commissioned")
[ ] dw-bridge canary prints verification rate > 0
[ ] work-feeder.service active; log shows launched/SKIP, no spam
[ ] lane-out/*.jsonl growing (check twice, 5 min apart)
[ ] telemetry JSON exists, schema harness.extraction-telemetry.v1
[ ] seven's next mirror shows the new machine_id (ask Sam to confirm)
[ ] load average settles below ~0.7 × cores after 30 min (no thrash)

## Rollback
sudo systemctl disable --now work-feeder.service extraction-telemetry.timer
#   box reverts to idle; nothing else consumed its shard (id-stable split)

## Known lane gaps (NOT this box's job to fix)
- tables lane: needs a page-index (extract_datasheet_page_evidence uses
  --page-index, not a doc queue)
- vector_curves: queue digitizer needs httpx (installed in step 2);
  extract_vector_curve_references is a per-plot gold tool, not queue-runnable
- family_matrix: needs census.jsonl from census_family_documents.py first
