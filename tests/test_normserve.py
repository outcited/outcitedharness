"""NORM-SERVE-01 tests (issue #64 contract).

Positive: ST1S15 proving sample (e2e-st1s15-raw.jsonl) loads, API serves
it with page-level provenance. Negative: missing provenance, failed vault
re-verification, missing rule_version/verified_by — all rejected, never
served.
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from harness.normserve.loader import Rejection, Store, load_rows
from harness.normserve.serve import make_handler
from harness.normserve.vault import verify_sha256

ST1S15_SHA = "82d2ee0cc7dd7c7aa47fed41ab13e9593d37f49d858470b56d50e648a104c222"

def make_rows(sha):
    rows = json.loads(json.dumps(SAMPLE_ROWS).replace(ST1S15_SHA, sha))
    return rows


SAMPLE_ROWS = [
    {"field": "vin_min_v", "method": "deterministic_power_axes_v2_v1",
     "page_1based": 8, "part_number": ST1S15_SHA, "pdf_sha": "",
     "schema": "harness.electronics-power-axes-v2.v1",
     "source_url": "https://www.st.com/resource/en/datasheet/st1s15.pdf",
     "table_kind": "characteristics", "unit": "V", "unit_as_printed": "V",
     "value": 2.3, "value_role": "range",
     "verbatim": "VIN | Operating input voltage range | 2.3 | 5.5 | V",
     "verdict": "printed_row"},
    {"field": "vin_max_v", "method": "deterministic_power_axes_v2_v1",
     "page_1based": 8, "part_number": ST1S15_SHA, "pdf_sha": "",
     "schema": "harness.electronics-power-axes-v2.v1",
     "source_url": "https://www.st.com/resource/en/datasheet/st1s15.pdf",
     "table_kind": "characteristics", "unit": "V", "unit_as_printed": "V",
     "value": 5.5, "value_role": "range",
     "verbatim": "VIN | Operating input voltage range | 2.3 | 5.5 | V",
     "verdict": "printed_row"},
    {"condition_verbatim": None, "field": "vout",
     "method": "deterministic_power_axes_v2_v1", "page_1based": 5,
     "part_number": ST1S15_SHA, "pdf_sha": "",
     "schema": "harness.electronics-power-axes-v2.v1",
     "source_url": "https://www.st.com/resource/en/datasheet/st1s15.pdf",
     "table_kind": "output_voltage", "unit": "V", "value_role": "fixed",
     "verbatim": "VOUT | Output voltage | 9 | V",
     "verdict": "printed_row", "vout_max_v": 9.0, "vout_min_v": 9.0},
]

# Non-entailing row (NEO review, merge 47cac640): the old positive sample
# asserted vout=9.0 from the versions-availability line
# "…versions of the ST1S15 available on | 9" — an over-claim the substring
# check cannot fully police. This fixture (claim value absent from the
# quoted row) is the canonical negative for the entailment gate.
NON_ENTAILING_ROW = {
    "condition_verbatim": None, "field": "vout",
    "method": "deterministic_power_axes_v2_v1", "page_1based": 2,
    "part_number": ST1S15_SHA, "pdf_sha": "",
    "schema": "harness.electronics-power-axes-v2.v1",
    "source_url": "https://www.st.com/resource/en/datasheet/st1s15.pdf",
    "table_kind": "characteristics", "unit": "V", "value_role": "fixed",
    "verbatim": "VIN | Operating input voltage range | 2.3 | 5.5 | V",
    "verdict": "printed_row", "vout_max_v": 9.0, "vout_min_v": 9.0,
}


@pytest.fixture()
def vault(tmp_path):
    import hashlib
    root = tmp_path / "vault"
    data = b"%PDF-1.7 ST1S15 fixture"
    sha = hashlib.sha256(data).hexdigest()
    blob = root / "blobs" / sha[0:2] / sha[2:4] / f"{sha}.pdf"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(data)
    return str(root), sha


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path / "norm.db"))


def test_vault_reverification(vault):
    root, sha = vault
    ok, detail = verify_sha256(root, sha)
    assert ok, detail
    ok, detail = verify_sha256(root, "f" * 64)
    assert not ok


def test_st1s15_sample_loads_with_vault(store, vault):
    root, sha = vault
    report = load_rows(store, make_rows(sha), vault_root=root,
                       opn_default="ST1S15")
    assert report.rejections == [], report.rejections
    assert report.loaded == 3
    claims = store.claims_for_opn("st1s15")
    fields = {c["field"]: c for c in claims}
    assert fields["vin_min_v"]["value"] == 2.3
    assert fields["vout"]["value"] == 9.0
    assert fields["vout"]["value_min"] is None and \
        fields["vout"]["value_max"] is None
    assert fields["vin_min_v"]["page"] == 8
    assert "2.3 | 5.5" in fields["vin_min_v"]["verbatim"]


def test_dry_run_writes_nothing(store, vault):
    root, sha = vault
    report = load_rows(store, make_rows(sha), vault_root=root,
                       dry_run=True, opn_default="ST1S15")
    assert report.loaded == 3 and report.rejections == []
    assert store.claims_for_opn("st1s15") == []


def test_serve_endpoint(store, vault):
    root, sha = vault
    load_rows(store, make_rows(sha), vault_root=root, opn_default="ST1S15")
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

def test_persist_without_vault_root_raises(store):
    with pytest.raises(ValueError, match="vault_root is mandatory"):
        load_rows(store, make_rows("a" * 64), opn_default="ST1S15")
    assert store.claims_for_opn("st1s15") == []


def test_non_entailing_verbatim_rejected(store, vault):
    root, sha = vault
    rows = json.loads(json.dumps([NON_ENTAILING_ROW]).replace(ST1S15_SHA, sha))
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "entail" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_reject_missing_verbatim(store, vault):
    root, sha = vault
    rows = json.loads(json.dumps(
        [dict(SAMPLE_ROWS[0], verbatim="")]).replace(ST1S15_SHA, sha))
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert len(report.rejections) == 1
    assert "verbatim" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_reject_missing_page(store, vault):
    root, sha = vault
    rows = json.loads(json.dumps([SAMPLE_ROWS[0]]).replace(ST1S15_SHA, sha))
    del rows[0]["page_1based"]
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1


def test_reject_bad_sha(store, vault):
    root, sha = vault
    rows = [dict(SAMPLE_ROWS[0], part_number="deadbeef")]
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "64-hex" in report.rejections[0]["reason"]


def test_reject_vault_hash_mismatch(store, vault):
    root, real_sha = vault
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


def test_reject_missing_rule_version_and_verified_by(store, vault):
    root, sha = vault
    rows = json.loads(json.dumps([SAMPLE_ROWS[0]]).replace(ST1S15_SHA, sha))
    del rows[0]["schema"]
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert "rule_version" in report.rejections[0]["reason"]
    # verified_by is the evidence-backed verdict — a row with no verdict
    # has nothing vouching for it and is rejected (method alone is NOT
    # verification; it is kept separately as extractor_method).
    rows = json.loads(json.dumps([SAMPLE_ROWS[0]]).replace(ST1S15_SHA, sha))
    del rows[0]["verdict"]
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert "verified_by" in report.rejections[0]["reason"]


def test_extractor_method_recorded_separately(store, vault):
    root, sha = vault
    report = load_rows(store, make_rows(sha), vault_root=root,
                       opn_default="ST1S15")
    assert report.loaded == 3
    claims = store.claims_for_opn("st1s15")
    for c in claims:
        assert c["verified_by"] == "quote_pass"
        assert c["extractor_method"] == "deterministic_power_axes_v2_v1"


def test_replay_loads_zero_new_rows(store, vault):
    root, sha = vault
    rows = make_rows(sha)
    first = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert first.loaded == 3 and first.duplicates == 0
    replay = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert replay.loaded == 0, "replay must not create new rows"
    assert replay.duplicates == 3
    assert replay.rejections == []
    assert len(store.claims_for_opn("st1s15")) == 3


def test_unique_key_null_safe(store, vault):
    root, sha = vault
    rows = [json.loads(json.dumps(
        SAMPLE_ROWS[0]).replace(ST1S15_SHA, sha))]
    rows[0].pop("value_role", None)
    rows[0].pop("condition_verbatim", None)
    first = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert first.loaded == 1
    replay = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert replay.loaded == 0 and replay.duplicates == 1, \
        "NULL value_role/condition_verbatim must dedupe (null-safe UNIQUE)"
    assert len(store.claims_for_opn("st1s15")) == 1


def test_reject_no_opn_and_no_default(store, vault):
    root, sha = vault
    rows = json.loads(json.dumps([SAMPLE_ROWS[0]]).replace(ST1S15_SHA, sha))
    report = load_rows(store, rows, vault_root=root)
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "opn" in report.rejections[0]["reason"]


def test_rejections_never_swallowed(store, vault):
    root, sha = vault
    rows = json.loads(json.dumps(
        [dict(SAMPLE_ROWS[0], verbatim=""),
         SAMPLE_ROWS[1]]).replace(ST1S15_SHA, sha))
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 1
    assert len(report.rejections) == 1
    assert report.rejections[0]["index"] == 0
    assert report.summary()["rejected"] == 1


# --- round-2 corrections (hw-x9g, NEO review at head 61e95297) ---

def _vout_row(sha, verbatim, value_kwargs=None):
    row = json.loads(json.dumps(SAMPLE_ROWS[2]).replace(ST1S15_SHA, sha))
    row["verbatim"] = verbatim
    row.update(value_kwargs or {})
    return row


def test_original_available_on_quote_rejected(store, vault):
    """The ORIGINAL failing quote: 'available on | 9' accepted vout 9.0
    under digit-boundary matching. Field binding rejects it: the row
    never labels vout."""
    root, sha = vault
    row = _vout_row(
        sha,
        "Different output voltage versions of the ST1S15 available on | 9",
        {"vout_max_v": 9.0, "vout_min_v": 9.0})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "entail" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_near_miss_numbers_rejected(store, vault):
    """Decimal / signed near-misses: 9 inside 0.9 / 9.5 / -9 is token
    presence, not entailment."""
    root, sha = vault
    for printed in ("0.9", "9.5", "-9"):
        row = _vout_row(sha, f"VOUT | Output voltage | {printed} | V",
                        {"vout_max_v": 9.0, "vout_min_v": 9.0})
        report = load_rows(store, [row], vault_root=root,
                           opn_default="ST1S15")
        assert report.loaded == 0, printed
        assert "entail" in report.rejections[0]["reason"], printed
    assert store.claims_for_opn("st1s15") == []


def test_wrong_field_binding_rejected(store, vault):
    """9 printed after ANOTHER field's label does not entail vout."""
    root, sha = vault
    row = _vout_row(sha, "IQ | Quiescent current | 9 | mA",
                    {"vout_max_v": 9.0, "vout_min_v": 9.0})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "entail" in report.rejections[0]["reason"]


