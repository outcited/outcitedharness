# NEO ↔ Gas City bridge — review package

Status: **candidate, unmerged**. Read the laws in `schemas/README.md` first.

- `bridge/` — finding contract, authorized idempotent handoff, NEO read-only
  status + continuity alerts, thin supervisor client.
- `schemas/` — `dw.finding.v1` + `dw.approval.gascity-ingest.v1` (the
  authorization law: GitHub findings are NEVER execution authorization).
- `tests/` — 20 tests, offline, stdlib only: `python3 -m unittest discover -s tests`
- `demo/` — staging demonstration (receipts in `demo/out/`) and the gated
  end-to-end acceptance test (`acceptance_e2e.py`).
- `ops/` — M5 tunnel keepalive + 10-min status publisher (launchd, PREPARED,
  NOT ACTIVE; `install-m5.sh` is the activation switch).
- `docs/` — architecture, NEO GitHub mechanism, diagnostics (ready-task stall
  + partial cache root cause), deploy/rollback, acceptance plan.

Demonstrated live 2026-10-27 (staging city only): refused→unauthorized,
accepted bead `ds-gb1` with read-back + event seq, resubmit→duplicate,
both-city snapshots. Production city never written.
