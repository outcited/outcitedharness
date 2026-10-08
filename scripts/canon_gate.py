#!/usr/bin/env python3
"""Natural-failure gold gate for the local canon (M4 protocol, 2026-10-03).

Assertions:
  1. gold-labels.jsonl original_bad_proposal (15 proposer defects) -> canon REJECTS
     (class match reported where comparable)
  2. corrected twins -> canon VERIFIES
  3. retest-2auditor-false-negatives-v3 proposals -> canon VERIFIES (must not
     repeat the old auditor's over-firing)
"""

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

GATE = Path("/Volumes/M5_4TB/exports/canon-gate/natural-failure-gold-v1")
CANON = "http://100.100.116.82:8900/v1/chat/completions"
MODEL = "qwen3.8-27b-fp8"

AUDITOR_SYSTEM = """You are the Physical Truth Auditor for an electronic component engineering database.
Your sole responsibility is to protect the database against hallucinations, mislabeled ratings, and unit errors.

You will be given:
- Input A (Raw Source Text): Unedited text extracted directly from the datasheet.
- Input B (Proposed Claims): Extracted parameters, values, and operating ranges.

Rules of Adjudication:
1. Category Truth: Never allow an Absolute Maximum Rating (stress limit that destroys the device) to be labeled as a Recommended Operating Condition.
2. Boundary Truth: For start-up thresholds (e.g. UVLO, turn-on), the design-safe maximum is the MAX column. For operating ranges, the safe minimum is the MIN column.
3. Unit & Scale Truth: Check whether units (V, mV, A, mA, uA, degC) match the table header.
4. Verbatim Provenance: If a number, pin, or feature is invented or interpolated without explicit backing in Input A, REJECT it.
5. Null Fields: Omit null fields from rejections. Only audit and reject non-null asserted values that are wrong or unprovenanced.
6. Supply-Pin Name Aliases: VCC, VDD, VSS, VIN, PVIN, AVIN, and VBUS are all valid supply-pin names for a device. If the datasheet states "VCC = 3.0 V to 5.5 V" in the Recommended Operating Conditions, that IS a valid operating supply-voltage range — accept it under vin_min_v / vin_max_v. Do not reject solely because the pin is called "VCC" instead of "VIN".
7. Part Identity: The proposal must describe THE device this document describes. Before verifying, check the part numbers, family names, and device descriptions printed in Input A. If the proposal's description or applications reference a device that this document does not cover (a different part number, a different vendor's product, a different device family), REJECT with reason_code hallucinated_value on the description and every contaminated field. A proposal about the wrong part is never partially verified.
8. Absolute-Max Table Cross-Check: Before verifying ANY vin_min_v, vin_max_v, vout_min_v, vout_max_v, iout_max_a, or temp claim, locate the table it comes from in Input A. If the value appears in an "Absolute Maximum Ratings", "Absolute Limits", or "Stress Ratings" table and NOT in a "Recommended Operating Conditions" or "Electrical Characteristics" context, REJECT with absolute_max_as_operating.
9. Bounds Require Quotes: Every non-null electrical bound in the proposal must be findable as a printed number in Input A. If you cannot locate a claimed bound (or a close variant with correct unit scaling) in the text, REJECT that field with hallucinated_value. Plausibility is not provenance — a plausible value with no printed backing is a hallucination.
10. Partial Coverage Is Fine: If Input A genuinely describes this part and every asserted value is printed, VERIFY even when the text excerpt is partial or the proposal omits optional fields. Do not use insufficient_context when the asserted claims themselves check out; use it only when the text cannot support auditing the asserted values at all.

Output Format: Return ONLY a valid compact JSON object:
{
  "verdict": "VERIFIED" | "REJECTED",
  "field_provenance": {"<field>": "<exact quoted line from Input A supporting this field, or null>"},
  "rejections": [
    {"field": "<field>", "claimed_value": "<val>", "reason_code": "<code_name>", "evidence": "<exact quote>"}
  ],
  "summary": "<terse one-sentence reason>"
}
PROCEDURE (follow exactly, in order):
a. For EACH non-null asserted field in Input B, find the exact line in Input A that supports it. Write it into field_provenance verbatim. If you cannot find the exact supporting line, write null for that field.
b. Any field whose provenance is null MUST appear in rejections (reason_code: hallucinated_value if no number matches anywhere; absolute_max_as_operating if the number appears only under Absolute Maximum/Stress ratings; column_role_mismatch if the number belongs to a different column or variant).
c. Apply Rules 7-10 to the quoted lines: a number quoted from the wrong table or wrong variant is rejected even though it is provenance.
d. verdict is REJECTED if any non-null field is rejected; VERIFIED only if every non-null field has valid provenance from the correct table.
Do not include conversational preamble, analysis paragraphs, or markdown outside the JSON.
Valid reason_codes: absolute_max_as_operating, wrong_quantity_basis, column_role_mismatch, unit_mismatch, hallucinated_value, insufficient_context."""


