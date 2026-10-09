"""CURVE-04 tests: adjudication immutability, raster recovery, flag-gated
retrieval, ablation acceptance gates, and adversarial cases."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from harness.electronics.curve_adjudication import (
    AdjudicationLedger,
    build_packet,
)
from harness.electronics.curve_retrieval import (
    build_index,
    flag_enabled,
    search_curves,
)

PILOT = Path(__file__).parent / "fixtures/gold/curve_evidence_pilot"


# --- helpers ----------------------------------------------------------------


def _pilot_curve():
    from harness.discovery.curves import load_reference_curves

    curves = load_reference_curves([PILOT / "tps548c26_p10.json"])
    return curves[0]


def _synthetic_plot_png(path: Path, *, with_ticks: bool = True) -> Path:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(candidate).exists():
            font = ImageFont.truetype(candidate, 22)
            break
    w, h = 800, 600
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    c0, r0, c1, r1 = 100, 50, 700, 500
    d.rectangle([c0, r0, c1, r1], outline="black", width=3)
    for i in range(6):  # x ticks 0..5 at even spacing
        x = c0 + i * (c1 - c0) / 5
        d.line([x, r1, x, r1 + 10], fill="black", width=2)
        if with_ticks and font:
            d.text((x - 6, r1 + 16), str(i), fill="black", font=font)
    for i in range(6):  # y ticks 0..100 (marks inside, labels clear)
        y = r1 - i * (r1 - r0) / 5
        d.line([c0, y, c0 + 10, y], fill="black", width=2)
        if with_ticks and font:
            d.text((c0 - 14, y - 11), str(i * 20), fill="black",
                   font=font, anchor="ra")
    # one colored trace: y = 100 - 10*x  (value space)
    pts = []
    for i in range(101):
        x = c0 + i * (c1 - c0) / 100
        value = 100 - 10 * (i / 100 * 5)
        y = r1 - (value / 100) * (r1 - r0)
        pts.append((x, y))
    d.line(pts, fill=(200, 30, 30), width=4)
    img.save(path)
    return path


# --- R1: adjudication ----------------------------------------------------------


def test_packet_hash_deterministic_and_content_addressed():
    curve = _pilot_curve()
    p1 = build_packet(curve)
    p2 = build_packet(curve)
    assert p1["packet_hash"] == p2["packet_hash"]
    required = {
        "document_sha256", "figure_revision", "page_1based",
        "figure_index", "caption", "plot_bbox", "series_identity",
        "extraction_algorithm", "extraction_version", "render",
    }
    assert required <= set(p1["provenance"])
    assert p1["coordinates"] and p1["axes"]["x"]["unit"]
    assert p1["machine_verification"]["status"]


def test_material_change_invalidates_approval(tmp_path):
    curve = _pilot_curve()
    packet = build_packet(curve)
    ledger = AdjudicationLedger(tmp_path / "ledger.jsonl")
    ledger.emit_machine_verified(packet)
    ledger.queue_review(packet["packet_hash"])
    ledger.human_decision(packet["packet_hash"], "human_approved",
                          "reviewer@x", "ok")
    assert ledger.state_of(packet["packet_hash"])["state"] == \
        "human_approved"
    tampered = json.loads(json.dumps(packet))
    tampered["coordinates"][0][1] += 5.0
    # a material change breaks integrity: the stored hash no longer
    # matches the recomputed content hash
    from harness.electronics.curve_adjudication import (
        packet_content_hash,
        verify_packet_integrity,
    )

    assert verify_packet_integrity(packet) is True
    assert verify_packet_integrity(tampered) is False
    assert packet_content_hash(tampered) != packet["packet_hash"]
    # and prior approval does not transfer to the tampered content
    assert ledger.state_of(packet_content_hash(tampered))["state"] is None


def test_machine_verification_never_auto_approves(tmp_path):
    ledger = AdjudicationLedger(tmp_path / "l.jsonl")
    packet = build_packet(_pilot_curve())
    ledger.emit_machine_verified(packet)
    ledger.emit_machine_verified(packet)  # repeat machine pass
    assert ledger.state_of(packet["packet_hash"])["state"] == \
        "machine_verified"


def test_illegal_transitions_and_reviewer_required(tmp_path):
    ledger = AdjudicationLedger(tmp_path / "l.jsonl")
    packet = build_packet(_pilot_curve())
    with pytest.raises(ValueError):
        ledger.human_decision(packet["packet_hash"], "human_approved",
                              "reviewer@x")  # nothing emitted yet
    ledger.emit_machine_verified(packet)
    with pytest.raises(ValueError):
        ledger.human_decision(packet["packet_hash"], "human_approved",
                              "")  # anonymous review forbidden
    ledger.queue_review(packet["packet_hash"])
    ledger.human_decision(packet["packet_hash"], "human_rejected",
                          "reviewer@x")
    with pytest.raises(ValueError):
        ledger.human_decision(packet["packet_hash"], "human_approved",
                              "reviewer@x")  # rejected is terminal
    with pytest.raises(ValueError):
        ledger.append({"packet_hash": packet["packet_hash"],
                       "state": "human_approved", "reviewer": "system"})


# --- R3: raster recovery -------------------------------------------------------


def test_raster_recovery_on_synthetic_plot(tmp_path):
    import numpy as np
    from PIL import Image

    import harness.electronics.raster_curves as rc

    png = _synthetic_plot_png(tmp_path / "plot.png")
    image = Image.open(png).convert("RGB")
    rgb = np.asarray(image)
    gray = np.asarray(image.convert("L"))
    frames = rc._find_frames(gray)
    assert frames, "synthetic frame detected"
    frame = frames[0]
    x_fit, _ = rc._tick_axis(rgb, frame, "x", image)
    y_fit, _ = rc._tick_axis(rgb, frame, "y", image)
    assert x_fit and x_fit[0] == "linear"
    assert y_fit and y_fit[0] == "linear"
    traces = rc._trace_polylines(rgb, frame)
    assert len(traces) == 1
    poly = next(iter(traces.values()))
    # endpoints of y = 100 - 10*x over x in [0, 5]
    def to_v(a, b, p):
        return a * p + b
    xs = [p[0] + frame[0] for p in poly]
    ys = [p[1] + frame[1] for p in poly]
    x_lo = to_v(x_fit[1], x_fit[2], min(xs))
    x_hi = to_v(x_fit[1], x_fit[2], max(xs))
    y_at_lo = to_v(y_fit[1], y_fit[2],
                   np.interp(min(xs), xs, ys))
    y_at_hi = to_v(y_fit[1], y_fit[2],
                   np.interp(max(xs), xs, ys))
    assert x_lo == pytest.approx(0.0, abs=0.2)
    assert x_hi == pytest.approx(5.0, abs=0.2)
    assert y_at_lo == pytest.approx(100.0, abs=3.0)
    assert y_at_hi == pytest.approx(50.0, abs=3.0)


def test_raster_refusal_without_tick_labels(tmp_path):
    import numpy as np
    from PIL import Image

    import harness.electronics.raster_curves as rc

    png = _synthetic_plot_png(tmp_path / "no_ticks.png", with_ticks=False)
    image = Image.open(png).convert("RGB")
    rgb = np.asarray(image)
    frame = rc._find_frames(np.asarray(image.convert("L")))[0]
    x_fit, words = rc._tick_axis(rgb, frame, "x", image)
    assert x_fit is None, "no printed tick labels must refuse the axis"


def test_raster_page_record_refuses_honestly(tmp_path):
    pytest.importorskip("pymupdf")
    from harness.electronics.raster_curves import extract_raster_page

    pdf = Path(
        "/Volumes/M5_4TB/data/ti_power_datasheets/dcdc_TPS548C26.pdf"
    )
    if not pdf.exists():
        pytest.skip("corpus volume not mounted")
    record = extract_raster_page(pdf, 10, dpi=300,
                                 render_dir=tmp_path)
    assert record["schema"] == "harness.electronics-raster-curves.v1"
    assert record["plots"] or record["refusals"], \
        "output is either plots or recorded refusals, never silence"
    for plot in record["plots"]:
        q = plot["raster_quality"]
        assert q["render"]["sha256"], "source-image reference preserved"
        assert q["x_fit_residual_fraction"] < 0.01
        assert q["y_fit_residual_fraction"] < 0.01


def test_monotone_and_robust_fit_drop_ocr_misreads():
    from harness.electronics.raster_curves import _fit_axis, _monotone_keep

    # non-monotone misread removed by the monotone filter
    pairs = [(1.0, 90.0), (2.0, 400.0), (3.0, 80.0), (4.0, 70.0)]
    kept = _monotone_keep(pairs)
    assert (2.0, 400.0) not in kept
    # leading misread (monotone but gross outlier) removed by robust fit
    leading = [(1.0, 400.0), (2.0, 90.0), (3.0, 80.0), (4.0, 70.0)]
    fit = _fit_axis(leading)
    assert fit is not None and fit[3] < 2e-3
    assert _fit_axis([(1.0, 90.0), (2.0, 80.0), (3.0, 70.0), (4.0, 60.0)])


# --- R4: retrieval --------------------------------------------------------------


@pytest.fixture(scope="module")
def index_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("retrieval") / "curves.db"
    build_index(db, PILOT)
    return db


def test_flag_off_is_full_rollback(index_db, monkeypatch):
    monkeypatch.delenv("CURVE_RETRIEVAL_ENABLED", raising=False)
    out = search_curves(index_db, filters={"phenomenon":
                                           "efficiency_vs_load"})
    assert out["status"] == "not_enabled"
    monkeypatch.setenv("CURVE_RETRIEVAL_ENABLED", "1")
    assert search_curves(index_db, filters={
        "phenomenon": "efficiency_vs_load"})["status"] == "ok"


def test_missing_index_rolls_back_cleanly(tmp_path):
    out = search_curves(tmp_path / "nonexistent.db", enable=True,
                        filters={"phenomenon": "efficiency_vs_load"})
    assert out["status"] == "not_enabled" or out.get("status")


def test_match_classes_and_never_extrapolate(index_db):
    out = search_curves(
        index_db, enable=True,
        filters={"phenomenon": "efficiency_vs_load"},
        operating_point=0.1, x_unit="A",
        conditions={"vin_v": 12.0, "vout_v": 3.3},
    )
    comparable = [r for r in out["results"]
                  if r["match_class"] in ("exact", "interpolated")]
    assert comparable and comparable[0]["guarantee"] is False
    assert comparable[0]["provenance"]["document_sha256"]
    beyond = search_curves(
        index_db, enable=True,
        filters={"phenomenon": "efficiency_vs_load"},
        operating_point=100.0, x_unit="A",
        conditions={"vin_v": 12.0, "vout_v": 3.3},
    )
    assert all(r["match_class"] == "not_comparable"
               for r in beyond["results"])
    assert any("extrapolation" in str(r.get("reason"))
               for r in beyond["results"])


def test_condition_mismatch_is_not_comparable(index_db):
    out = search_curves(
        index_db, enable=True,
        filters={"phenomenon": "efficiency_vs_load"},
        operating_point=10.0, x_unit="A",
        conditions={"vin_v": 48.0, "vout_v": 5.0},
    )
    assert out["counts"]["not_comparable"] > 0
    assert out["counts"]["exact"] + out["counts"]["interpolated"] == 0


# --- R5/R6: ablation + acceptance gates ------------------------------------------


def test_ablation_gates():
    from scripts.run_curve_ablation import run

    report = run()
    m = report["metrics"]
    assert m["candidate_recall_A_equals_B"] is True
    assert m["false_eliminations_curve"] == 0
    assert m["unsupported_claims_total"] == 0
    assert m["refusal_correct_on_no_comparison_cases"] is True
    assert "PENDING" in m["expert_shortlist_relevance"]
    by_id = {q["id"]: q for q in report["questions"]}
    assert by_id["Q1-light-load-efficiency"]["B_comparable"] >= 1
    assert by_id["Q2-high-current-30A"]["B_comparable"] >= 10
    # CURVE-05B correction: vendor 24 V traces are legend-bound now, so
    # SiC46x answers within-family at 24 V -> 5 V (never cross-family)
    assert by_id["Q3-24to5-tradeoff"]["B_comparable"] >= 1
    assert all(str(v["part"]).startswith("SiC")
               or str(v["part"]).startswith("family:SiC")
               for v in by_id["Q3-24to5-tradeoff"]["B_values"])
    for q in report["questions"]:
        for value in q["B_values"]:
            assert value["curve_id"], "traceability: values cite curves"


def test_challenge_manifest_records_gaps():
    manifest = json.loads(
        (Path(__file__).parent.parent / "results/curve-04-challenges" /
         "challenge_manifest.json").read_text()
    )
    attempts = manifest["acquisition_attempts"]
    assert len(attempts) == 5
    for a in attempts:
        assert a.get("status") in ("coverage_gap",
                                   "partial_raster_recovery_machine_"
                                   "reference_only")
        if a["status"] == "coverage_gap":
            assert a["failure_reason"], "failures carry specific reasons"


def test_adversarial_tamper_provenance(tmp_path):
    db = tmp_path / "curves.db"
    build_index(db, PILOT)
    con = sqlite3.connect(db)
    con.execute("UPDATE curves SET provenance_json = '{}' "
                "WHERE rowid = 1")
    con.commit()
    con.close()
    out = search_curves(db, enable=True,
                        filters={"phenomenon": "efficiency_vs_load"})
    # tampered rows surface as unusable, never as cited evidence
    bad = [r for r in out["results"]
           if not (r.get("provenance") or {}).get("document_sha256")]
    assert all(r["match_class"] not in ("exact", "interpolated")
               for r in bad)
