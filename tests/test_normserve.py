"""NORM-SERVE-01 tests (issue #64 contract) + hw-x9g round-5 admission gate.

Round 5 (repair of the f3df0c2d rejection): the admission gate
RE-READS the source PDF from the vault and verifies cited CELL content,
field/header/unit/role binding, applicability, and conditions against
CURRENT BYTES. The positive control (ST1S15 / TA25C / VIN 2.3-5.5 V,
real fitz-generated PDF) loads ONLY with a complete source-resolvable
receipt; every f3df bypass (invented quoted_text '999V', unrelated OPN,
invented conditions, page 999, negative coordinates, vault-only values,
declared column_unit overriding a printed unit) is HELD with a reason
code. Store safeguards (PR66) stay: replay-zero, null-safe uniqueness,
distinct-page retention, same-page conflict refusal, old-store
preflight refusals — all executed here.
"""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from harness.normserve.loader import Rejection, Store, load_rows
from harness.normserve.serve import make_handler
from harness.normserve.schema import SCHEMA_VERSION
from harness.normserve.vault import verify_sha256

ST1S15_SHA = "82d2ee0cc7dd7c7aa47fed41ab13e9593d37f49d858470b56d50e648a104c222"

VIN_VERBATIM = "VIN | Operating input voltage range | 2.3 | 5.5 | V"
VIN_HEADER = "VIN Min Typ Max Unit"

# Real-PDF vault pages (fitz-generated; get_text must reproduce these
# lines). Page 1: applicability + condition sources (Table 25 style).
# Page 2: the VIN characteristics table. Page 3: VOUT + second VIN.
VAULT_PAGES = [
    ["ST1S15",
     "VDD = 3.6 V (local table condition)",
     "TA = 25 C (section 6.1.2 default, inherited)",
     "TA = 105 C (column condition)"],
    [VIN_HEADER,
     VIN_VERBATIM,
     "VIN | Operating input voltage range | 2.3 to 5.5 | V",
     "VIN | Operating input voltage range | 4.7 | 5.5 | V",
     "IQ Min Typ Max Unit",
     "IQ | Supply current | typ | 22.0 | 23.8 | mA"],
    ["VOUT Min Typ Max Unit",
     "VOUT | Output voltage | 9 | V",
     "VOUT | Output voltage | typ | 9 | V",
     "VOUT | Output voltage | 9mV",
     "VIN Min Typ Max Unit",
     "VIN | Operating input voltage range | 4.7 | 5.5 | V"],
]

CONDITION_LOCAL_VDD = {"verbatim": "VDD = 3.6 V (local table condition)",
                       "precedence": "local", "source_page": 1}
COND_TA25_INHERITED = {
    "verbatim": "TA = 25 C (section 6.1.2 default, inherited)",
    "precedence": "inherited", "source_page": 1}
COND_TA105_COLUMN = {"verbatim": "TA = 105 C (column condition)",
                     "precedence": "column", "source_page": 1}
DEFAULT_CONDITIONS = [dict(CONDITION_LOCAL_VDD), dict(COND_TA25_INHERITED)]


def make_pdf_bytes(pages_lines):
    """Build a real PDF whose pages carry exactly pages_lines."""
    import fitz
    doc = fitz.open()
    for lines in pages_lines:
        page = doc.new_page()
        y = 72
        for ln in lines:
            page.insert_text((72, y), ln)
            y += 14
    return doc.tobytes()


def make_real_vault(tmp_path, pages_lines=None):
    root = tmp_path / "vault"
    data = make_pdf_bytes(pages_lines or VAULT_PAGES)
    sha = hashlib.sha256(data).hexdigest()
    blob = root / "blobs" / sha[0:2] / sha[2:4] / f"{sha}.pdf"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(data)
    return str(root), sha


def make_rows(sha):
    rows = json.loads(json.dumps(SAMPLE_ROWS).replace(ST1S15_SHA, sha))
    return rows


SAMPLE_ROWS = [
    {"field": "vin_min_v", "method": "deterministic_power_axes_v2_v1",
     "page_1based": 2, "part_number": ST1S15_SHA, "pdf_sha": "",
     "schema": "harness.electronics-power-axes-v2.v1",
     "source_url": "https://www.st.com/resource/en/datasheet/st1s15.pdf",
     "table_kind": "characteristics", "unit": "V", "unit_as_printed": "V",
     "value": 2.3, "value_role": "range", "verbatim": VIN_VERBATIM,
     "verdict": "printed_row"},
    {"field": "vin_max_v", "method": "deterministic_power_axes_v2_v1",
     "page_1based": 2, "part_number": ST1S15_SHA, "pdf_sha": "",
     "schema": "harness.electronics-power-axes-v2.v1",
     "source_url": "https://www.st.com/resource/en/datasheet/st1s15.pdf",
     "table_kind": "characteristics", "unit": "V", "unit_as_printed": "V",
     "value": 5.5, "value_role": "range", "verbatim": VIN_VERBATIM,
     "verdict": "printed_row"},
    {"condition_verbatim": None, "field": "vout",
     "method": "deterministic_power_axes_v2_v1", "page_1based": 3,
     "part_number": ST1S15_SHA, "pdf_sha": "",
     "schema": "harness.electronics-power-axes-v2.v1",
     "source_url": "https://www.st.com/resource/en/datasheet/st1s15.pdf",
     "table_kind": "output_voltage", "unit": "V", "unit_as_printed": "V",
     "value": 9.0, "value_role": "fixed",
     "verbatim": "VOUT | Output voltage | 9 | V",
     "verdict": "printed_row"},
]

# Non-entailing row (NEO review, merge 47cac640): claim value absent
# from the quoted row — the canonical negative for the entailment gate.
NON_ENTAILING_ROW = {
    "condition_verbatim": None, "field": "vout",
    "method": "deterministic_power_axes_v2_v1", "page_1based": 3,
    "part_number": ST1S15_SHA, "pdf_sha": "",
    "schema": "harness.electronics-power-axes-v2.v1",
    "source_url": "https://www.st.com/resource/en/datasheet/st1s15.pdf",
    "table_kind": "characteristics", "unit": "V", "value_role": "fixed",
    "verbatim": VIN_VERBATIM,
    "verdict": "printed_row", "vout_max_v": 9.0, "vout_min_v": 9.0,
}


