# Reassessment evidence (post B1–B6 remediation)

Regenerated with the same instruments used for the original assessment, run
against the remediated code:

- M5 `discovery/01-production-remediation` (see `READINESS_VERDICT.json` for
  the per-blocker commit list)
- M4 `curve/08c-parity-repair@f02f08a21021240de11e951749613b1123ef2071`

| File | Instrument |
|---|---|
| `pilot_records.json`, `pilot_summary.json` | `scripts/discovery01_production_pilot.py` (15 scenarios: P1–P9 on the labeled legacy population, P10–P14 on the production in-cohort population) |
| `performance_observations.json` | `scripts/discovery01_production_pilot.py --perf` |
| `restart_check.json`, `restart_anchor.json` | `scripts/discovery01_production_pilot.py --post-restart` (P9, after restarting both services) |
| `security_posture.json` | `scripts/discovery01_readiness_probes.py` |
| `data_quality.json` | `scripts/discovery01_readiness_probes.py` |
| `provenance_probe.json` | `scripts/discovery01_readiness_probes.py` |
| `release_identity.json` | `scripts/discovery01_readiness_probes.py` |
| `identity_reconciliation.json` | `scripts/discovery01_readiness_probes.py` (both populations) |

The original assessment artifacts are preserved unmodified in
`results/discovery01-production-readiness/` as committed at
`discovery/01-final-parity@fe6a7e60`; they are the "before" evidence quoted
throughout `DISCOVERY01_PRODUCTION_READINESS.md`. Per-blocker before/after
evidence for the remediation itself lives one level up in
`results/discovery01-remediation/` (`b1_identity_authority.json`,
`b2_provenance_resolution.json`, `b3_release_pin_cache.json`,
`b4_auth_fail_closed.json`, `b5_release_priming.json`,
`b5_release_fingerprint_latency.json`, `b6_candidate_transport.json`,
`b6_restart.json`, `parity_after_remediation.json`).

To regenerate without touching the assessment record:

```
READINESS_OUT_DIR=results/discovery01-remediation/reassessment \
  PILOT_M5_TOKEN=... PILOT_M4_TOKEN=... \
  .venv/bin/python scripts/discovery01_production_pilot.py
```
