"""Facet breakdown over the evidence index (category dwindling, 2026-10-08).

The retrieval layer's job for a facet UI: given the cohort a user has
narrowed so far, return the next dimensions and their value counts, ordered
by how much each choice would reduce the cohort. The UI then dwindles
permutations instead of asking the engineer to guess vocabulary.

Honesty rules carried over: facet values come from printed/extracted signal
(taxonomy.py), unclassified units stay unclassified rather than guessed,
and every facet value reports its evidence-grade vs discovery-only split so
the UI never presents placeholder material as verified.
"""

from __future__ import annotations

import sqlite3

from harness.search import taxonomy

FACETS_SCHEMA = "harness.search-facets.v1"

_DIMENSIONS = ("category", "subcategory", "vendor", "grain")


def ensure_schema(con: sqlite3.Connection) -> None:
    try:
        con.execute("ALTER TABLE units ADD COLUMN subcategory TEXT")
        con.commit()
    except sqlite3.OperationalError:
        pass


def backfill(con: sqlite3.Connection, *, batch: int = 500) -> dict:
    """Classify units missing a normalized category/subcategory.

    Reads only stored unit text; fail-closed (no match -> no category).
    """
    ensure_schema(con)
    rows = con.execute(
        "SELECT unit_id, category, subcategory, text_repr FROM units"
        " WHERE retired=0 AND (subcategory IS NULL)")
    updates = []
    changed = 0
    for row in rows:
        category, subcategory = taxonomy.classify(
            existing_category=row["category"], text=row["text_repr"])
        if category == row["category"] and \
                subcategory == row["subcategory"]:
            continue
        updates.append((category, subcategory, row["unit_id"]))
        changed += 1
        if len(updates) >= batch:
            con.executemany(
                "UPDATE units SET category=?, subcategory=? WHERE unit_id=?",
                updates)
            updates = []
    if updates:
        con.executemany(
            "UPDATE units SET category=?, subcategory=? WHERE unit_id=?",
            updates)
    con.commit()
    total = con.execute(
        "SELECT COUNT(*) FROM units WHERE retired=0").fetchone()[0]
    classified = con.execute(
        "SELECT COUNT(*) FROM units WHERE retired=0 AND category IS NOT NULL"
    ).fetchone()[0]
    return {"classified": changed, "total_active": total,
            "classified_active": classified,
            "coverage": round(classified / max(1, total), 4)}


def _where(filters: dict) -> tuple[str, list]:
    clauses = ["retired=0"]
    args: list = []
    for key in ("category", "subcategory", "vendor", "family", "grain",
                "evidence_grade"):
        value = filters.get(key)
        if value is None:
            continue
        if key == "grain" and isinstance(value, (list, tuple)):
            clauses.append(f"grain IN ({','.join('?' * len(value))})")
            args.extend(value)
        else:
            clauses.append(f"{key} = ?")
            args.append(value)
    return " AND ".join(clauses), args


def facets(con: sqlite3.Connection, filters: dict | None = None) -> dict:
    """Cohort size + next-dimension value counts, ordered by reduction.

    Reduction for a value v = cohort_size - cohort_size_with_v: choosing a
    value that halves the cohort outranks one that trims 2%.
    """
    filters = dict(filters or {})
    ensure_schema(con)
    where, args = _where(filters)
    cohort = con.execute(
        f"SELECT COUNT(*) FROM units WHERE {where}", args).fetchone()[0]
    out: dict[str, list[dict]] = {}
    for dim in _DIMENSIONS:
        if filters.get(dim) is not None:
            continue  # already chosen — don't re-offer
        rows = con.execute(
            f"SELECT COALESCE({dim}, '(unclassified)') v, COUNT(*) n,"
            f" SUM(CASE WHEN doc_sha256 NOT LIKE 'unhashed:%' AND page IS NOT"
            f" NULL THEN 1 ELSE 0 END) graded"
            f" FROM units WHERE {where} GROUP BY 1 ORDER BY n DESC LIMIT 25",
            args).fetchall()
        out[dim] = [
            {"value": r[0], "count": r[1], "evidence_grade": r[2],
             "reduction": cohort - r[1]} for r in rows
        ]
    return {
        "schema": FACETS_SCHEMA,
        "filters": filters,
        "cohort": cohort,
        "facets": out,
        "notice": ("Facet values are extracted signal, not guessed; "
                   "unclassified stays unclassified. evidence_grade counts "
                   "exclude placeholder-identity units."),
    }