@pytest.fixture()
def vault(tmp_path):
    """Real-PDF vault (round 5): the admission gate re-reads these bytes."""
    return make_real_vault(tmp_path)


@pytest.fixture()
def fake_vault(tmp_path):
    """Byte blob only (hash-check fixture); source re-resolution cannot
    open it, so anything needing admission HELDs — used by schema-negative
    tests that reject BEFORE the gate."""
    root = tmp_path / "fakevault"
    data = b"%PDF-1.7 ST1S15 fixture"
    sha = hashlib.sha256(data).hexdigest()
    blob = root / "blobs" / sha[0:2] / sha[2:4] / f"{sha}.pdf"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(data)
    return str(root), sha


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path / "norm.db"))


def structured(raw, sha, quoted_text, column_key, column_role, header,
               column_unit="V", page=2, table=0, row_idx=1, column=2,
               applicability=None, conditions=None, verified_by="quote_pass"):
    """Attach the structured receipt a table-aware extractor emits,
    including the source-quoted column header."""
    row = dict(raw)
    row["part_number"] = sha
    row["pdf_sha"] = ""
    row["page_1based"] = page
    row["evidence"] = [{
        "kind": "table_cell", "document_sha256": sha,
        "source_uri": f"file://vault/{sha}.pdf",
        "page_1based": page, "table_index": table, "row_index": row_idx,
        "column_index": column, "quoted_text": quoted_text,
        "table_headers_on_page": [header],
    }]
    row["field_binding"] = {"table": table, "column_key": column_key,
                            "column_role": column_role,
                            "column_unit": column_unit,
                            "header_verbatim": header}
    row["applicability"] = applicability if applicability is not None else {
        "structure": "device_summary", "structure_page": 1,
        "cell_text": "ST1S15"}
    row["conditions"] = conditions if conditions is not None else [
        dict(c) for c in DEFAULT_CONDITIONS]
    if verified_by:
        row["verdict"] = {"quote_pass": "printed_row",
                          "applicability_rule": "applicability_rule",
                          "vault_sha_recompute": "vault_sha_recompute"}[
                              verified_by]
    return row


def sample_structured_rows(sha):
    return [
        structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin", "min",
                   VIN_HEADER, page=2),
        structured(make_rows(sha)[1], sha, "2.3 | 5.5 | V", "vin", "max",
                   VIN_HEADER, page=2),
        structured(make_rows(sha)[2], sha, "9 | V", "vout", "value",
                   "VOUT Min Typ Max Unit", page=3, row_idx=1),
    ]


def test_vault_reverification(vault):
    root, sha = vault
    ok, detail = verify_sha256(root, sha)
    assert ok, detail
    ok, detail = verify_sha256(root, "f" * 64)
    assert not ok


def test_st1s15_structured_sample_loads(store, vault):
    """Positive control: ST1S15 / TA25C / VIN 2.3-5.5V prints in the PDF
    and the receipt resolves against the current bytes -> loads."""
    root, sha = vault
    report = load_rows(store, sample_structured_rows(sha),
                       vault_root=root, opn_default="ST1S15")
    assert report.holds == [], report.summary()
    assert report.loaded == 3
    claims = store.claims_for_opn("st1s15")
    fields = {c["field"]: c for c in claims}
    assert fields["vin_min_v"]["value"] == 2.3
    assert fields["vout"]["value"] == 9.0
    assert fields["vin_min_v"]["page"] == 2


def test_unstructured_sample_rows_held(store, vault):
    """CONDITIONAL control: the same OPN without a structured receipt is
    HELD — never force-served to satisfy a serve requirement."""
    root, sha = vault
    report = load_rows(store, make_rows(sha), vault_root=root,
                       opn_default="ST1S15")
    assert report.loaded == 0
    assert len(report.holds) == 3
    assert {h["reason_code"] for h in report.holds} == \
        {"no_structured_observation"}
    assert store.claims_for_opn("st1s15") == []


def test_dry_run_writes_nothing(store, vault):
    root, sha = vault
    report = load_rows(store, sample_structured_rows(sha), vault_root=root,
                       dry_run=True, opn_default="ST1S15")
    assert report.loaded == 3 and report.holds == []
    assert store.claims_for_opn("st1s15") == []


def test_serve_endpoint(store, vault):
    root, sha = vault
    load_rows(store, sample_structured_rows(sha), vault_root=root,
              opn_default="ST1S15")
    import io
    from http.server import ThreadingHTTPServer
    import threading
    import urllib.request

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/v1/opn/st1s15") as r:
            payload = json.loads(r.read())
        assert payload["opn"] == "ST1S15"
        assert payload["count"] == 3
        c = payload["claims"][0]
        for k in ("doc_sha", "page", "verbatim", "verified_by",
                  "rule_version"):
            assert c[k], f"provenance field {k} missing from serve payload"
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/v1/opn/NOPE") as r:
                assert r.status == 404
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()


# --- negative tests (mandatory, issue #64 + merge-47cac640 review) ---
# These reject at the SCHEMA layer, before the gate, so the byte-only
# vault fixture suffices.

def test_persist_without_vault_root_raises(store):
    with pytest.raises(ValueError, match="vault_root is mandatory"):
        load_rows(store, make_rows("a" * 64), opn_default="ST1S15")
    assert store.claims_for_opn("st1s15") == []


def test_non_entailing_verbatim_rejected(store, fake_vault):
    root, sha = fake_vault
    rows = json.loads(json.dumps([NON_ENTAILING_ROW]).replace(ST1S15_SHA, sha))
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "entail" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_reject_missing_verbatim(store, fake_vault):
    root, sha = fake_vault
    rows = json.loads(json.dumps(
        [dict(SAMPLE_ROWS[0], verbatim="")]).replace(ST1S15_SHA, sha))
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert len(report.rejections) == 1
    assert "verbatim" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_reject_missing_page(store, fake_vault):
    root, sha = fake_vault
    rows = json.loads(json.dumps([SAMPLE_ROWS[0]]).replace(ST1S15_SHA, sha))
    del rows[0]["page_1based"]
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1


