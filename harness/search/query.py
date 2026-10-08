"""Hybrid evidence retrieval (PRD-SEARCH-01 R2/R4).

Fuses three signals over the evidence-unit store (harness/search/units.py):

1. FTS5 bm25 over normalized technical text (unicode61) — exact technical
   terms with symbol/unit folding (100mA ~ 100 ma, RDS(on) ~ rdson);
2. trigram FTS5 over identifiers — substring/prefix part-number and family
   search for engineers who do not know the MPN;
3. optional per-unit vectors + cosine — semantic natural-language matching
   (vocabulary mismatch must not bury a relevant result).

Boosts are transparent and reported in `rationale` per hit: applicability
coverage (primary > family > mention), verification state, family filter
match. A result NEVER claims engineering qualification — the envelope says
so explicitly and `qualification` is always "none".

Scoring is deterministic given (index, query, embedder output): same inputs
-> same ranking, so the frozen benchmark is reproducible.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Callable, Sequence

from harness.search import units as units_store
from harness.search.expand import axis_queries, expand_intent

RESPONSE_SCHEMA = "harness.search-response.v1"

# Ranking-policy identity: part of the retrieval release fingerprint. Change
# the policy -> change this string -> the release id changes. A changed
# ranking system must never silently keep the same retrieval identity.
RANKING_POLICY = ("hybrid-v1(sem=0.50,text=0.35,ident=0.35,"
                  "boosts=coverage/state/family,aux=expand4,"
                  "vector=brutefloat32-numpy-v1)")

# Fusion weights (documented contract; benchmark may tune but never silently)
W_SEMANTIC = 0.5
W_TEXT = 0.35
W_IDENT = 0.35

_BOOST_COVERAGE = {"primary": 0.06, "family": 0.0, "mention": -0.02,
                   "ordering_table": 0.0}
_BOOST_STATE = {"m4_verified": 0.10, "supported": 0.06, "unverified": 0.0,
                "ambiguous": -0.03, "unsupported": -0.15, "not_in_doc": -0.15,
                "rejected": -0.25}


def make_embedder(url: str, model: str = "bge-m3-cr-tapes-v1",
                  timeout: float = 30.0):
    """OpenAI-style /v1/embeddings client over the serving-qualified fleet
    embedders (dgx1/e10b :8800/:8804). Returns a callable for search()."""
    import json as _json
    import urllib.request

    def embed(texts: list[str]):
        req = urllib.request.Request(
            url, data=_json.dumps({"model": model, "input": texts}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = _json.loads(resp.read())
        ordered = sorted(payload["data"], key=lambda d: d["index"])
        return [item["embedding"] for item in ordered]

    return embed


def fts_query(text: str) -> str:
    """Escape a normalized query into an FTS5 OR-of-phrases match string."""
    normed = units_store.technical_normalize(text)
    if not normed:
        return ""
    tokens = [t for t in normed.split() if len(t) >= 1][:24]
    if not tokens:
        return ""
    phrases = []
    if len(tokens) > 1:
        phrases.append(" ".join(tokens))
    phrases.extend(tokens)
    return " OR ".join(f'"{p}"' for p in phrases)


def _fts_scores(con: sqlite3.Connection, match: str, table: str,
                limit: int) -> dict[str, float]:
    """bm25 -> normalized 0..1 (higher = better), saturating at +3.0."""
    if not match:
        return {}
    try:
        rows = con.execute(
            f"SELECT unit_id, bm25({table}) AS rank FROM {table}"
            f" WHERE {table} MATCH ? ORDER BY rank LIMIT ?",
            (match, limit)).fetchall()
    except sqlite3.OperationalError:
        return {}
    out = {}
    for row in rows:
        raw = -row["rank"]  # bm25 returns negative-better
        if raw <= 0:
            out[row["unit_id"]] = 0.0
        else:
            out[row["unit_id"]] = raw / (raw + 3.0)
    return out


def _vector_scores(con: sqlite3.Connection, query_vector: Sequence[float],
                   limit: int) -> dict[str, float]:
    """Cosine over ALL indexed vectors — never restricted to FTS candidates
    (vocabulary mismatch is precisely what this path solves). numpy matrix
    path when available; pure-Python fallback otherwise."""
    rows = con.execute(
        "SELECT v.unit_id, v.vec FROM vectors v JOIN units u ON"
        " u.unit_id = v.unit_id AND u.retired=0").fetchall()
    if not rows:
        return {}
    try:
        import numpy as np
        q = np.asarray(query_vector, dtype=np.float32)
        matrix = np.vstack([np.frombuffer(row["vec"], dtype=np.float32)
                            for row in rows])
        norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(q)
        norms[norms == 0] = 1.0
        sims = (matrix @ q) / norms
        order = np.argsort(-sims)[:limit]
        return {rows[i]["unit_id"]: float(sims[i]) for i in order
                if sims[i] > 0.0}
    except ImportError:
        scored = []
        for row in rows:
            cos = units_store.cosine(query_vector,
                                     units_store.unpack_vector(row["vec"]))
            if cos > 0.0:
                scored.append((row["unit_id"], cos))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return dict(scored[:limit])


def _candidate_ids(con: sqlite3.Connection, query: str, aux: list[str],
                   limit: int) -> tuple[dict[str, float], dict[str, float]]:
    """(text_scores, ident_scores) across original + auxiliary phrasings."""
    text: dict[str, float] = {}
    ident: dict[str, float] = {}
    candidates = 4 * limit + 40
    for phrasing in [query] + aux[:4]:
        tq = fts_query(phrasing)
        for uid, score in _fts_scores(con, tq, "units_fts", candidates).items():
            text[uid] = max(text.get(uid, 0.0), score)
        iq = fts_query(phrasing)
        for uid, score in _fts_scores(con, iq, "ident_fts", candidates).items():
            ident[uid] = max(ident.get(uid, 0.0), score)
    return text, ident


def _passes_filters(unit: dict, filters: dict[str, Any]) -> bool:
    if not filters.get("include_retired", False) and unit["retired"]:
        return False
    if filters.get("vendor"):
        if (unit["vendor"] or "").lower() != filters["vendor"].lower():
            return False
    if filters.get("family"):
        if (unit["family"] or "").lower() != filters["family"].lower():
            return False
    if filters.get("category"):
        if (unit["category"] or "") != filters["category"]:
            return False
    if filters.get("grain"):
        grains = set(filters["grain"])
        if isinstance(filters["grain"], str):
            grains = {filters["grain"]}
        if unit["grain"] not in grains:
            return False
    grade = unit.get("evidence_grade") or units_store.unit_evidence_grade(unit)
    if filters.get("evidence_grade") and grade != filters["evidence_grade"]:
        return False
    min_state = filters.get("min_verification")
    if min_state:
        ladder = ["rejected", "not_in_doc", "unsupported", "ambiguous",
                  "unverified", "supported", "m4_verified"]
        if ladder.index(unit["verification_state"]) < ladder.index(min_state):
            return False
        # P1 gate: only evidence-grade material may ever satisfy a
        # verified-evidence bar. Discovery-only material cannot.
        if grade == "discovery_only" and min_state != "unverified" and \
                ladder.index(min_state) > ladder.index("unverified"):
            return False
    return True


def _applicability_view(unit: dict) -> list[dict]:
    """Applicability exactly as stored — never widened, never hidden."""
    return unit["applicability"]


def search(con: sqlite3.Connection, query: str, *, limit: int = 10,
           filters: dict[str, Any] | None = None,
           embed: Callable[[list[str]], Any] | None = None,
           with_interpretations: bool = True) -> dict:
    """Run one hybrid search; returns the versioned response envelope (R4)."""
    started = time.time()
    filters = dict(filters or {})
    limit = max(1, min(int(limit or 10), 50))
    expansion = expand_intent(query)
    aux = axis_queries(query)

    text_scores, ident_scores = _candidate_ids(con, query, aux, limit)
    vector_scores: dict[str, float] = {}
    if embed is not None and units_store.vectors_available(con):
        try:
            embedded = embed([query])
            query_vector = list(embedded[0]) if embedded else []
            if query_vector:
                vector_scores = _vector_scores(con, query_vector,
                                               4 * limit + 40)
        except Exception:
            vector_scores = {}

    all_ids = set(text_scores) | set(ident_scores) | set(vector_scores)
    if not all_ids:
        return _envelope(query, expansion, [], started, con,
                         with_interpretations)

    hits = []
    for uid in all_ids:
        row = con.execute(
            "SELECT * FROM units WHERE unit_id=?", (uid,)).fetchone()
        if row is None:
            continue
        unit = units_store.row_to_unit(row)
        if not _passes_filters(unit, filters):
            continue
        rationale: list[str] = []
        score = 0.0
        t = text_scores.get(uid, 0.0)
        i = ident_scores.get(uid, 0.0)
        s = vector_scores.get(uid, 0.0)
        if s:
            score += W_SEMANTIC * s
            rationale.append(f"semantic:cos={s:.3f}")
        if t:
            score += W_TEXT * t
            rationale.append(f"text:bm25norm={t:.3f}")
        if i:
            score += W_IDENT * i
            rationale.append(f"ident:trigram={i:.3f}")
        if not rationale:
            continue
        boosts = 0.0
        for app in unit["applicability"]:
            kind = app.get("coverage_kind")
            if kind in _BOOST_COVERAGE:
                boosts += _BOOST_COVERAGE[kind]
                if kind == "primary":
                    rationale.append("boost:coverage=primary")
                    break
        state_boost = _BOOST_STATE.get(unit["verification_state"], 0.0)
        boosts += state_boost
        if state_boost:
            rationale.append(f"boost:verification={unit['verification_state']}")
        if filters.get("family") and unit["family"]:
            boosts += 0.05
            rationale.append("boost:family-filter-match")
        score += boosts
        hits.append({"unit": unit, "score": round(score, 4),
                     "rationale": rationale,
                     "applicability": _applicability_view(unit)})
    hits.sort(key=lambda h: (-h["score"], h["unit"]["unit_id"]))
    hits = hits[:limit]
    return _envelope(query, expansion, hits, started, con,
                     with_interpretations)


def _slim_structured(structured: dict | None) -> dict | None:
    """Curve payloads carry hundreds of digitized points; the search
    envelope summarizes them. The full payload rides on GET /v1/units/{id}."""
    if not structured or structured.get("schema") != \
            "harness.search-curve-figure.v1":
        return structured
    slim = dict(structured)
    slim["series"] = [
        {
            "name": s.get("name"),
            "condition": s.get("condition"),
            "x_range": s.get("x_range"),
            "point_count": len(s.get("points") or []),
            "sample_points": (s.get("points") or [])[:3],
        }
        for s in structured.get("series") or []
    ]
    return slim


def _envelope(query: str, expansion: dict, hits: list[dict],
              started: float, con: sqlite3.Connection,
              with_interpretations: bool) -> dict:
    release = units_store.index_release(con,
                                        policy_identity=RANKING_POLICY)
    return {
        "schema": RESPONSE_SCHEMA,
        "query": {
            "original": (query or "").strip(),
            "interpretations": (expansion["interpretations"]
                                if with_interpretations else []),
        },
        "units": [
            {
                "unit_id": h["unit"]["unit_id"],
                "evidence_id": h["unit"].get("evidence_id"),
                "evidence_grade": h["unit"].get("evidence_grade"),
                "grain": h["unit"]["grain"],
                "doc_sha256": h["unit"]["doc_sha256"],
                "page": h["unit"]["page"],
                "locator": h["unit"]["locator"],
                "text_repr": h["unit"]["text_repr"],
                "structured": _slim_structured(h["unit"].get("structured")),
                "vendor": h["unit"]["vendor"],
                "family": h["unit"]["family"],
                "category": h["unit"]["category"],
                "doc_class": h["unit"]["doc_class"],
                "revision": {"rev_code": h["unit"]["rev_code"],
                             "rev_date": h["unit"]["rev_date"]},
                "applicability": h["applicability"],
                "verification_state": h["unit"]["verification_state"],
                "verification_source": h["unit"]["verification_source"],
                "extraction_version": h["unit"]["extraction_version"],
                "score": h["score"],
                "rationale": h["rationale"],
            }
            for h in hits
        ],
        "provenance": {
            "source_db": "search_index.db",
            "release": release["release"],
            "unit_count": release["unit_count"],
            "active_count": release["active_count"],
            "returned_evidence_grade": sum(
                1 for h in hits
                if (h["unit"].get("evidence_grade") or "discovery_only")
                == "evidence_grade"),
            "returned_discovery_only": sum(
                1 for h in hits
                if (h["unit"].get("evidence_grade") or "discovery_only")
                == "discovery_only"),
        },
        "qualification": "none",
        "notice": ("Relevance is not engineering qualification. "
                   "Interpretations are hypotheses. Applicability is reported "
                   "exactly as extracted; family evidence is never widened "
                   "to OPN evidence. Units graded discovery_only (placeholder "
                   "document identity or imprecise locator) are visibly "
                   "marked and never presented as verified evidence."),
        "took_ms": round((time.time() - started) * 1000.0, 2),
    }
