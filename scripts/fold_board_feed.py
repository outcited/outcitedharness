#!/usr/bin/env python3
"""fold_board_feed.py — board-facts aisle: container children -> parts_board.jsonl.

Groups container children (lineage.jsonl) joined to their extracted facets
(children-facets.jsonl) by parent container, and emits one PartRecord per
design container: what the archive contained (gerber/drill/spice/sidecars)
plus quick facts. New grain: boards, not parts — part_number is the design
slug; provenance points at the container blob.
"""
import json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/Users/samkim/Harnessv1")
LINEAGE = ROOT / "results/containers-20261012/lineage.jsonl"
FACETS = Path("/Users/samkim/lane-out/children-facets.jsonl")
OUT = Path("/Users/samkim/mnt/yosemiteraid5/Public/m5-opencode/api-store-export/parts_board.jsonl")

_SLUG = re.compile(r"[^a-z0-9]+")


def slug(s):
    s = _SLUG.sub("-", s.lower()).strip("-")
    return s[:96] or "unknown-board"


def main():
    facts = {}
    for line in FACETS.open():
        r = json.loads(line)
        facts[r["source_path"]] = r

    boards = {}
    for line in LINEAGE.open():
        r = json.loads(line)
        if r.get("status") != "ok":
            continue
        c = r["container_sha"]
        b = boards.setdefault(c, {"container_path": r["container_path"],
                                  "batch": r.get("batch"),
                                  "children": [], "names": []})
        b["children"].append(r)
        seg = (r.get("child_name") or "").split("/")[0]
        if seg and seg not in b["names"]:
            b["names"].append(seg)

    out_rows = []
    for csha, b in sorted(boards.items()):
        kids = b["children"]
        cls = Counter(k.get("child_class") or "unknown" for k in kids)
        fam_exts = Counter()
        apertures = drill_tools = spice_n = 0
        model_names = []
        design_name = b["names"][0] if b["names"] else csha[:16]
        for k in kids:
            f = facts.get(k.get("extracted_path"))
            if not f:
                continue
            fam_exts[f["ext"] or "(none)"] += 1
            ff = f.get("facts") or {}
            apertures += int(ff.get("apertures") or 0)
            drill_tools += int(ff.get("tools") or 0)
            for m in (ff.get("models") or [])[:5]:
                if len(model_names) < 12:
                    model_names.append(m.split()[1] if len(m.split()) > 1 else m)
        claims = [
            {"field": "board_design", "value": design_name[:96], "unit": None,
             "source": {"source_pdf": b["container_path"], "verbatim": design_name}},
            {"field": "container_batch", "value": b["batch"], "unit": None,
             "source": {"source_pdf": b["container_path"], "verbatim": b["batch"]}},
            {"field": "children_total", "value": len(kids), "unit": "files",
             "source": {"source_pdf": b["container_path"], "verbatim": f"{len(kids)} children"}},
            {"field": "gerber_files", "value": cls.get("gerber", 0), "unit": "files",
             "source": {"source_pdf": b["container_path"], "verbatim": ",".join(
                 [e for e in fam_exts if e in (".gbr", ".gtl", ".gbl", ".gto", ".art", ".pho", ".gpi")][:6])}},
            {"field": "drill_files", "value": cls.get("drill", 0), "unit": "files",
             "source": {"source_pdf": b["container_path"], "verbatim": "drill"}},
            {"field": "spice_models_named", "value": len(model_names), "unit": "models",
             "source": {"source_pdf": b["container_path"], "verbatim": "; ".join(model_names[:6])}},
            {"field": "sidecar_texts", "value": cls.get("sidecar_text", 0), "unit": "files",
             "source": {"source_pdf": b["container_path"], "verbatim": "sidecars"}},
            {"field": "document_pdfs", "value": cls.get("document_pdf", 0) + cls.get("pdf", 0),
             "unit": "files", "source": {"source_pdf": b["container_path"], "verbatim": "pdfs"}},
            {"field": "gerber_aperture_marks", "value": apertures, "unit": "marks",
             "source": {"source_pdf": b["container_path"], "verbatim": "aperture sum"}},
            {"field": "drill_tool_marks", "value": drill_tools, "unit": "marks",
             "source": {"source_pdf": b["container_path"], "verbatim": "tool sum"}},
        ]
        out_rows.append({
            "part_number": slug(design_name), "vendor": "unknown",
            "aisle": "boards", "category_source": "container-lineage",
            "container_sha256": csha, "claims": claims,
        })

    tmp = OUT.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                           for r in out_rows))
    tmp.replace(OUT)
    print(f"parts_board.jsonl: {len(out_rows)} boards, "
          f"{sum(len(r['claims']) for r in out_rows)} claims")


if __name__ == "__main__":
    main()