def test_reject_bad_sha(store, fake_vault):
    root, sha = fake_vault
    rows = [dict(SAMPLE_ROWS[0], part_number="deadbeef")]
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "64-hex" in report.rejections[0]["reason"]


def test_reject_vault_hash_mismatch(store, fake_vault):
    root, real_sha = fake_vault
    sha = "a" * 64
    rows = make_rows(real_sha)
    for r in rows:
        r["part_number"] = sha
    blob = Path(root) / "blobs" / "aa" / "aa" / f"{sha}.pdf"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"different bytes")
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 3
    assert all("mismatch" in r["reason"] for r in report.rejections)


def test_reject_missing_rule_version_and_verified_by(store, fake_vault):
    root, sha = fake_vault
    rows = json.loads(json.dumps([SAMPLE_ROWS[0]]).replace(ST1S15_SHA, sha))
    del rows[0]["schema"]
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert "rule_version" in report.rejections[0]["reason"]
    rows = json.loads(json.dumps([SAMPLE_ROWS[0]]).replace(ST1S15_SHA, sha))
    del rows[0]["verdict"]
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert "verified_by" in report.rejections[0]["reason"]


def test_extractor_method_recorded_separately(store, vault):
    root, sha = vault
    report = load_rows(store, sample_structured_rows(sha), vault_root=root,
                       opn_default="ST1S15")
    assert report.loaded == 3
    claims = store.claims_for_opn("st1s15")
    for c in claims:
        assert c["verified_by"] == "quote_pass"
        assert c["extractor_method"] == "deterministic_power_axes_v2_v1"


# --- store safeguards (PR66 restoration; regression retention) ---

def test_replay_loads_zero_new_rows(store, vault):
    """Replay of the identical load adds ZERO new rows; duplicates are
    reported. Includes NULL condition_verbatim keys (null-safe)."""
    root, sha = vault
    rows = sample_structured_rows(sha)
    for r in rows:
        r["condition_verbatim"] = None
    first = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert first.loaded == 3 and first.duplicates == 0
    replay = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert replay.loaded == 0, "replay must not create new rows"
    assert replay.duplicates == 3
    assert replay.rejections == []
    assert len(store.claims_for_opn("st1s15")) == 3


def test_unique_key_null_safe(store, vault):
    """NULL condition_verbatim must dedupe (null-safe COALESCE UNIQUE
    index), not double-load; NULL value_role/condition dedupe is
    additionally checked at the store layer (the gate binds roles)."""
    root, sha = vault
    rows = [structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin",
                       "min", VIN_HEADER, page=2)]
    rows[0].pop("condition_verbatim", None)
    first = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert first.loaded == 1, first.summary()
    replay = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert replay.loaded == 0 and replay.duplicates == 1, \
        "NULL condition_verbatim must dedupe (null-safe UNIQUE)"
    assert len(store.claims_for_opn("st1s15")) == 1
    # store layer: NULL value_role AND condition_verbatim dedupe
    row = json.loads(json.dumps(store.claims_for_opn("st1s15")[0]))
    row["value_role"] = None
    row["condition_verbatim"] = None
    assert store.insert(row) == "loaded"
    assert store.insert(row) == "duplicate"
    claims = store.claims_for_opn("st1s15")
    assert len(claims) == 2
    assert sum(1 for c in claims if c["value_role"] is None) == 1


def test_distinct_page_observations_both_retained(store, vault):
    """2.3@page2 and 4.7@page3 share the pre-v2 key; page is in the v2
    key: both are retained, neither silently deduped away."""
    root, sha = vault
    r1 = structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin", "min",
                    VIN_HEADER, page=2)
    r2 = structured(dict(make_rows(sha)[0], value=4.7,
                         verbatim="VIN | Operating input voltage range | "
                                  "4.7 | 5.5 | V"),
                    sha, "4.7 | 5.5 | V", "vin", "min", VIN_HEADER, page=3)
    report = load_rows(store, [r1, r2], vault_root=root,
                       opn_default="ST1S15")
    assert report.loaded == 2, report.summary()
    assert report.duplicates == 0 and report.rejections == []
    claims = [c for c in store.claims_for_opn("st1s15")
              if c["field"] == "vin_min_v"]
    assert sorted(c["page"] for c in claims) == [2, 3]
    assert sorted(c["value"] for c in claims) == [2.3, 4.7]


