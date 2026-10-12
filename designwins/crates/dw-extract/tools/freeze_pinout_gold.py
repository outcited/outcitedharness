#!/usr/bin/env python3
"""Freeze the pinout gold set from the verified candidate runs.

The gold rule (accepted by mac-mini 2026-10-11): a pin row counts only if it
RESOLVES TO EVIDENCE — its name appears in the page text at the claimed page,
with quote + page + sha. Model output without resolution is a candidate, not
gold (the blob-existence fallacy).

Sources (all read-only):
  results/datasheet-{mcu,ti-power}-pin-*/**/attempts.jsonl        work_id -> doc, page
  results/datasheet-.../local-results.jsonl                        work_id -> pin rows
  results/datasheet-.../pillar-evidence.jsonl                      work_id -> page blocks

Output (data, not code — outside the repo):
  yosemiteraid5 Public/m5-opencode/pinout-gold/<release>/   work copy
  NAS m5-4tb pinout-gold/<release>/                          immutable side
  rows.jsonl + heldout_rows.jsonl + negative_controls.jsonl + manifest.json

Verification classes per row (the honest split, like the evidence chain):
  exact       — the full pin name appears in a page block text
  normalized  — hyphen/space-folded name appears
  unverified  — no text resolution: REJECTED, counted, never gold
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path("/Users/samkim/Harnessv1")
WORK = Path("/Users/samkim/mnt/yosemiteraid5/Public/m5-opencode/pinout-gold")
NAS = Path("/Users/samkim/Volumes/Electronicdataassets/m5-4tb/pinout-gold")
RELEASE = "pinout-gold-r1.1-20261012"

MCU_RUNS = sorted((REPO / "results").glob("datasheet-mcu-pin-*"))
POWER_RUNS = sorted((REPO / "results").glob("datasheet-ti-power-pin-*"))

# The gold `type` is the DOCUMENT-LITERAL token (what the table prints).
# The 5-class mapping is a separate scored layer (mac-mini finding (3):
# the docs' own legend is the authority — "Type: I = input, O = output,
# P = power": ground PRINTS as P here, so 'ground' never appears in gold).
TYPE_TOKEN_RE = re.compile(r"\b(I/O|I|O|P|NC|N/C|\?|-)\b")


def literal_type(row, quote: str) -> str:
    """Document-literal type token: row field first, quote token second.

    r1 read only the row field, so rows missing it fell to 'other' while
    their quote still printed I/O (mac-mini finding (1): one token, two
    truths). The quote is the document's own word and wins ties.
    """
    raw = str(row.get("type") or row.get("dir") or "").strip()
    m_raw = TYPE_TOKEN_RE.search(raw)
    if m_raw:
        return m_raw.group(1)
    m_q = TYPE_TOKEN_RE.search(quote or "")
    if m_q:
        return m_q.group(1)
    return raw or "?"


# The mapping layer (scored separately, never the gold's own truth).
TYPE_MAP = {
    "i/o": "io", "i": "io", "o": "io", "io": "io", "in": "io", "out": "io",
    "input": "io", "output": "io", "ai": "io", "ao": "io", "ia": "io",
    "oa": "io", "ai/ao": "io",
    "p": "power", "pwr": "power", "power": "power", "vdd": "power",
    "g": "ground", "gnd": "ground", "ground": "ground",
    "nc": "nc", "n/c": "nc", "n.c.": "nc",
}
# Everything else is 'other' — including "-": the doc legends name I/O/P only
# ("Type: I = input, O = output, P = power"), so "-" carries no type meaning
# we are entitled to read. The token layer keeps what the table printed.


def map_type(token: str) -> str:
    return TYPE_MAP.get(str(token or "").strip().lower(), "other")


def norm_name(name: str) -> str:
    return re.sub(r"[\s\-–_]+", "", name).lower()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def load_jsonl(path: Path):
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def collect_runs(runs):
    """Join attempts x local-results x pillar-evidence per run shard."""
    shards = []
    for run in runs:
        for sub in sorted(run.iterdir()):
            att = sub / "attempts.jsonl"
            loc = sub / "local-results.jsonl"
            pil = sub / "pillar-evidence.jsonl"
            if att.exists() and loc.exists():
                shards.append((run.name, sub, att, loc, pil if pil.exists() else None))
    return shards


def verify_row(name: str, pin, blocks) -> tuple[str | None, str, str]:
    """Resolve one pin row against page blocks. -> (class, quote, block_text)."""
    if not blocks:
        return (None, "", "")
    texts = [b.get("text", "") for b in blocks]
    joined = "\n".join(texts)
    if name and name in joined:
        for t in texts:
            if name in t:
                return ("exact", t.strip()[:400], t.strip()[:400])
    nn = norm_name(name)
    if nn and len(nn) >= 4:
        for t in texts:
            if nn in norm_name(t):
                return ("normalized", t.strip()[:400], t.strip()[:400])
    # pin-number-only fallback: "C2" / "18" appearing alone is too weak —
    # REJECT (counted). A number is not a name.
    return (None, "", "")


def main() -> int:
    shards = collect_runs(MCU_RUNS) + collect_runs(POWER_RUNS)
    print(f"shards: {len(shards)}")
    rows = []
    counts = {"exact": 0, "normalized": 0, "unverified": 0, "rows_seen": 0, "docs": set()}
    doc_rows: dict[str, list] = {}
    for run_name, sub, att_path, loc_path, pil_path in shards:
        attempts = {r["work_id"]: r for r in load_jsonl(att_path)}
        blocks_by_work = {}
        if pil_path:
            for r in load_jsonl(pil_path):
                blocks_by_work[r["work_id"]] = (r.get("page") or {}).get("blocks", [])
        stratum = "mcu_pinout" if "mcu" in run_name else "qfn_ballout"
        for r in load_jsonl(loc_path):
            work_id = r.get("work_id")
            att = attempts.get(work_id)
            if not att:
                continue
            doc = att.get("document_sha256")
            page = att.get("page_1based")
            if not doc or not page:
                continue
            pins = (r.get("result") or {}).get("pins") or []
            blocks = blocks_by_work.get(work_id, [])
            table_id = f"pin-table-p{page}"
            for pin in pins:
                counts["rows_seen"] += 1
                name = str(pin.get("name") or "")
                vclass, quote, _ = verify_row(name, pin.get("pin_no"), blocks)
                if vclass is None:
                    counts["unverified"] += 1
                    continue
                counts[vclass] += 1
                counts["docs"].add(doc)
                row = {
                    "class": stratum,
                    "doc": doc,
                    "table_id": table_id,
                    "pin": str(pin.get("pin_no")),
                    "name": name,
                    "type": map_type(literal_type(pin, quote)),
                    "type_token": literal_type(pin, quote),
                    "quote": quote,
                    "page": page,
                    "sha": sha256_text(quote),
                }
                rows.append(row)
                doc_rows.setdefault(doc, []).append(row)

    # Key-grain dedup (mac-mini finding (2)): r1 shipped one row per
    # candidate with no dedup, so 12,614 rows covered 4,342 keys and the
    # scorer's last-row-wins was arbitrary truth. Majority vote on
    # (name, type); ties are CONTRADICTORY and cannot be gold — excluded
    # and counted (gold never contains contradictions).
    by_key: dict[tuple, dict] = {}
    for r in rows:
        k = (r["doc"], r["table_id"], r["pin"])
        slot = by_key.setdefault(k, {"variants": {}, "rows": []})
        variant = (r["name"], r["type"])
        slot["variants"][variant] = slot["variants"].get(variant, 0) + 1
        slot["rows"].append(r)
    deduped, conflicts, tie_keys = [], 0, 0
    for k, slot in by_key.items():
        top = max(slot["variants"].values())
        winners = [v for v, c in slot["variants"].items() if c == top]
        if len(winners) > 1:
            tie_keys += 1
            continue
        if len(slot["variants"]) > 1:
            conflicts += 1
        winner = winners[0]
        row = next(r for r in slot["rows"] if (r["name"], r["type"]) == winner)
        row["vote"] = {"majority": top, "variants": len(slot["variants"])}
        deduped.append(row)
    print(f"key-grain dedup: {len(rows)} rows -> {len(deduped)} keys; "
          f"{conflicts} keys had conflicts (resolved by majority); {tie_keys} ties excluded (contradictory)")
    rows = deduped
    for r in rows:
        doc_rows[r["doc"]] = [x for x in doc_rows.get(r["doc"], [])] if r["doc"] in doc_rows else []
    doc_rows = {}
    for r in rows:
        doc_rows.setdefault(r["doc"], []).append(r)

    docs = sorted(doc_rows)
    # Doc-level 75/25 split, deterministic: every 4th doc (sorted by sha) held out.
    heldout = set(docs[i] for i in range(3, len(docs), 4))
    exp_rows = [r for r in rows if r["doc"] not in heldout]
    held_rows = [r for r in rows if r["doc"] in heldout]

    # Negative controls: gold rows with deliberately wrong answers that the
    # scorer MUST catch (mac-mini's must_catch contract).
    neg = []
    for i, r in enumerate(rows[:20:2]):
        neg.append({
            "case_id": f"neg-type-{i}",
            "description": "wrong pin type on a verified row",
            "wrong_answer": {**r, "type": "ground" if r["type"] != "ground" else "io"},
            "must_catch": True,
        })
    for i, r in enumerate(rows[1:20:2]):
        neg.append({
            "case_id": f"neg-name-{i}",
            "description": "wrong signal name on a verified row (blob-existence fallacy)",
            "wrong_answer": {**r, "name": "N/C"},
            "must_catch": True,
        })

    out = WORK / RELEASE
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"release": RELEASE, "files": {}, "counts": {}}
    for fname, data in (
        ("rows.jsonl", exp_rows),
        ("heldout_rows.jsonl", held_rows),
        ("negative_controls.jsonl", neg),
    ):
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in data)
        (out / fname).write_text(body)
        manifest["files"][fname] = {
            "sha256": sha256_text(body),
            "rows": len(data),
        }
    manifest["counts"] = {
        "docs_total": len(docs),
        "docs_heldout": len(heldout),
        "rows_seen": counts["rows_seen"],
        "rows_exact": counts["exact"],
        "rows_normalized": counts["normalized"],
        "rows_unverified_rejected": counts["unverified"],
        "rows_gold": len(rows),
        "rows_experiment": len(exp_rows),
        "rows_heldout": len(held_rows),
        "strata": {
            s: sum(1 for r in rows if r["class"] == s)
            for s in ("mcu_pinout", "qfn_ballout", "connector")
        },
    }
    body = json.dumps(manifest, sort_keys=True)
    manifest["manifest_sha256"] = sha256_text(body)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))

    # Immutable side: same bytes to the NAS.
    nas = NAS / RELEASE
    nas.mkdir(parents=True, exist_ok=True)
    for fname in ("rows.jsonl", "heldout_rows.jsonl", "negative_controls.jsonl", "manifest.json"):
        (nas / fname).write_bytes((out / fname).read_bytes())

    print(json.dumps(manifest["counts"], indent=2))
    print("manifest_sha256:", manifest["manifest_sha256"])
    print("held_out_ref:", f"claims:sha256:{manifest['manifest_sha256']}")
    print("work copy:", out)
    print("immutable:", nas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
