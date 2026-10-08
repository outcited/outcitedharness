"""Incremental evidence-unit indexer (PRD-SEARCH-01 R5, deliverable 4).

Read-only adapters over existing extraction outputs; every unit is stamped
with the extraction version it came from and written idempotently through
units.replace_document (natural key: doc sha + grain + locator + version).

Sources wired v1:

1. catalog.db claims_canonical — claim-grain units. Multiple claims that
   share one printed cell (min/typ/max of the same symbol) fold into ONE
   unit: one cell is one piece of evidence. Coverage comes from
   part_sources verbatim (burn-primary stays burn-primary).
2. catalog.db part_sources + parts.family — family/series relationship
   units (what family a document documents, and for whom).
3. power-topology wave jsonl (extract-results/power-topology-v1.jsonl) —
   application-grain units from printed topology/isolation evidence with
   real sha256 + page + quote.

Honesty notes:

- the power-burn wave persists docs under ``unhashed:<stem>`` identities
  with null pages; those units keep exactly that provenance (locator =
  row/column headers + quote). When the substrate backfills real hashes
  and pages, a later extraction_version upgrades the units — never a
  silent rewrite.
- verification_state defaults to ``unverified``; an ``adjudications``
  mapping {(doc_sha, quote): state} copied from the pipeline adjudication
  ledger annotates states without this layer ever minting verdicts.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator

from harness.search import units as units_store

_POWER_AISLES = {"discrete-mosfets", "power-management", "battery-management",
                 "power"}
_MCU_AISLES = {"microcontrollers", "mcu", "microcontroller"}
_CONNECTOR_AISLES = {"connectors", "connector", "interconnect"}


def _norm_aisle(aisle: str | None) -> str | None:
    if not aisle:
        return None
    key = aisle.strip().lower()
    if key in _POWER_AISLES or "power" in key or "mosfet" in key:
        return "power"
    if key in _MCU_AISLES or "mcu" in key:
        return "mcu"
    if key in _CONNECTOR_AISLES or "connector" in key:
        return "connectors"
    return aisle.strip().lower()


def _claim_text(symbol: str, qualifier: str | None, value_text: str,
                unit: str | None, condition: str) -> str:
    head = " ".join(t for t in (symbol, qualifier or "") if t).strip()
    value = f"{value_text} {unit or ''}".strip()
    tail = f"@ {condition}" if condition else ""
    return " ".join(x for x in (head, "=", value, tail) if x)


def _cell_key(claim: sqlite3.Row) -> tuple:
    prov = json.loads(claim["provenance"] or "{}")
    quote = prov.get("quote") or ""
    row_header = prov.get("row_header") or ""
    column_header = prov.get("column_header") or ""
    page = prov.get("page")
    return (page, row_header, column_header, quote)


def catalog_from_corpus_jsonl(jsonl_path: str | Path) -> sqlite3.Connection:
    """In-memory catalog-shaped DB from a fixture corpus jsonl.

    Record kinds: ``_kind: part`` (opn, vendor, family, package, doc_sha256,
    coverage_kind, evidence) and claim rows (opn, symbol, ..., quote,
    row_header, column_header, page, doc_sha256). Used by the benchmark
    fixture mode and tests; never touches the real catalog.
    """
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE parts (opn TEXT PRIMARY KEY, vendor TEXT, family TEXT,
                            package TEXT);
        CREATE TABLE part_sources (opn TEXT, doc_sha256 TEXT,
                                   coverage_kind TEXT, evidence TEXT);
        CREATE TABLE doc_revisions (doc_sha256 TEXT PRIMARY KEY,
                                    rev_code TEXT, rev_date TEXT,
                                    doc_kind TEXT, supersedes_sha TEXT);
        CREATE TABLE claims_canonical (id INTEGER PRIMARY KEY, opn TEXT,
            symbol TEXT, qualifier TEXT, condition_norm TEXT, value REAL,
            unit TEXT, value_text TEXT, provenance TEXT, extractor TEXT,
            extractor_version TEXT, doc_sha256 TEXT, created_at REAL);
        """)
    with Path(jsonl_path).open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("_kind") == "part":
                con.execute("INSERT OR REPLACE INTO parts VALUES (?,?,?,?)",
                            (record["opn"], record.get("vendor"),
                             record.get("family"), record.get("package")))
                con.execute(
                    "INSERT OR REPLACE INTO part_sources VALUES (?,?,?,?)",
                    (record["opn"], record.get("doc_sha256"),
                     record.get("coverage_kind", "primary"),
                     record.get("evidence")))
            else:
                con.execute(
                    "INSERT INTO claims_canonical (opn, symbol, qualifier,"
                    " condition_norm, value, unit, value_text, provenance,"
                    " extractor, extractor_version, doc_sha256, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (record["opn"], record["symbol"], record.get("qualifier"),
                     record.get("condition_norm") or "",
                     record.get("value"), record.get("unit"),
                     record.get("value_text"),
                     json.dumps({
                         "quote": record.get("quote"),
                         "row_header": record.get("row_header"),
                         "column_header": record.get("column_header"),
                         "sha": record.get("doc_sha256"),
                         "page": record.get("page"),
                     }),
                     record.get("extractor", "fixture"),
                     record.get("extractor_version", "fixture-v1"),
                     record["doc_sha256"], record.get("created_at", 1.0)))
    return con