def test_conflicting_same_key_observation_refused_loudly(store, vault):
    """Same key (opn+sha+field+page+role+condition), different value:
    refused loudly as a conflict — never silently ignored, never
    mislabeled as a duplicate (2.3 -> 5.5-class defect)."""
    root, sha = vault
    r1 = structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin", "min",
                    VIN_HEADER, page=2)
    r2 = structured(dict(make_rows(sha)[0], value=4.7,
                         verbatim="VIN | Operating input voltage range | "
                                  "4.7 | 5.5 | V"),
                    sha, "4.7 | 5.5 | V", "vin", "min", VIN_HEADER, page=2,
                    row_idx=3)
    load_rows(store, [r1], vault_root=root, opn_default="ST1S15")
    report = load_rows(store, [r2], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and report.duplicates == 0
    assert len(report.rejections) == 1
    assert "conflicting observation" in report.rejections[0]["reason"]
    claims = [c for c in store.claims_for_opn("st1s15")
              if c["field"] == "vin_min_v"]
    assert len(claims) == 1 and claims[0]["value"] == 2.3


def _make_v1_store(path):
    """A PR65-era store: v1 table WITHOUT extractor_method, inline
    UNIQUE, schema_version norm-serve.v1."""
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS norm_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        opn TEXT NOT NULL, doc_sha TEXT NOT NULL, field TEXT NOT NULL,
        value REAL, value_min REAL, value_max REAL, unit TEXT,
        unit_as_printed TEXT, value_role TEXT, condition_verbatim TEXT,
        verdict TEXT, table_kind TEXT, page INTEGER NOT NULL,
        verbatim TEXT NOT NULL, span TEXT, verified_by TEXT NOT NULL,
        rule_version TEXT NOT NULL, evidence_ref TEXT, source_url TEXT,
        schema TEXT, schema_version TEXT NOT NULL,
        loaded_at REAL NOT NULL,
        UNIQUE (opn, doc_sha, field, value_role, condition_verbatim)
    );""")
    con.commit()
    con.close()


def test_v1_store_refused_fail_closed_empty(tmp_path):
    db = str(tmp_path / "v1-empty.db")
    _make_v1_store(db)
    with pytest.raises(Rejection, match="predates norm-serve.v2"):
        Store(db)


def test_v1_store_refused_fail_closed_populated(tmp_path):
    """Populated PR65 store: same refusal — and the populated rows are
    never touched (no auto-migration)."""
    db = str(tmp_path / "v1-populated.db")
    _make_v1_store(db)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO norm_claims (opn, doc_sha, field, value, unit,"
        " value_role, condition_verbatim, verdict, table_kind, page,"
        " verbatim, verified_by, rule_version, schema_version, loaded_at)"
        " VALUES ('ST1S15', ?, 'vout', 9.0, 'V', 'fixed', NULL,"
        " 'printed_row', 't', 5, 'VOUT | 9 | V', 'quote_pass', 'rv',"
        " 'norm-serve.v1', 0.0)", ("b" * 64,))
    con.commit()
    before = con.execute("SELECT COUNT(*) FROM norm_claims").fetchone()[0]
    con.close()
    with pytest.raises(Rejection, match="predates norm-serve.v2"):
        Store(db)
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM norm_claims").fetchone()[0] \
        == before
    con.close()


def test_duplicate_existing_rows_refused_fail_closed(tmp_path):
    """Creating uq_norm_claims_key over existing duplicates is refused
    fail-closed, naming the required operator action."""
    db = str(tmp_path / "dups.db")
    store = Store(db)
    store.con.execute("DROP INDEX IF EXISTS uq_norm_claims_key")
    store.con.commit()
    store.con.close()
    con = sqlite3.connect(db)
    base = ("ST1S15", "a" * 64, "vout", 9.0, None, None, "V", "V", "fixed",
            None, "printed_row", "output_voltage", 5, "VOUT | Out | 9 | V",
            None, "quote_pass", "rv", "m")
    base = base + (SCHEMA_VERSION, 0.0)
    for _ in range(2):
        con.execute(
            "INSERT INTO norm_claims (opn, doc_sha, field, value, value_min,"
            " value_max, unit, unit_as_printed, value_role,"
            " condition_verbatim, verdict, table_kind, page, verbatim,"
            " span, verified_by, rule_version, extractor_method,"
            " schema_version, loaded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", base)
    con.commit()
    con.close()
    with pytest.raises(Rejection, match="duplicate key group"):
        Store(db)


def _make_empty_old_index_store(path):
    """A PR66-era store: v2 columns but the OLD no-page unique index —
    empty, so every row-level check passes."""
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS norm_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        opn TEXT NOT NULL, doc_sha TEXT NOT NULL, field TEXT NOT NULL,
        value REAL, value_min REAL, value_max REAL, unit TEXT,
        unit_as_printed TEXT, value_role TEXT, condition_verbatim TEXT,
        verdict TEXT, table_kind TEXT, page INTEGER NOT NULL,
        verbatim TEXT NOT NULL, span TEXT, verified_by TEXT NOT NULL,
        rule_version TEXT NOT NULL, extractor_method TEXT,
        evidence_ref TEXT, source_url TEXT,
        schema TEXT, schema_version TEXT NOT NULL,
        loaded_at REAL NOT NULL
    );""")
    con.execute(
        "CREATE UNIQUE INDEX uq_norm_claims_key ON norm_claims("
        "opn, doc_sha, field, value_role, condition_verbatim)")
    con.commit()
    con.close()


def test_empty_old_index_store_refused_fail_closed(tmp_path):
    """Empty old-index store: the no-page unique key would corrupt the
    first second-page insert. Preflight refuses BEFORE any load."""
    db = str(tmp_path / "old-index.db")
    _make_empty_old_index_store(db)
    with pytest.raises(Rejection, match="refusing to open"):
        Store(db)


def test_populated_old_index_store_refused_fail_closed(tmp_path):
    """Populated old-index store: same refusal, rows untouched."""
    db = str(tmp_path / "old-index-populated.db")
    _make_empty_old_index_store(db)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO norm_claims (opn, doc_sha, field, value, unit,"
        " value_role, condition_verbatim, verdict, table_kind, page,"
        " verbatim, verified_by, rule_version, extractor_method,"
        " schema_version, loaded_at)"
        " VALUES ('ST1S15', ?, 'vout', 9.0, 'V', 'fixed', NULL,"
        " 'printed_row', 't', 5, 'VOUT | 9 | V', 'quote_pass', 'rv', 'm',"
        " ?, 0.0)", ("c" * 64, SCHEMA_VERSION))
    con.commit()
    con.close()
    with pytest.raises(Rejection, match="refusing to open"):
        Store(db)


def test_rejections_never_swallowed(store, vault):
    root, sha = vault
    rows = [dict(SAMPLE_ROWS[0], verbatim="")] + sample_structured_rows(sha)[1:]
    rows[1] = json.loads(json.dumps(rows[1]).replace(ST1S15_SHA, sha))
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 2
    assert len(report.rejections) == 1
    assert report.rejections[0]["index"] == 0
    assert report.summary()["rejected"] == 1


# --- round-2/3 semantic negatives (schema layer; byte-only vault) ---

def _vout_row(sha, verbatim, value_kwargs=None):
    row = json.loads(json.dumps(SAMPLE_ROWS[2]).replace(ST1S15_SHA, sha))
    row["verbatim"] = verbatim
    row.update(value_kwargs or {})
    return row