def canon_auditor(text, proposal, timeout=240):
    body = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": 2000,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": AUDITOR_SYSTEM},
            {"role": "user", "content":
                f"Input A (Raw Source Text):\n{text}\n\nInput B (Proposed Claims):\n{json.dumps(proposal, ensure_ascii=False)}"},
        ],
    }).encode()
    req = urllib.request.Request(CANON, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        content = json.loads(r.read())["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return {"verdict": "UNPARSEABLE", "raw": content[:200], "rejections": []}
    return json.loads(m.group(0))


def main():
    docs = {}
    for d in json.loads((GATE / "pilot_100_docs_with_text.json").read_text()):
        docs[d["sha256"]] = "\n".join(t for _, t in d.get("text", []))

    results = {"bad_rejected": 0, "bad_total": 0, "twin_verified": 0, "twin_total": 0,
               "trap_verified": 0, "trap_total": 0, "details": []}
    rows = [json.loads(l) for l in (GATE / "gold-labels.jsonl").open()]
    for i, row in enumerate(rows):
        text = docs.get(row["sha256"], "")[:28000]
        for label, prop, key in [
            ("bad", row["original_bad_proposal"], "bad"),
            ("twin", row["corrected_proposal"], "twin"),
        ]:
            t0 = time.time()
            out = canon_auditor(text, prop)
            verdict = out.get("verdict", "?")
            dt = time.time() - t0
            if label == "bad":
                results["bad_total"] += 1
                ok = verdict == "REJECTED"
                results["bad_rejected"] += ok
                reasons = {r.get("reason_code") for r in out.get("rejections", [])}
            else:
                results["twin_total"] += 1
                ok = verdict == "VERIFIED"
                results["twin_verified"] += ok
                reasons = set()
            results["details"].append({
                "row": i, "case": label, "ok": ok, "verdict": verdict, "s": round(dt, 1),
                "expected_classes": row.get("original_rejection_reasons") if label == "bad" else [],
                "canon_reasons": sorted(reasons),
            })
            print(f"[{i}] {label}: {verdict} ({dt:.1f}s) {'OK' if ok else 'MISS'}"
                  + (f" reasons={sorted(reasons)}" if label == "bad" else ""), flush=True)

    for line in (GATE / "retest-2auditor-false-negatives-v3.jsonl").open():
        row = json.loads(line)
        text = docs.get(row["sha256"], "")[:28000]
        out = canon_auditor(text, row["proposal"])
        ok = out.get("verdict") == "VERIFIED"
        results["trap_total"] += 1
        results["trap_verified"] += ok
        results["details"].append({"case": "trap", "ok": ok, "verdict": out.get("verdict"),
                                   "rejections": out.get("rejections", [])[:2]})
        print(f"[trap] {out.get('verdict')} {'OK' if ok else 'MISS — over-firing repeated'}", flush=True)

    summary = {
        "canon": MODEL,
        "bad_rejected": f"{results['bad_rejected']}/{results['bad_total']}",
        "twin_verified": f"{results['twin_verified']}/{results['twin_total']}",
        "traps_verified": f"{results['trap_verified']}/{results['trap_total']}",
        "pass": (results["bad_rejected"] == results["bad_total"]
                 and results["twin_verified"] == results["twin_total"]
                 and results["trap_verified"] == results["trap_total"]),
    }
    Path("results/adjudication/canon-natural-gold-gate.json").write_text(
        json.dumps({"summary": summary, "details": results["details"]}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
