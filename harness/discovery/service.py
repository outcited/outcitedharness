"""Discovery service — the logic behind the API front door (Phase 2).

Deterministic, read-only over catalog.db. Loads the candidate population
once per discover, pins the corpus release (spec section 45), runs the
Phase-1 evaluator, and serves the question engine's next-knife ranking
by expected cohort reduction (spec sections 29-30). No model calls.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from typing import Any

from harness.discovery import store
from harness.discovery.evaluator import (
    AXES,
    Atom,
    axis_for_symbol,
    evaluate_design,
    evaluate_part,
)

CATALOG_DEFAULT = "/Volumes/M5_4TB/extract-results/catalog.db"
TOPOLOGY_WAVE_DEFAULT = (
    "/Volumes/M5_4TB/extract-results/power-topology-v1.jsonl")

AXIS_HELP = {
    "vds_rating_v": (
        "The blocking voltage the switch must survive. For a 24 V "
        "industrial rail (18-36 V) pick at least the 36 V maximum, plus "
        "margin you choose (a 20%% margin makes the effective "
        "requirement %.1f V)."),
    "id_continuous_a": (
        "Continuous current the device must carry at temperature. "
        "Datasheets derate: a part rated 5 A at TC=25C may carry far less "
        "at 100C — add a required condition to pin the evidence."),
    "tj_max_c": ("Maximum junction temperature the part is rated for. "
                 "125C is common; 150C+ for automotive/industrial."),
    "tstg_range_c": ("The storage/operating temperature span that must "
                     "contain your environment."),
    "rds_on_ohm": ("Maximum on-resistance (conduction loss driver). "
                   "Softer constraint: fails score lower, never eliminate."),
}


def corpus_release(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT count(*) parts, coalesce(max(first_seen), 0) latest"
        " FROM parts").fetchone()
    claims = con.execute("SELECT count(*) n FROM claims_canonical").fetchone()
    digest = hashlib.sha256(
        f"{row['parts']}:{row['latest']}:{claims['n']}".encode()).hexdigest()[:10]
    return f"dw-power-r{row['parts']}-{digest}"


def load_population(catalog_path: str = CATALOG_DEFAULT) -> dict[str, list]:
    con = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    claims_by_part: dict[str, list[dict]] = defaultdict(list)
    for row in con.execute(
            "SELECT opn, symbol, qualifier, condition_norm, value, unit,"
            " provenance FROM claims_canonical"):
        claims_by_part[row["opn"]].append({
            "symbol": row["symbol"], "qualifier": row["qualifier"],
            "condition": row["condition_norm"], "value": row["value"],
            "unit": row["unit"],
            "provenance": json.loads(row["provenance"]),
        })
    con.close()
    return dict(claims_by_part)


def _topology_distribution(passing: list[str],
                           wave_path: str = TOPOLOGY_WAVE_DEFAULT) -> dict:
    dist: dict[str, int] = defaultdict(int)
    try:
        with open(wave_path) as h:
            for line in h:
                row = json.loads(line)
                if row.get("part_number") in passing:
                    for t in row.get("topologies") or []:
                        dist[t] += 1
    except FileNotFoundError:
        return {}
    return dict(dist)


def create_design(db_path: str | None, aisle: str = "power",
                  name: str | None = None) -> str:
    con = store.connect(db_path)
    try:
        return store.create_design(con, aisle=aisle, name=name)
    finally:
        con.close()


def add_requirements(db_path: str | None, design_id: str,
                     atoms: list[dict], replace: bool = False) -> list[dict]:
    con = store.connect(db_path)
    try:
        if not con.execute("SELECT 1 FROM designs WHERE design_id=?",
                           (design_id,)).fetchone():
            raise KeyError(f"no such design {design_id}")
        for raw in atoms:
            atom = Atom(axis=raw["axis"],
                        op=raw.get("op"),
                        value=raw.get("value"),
                        hard=raw.get("hard", True),
                        margin_pct=raw.get("margin_pct"),
                        required_condition=raw.get("required_condition"))
            store.upsert_atom(con, design_id, {
                "axis": atom.axis, "op": atom.op, "value": atom.value,
                "hard": atom.hard, "margin_pct": atom.margin_pct,
                "required_condition": atom.required_condition,
                "state": raw.get("state", "active"),
            }, replace=replace)
        return store.active_atoms(con, design_id)
    finally:
        con.close()


def discover(db_path: str | None, design_id: str,
             catalog_path: str = CATALOG_DEFAULT,
             population: dict[str, list] | None = None) -> dict[str, Any]:
    con = store.connect(db_path)
    try:
        atoms_raw = store.active_atoms(con, design_id)
        if not atoms_raw:
            raise ValueError("design has no active atoms")
        atoms = [Atom(axis=a["axis"], op=a["op"], value=a["value"],
                      hard=a["hard"], margin_pct=a["margin_pct"],
                      required_condition=a["required_condition"])
                 for a in atoms_raw]
    finally:
        con.close()

    population = population if population is not None else \
        load_population(catalog_path)
    cat_con = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    cat_con.row_factory = sqlite3.Row
    release = corpus_release(cat_con)
    cat_con.close()

    result = evaluate_design(atoms, population)
    passing = result["cohorts"]["passing"]
    summary = {
        "design_id": design_id,
        "corpus_release": release,
        "candidates": result["candidates"],
        "cohorts": {k: len(v) for k, v in result["cohorts"].items()},
        "constraint_driver": result["constraint_driver"],
        "topology_distribution": _topology_distribution(passing),
        "sample_passing": passing[:20],
    }
    con = store.connect(db_path)
    try:
        store.record_result(con, design_id, release,
                            {"summary": summary,
                             "cohorts": result["cohorts"],
                             "ledger": {p: {a: v.__dict__ for a, v in
                                            vs.items()}
                                        for p, vs in
                                        result["ledger"].items()}})
    finally:
        con.close()
    return summary


def ledger(db_path: str | None, design_id: str,
           cohort: str | None = None, limit: int = 50) -> dict[str, Any]:
    con = store.connect(db_path)
    try:
        result = store.latest_result(con, design_id)
    finally:
        con.close()
    if not result:
        raise ValueError("design has no discover result yet")
    payload = result["summary"]
    cohorts: dict[str, list] = payload.get("cohorts", {})
    full: dict = payload.get("ledger", {})
    parts = set(full)
    wanted = set(cohorts.get(cohort, [])) if cohort else parts
    rows = [{"part": p, "verdicts": full[p]}
            for p in sorted(parts & wanted)]
    return {"corpus_release": result["corpus_release"],
            "cohort": cohort, "rows": rows[:limit]}


def next_question(db_path: str | None, design_id: str,
                  population: dict[str, list] | None = None,
                  catalog_path: str = CATALOG_DEFAULT) -> dict[str, Any]:
    """Rank unanswered axes by expected cohort reduction (spec 29-30).

    For each axis the design has not pinned, compute the candidate value
    distribution (best claim per part), propose the median as the
    screening question, and report how many current candidates would
    FAIL at that screen — the deterministic, LLM-free knife ranking.
    """
    con = store.connect(db_path)
    try:
        atoms_raw = store.active_atoms(con, design_id)
        answered = {a["axis"] for a in atoms_raw}
    finally:
        con.close()

    population = population if population is not None else \
        load_population(catalog_path)
    questions = []
    for axis_name, axis in AXES.items():
        if axis_name in answered:
            continue
        values = []
        for part, claims in population.items():
            best = None
            for c in claims:
                if axis_for_symbol(c.get("symbol")) != axis_name:
                    continue
                v = c.get("value")
                if isinstance(v, (int, float)):
                    best = max(best, v) if best is not None else v
            if best is not None:
                values.append(best)
        if not values:
            questions.append({"axis": axis_name, "askable": False,
                              "parts_with_claims": 0})
            continue
        values.sort()
        median = values[len(values) // 2]
        # expected cohort reduction if the median became a hard requirement
        probe = Atom(axis=axis_name, value=median)
        fail = 0
        considered = 0
        for part, claims in population.items():
            verdicts = evaluate_part(part, [probe], claims)
            v = verdicts[axis_name]
            if v.verdict in ("PASS", "NEAR_MISS", "FAIL"):
                considered += 1
                if v.verdict == "FAIL":
                    fail += 1
        questions.append({
            "axis": axis_name,
            "askable": True,
            "parts_with_claims": len(values),
            "proposed_value": median,
            "expected_fail_at_median": fail,
            "expected_reduction_pct": round(
                100.0 * fail / max(considered, 1), 1),
            "help": AXIS_HELP.get(axis_name, ""),
        })
    questions.sort(key=lambda q: -q.get("expected_fail_at_median", 0))
    return {"design_id": design_id, "questions": questions,
            "note": ("expected_reduction is over parts whose claims answer "
                     "the axis; UNKNOWN never eliminates (spec section 7)")}


def get_design(db_path: str | None, design_id: str) -> dict[str, Any]:
    con = store.connect(db_path)
    try:
        row = con.execute("SELECT * FROM designs WHERE design_id=?",
                          (design_id,)).fetchone()
        if not row:
            raise KeyError(f"no such design {design_id}")
        result = store.latest_result(con, design_id)
        latest = None
        if result:
            payload = result["summary"]
            latest = payload.get("summary", payload)
        return {"design_id": row["design_id"], "aisle": row["aisle"],
                "name": row["name"],
                "corpus_release": row["corpus_release"],
                "created_at": row["created_at"],
                "atoms": store.active_atoms(con, design_id),
                "latest_result": latest}
    finally:
        con.close()