def test_original_available_on_quote_rejected(store, fake_vault):
    """The ORIGINAL failing quote: 'available on | 9' accepted vout 9.0
    under digit-boundary matching. Field binding rejects it: the row
    never labels vout."""
    root, sha = fake_vault
    row = _vout_row(
        sha,
        "Different output voltage versions of the ST1S15 available on | 9",
        {"vout_max_v": 9.0, "vout_min_v": 9.0})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "entail" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_near_miss_numbers_rejected(store, fake_vault):
    """Decimal / signed near-misses: 9 inside 0.9 / 9.5 / -9 is token
    presence, not entailment."""
    root, sha = fake_vault
    for printed in ("0.9", "9.5", "-9"):
        row = _vout_row(sha, f"VOUT | Output voltage | {printed} | V",
                        {"vout_max_v": 9.0, "vout_min_v": 9.0})
        report = load_rows(store, [row], vault_root=root,
                           opn_default="ST1S15")
        assert report.loaded == 0, printed
        assert "entail" in report.rejections[0]["reason"], printed
    assert store.claims_for_opn("st1s15") == []


def test_wrong_field_binding_rejected(store, fake_vault):
    """9 printed after ANOTHER field's label does not entail vout."""
    root, sha = fake_vault
    row = _vout_row(sha, "IQ | Quiescent current | 9 | mA",
                    {"vout_max_v": 9.0, "vout_min_v": 9.0})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "entail" in report.rejections[0]["reason"]


def test_verified_by_vocabulary_enforced(store, fake_vault):
    """verified_by is a receipt from an evidence-backed vocabulary; an
    arbitrary raw verdict ('REJECTED') must be refused, never loaded."""
    root, sha = fake_vault
    row = json.loads(json.dumps(SAMPLE_ROWS[0]).replace(ST1S15_SHA, sha))
    row["verdict"] = "REJECTED"
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "vocabulary" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_unit_not_bound_rejected(store, fake_vault):
    """'9mV' printed but 9 V claimed: the printed unit does not bind."""
    root, sha = fake_vault
    row = _vout_row(sha, "VOUT | Output voltage | 9mV",
                    {"vout_max_v": 9.0, "vout_min_v": 9.0})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "unit" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_cross_field_current_voltage_rejected(store, fake_vault):
    """VOUT row then IQ 9 mA row: the CURRENT value must not be served
    as the VOLTAGE field."""
    root, sha = fake_vault
    row = _vout_row(
        sha, "VOUT | Output voltage | 1.2 | V\nIQ | Quiescent current | 9 | mA",
        {"vout_max_v": 9.0, "vout_min_v": 9.0})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "entail" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_page_reference_number_rejected(store, fake_vault):
    """'VOUT | See page | 9': the 9 is a page number, not a value."""
    root, sha = fake_vault
    row = _vout_row(sha, "VOUT | See page | 9",
                    {"vout_max_v": 9.0, "vout_min_v": 9.0})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "page reference" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_min_taken_from_range_upper_bound_rejected(store, vault):
    """VIN min 5.5 against a printed 2.3 to 5.5 range: min must bind to
    the LOWER endpoint; the correct endpoint loads (source-backed)."""
    root, sha = vault
    row = structured(
        _vout_row(sha, "VIN | Operating input voltage range | 2.3 to 5.5 | V",
                  {"field": "vin_min_v", "value": 5.5, "unit": "V",
                   "value_role": "range"}),
        sha, "2.3 to 5.5 | V", "vin", "min", VIN_HEADER, page=2,
        row_idx=2)
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "lower bound" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []
    row_ok = structured(
        _vout_row(sha, "VIN | Operating input voltage range | 2.3 to 5.5 | V",
                  {"field": "vin_min_v", "value": 2.3, "unit": "V",
                   "value_role": "range"}),
        sha, "2.3 to 5.5 | V", "vin", "min", VIN_HEADER, page=2,
        row_idx=2)
    report = load_rows(store, [row_ok], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 1, report.summary()


def test_typ_mislabeled_max_rejected(store, vault):
    """'typ 9' printed but max claimed: refused; claiming the printed
    role with a source-resolvable receipt loads."""
    root, sha = vault
    row = structured(
        _vout_row(sha, "VOUT | Output voltage | typ | 9 | V",
                  {"vout_max_v": 9.0, "vout_min_v": 9.0,
                   "value_role": "max"}),
        sha, "typ | 9 | V", "vout", "max", "VOUT Min Typ Max Unit", page=3,
        row_idx=2)
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "role" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []
    row_ok = structured(
        _vout_row(sha, "VOUT | Output voltage | typ | 9 | V",
                  {"value": 9.0, "value_role": "typ"}),
        sha, "typ | 9 | V", "vout", "typ", "VOUT Min Typ Max Unit", page=3,
        row_idx=2)
    report = load_rows(store, [row_ok], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 1, report.summary()


def test_unrelated_opn_rejected_and_structured_opn_loads(store, vault):
    """Unrelated OPN: refused at the schema layer (not evidenced in the
    row); the printed OPN with a source-resolvable applicability loads."""
    root, sha = vault
    row = json.loads(json.dumps(SAMPLE_ROWS[2]).replace(ST1S15_SHA, sha))
    report = load_rows(store, [row], vault_root=root, opn_default="LM9999X")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "applicability" in report.rejections[0]["reason"]
    assert store.claims_for_opn("LM9999X") == []
    row_ok = structured(make_rows(sha)[2], sha, "9 | V", "vout", "value",
                        "VOUT Min Typ Max Unit", page=3)
    report = load_rows(store, [row_ok], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 1, report.summary()


def test_applicability_rule_requires_receipt(store, vault):
    """Raw applicability_rule verdict WITHOUT a rule receipt is refused
    at the schema layer; WITH evidence_ref + span naming the OPN plus a
    ratified rule at the claimed version it loads."""
    root, sha = vault
    row = json.loads(json.dumps(SAMPLE_ROWS[2]).replace(ST1S15_SHA, sha))
    row["verdict"] = "applicability_rule"
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "self-assertion" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


# --- round 5: source-coordinate resolution (f3df0c2d repairs) ---

def test_table25_local_over_inherited_conditions(store, vault):
    """Canonical Table 25 case (sha 80848a22 pattern): typ 22.0 carries
    VDD 3.6 V as the LOCAL table override + TA 25 C INHERITED; max 23.8
    carries TA 105 C from its column. Local beats the section default —
    and the section-default VDD 3.3 V is never inherited over 3.6 V."""
    root, sha = vault
    typ_row = structured(
        dict(SAMPLE_ROWS[0], field="iq", value=22.0, unit="mA",
             unit_as_printed="mA", value_role="typ",
             verbatim="IQ | Supply current | typ | 22.0 | 23.8 | mA"),
        sha, "typ | 22.0 | 23.8 | mA", "iq", "typ", "IQ Min Typ Max Unit",
        page=2, conditions=[dict(CONDITION_LOCAL_VDD),
                            dict(COND_TA25_INHERITED)])
    max_row = structured(
        dict(SAMPLE_ROWS[0], field="iq", value=23.8, unit="mA",
             unit_as_printed="mA", value_role="max",
             verbatim="IQ | Supply current | typ | 22.0 | 23.8 | mA"),
        sha, "typ | 22.0 | 23.8 | mA", "iq", "max", "IQ Min Typ Max Unit",
        page=2, conditions=[dict(CONDITION_LOCAL_VDD),
                            dict(COND_TA105_COLUMN)])
    report = load_rows(store, [typ_row, max_row], vault_root=root,
                       opn_default="ST1S15")
    assert report.loaded == 2 and report.holds == [], report.summary()
    claims = sorted(store.claims_for_opn("st1s15"),
                    key=lambda c: c["value"])
    roles = {c["value"]: c["value_role"] for c in claims}
    assert roles == {22.0: "typ", 23.8: "max"}


def test_invented_quoted_cell_held(store, vault):
    """f3df bypass 5: an INVENTED quoted_text '999V' — the PDF prints
    only 2.3/5.5, so the cited cell does not resolve against the
    current bytes -> HELD cell_not_in_source, never loaded."""
    root, sha = vault
    row = structured(make_rows(sha)[0], sha, "999V", "vin", "min",
                     VIN_HEADER, page=2)
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.holds) == 1
    assert report.holds[0]["reason_code"] == "cell_not_in_source"
    assert store.claims_for_opn("st1s15") == []


def test_value_not_printed_in_cited_cell_held(store, vault):
    """Cell text is real and printed in the source, but does not
    contain the claimed value (5.5 claimed against the 2.3 cell)."""
    root, sha = vault
    row = structured(make_rows(sha)[1], sha, "2.3", "vin", "max",
                     VIN_HEADER, page=2, column=1)
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.holds) == 1
    assert report.holds[0]["reason_code"] == "value_not_in_cell"


def test_page_999_out_of_range_held(store, vault):
    """f3df bypass 6: cited page 999 beyond the document's real page
    count -> HELD, never resolved from self-declared metadata."""
    root, sha = vault
    row = structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin", "min",
                     VIN_HEADER, page=999)
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.holds) == 1
    assert report.holds[0]["reason_code"] == "no_structured_observation"
    assert "out of range" in report.holds[0]["detail"]