def claim_units(catalog_con: sqlite3.Connection, *,
                adjudications: dict[tuple[str, str], str] | None = None,
                aisles: dict[str, str] | None = None) -> Iterator[dict]:
    """Yield claim-grain units from the catalog, one per (doc, version, cell).

    Only the latest extraction version per doc is indexed: older versions
    remain in the catalog as history; search reflects current truth.
    """
    adjudications = adjudications or {}
    aisles = aisles or {}
    # SQLite bare-column rule: extractor_version comes from the row that
    # produced MAX(created_at) — one scan, no correlated subquery.
    latest = {r[0]: r[1] for r in catalog_con.execute(
        "SELECT doc_sha256, extractor_version, MAX(created_at)"
        " FROM claims_canonical GROUP BY doc_sha256")}
    coverage: dict[tuple[str, str], str] = {
        (r["opn"], r["doc_sha256"]): r["coverage_kind"]
        for r in catalog_con.execute(
            "SELECT opn, doc_sha256, coverage_kind FROM part_sources")}
    parts = {r["opn"]: r for r in catalog_con.execute(
        "SELECT opn, vendor, family, package FROM parts")}
    revisions = {r["doc_sha256"]: r for r in catalog_con.execute(
        "SELECT doc_sha256, rev_code, rev_date FROM doc_revisions")}

    claims_by_doc: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for claim in catalog_con.execute("SELECT * FROM claims_canonical"):
        version = latest.get(claim["doc_sha256"])
        if version is not None and claim["extractor_version"] != version:
            continue
        claims_by_doc[claim["doc_sha256"]].append(claim)

    for doc_sha, claims in claims_by_doc.items():
        version = claims[0]["extractor_version"]
        by_cell: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
        for claim in claims:
            by_cell[_cell_key(claim)].append(claim)
        for cell, group in by_cell.items():
            page, row_header, column_header, quote = cell
            first = group[0]
            opn = first["opn"]
            part = parts.get(opn)
            vendor = (part["vendor"] if part else None) or None
            family = (part["family"] if part else None) or None
            locator: dict[str, Any] = {
                "kind": ("table_cell" if (row_header or column_header)
                         else "text_span"),
            }
            if row_header:
                locator["row_header"] = row_header
            if column_header:
                locator["column_header"] = column_header
            if quote:
                locator["quote"] = quote
            lines = []
            for claim in group:
                lines.append(_claim_text(
                    claim["symbol"], claim["qualifier"],
                    str(claim["value_text"]), claim["unit"],
                    claim["condition_norm"] or ""))
            prov_line = " ".join(
                f'{k}="{v}"' for k, v in
                (("row", row_header), ("col", column_header)) if v)
            if page:
                prov_line = (prov_line + f" p.{page}").strip()
            text_repr = (opn + " " + (vendor or "")).strip() + " — " + \
                "; ".join(lines)
            if prov_line:
                text_repr += f" [{prov_line}]"
            if quote:
                text_repr += f' "{quote}"'
            kind = coverage.get((opn, doc_sha), "mention")
            state = adjudications.get((doc_sha, quote), "unverified")
            rev = revisions.get(doc_sha)
            yield units_store.make_unit(
                doc_sha256=doc_sha, grain="claim",
                locator=locator, text_repr=text_repr,
                extraction_version=f"{first['extractor']}/{version}",
                page=page, vendor=vendor,
                doc_class="unknown", category=aisles.get(opn.upper()),
                family=family,
                ident=[t for t in (opn, family, vendor) if t],
                applicability=[{"scope": "opn", "value": opn,
                                "coverage_kind": kind}],
                rev_code=rev["rev_code"] if rev else None,
                rev_date=rev["rev_date"] if rev else None,
                verification_state=state,
                verification_source=("adjudication_ledger"
                                     if state != "unverified" else None))


