# Seven Telemetry Contract — Read-Only Extraction Observability

**Schema:** `harness.extraction-telemetry.v1`  
**Authority:** Read-only. Seven observes and proposes. Seven does not dispatch, reassign, or modify workers.  
**Date:** 2026-10-18

---

## Purpose

Seven (Engineering Director) must distinguish:
- **Busy extractor** (high CPU, many workers, but low useful output)
- **Productive extractor** (verified, normalized rows flowing to database)
- **Starved extractor** (queue empty, feeder stalled, false starvation)
- **Downstream bottleneck** (extraction ahead of verification/normalization)

---

## Machine Record Schema

```json
{
  "schema": "harness.extraction-telemetry.v1",
  "timestamp": "ISO-8601",
  "machine_id": "happy|m5|machine-a|machine-b|seven",
  "machine_role": "extraction|flexible|director|development",
  "hardware": {
    "cpu_threads": 32,
    "ram_total_mb": 61440,
    "disk_total_gb": 1800
  },
  "utilization": {
    "cpu_pct": 90.8,
    "ram_used_mb": 18018,
    "disk_used_gb": 153,
    "load_average": 10.50
  },
  "services": [
    {
      "name": "extract-thermal-metrics",
      "status": "active|inactive|failed",
      "workers": 33,
      "restarts": 0
    }
  ],
  "feeder": {
    "heartbeat": "ISO-8601",
    "status": "running|stalled|stopped"
  },
  "lanes": [
    {
      "name": "thermal_metrics",
      "extraction_type": "thermal",
      "extraction_version": "deterministic_thermal_metrics_v1",
      "queue_source": "/tmp/vault-queue.json",
      "queue_items_total": 118795,
      "queue_items_completed": 3692,
      "queue_items_running": 33,
      "queue_items_ready": 115103,
      "queue_items_failed": 0,
      "queue_items_blocked": 0,
      "output_rows": 3692,
      "output_file": "/home/samsonkim/lane-out/thermal-vault.jsonl",
      "last_progress": "ISO-8601",
      "status": "running|completed|blocked|failed"
    }
  ],
  "downstream": {
    "raw_rows_unnormalized": 135000,
    "verified_claims": 9400,
    "normalized_claims": 9400,
    "identity_bound": null,
    "comparable_accepted": null,
    "note": "null = not measured yet"
  },
  "telemetry_status": "complete|partial|unknown",
  "unknown_fields": ["identity_bound", "comparable_accepted"]
}
```

---

## What Seven Must Distinguish

| Scenario | CPU | Workers | Output rows | Feeder heartbeat | Queue items | Interpretation |
|---|---|---|---|---|---|---|
| Busy but useless | 95% | 70 | Stale | Fresh | High | Workers spinning on bad data |
| Productive | 90% | 62 | Growing | Fresh | Decreasing | Normal extraction |
| Starved | 10% | 0 | Stale | Stale | 0 | Queue empty or feeder dead |
| Downstream bottleneck | 90% | 62 | Growing fast | Fresh | Decreasing | Normalization/verification behind |

---

## Delivery

Seven receives telemetry via:
1. **File read:** `/var/lib/extraction-telemetry/` (one JSON per machine, refreshed every 60s)
2. **HTTP poll:** `http://<machine>:9100/telemetry` (if HTTP server enabled)

**No autonomous dispatch. Seven proposes, humans authorize.**

---

## Unknown Marking

Any field that cannot be measured must be explicitly `"null"` or listed in `"unknown_fields"`.  
**Never leave a field absent.** Absence = unknown. Null = measured as zero/none.  
This prevents false starvation reports from empty legacy queues.

---

## Example Record (happy, 2026-10-18 07:00 UTC)

```json
{
  "schema": "harness.extraction-telemetry.v1",
  "timestamp": "2026-10-18T07:00:00Z",
  "machine_id": "happy",
  "machine_role": "extraction",
  "hardware": {
    "cpu_threads": 32,
    "ram_total_mb": 61440,
    "disk_total_gb": 1800
  },
  "utilization": {
    "cpu_pct": 90.8,
    "ram_used_mb": 18018,
    "disk_used_gb": 153,
    "load_average": 10.50
  },
  "services": [
    {"name": "extract-thermal-metrics", "status": "active", "workers": 33, "restarts": 0},
    {"name": "extract-aec-grades", "status": "active", "workers": 25, "restarts": 0},
    {"name": "work-feeder", "status": "active", "workers": 1, "restarts": 0}
  ],
  "feeder": {
    "heartbeat": "2026-10-18T06:59:23Z",
    "status": "running"
  },
  "lanes": [
    {
      "name": "thermal_metrics",
      "extraction_type": "thermal",
      "extraction_version": "deterministic_thermal_metrics_v1",
      "queue_source": "/tmp/vault-queue.json",
      "queue_items_total": 118795,
      "queue_items_completed": 3692,
      "queue_items_running": 33,
      "queue_items_ready": 115103,
      "queue_items_failed": 0,
      "queue_items_blocked": 0,
      "output_rows": 3692,
      "output_file": "/home/samsonkim/lane-out/thermal-vault.jsonl",
      "last_progress": "2026-10-18T06:59:00Z",
      "status": "running"
    }
  ],
  "downstream": {
    "raw_rows_unnormalized": 135000,
    "verified_claims": 9400,
    "normalized_claims": 9400,
    "identity_bound": null,
    "comparable_accepted": null,
    "note": "measured via dw-bridge canary on 100-record samples"
  },
  "telemetry_status": "complete",
  "unknown_fields": ["identity_bound", "comparable_accepted"]
}
```