def test_negative_coordinates_held(store, vault):
    """f3df bypass 7: negative table/row/column coordinates accepted
    solely from self-declared structure -> now HELD."""
    root, sha = vault
    row = structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin", "min",
                     VIN_HEADER, page=2, row_idx=-1)
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.holds) == 1
    assert report.holds[0]["reason_code"] == "no_structured_observation"


def test_unrelated_opn_applicability_page_held(store, vault):
    """f3df bypass 6 (cont.): an applicability cell naming the OPN on a
    page where it is NOT printed -> HELD applicability_not_in_source."""
    root, sha = vault
    row = structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin", "min",
                     VIN_HEADER, page=2,
                     applicability={"structure": "device_summary",
                                    "structure_page": 3,
                                    "cell_text": "ST1S15"})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.holds) == 1
    assert report.holds[0]["reason_code"] == "applicability_not_in_source"


def test_opn_not_printed_cell_value_held(store, vault):
    """Applicability cell text that is printed in the source but is NOT
    the OPN itself -> HELD opn_unbound (prose/condition mentions are
    not applicability)."""
    root, sha = vault
    row = structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin", "min",
                     VIN_HEADER, page=2,
                     applicability={
                         "structure": "device_summary",
                         "structure_page": 1,
                         "cell_text": "TA = 25 C (section 6.1.2 default, "
                                      "inherited)"})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.holds) == 1
    assert report.holds[0]["reason_code"] == "opn_unbound"


def test_invented_condition_held(store, vault):
    """Invented condition (TA 999) whose verbatim is not printed on its
    source page -> HELD condition_not_in_source."""
    root, sha = vault
    row = structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin", "min",
                     VIN_HEADER, page=2,
                     conditions=[{"verbatim": "TA = 999 C (invented)",
                                  "precedence": "inherited",
                                  "source_page": 1}])
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.holds) == 1
    assert report.holds[0]["reason_code"] == "condition_not_in_source"
    assert store.claims_for_opn("st1s15") == []


def test_condition_source_page_out_of_range_held(store, vault):
    root, sha = vault
    row = structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin", "min",
                     VIN_HEADER, page=2,
                     conditions=[dict(CONDITION_LOCAL_VDD,
                                      source_page=999)])
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.holds) == 1
    assert report.holds[0]["reason_code"] == "condition_not_in_source"


def test_header_not_in_source_held(store, vault):
    """A column header that is not printed on the cited page cannot
    bind the field -> HELD header_not_in_source."""
    root, sha = vault
    row = structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin", "min",
                     "VIN Value Min Unit", page=2)
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.holds) == 1
    assert report.holds[0]["reason_code"] == "header_not_in_source"


def test_role_not_in_header_held(store, vault):
    """Min/Typ/Max header mislabel: column_role 'min' but the printed
    header carries no role word -> HELD role_not_in_header."""
    root, sha = vault
    row = structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin", "min",
                     "Operating input voltage range", page=2)
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.holds) == 1
    assert report.holds[0]["reason_code"] == "role_not_in_header"