def test_verified_by_vocabulary_enforced(store, vault):
    """verified_by is a receipt from an evidence-backed vocabulary; an
    arbitrary raw verdict ('REJECTED') must be refused, never loaded."""
    root, sha = vault
    row = json.loads(json.dumps(SAMPLE_ROWS[0]).replace(ST1S15_SHA, sha))
    row["verdict"] = "REJECTED"
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "vocabulary" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def _make_v1_store(path):
    """A PR65-era store: v1 table WITHOUT extractor_method, inline
    UNIQUE, schema_version norm-serve.v1."""
    import sqlite3
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


def test_v1_store_refused_fail_closed(tmp_path):
    """NEO round-2 (3): opening a PR65 store with the new loader raised
    OperationalError (missing extractor_method). Now: fail-closed
    refusal with instruction — never auto-migrate."""
    db = str(tmp_path / "v1.db")
    _make_v1_store(db)
    with pytest.raises(Rejection, match="predates norm-serve.v2"):
        Store(db)


def test_duplicate_existing_rows_refused_fail_closed(tmp_path):
    """NEO round-2 (3): creating uq_norm_claims_key over existing
    duplicates raised raw IntegrityError. Now: fail-closed refusal
    naming the problem and the required operator action."""
    import sqlite3
    db = str(tmp_path / "dups.db")
    store = Store(db)
    store.con.execute("DROP INDEX IF EXISTS uq_norm_claims_key")
    store.con.commit()
    store.con.close()
    con = sqlite3.connect(db)
    base = ("ST1S15", "a" * 64, "vout", 9.0, None, None, "V", "V", "fixed",
            None, "printed_row", "output_voltage", 5, "VOUT | Out | 9 | V",
            None, "quote_pass", "rv", "m")
    from harness.normserve.schema import SCHEMA_VERSION
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