def family_units(catalog_con: sqlite3.Connection) -> Iterator[dict]:
    """Family/series relationship units: what each document documents.

    Built only from part_sources rows (printed-evidence registrations):
    a doc's coverage of OPNs/families is exactly what the census recorded.
    """
    coverage_rows = catalog_con.execute(
        "SELECT ps.opn, ps.doc_sha256, ps.coverage_kind, ps.evidence,"
        " p.vendor, p.family FROM part_sources ps"
        " LEFT JOIN parts p ON p.opn = ps.opn").fetchall()
    by_doc: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in coverage_rows:
        by_doc[row["doc_sha256"]].append(row)
    for doc_sha, rows in by_doc.items():
        opns = sorted({r["opn"] for r in rows})
        vendors = sorted({r["vendor"] for r in rows if r["vendor"]})
        families = sorted({r["family"] for r in rows if r["family"]})
        kinds = sorted({r["coverage_kind"] for r in rows})
        evidence = next((r["evidence"] for r in rows if r["evidence"]), None)
        text_repr = "document covers {} part(s) [{}]".format(
            len(opns), ", ".join(kinds))
        if families:
            text_repr += " families: " + ", ".join(families)
        if len(opns) <= 8:
            text_repr += " opns: " + ", ".join(opns)
        else:
            text_repr += f" e.g. {', '.join(opns[:8])} (+{len(opns)-8})"
        if evidence:
            text_repr += f' — "{evidence}"'
        locator = {"kind": "source_record", "relationship": "family_coverage"}
        yield units_store.make_unit(
            doc_sha256=doc_sha, grain="family", locator=locator,
            text_repr=text_repr, extraction_version="catalog/part_sources.v1",
            vendor=vendors[0] if len(vendors) == 1 else None,
            doc_class="unknown",
            family=families[0] if len(families) == 1 else None,
            ident=opns[:16] + families,
            applicability=[
                {"scope": "opn", "value": r["opn"],
                 "coverage_kind": r["coverage_kind"]} for r in rows])


def topology_units(wave_path: str) -> Iterator[dict]:
    """Application-grain units from the power-topology wave (printed quotes,
    real sha256 + page). One unit per part-document with its evidence."""
    path = Path(wave_path)
    if not path.exists():
        return
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            topologies = record.get("topologies") or []
            integration = record.get("integration_class")
            isolated = record.get("isolated")
            evidence = record.get("evidence") or []
            if not (topologies or integration is not None
                    or isolated is not None):
                continue
            part = record["part_number"]
            vendor = record.get("vendor")
            aisle = _norm_aisle(record.get("selector_aisle"))
            pieces = []
            if topologies:
                pieces.append("topologies: " + ", ".join(topologies))
            if integration is not None:
                pieces.append(f"integration class: {integration}")
            if isolated is not None:
                pieces.append("isolated: " + ("yes" if isolated else "no"))
            for ev in evidence[:6]:
                quote = (ev.get("quote") or "").strip()
                page = ev.get("page")
                if quote:
                    pieces.append(f'p.{page} "{quote}"' if page
                                  else f'"{quote}"')
            text_repr = " ".join(t for t in (part, vendor or "",
                                             "documented application fit:")
                                 if t) + " " + "; ".join(pieces)
            pages = [ev.get("page") for ev in evidence if ev.get("page")]
            locator = {"kind": "text_span",
                       "relationship": "documented_application",
                       "quotes": [ev.get("quote") for ev in evidence[:6]]}
            yield units_store.make_unit(
                doc_sha256=record["sha256"], grain="application",
                locator=locator, text_repr=text_repr,
                extraction_version=record.get("reader", "power_topology.v1"),
                page=min(pages) if pages else None,
                vendor=vendor, doc_class="datasheet", category=aisle,
                ident=[part, vendor or ""],
                applicability=[{"scope": "opn", "value": part,
                                "coverage_kind": "primary"}])


def aisle_map_from_wave(wave_path: str) -> dict[str, str]:
    """part -> normalized aisle map (used to categorize claim units)."""
    out: dict[str, str] = {}
    path = Path(wave_path)
    if not path.exists():
        return out
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            aisle = _norm_aisle(record.get("selector_aisle"))
            if aisle:
                out[record["part_number"].upper()] = aisle
    return out


def _grouped(units: Iterable[dict]) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for unit in units:
        out[(unit["doc_sha256"], unit["extraction_version"])].append(unit)
    return out


def index_catalog(search_con: sqlite3.Connection, catalog_path: str, *,
                  adjudications: dict[tuple[str, str], str] | None = None,
                  wave_path: str | None = None,
                  dry_run: bool = False) -> dict:
    """Index catalog claims + families (+ topology wave) incrementally."""
    catalog_con = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    catalog_con.row_factory = sqlite3.Row
    try:
        aisles = aisle_map_from_wave(wave_path) if wave_path else {}
        sources = [
            claim_units(catalog_con, adjudications=adjudications,
                        aisles=aisles),
            family_units(catalog_con),
        ]
        if wave_path:
            sources.append(topology_units(wave_path))
        merged: list[dict] = []
        for source in sources:
            merged.extend(source)
    finally:
        catalog_con.close()
    stats = {"docs": 0, "units": 0, "added": 0, "replaced": 0, "removed": 0}
    if dry_run:
        grouped = _grouped(merged)
        stats["docs"] = len(grouped)
        stats["units"] = len(merged)
        return stats
    # One transaction for the whole run: per-doc commits are fsync-bound on
    # the M5 volume (~300ms/doc); batched, the same load is sequential.
    for (doc_sha, version), group in _grouped(merged).items():
        result = units_store.replace_document(
            search_con, group, doc_sha256=doc_sha,
            extraction_version=version, commit=False)
        stats["docs"] += 1
        stats["units"] += result["units"]
        stats["added"] += result["added"]
        stats["replaced"] += result["replaced"]
        stats["removed"] += result["removed"]
    search_con.commit()
    return stats
