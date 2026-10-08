#!/usr/bin/env python3
"""Canon determinism probe: fixed claim set, N reruns, batch-varied.

L0 for the judge layer: same claims -> same verdicts across calls and across
interleaved traffic. Measures verdict-level stability (not token-level).
"""

import json
import statistics
import sys
import time

sys.path.insert(0, "/Users/samkim/Harnessv1")
from harness.pipeline.api import call_canon, CANON_MODEL

TEXT = ("Absolute Maximum Ratings: VDS 60 V, VGS +/-20 V, ID 5 A continuous."
        " Recommended Operating Conditions: VIN 2.5 V to 5.5 V, Ta -40 to 85 C."
        " Electrical Characteristics: RDS(on) 5.5 mOhm max at VGS=10V, 8 mOhm max at VGS=4.5V."
        " VGS(th) min 1.0 V typ 1.6 V max 2.5 V at DS=10V ID=1mA.")

PROBE_CLAIMS = [
    {"symbol": "VDS", "value": 60, "unit": "V", "qualifier": None, "condition": None},
    {"symbol": "RDS_on", "value": 999, "unit": "kOhm", "qualifier": None, "condition": None},
    {"symbol": "VIN_min", "value": 2.5, "unit": "V", "qualifier": None, "condition": None},
    {"symbol": "VIN_max", "value": 5.5, "unit": "V", "qualifier": None, "condition": None},
    {"symbol": "VGS_th", "value": 1.5, "unit": "V", "qualifier": "typical", "condition": None},
    {"symbol": "Ta_max", "value": 85, "unit": "C", "qualifier": None, "condition": None},
    {"symbol": "ID", "value": 50, "unit": "A", "qualifier": None, "condition": None},
]

NOISE = "Tell me a fun fact about capacitors in one sentence."


def verdict_key(verdicts):
    return json.dumps([(v.get("i"), v.get("verdict"), v.get("reason_code")) for v in verdicts],
                      sort_keys=True)


def main(runs=5):
    keys = []
    latencies = []
    for r in range(runs):
        if r % 2 == 1:
            try:
                from harness.pipeline.api import CANON_ENDPOINT
                import urllib.request
                body = json.dumps({"model": CANON_MODEL, "temperature": 0.7,
                                   "max_tokens": 60,
                                   "messages": [{"role": "user", "content": NOISE}]}).encode()
                req = urllib.request.Request(CANON_ENDPOINT, data=body,
                                              headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=60).read()
            except Exception:
                pass
        t0 = time.time()
        v = call_canon(TEXT, PROBE_CLAIMS)
        latencies.append(time.time() - t0)
        keys.append(verdict_key(v))
        print(f"run {r+1}: {time.time()-t0:.1f}s verdicts={len(v)}", flush=True)
    distinct = len(set(keys))
    report = {
        "judge": CANON_MODEL, "runs": runs, "distinct_verdict_sets": distinct,
        "verdict_level_deterministic": distinct == 1,
        "latency_s": {"median": round(statistics.median(latencies), 1),
                      "spread": round(max(latencies) - min(latencies), 1)},
    }
    print(json.dumps(report, indent=2))
    return 0 if distinct == 1 else 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 5))