def test_distinct_page_observations_both_retained(store, vault):
    """NEO round-2 (5): 2.3@page8 then 4.7@page9 collided on the old key
    and the second was silently IGNORED. page is in the v2 key: both are
    legitimate distinct observations and both are retained."""
    root, sha = vault
    r1 = json.loads(json.dumps(SAMPLE_ROWS[0]).replace(ST1S15_SHA, sha))
    r2 = dict(r1, value=4.7, page_1based=9,
              verbatim="VIN | Operating input voltage range | 4.7 | 5.5 | V")
    report = load_rows(store, [r1, r2], vault_root=root,
                       opn_default="ST1S15")
    assert report.loaded == 2, report.summary()
    assert report.duplicates == 0 and report.rejections == []
    claims = [c for c in store.claims_for_opn("st1s15")
              if c["field"] == "vin_min_v"]
    assert sorted(c["page"] for c in claims) == [8, 9]
    assert sorted(c["value"] for c in claims) == [2.3, 4.7]


def test_conflicting_same_key_observation_refused_loudly(store, vault):
    """Same key, different value: refused loudly as a conflict — never
    silently ignored, never mislabeled as a duplicate."""
    root, sha = vault
    r1 = json.loads(json.dumps(SAMPLE_ROWS[0]).replace(ST1S15_SHA, sha))
    r2 = dict(r1, value=4.7,
              verbatim="VIN | Operating input voltage range | 4.7 | 5.5 | V")
    load_rows(store, [r1], vault_root=root, opn_default="ST1S15")
    report = load_rows(store, [r2], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and report.duplicates == 0
    assert len(report.rejections) == 1
    assert "conflicting observation" in report.rejections[0]["reason"]
    claims = [c for c in store.claims_for_opn("st1s15")
              if c["field"] == "vin_min_v"]
    assert len(claims) == 1 and claims[0]["value"] == 2.3


# --- round-3 corrections (hw-x9g, NEO consolidated review of f41c18eb):
# semantic binding — value + unit + role + range + page context + OPN
# applicability must ALL bind to the printed row, or the row is held. ---

def test_unit_not_bound_rejected(store, vault):
    """(1) '9mV' printed but 9 V claimed: the value token matches, the
    printed unit does not — the row is held, never served as 9 V."""
    root, sha = vault
    row = _vout_row(sha, "VOUT | Output voltage | 9mV",
                    {"vout_max_v": 9.0, "vout_min_v": 9.0})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "unit" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_printed_unit_served_with_claim_loads(store, vault):
    """Positive control for (1): 9 V printed and 9 V claimed binds."""
    root, sha = vault
    row = _vout_row(sha, "VOUT | Output voltage | 9mV",
                    {"vout_max_v": 9.0, "vout_min_v": 9.0, "unit": "mV"})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 1, report.summary()
    assert store.claims_for_opn("st1s15")[0]["unit"] == "mV"


def test_cross_field_current_voltage_rejected(store, vault):
    """(2) VOUT 1.2 V row then IQ 9 mA row: the CURRENT value must not
    be served as the VOLTAGE field — binding is line-scoped to the
    field's own label row."""
    root, sha = vault
    row = _vout_row(
        sha, "VOUT | Output voltage | 1.2 | V\nIQ | Quiescent current | 9 | mA",
        {"vout_max_v": 9.0, "vout_min_v": 9.0})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "entail" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_page_reference_number_rejected(store, vault):
    """(3) 'VOUT | See page | 9': the 9 is a page number, not a value —
    held, never served as 9 V."""
    root, sha = vault
    row = _vout_row(sha, "VOUT | See page | 9",
                    {"vout_max_v": 9.0, "vout_min_v": 9.0})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "page reference" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_min_taken_from_range_upper_bound_rejected(store, vault):
    """(4) VIN min 5.5 against a printed 2.3 to 5.5 range: the min must
    bind to the range's LOWER endpoint."""
    root, sha = vault
    row = _vout_row(sha, "VIN | Operating input voltage range | 2.3 to 5.5 | V",
                    {"field": "vin_min_v", "value": 5.5, "unit": "V",
                     "value_role": "range"})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "lower bound" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []
    # positive control: the correct endpoint binds
    row_ok = _vout_row(
        sha, "VIN | Operating input voltage range | 2.3 to 5.5 | V",
        {"field": "vin_min_v", "value": 2.3, "unit": "V",
         "value_role": "range"})
    report = load_rows(store, [row_ok], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 1, report.summary()


def test_typ_mislabeled_max_rejected(store, vault):
    """(5) 'typ 9' printed but max claimed: the role must bind to the
    printed role."""
    root, sha = vault
    row = _vout_row(sha, "VOUT | Output voltage | typ | 9 | V",
                    {"vout_max_v": 9.0, "vout_min_v": 9.0,
                     "value_role": "max"})
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "role" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []
    # positive control: claiming the printed role binds
    row_ok = _vout_row(sha, "VOUT | Output voltage | typ | 9 | V",
                       {"value": 9.0, "value_role": "typ"})
    report = load_rows(store, [row_ok], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 1, report.summary()


def test_unrelated_opn_held(store, vault):
    """(6) UNRELATED-OPN: the OPN appears nowhere in the quote,
    condition, source URL, or evidence_ref — applicability is not
    evidenced, the row is held."""
    root, sha = vault
    row = json.loads(json.dumps(SAMPLE_ROWS[2]).replace(ST1S15_SHA, sha))
    report = load_rows(store, [row], vault_root=root, opn_default="LM9999X")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "applicability" in report.rejections[0]["reason"]
    assert store.claims_for_opn("LM9999X") == []
    # positive control: the real OPN is evidenced via source_url and loads
    report = load_rows(store, [json.loads(json.dumps(SAMPLE_ROWS[2])
                              .replace(ST1S15_SHA, sha))],
                       vault_root=root, opn_default="ST1S15")
    assert report.loaded == 1, report.summary()


def test_applicability_rule_self_assertion_rejected(store, vault):
    """(7) raw applicability_rule verdict WITHOUT a rule receipt
    (evidence_ref + span naming the OPN) is self-assertion — refused."""
    root, sha = vault
    row = json.loads(json.dumps(SAMPLE_ROWS[2]).replace(ST1S15_SHA, sha))
    row["verdict"] = "applicability_rule"
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    reason = report.rejections[0]["reason"]
    assert "self-assertion" in reason
    assert store.claims_for_opn("st1s15") == []
    # with an actual receipt (evidence_ref + span naming the OPN) it loads
    row_ok = dict(row, evidence_ref=f"applicability:ST1S15@p1",
                  span="ST1S15: device applicable to 2A buck")
    report = load_rows(store, [row_ok], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 1, report.summary()


def _make_empty_old_index_store(path):
    """A PR66-era store: v2 columns (extractor_method present) but the
    OLD no-page unique index — empty, so every row-level check passes."""
    import sqlite3
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
    """(8) an EMPTY old-schema store passes row-level checks but its
    no-page unique key makes the first second-page insert raise raw
    IntegrityError. The preflight must detect the old index and refuse
    BEFORE any load — fail-closed, never auto-migrate."""
    db = str(tmp_path / "old-index.db")
    _make_empty_old_index_store(db)
    with pytest.raises(Rejection, match="OLD no-page"):
        Store(db)
