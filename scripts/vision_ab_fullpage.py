#!/usr/bin/env python3
"""Full-page variant of the A/B test — where the map (WHERE) should
actually matter: no crop advantage for either arm."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import importlib.util
spec = importlib.util.spec_from_file_location("ab", str(Path(__file__).resolve().parent / "vision_ab_test.py"))
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)

cases = json.loads(Path("scratch/m5-soak/ab-cases.json").read_text())[:2]
results = []
for case in cases:
    pdf = Path(case["pdf"])
    env = ab.ve.build_envelope(pdf, case["page"], case["part"])
    fig_env = next((f for f in env["figures"] if f["figure_index"] == case["figure_index"]), None)
    gt_rec = json.loads(Path(case["gt"]).read_text())
    gt_plots = [p for p in gt_rec.get("plots", []) if p.get("_figure_index") == case["figure_index"]]
    # FULL PAGE render (no clip) — both arms see the whole page
    import pymupdf
    doc = pymupdf.open(pdf)
    pix = doc[case["page"] - 1].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5))
    png = pix.tobytes("png"); doc.close()
    envelope_block = json.dumps(fig_env, ensure_ascii=False, indent=1) if fig_env else "{}"
    for arm, prompt in (
        ("C_fullpage_blind", ab.BASE_PROMPT),
        ("D_fullpage_envelope", ab.BASE_PROMPT
         + "\n\nCONTEXT ENVELOPE (the map: which figure, where it sits, "
           "what it contains; trust it to locate and name):\n" + envelope_block
         + f"\nDevice: {case['part']}, page {case['page']}. "
           "Digitize the figure this envelope describes."),
    ):
        vision, dt = ab.call_tp4_vision(png, prompt)
        s = ab.score(vision, gt_plots) if vision and "error" not in vision else {"error": (vision or {}).get("error", "?")}
        rec = {"case": Path(case["gt"]).stem, "arm": arm, "latency_s": round(dt, 1), "score": s,
               "vision_labels": {"x": (vision or {}).get("x_label"), "y": (vision or {}).get("y_label")}}
        results.append(rec)
        print(json.dumps(rec, ensure_ascii=False)[:300], flush=True)
Path("scratch/m5-soak/ab-fullpage-results.json").write_text(json.dumps(results, indent=1))