def test_column_unit_cannot_override_printed_unit(store, vault):
    """f3df appendix unit-conflict counterexample: the PDF prints
    '9mV' but the extractor declares column_unit V and claims 9 V.
    The schema layer refuses (printed mV != claimed V); at the GATE,
    the printed-first rule holds the row (unit_unbound) even when the
    declared column_unit matches the claim. Claiming the printed mV
    loads."""
    root, sha = vault
    row = structured(
        dict(SAMPLE_ROWS[2], verbatim="VOUT | Output voltage | 9mV",
             unit="V", unit_as_printed="mV", value=9.0),
        sha, "VOUT | Output voltage | 9mV", "vout", "value",
        "VOUT Min Typ Max Unit", page=3, column_unit="V", row_idx=3)
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert len(report.rejections) == 1
    assert "unit" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []
    # gate level: the printed unit wins even over a matching column_unit
    from harness.normserve import admission as admission_mod
    typed = {"opn": "ST1S15", "doc_sha": sha, "field": "vout", "value": 9.0,
             "value_min": None, "value_max": None, "unit": "V",
             "value_role": "fixed", "page": 3, "verbatim": "x",
             "verified_by": "quote_pass", "rule_version": "rv"}
    raw = {"evidence": [{"kind": "table_cell", "document_sha256": sha,
                         "page_1based": 3, "table_index": 0,
                         "row_index": 3, "column_index": 2,
                         "quoted_text": "9mV"}],
           "field_binding": {"table": 0, "column_key": "vout",
                             "column_role": "value", "column_unit": "V",
                             "header_verbatim": "VOUT Min Typ Max Unit"},
           "applicability": {"structure": "device_summary",
                             "structure_page": 1, "cell_text": "ST1S15"},
           "conditions": [dict(c) for c in DEFAULT_CONDITIONS]}
    decision = admission_mod.evaluate(typed, raw, vault_root=root)
    assert not decision.served
    assert decision.reason_code == "unit_unbound"
    row_ok = structured(
        dict(SAMPLE_ROWS[2], verbatim="VOUT | Output voltage | 9mV",
             unit="mV", unit_as_printed="mV", value=9.0),
        sha, "VOUT | Output voltage | 9mV", "vout", "value",
        "VOUT Min Typ Max Unit", page=3, column_unit="V", row_idx=3)
    report = load_rows(store, [row_ok], vault_root=root,
                       opn_default="ST1S15")
    assert report.loaded == 1 and report.holds == [], report.summary()


def test_vault_sha_recompute_gets_no_metadata_bypass(store, vault):
    """f3df bypass 7: vault_sha_recompute accepted invented coordinates
    solely from self-declared structure. Round 5: source re-resolution
    runs for EVERY receipt — a vault_sha_recompute row citing a cell
    that is not printed is HELD."""
    root, sha = vault
    row = structured(make_rows(sha)[0], sha, "invented 999 cell", "vin",
                     "min", VIN_HEADER, page=2,
                     verified_by="vault_sha_recompute")
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.holds) == 1
    assert report.holds[0]["reason_code"] == "cell_not_in_source"


def test_rule_version_alone_is_not_applicability_proof(store, vault):
    """A caller-supplied version-only registry entry does not ratify a
    rule -> HELD rule_unresolved."""
    root, sha = vault
    raw = make_rows(sha)[2]
    raw["verdict"] = "applicability_rule"
    raw["evidence_ref"] = "applicability:ST1S15@p1"
    raw["span"] = "ST1S15: device applicable to 2A buck"
    row = structured(raw, sha, "9 | V", "vout", "value",
                     "VOUT Min Typ Max Unit", page=3,
                     verified_by="applicability_rule",
                     applicability={"structure": "device_summary",
                                    "structure_page": 1,
                                    "cell_text": "ST1S15",
                                    "rule_id": "rule-42"})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.holds) == 1
    assert report.holds[0]["reason_code"] == "rule_unresolved"
    # ratified at the claimed version -> loads
    report = load_rows(store, [json.loads(json.dumps(row))], vault_root=root,
                       opn_default="ST1S15",
                       ratified_rules={"rule-42": {
                           "rule_version": raw["schema"]}})
    assert report.loaded == 1 and report.holds == [], report.summary()


def test_missing_verbatim_span_in_source_held(store, vault):
    """A verbatim that is not printed on the CITED page cannot be
    resolved (here: the VIN span exists only on page 2, cited 3)
    -> HELD span_not_in_source."""
    root, sha = vault
    row = structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin", "min",
                     VIN_HEADER, page=3)
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.holds) == 1
    assert report.holds[0]["reason_code"] == "span_not_in_source"


# --- round 6 (hw-x9g, NEO round-5 review at PR75 head 3718c73c): the
# --- four remaining failures, each resolved against the source bytes ---

def test_invented_table_row_column_indices_held(store, vault):
    """Failure 1: invented table_index/row_index/column_index 999 no
    longer admits — structure coordinates are source-RESOLVED:
    table_index must fall inside the printed table_headers_on_page
    anchors, row_index must equal the quoted row's line distance from
    the cited header, and column_index must reach the claimed value in
    the row's printed cells."""
    root, sha = vault
    for kwargs, coord in [({"table": 999}, "table_index"),
                          ({"row_idx": 999}, "row_index"),
                          ({"column": 999}, "column_index")]:
        row = structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin",
                         "min", VIN_HEADER, page=2, **kwargs)
        report = load_rows(store, [row], vault_root=root,
                           opn_default="ST1S15")
        assert report.loaded == 0, (coord, report.summary())
        assert len(report.holds) == 1, (coord, report.summary())
        assert report.holds[0]["reason_code"] == "structure_not_in_source", \
            (coord, report.holds[0])
    assert store.claims_for_opn("st1s15") == []


