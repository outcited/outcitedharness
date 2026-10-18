#!/usr/bin/env python3
"""Vision router — the smallest-seam integration for CONTEXT-FIRST.

Implements the staged routing policy (PRD-CONTEXT-FIRST-EXTRACTION +
directive): no new extraction architecture; the existing extractors decide
the route.

  1. vector figure where the deterministic extractor succeeds
        -> deterministic extraction (NO vision call)
  2. good tight crop (digital text page, single target figure)
        -> existing baseline behavior by default (bare prompt); envelope
           is tested for regression and adopted ONLY if it does not make
           correct results worse
  3. full page or difficult multi-region
        -> figure location + source-grounded envelope
  4. raster / partial-text / identity-heavy
        -> envelope with ONLY fields that exist and carry a source
           locator (status ok + non-null locator)

Every route decision and every envelope field carries a completeness
flag; nothing is fabricated (envelope v2 contract).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ve = _load("vision_envelope", "scripts/vision_envelope.py")


def locator_filter(envelope: dict) -> dict:
    """For raster/identity classes: keep only fields with status ok AND a
    source locator. Everything else is dropped to explicit missing."""

    def keep(f):
        return (isinstance(f, dict) and "status" in f
                and f.get("status") == "ok" and f.get("source_locator"))

    out = dict(envelope)
    figs = []
    for fig in envelope.get("figures", []):
        g = dict(fig)
        for key in ("caption", "plot_type", "package_token"):
            if not keep(g.get(key)):
                g[key] = {"value": None, "status": "missing",
                          "source_locator": None}
        for axis in ("x_axis", "y_axis"):
            a = dict(g.get(axis) or {})
            for k, v in list(a.items()):
                if isinstance(v, dict) and not keep(v):
                    a[k] = {"value": None, "status": "missing",
                            "source_locator": None}
            g[axis] = a
        if not g.get("legend_series") or isinstance(g.get("legend_series"), dict):
            g["legend_series"] = {"value": None, "status": "missing",
                                  "source_locator": None}
        if not g.get("printed_conditions") or isinstance(g.get("printed_conditions"), dict):
            g["printed_conditions"] = {"value": None, "status": "missing",
                                       "source_locator": None}
        figs.append(g)
    out["figures"] = figs
    out["locator_filtered"] = True
    return out


def completeness(envelope: dict) -> dict:
    ok = missing = unknown = 0
    def walk(x):
        nonlocal ok, missing, unknown
        if isinstance(x, dict):
            if "status" in x:
                if x["status"] == "ok":
                    ok += 1
                elif x["status"] == "missing":
                    missing += 1
                else:
                    unknown += 1
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(envelope)
    return {"fields_ok": ok, "fields_missing": missing, "fields_unknown": unknown}


def vector_available(pdf: Path, page_1based: int, figure_index: int):
    """Deterministic extractor success with acceptable residuals
    (the extractor's own fail-closed bars)."""
    evc = _load("evc", "scripts/extract_vector_curve_references.py")
    rec = evc.extract_page(pdf, page_1based)
    for p in rec.get("plots", []):
        if p.get("_figure_index") == figure_index:
            q = p.get("_numeric_quality") or {}
            xr = q.get("x_fit_residual_pct_of_span")
            yr = q.get("y_fit_residual_pct_of_span")
            if (p.get("series") and xr is not None and yr is not None
                    and xr < 0.5 and yr < 0.5):
                return {"available": True, "series": len(p["series"]),
                        "residuals_pct": {"x": xr, "y": yr},
                        "digitized": {
                            "title": p.get("title"),
                            "series": [{"name": s.get("name"),
                                        "points": s.get("points")}
                                       for s in p["series"]],
                        }}
    return {"available": False}


def route(pdf: Path, page_1based: int, figure_index: int | None,
          caller_identity: str | None, crop_kind: str,
          text_layer_chars: int) -> dict:
    """Return the route decision + payload for the vision call
    (or the deterministic result — in which case no call is made)."""
    env = ve.build_envelope(pdf, page_1based, caller_identity)
    comp = completeness(env)
    rasterish = text_layer_chars < 400
    decision = None
    payload = {}

    if figure_index is not None:
        vec = vector_available(pdf, page_1based, figure_index)
        if vec["available"]:
            decision = "deterministic_no_vision"
            payload = {"deterministic": vec["digitized"],
                       "route_reason": "vector extractor passed fail-closed bars"}
        elif crop_kind == "tight" and not rasterish:
            decision = "tight_crop_baseline"
            payload = {"envelope": env, "use_envelope_text": False,
                       "route_reason": "good tight crop on digital page: baseline default"}
        else:
            decision = "fullpage_or_difficult_with_envelope"
            payload = {"envelope": env, "use_envelope_text": True,
                       "route_reason": "full page / multi-region / difficult"}
    else:
        if rasterish:
            decision = "raster_locator_envelope"
            env = locator_filter(env)
            payload = {"envelope": env, "use_envelope_text": True,
                       "route_reason": "raster/partial-text: locator-verified fields only"}
        else:
            decision = "table_or_identity_envelope"
            env = locator_filter(env)
            payload = {"envelope": env, "use_envelope_text": True,
                       "route_reason": "identity-heavy/table: locator-verified fields only"}
    return {"decision": decision, "envelope_completeness": comp,
            "trust_policy": ve.TRUST_POLICY, **payload}