def test_truthful_coordinates_still_load(store, vault):
    """Round-6 positive control: the same rows with truthful coordinates
    (table 0, row 1, column 2) still load — resolution is strict but
    does not over-hold the valid receipt."""
    root, sha = vault
    report = load_rows(store, sample_structured_rows(sha), vault_root=root,
                       opn_default="ST1S15")
    assert report.holds == [] and report.loaded == 3, report.summary()


def test_header_printed_unit_wins_over_column_unit(store, tmp_path):
    """Failure 2: printed header 'Unit mV' + declared column_unit V must
    NOT admit 9 as 9V — when the cell prints no unit of its own, the
    printed header is the unit evidence; a conflicting declared
    column_unit is ignored (printed wins), so claiming V is HELD and
    claiming the printed mV loads."""
    pages = [
        ["ST1S15",
         "VDD = 3.6 V (local table condition)",
         "TA = 25 C (section 6.1.2 default, inherited)"],
        ["VOUT Min Typ Max Unit mV",
         "VOUT | Output voltage | 9"],
    ]
    root, sha = make_real_vault(tmp_path, pages)
    store = Store(str(tmp_path / "norm.db"))
    row_v = structured(
        dict(SAMPLE_ROWS[2], unit="V", unit_as_printed=None, value=9.0,
             verbatim="VOUT | Output voltage | 9"),
        sha, "VOUT | Output voltage | 9", "vout", "value",
        "VOUT Min Typ Max Unit mV", page=2, column_unit="V")
    report = load_rows(store, [row_v], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0, report.summary()
    assert len(report.holds) == 1, report.summary()
    assert report.holds[0]["reason_code"] == "unit_unbound"
    assert "header" in report.holds[0]["detail"]
    row_mv = structured(
        dict(SAMPLE_ROWS[2], unit="mV", unit_as_printed="mV", value=9.0,
             verbatim="VOUT | Output voltage | 9"),
        sha, "VOUT | Output voltage | 9", "vout", "value",
        "VOUT Min Typ Max Unit mV", page=2, column_unit="V")
    report = load_rows(store, [row_mv], vault_root=root,
                       opn_default="ST1S15")
    assert report.loaded == 1 and report.holds == [], report.summary()


def test_negated_applicability_held(store, tmp_path):
    """Failure 3: the document explicitly says 'Not applicable to
    ST1S15' — the OPN printed ONLY inside a negated sentence is not
    positive printed evidence -> HELD applicability_negated."""
    pages = [
        ["Not applicable to ST1S15"],
        ["VOUT Min Typ Max Unit",
         "VOUT | Output voltage | 9 | V"],
    ]
    root, sha = make_real_vault(tmp_path, pages)
    store = Store(str(tmp_path / "norm.db"))
    row = structured(make_rows(sha)[2], sha, "9 | V", "vout", "value",
                     "VOUT Min Typ Max Unit", page=2,
                     applicability={"structure": "device_summary",
                                    "structure_page": 1,
                                    "cell_text": "ST1S15"})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0, report.summary()
    assert len(report.holds) == 1, report.summary()
    assert report.holds[0]["reason_code"] == "applicability_negated"
    assert store.claims_for_opn("st1s15") == []


def test_distinct_conditions_both_retained(store, vault):
    """Failure 4: TA25 and TA105 observations that share every other key
    element no longer COLLAPSE as duplicates — the canonical condition
    signature is part of the v3 unique key, so both distinct condition
    bindings are retained and an identical replay adds zero."""
    root, sha = vault
    ta25 = structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin", "min",
                      VIN_HEADER, page=2,
                      conditions=[dict(CONDITION_LOCAL_VDD),
                                  dict(COND_TA25_INHERITED)])
    ta105 = structured(make_rows(sha)[0], sha, "2.3 | 5.5 | V", "vin", "min",
                       VIN_HEADER, page=2,
                       conditions=[dict(CONDITION_LOCAL_VDD),
                                   dict(COND_TA105_COLUMN)])
    report = load_rows(store, [ta25, ta105], vault_root=root,
                       opn_default="ST1S15")
    assert report.loaded == 2, report.summary()
    assert report.duplicates == 0 and report.holds == []
    replay = load_rows(store, [ta25, ta105], vault_root=root,
                       opn_default="ST1S15")
    assert replay.loaded == 0 and replay.duplicates == 2, replay.summary()
    claims = [c for c in store.claims_for_opn("st1s15")
              if c["field"] == "vin_min_v"]
    sigs = {c["conditions_sig"] for c in claims}
    assert len(sigs) == 2 and all(sigs), claims


def test_v2_store_refused_fail_closed(tmp_path):
    """A populated norm-serve.v2 store (no conditions_sig column, no
    conditions in the unique key) is REFUSED by the v3 preflight —
    live data is never auto-migrated, distinct-condition inserts would
    otherwise fail or collapse."""
    db = str(tmp_path / "v2.db")
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE norm_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opn TEXT NOT NULL, doc_sha TEXT NOT NULL, field TEXT NOT NULL,
            value REAL, value_min REAL, value_max REAL, unit TEXT,
            unit_as_printed TEXT, value_role TEXT, condition_verbatim TEXT,
            verdict TEXT, table_kind TEXT, page INTEGER NOT NULL,
            verbatim TEXT NOT NULL, span TEXT, verified_by TEXT NOT NULL,
            rule_version TEXT NOT NULL, extractor_method TEXT,
            evidence_ref TEXT, source_url TEXT, schema TEXT,
            schema_version TEXT NOT NULL, loaded_at REAL NOT NULL);
        CREATE UNIQUE INDEX uq_norm_claims_key ON norm_claims(
            opn, doc_sha, field, page, COALESCE(value_role, ''),
            COALESCE(condition_verbatim, ''));
    """)
    con.execute(
        "INSERT INTO norm_claims (opn, doc_sha, field, page, verbatim, "
        "verified_by, rule_version, schema_version, loaded_at) "
        "VALUES ('ST1S15', ?, 'vout', 3, '9 V', 'quote_pass', 'rv', "
        "'norm-serve.v2', 0.0)", ("c" * 64,))
    con.commit()
    con.close()
    with pytest.raises(Rejection, match="predates norm-serve.v3"):
        Store(db)
