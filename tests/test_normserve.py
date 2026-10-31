"""NORM-SERVE-01 tests (issue #64 contract) + hw-x9g round-4 admission gate.

Positive: ST1S15 proving sample with a REAL structured receipt (cell
coordinates, structure-bound field/unit/role, printed-structure
applicability, bound conditions, quote_pass re-extraction from current
bytes) loads and serves. Without that receipt it is HELD (conditional
control — never forced to pass).
Negative: missing provenance, failed vault re-verification — rejected;
missing structured observation, unbound field/role/unit, unbound OPN,
unbound conditions, self-asserted receipts — HELD with reason codes,
never swallowed, never stored.
"""

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from harness.normserve.loader import Store, load_rows
from harness.normserve.serve import make_handler
from harness.normserve.vault import verify_sha256

ST1S15_SHA = "82d2ee0cc7dd7c7aa47fed41ab13e9593d37f49d858470b56d50e648a104c222"

VIN_VERBATIM = "VIN | Operating input voltage range | 2.3 | 5.5 | V"
VOUT_VERBATIM = "Different output voltage versions of the ST1S15 available on | 9"


def make_pdf_bytes(pages_text):
    """Build a real (minimal) PDF whose page text is exactly pages_text."""
    import fitz
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    return doc.tobytes()

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
     "method": "deterministic_power_axes_v2_v1", "page_1based": 2,
     "part_number": ST1S15_SHA, "pdf_sha": "",
     "schema": "harness.electronics-power-axes-v2.v1",
     "source_url": "https://www.st.com/resource/en/datasheet/st1s15.pdf",
     "table_kind": "characteristics", "unit": "V", "value_role": "fixed",
     "verbatim": "Different output voltage versions of the ST1S15 available on | 9",
     "verdict": "printed_row", "vout_max_v": 9.0, "vout_min_v": 9.0},
]


@pytest.fixture()
def vault(tmp_path):
    """Vault with a real PDF: page 1 carries the VIN row, page 2 the VOUT
    page-reference line (quoted verbatim only — '9' there is NOT a value)."""
    root = tmp_path / "vault"
    data = make_pdf_bytes([
        VIN_VERBATIM + "\nIQ | 22.0 | 23.8 | uA", VOUT_VERBATIM])
    sha = hashlib.sha256(data).hexdigest()
    blob = root / "blobs" / sha[0:2] / sha[2:4] / f"{sha}.pdf"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(data)
    return str(root), sha


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path / "norm.db"))


VOUT_COND_VIN = {
    "verbatim": "VDD = 3.6 V (local table condition)",
    "precedence": "local", "source_page": 1,
}
COND_TA25_INHERITED = {
    "verbatim": "TA = 25 C (section 6.1.2 default, inherited)",
    "precedence": "inherited", "source_page": 1,
}


def structured(raw, sha, quoted_text, column_key, column_role,
               column_unit="V", page=1, table=0, row_idx=0, column=2,
               applicability=None, conditions=None, verified_by="quote_pass",
               value_role=None):
    """Attach the full structured receipt an existing table-aware extractor
    emits. applicability defaults to a printed device-summary structure."""
    row = dict(raw)
    row["part_number"] = sha
    row["pdf_sha"] = ""
    row["page_1based"] = page
    row["evidence"] = [{
        "kind": "table_cell", "document_sha256": sha,
        "source_uri": f"file://vault/{sha}.pdf",
        "page_1based": page, "table_index": table, "row_index": row_idx,
        "column_index": column, "quoted_text": quoted_text,
    }]
    row["field_binding"] = {"table": table, "column_key": column_key,
                            "column_role": column_role,
                            "column_unit": column_unit}
    row["applicability"] = applicability or {
        "structure": "device_summary", "structure_page": 1,
        "cell_text": "ST1S15"}
    row["conditions"] = conditions if conditions is not None else [
        dict(VOUT_COND_VIN), dict(COND_TA25_INHERITED)]
    row["method"] = verified_by
    if value_role:
        row["value_role"] = value_role
    return row


def test_vault_reverification(vault):
    root, sha = vault
    ok, detail = verify_sha256(root, sha)
    assert ok, detail
    ok, detail = verify_sha256(root, "f" * 64)
    assert not ok


def test_unstructured_sample_rows_held(store, vault):
    """Requirement (B): the ST1S15 control is conditional. The legacy
    unstructured sample carries NO cell coordinates -> HELD, never served."""
    root, sha = vault
    report = load_rows(store, make_rows(sha), vault_root=root,
                       opn_default="ST1S15")
    assert report.loaded == 0
    assert len(report.holds) == 3
    assert all(h["reason_code"] == "no_structured_observation"
               for h in report.holds)
    assert store.claims_for_opn("st1s15") == []


def test_st1s15_structured_receipt_serves(store, vault):
    """Positive control WITH a real receipt: cell coordinates +
    structure-bound field/unit/role + printed applicability + bound
    conditions + quote_pass re-extracted from CURRENT bytes."""
    root, sha = vault
    rows = [
        structured(SAMPLE_ROWS[0], sha, "2.3 | 5.5 | V", "vin", "min",
                   value_role="range",
                   conditions=[dict(VOUT_COND_VIN, verbatim=VOUT_COND_VIN[
                       "verbatim"].replace("VDD", "VIN"))]),
        structured(SAMPLE_ROWS[1], sha, "2.3 | 5.5 | V", "vin", "max",
                   value_role="range"),
    ]
    # field derived from column_key: vin_min_v / vin_max_v
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 2, report.summary()
    assert report.holds == []
    claims = store.claims_for_opn("st1s15")
    fields = {c["field"]: c for c in claims}
    assert fields["vin_min_v"]["value"] == 2.3
    assert fields["vin_max_v"]["value"] == 5.5
    assert fields["vin_min_v"]["page"] == 1


def test_dry_run_writes_nothing(store, vault):
    root, sha = vault
    row = structured(SAMPLE_ROWS[0], sha, "2.3 | 5.5 | V", "vin", "min")
    report = load_rows(store, [row], vault_root=root,
                       dry_run=True, opn_default="ST1S15")
    assert report.loaded == 1 and report.holds == []
    assert store.claims_for_opn("st1s15") == []


def test_serve_endpoint(store, vault):
    root, sha = vault
    row = structured(SAMPLE_ROWS[0], sha, "2.3 | 5.5 | V", "vin", "min")
    load_rows(store, [row], vault_root=root, opn_default="ST1S15")
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
        assert payload["count"] == 1
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


# --- hw-x9g round 4: the five reproduced PR72 failures must HOLD ---

def test_hold_cross_field_binding(store, vault):
    """VOUT 1.2V row then IQ 9mA row -> VOUT served 9V. A CURRENT value
    must not bind to a VOLTAGE field: field must derive from column_key."""
    root, sha = vault
    row = structured(SAMPLE_ROWS[2], sha, "IQ | 9 | mA", "vin", "value")
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert report.holds[0]["reason_code"] == "field_unbound"


def test_hold_unit_unbound(store, vault):
    """Missing unit or 'Unit mV' before 9 must not serve as 9V."""
    root, sha = vault
    row = structured(SAMPLE_ROWS[2], sha, "9", "vout", "value",
                     column_unit=None)
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert report.holds[0]["reason_code"] == "unit_unbound"
    row = structured(SAMPLE_ROWS[2], sha, "9", "vout", "value",
                     column_unit=None)
    row["unit"] = "V"
    row["evidence"][0]["quoted_text"] = "Unit mV 9"
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert report.holds[0]["reason_code"] == "unit_unbound"


def test_hold_role_header_mislabel(store, vault):
    """Max 9 printed under Min|Typ|Max headers must not serve as typ 9."""
    root, sha = vault
    row = structured(SAMPLE_ROWS[0], sha, "2.3 | 5.5 | V", "vin", "min",
                     value_role="typ")
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert report.holds[0]["reason_code"] == "role_unbound"


def test_hold_opn_via_url_or_evidence_ref(store, vault):
    """OPN from URL query/path/prefix-substring/negated-condition/
    arbitrary evidence_ref is NOT applicability."""
    root, sha = vault
    bad_applicabilities = [
        {"structure": "ordering_table", "structure_page": 1,
         "cell_text": "", "source_url":
             "https://x.com/ds.pdf?opn=ST1S15"},
        {"structure": "ordering_table", "structure_page": 1,
         "cell_text": "ST1S15X (prefix substring only)"},
        {"structure": "ordering_table", "structure_page": 1,
         "cell_text": "not the ST1S15 (negated)"},
        {"structure": "ordering_table", "structure_page": 1,
         "cell_text": "ST1S15 3.3V SOT-23-5 (row prose, not the cell value)"},
    ]
    for app in bad_applicabilities:
        row = structured(SAMPLE_ROWS[0], sha, "2.3 | 5.5 | V", "vin",
                         "min", applicability=app)
        report = load_rows(store, [row], vault_root=root,
                           opn_default="ST1S15")
        assert report.loaded == 0, app
        assert report.holds[0]["reason_code"] == "opn_unbound"
    row = structured(SAMPLE_ROWS[0], sha, "2.3 | 5.5 | V", "vin", "min")
    del row["applicability"]
    row["evidence_ref"] = "opn-ST1S15-somewhere"
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert report.holds[0]["reason_code"] == "opn_unbound"


def test_hold_garbage_receipt(store, vault):
    """Self-asserted verified_by strings and unresolvable applicability_rule
    receipts never serve."""
    root, sha = vault
    row = structured(SAMPLE_ROWS[0], sha, "2.3 | 5.5 | V", "vin", "min",
                     verified_by="not a receipt")
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert report.holds[0]["reason_code"] == "receipt_unknown_vocabulary"
    row = structured(SAMPLE_ROWS[0], sha, "2.3 | 5.5 | V", "vin", "min",
                     verified_by="applicability_rule")
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert report.holds[0]["reason_code"] == "rule_unresolved"


def test_hold_value_not_in_cited_cell(store, vault):
    """quote_pass re-extracts: a value absent from the cited cell text
    (decimal 0.9 / negative -9 / page-ref 9 style negatives) cannot serve."""
    root, sha = vault
    row = structured(dict(SAMPLE_ROWS[0], value=0.9, vin_min_v=0.9), sha,
                     "2.3 | 5.5 | V", "vin", "min")
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert report.holds[0]["reason_code"] == "receipt_unverifiable"


# --- round-4 requirement (A): role + conditions at the same observation ---

def test_hold_conditions_unbound(store, vault):
    root, sha = vault
    row = structured(SAMPLE_ROWS[0], sha, "2.3 | 5.5 | V", "vin", "min")
    row["conditions"] = []
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert report.holds[0]["reason_code"] == "conditions_unbound"
    row["conditions"] = [{"verbatim": "TA = 25 C", "precedence": "guessed",
                          "source_page": 1}]
    report = load_rows(store, [row], vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert report.holds[0]["reason_code"] == "conditions_unbound"


def test_table25_canonical_local_over_inherited(store, vault):
    """Table 25 canonical binding (NEO correction): typ 22.0 = VDD 3.6 V
    (LOCAL, table title 'from VDD at 3.6 V') + TA 25 C (inherited default);
    max 23.8 = VDD 3.6 V (local) + TA 105 C (column). Local overrides the
    section-default VDD 3.3 V — precedence recorded per condition row."""
    root, sha = vault
    typ_row = structured(
        {"field": "iq", "value": 22.0, "unit": "uA", "value_role": "typ",
         "schema": "harness.electronics-power-axes-v2.v1", "pdf_sha": "",
         "part_number": sha, "verbatim": "IQ | 22.0 | 23.8 | uA",
         "condition_verbatim": "VDD = 3.6 V; TA = 25 C"},
        sha, "22.0 | uA", "iq", "typ", column_unit="uA",
        conditions=[
            {"verbatim": "VDD = 3.6 V (table title 'from VDD at 3.6 V')",
             "precedence": "local", "source_page": 1},
            {"verbatim": "TA = 25 C (section 6.1.2 default, inherited)",
             "precedence": "inherited", "source_page": 1},
        ])
    max_row = structured(
        {"field": "iq", "value": 23.8, "unit": "uA", "value_role": "max",
         "iq_max_v": 23.8,
         "schema": "harness.electronics-power-axes-v2.v1", "pdf_sha": "",
         "part_number": sha, "verbatim": "IQ | 22.0 | 23.8 | uA",
         "condition_verbatim": "VDD = 3.6 V; TA = 105 C"},
        sha, "23.8 | uA", "iq", "max", column_unit="uA",
        conditions=[
            {"verbatim": "VDD = 3.6 V (table title 'from VDD at 3.6 V')",
             "precedence": "local", "source_page": 1},
            {"verbatim": "TA = 105 C (column condition)",
             "precedence": "column", "source_page": 1},
        ])
    report = load_rows(store, [typ_row, max_row], vault_root=root,
                       opn_default="ST1S15")
    assert report.loaded == 2, report.summary()
    conds = {c["value"]: c["condition_verbatim"] for c in
             store.claims_for_opn("st1s15")}
    assert "VDD = 3.6 V; TA = 25 C" in conds.values()
    assert "VDD = 3.6 V; TA = 105 C" in conds.values()


# --- negative tests (mandatory, issue #64) ---

def test_reject_missing_verbatim(store):
    rows = [dict(SAMPLE_ROWS[0], verbatim="")]
    report = load_rows(store, rows, opn_default="ST1S15")
    assert report.loaded == 0
    assert len(report.rejections) == 1
    assert "verbatim" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_reject_missing_page(store):
    rows = [dict(SAMPLE_ROWS[0])]
    del rows[0]["page_1based"]
    report = load_rows(store, rows, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1


def test_reject_bad_sha(store):
    rows = [dict(SAMPLE_ROWS[0], part_number="deadbeef")]
    report = load_rows(store, rows, opn_default="ST1S15")
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


def test_reject_missing_rule_version_and_verified_by(store):
    rows = [dict(SAMPLE_ROWS[0])]
    del rows[0]["schema"]
    report = load_rows(store, rows, opn_default="ST1S15")
    assert report.loaded == 0
    assert "rule_version" in report.rejections[0]["reason"]
    rows = [dict(SAMPLE_ROWS[0])]
    del rows[0]["method"]
    report = load_rows(store, rows, opn_default="ST1S15")
    assert report.loaded == 0
    assert "verified_by" in report.rejections[0]["reason"]


def test_reject_no_opn_and_no_default(store):
    report = load_rows(store, [dict(SAMPLE_ROWS[0])])
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "opn" in report.rejections[0]["reason"]


def test_rejections_and_holds_never_swallowed(store, vault):
    """Rejections and admission HOLDs are both recorded, never swallowed."""
    root, sha = vault
    bad = structured(SAMPLE_ROWS[0], sha, "2.3 | 5.5 | V", "vin", "min")
    bad["verbatim"] = ""
    held = dict(SAMPLE_ROWS[1], part_number=sha, pdf_sha="")
    report = load_rows(store, [bad, held], vault_root=root,
                       opn_default="ST1S15")
    assert report.loaded == 0
    assert len(report.rejections) == 1
    assert report.rejections[0]["index"] == 0
    assert "verbatim" in report.rejections[0]["reason"]
    assert len(report.holds) == 1
    assert report.holds[0]["index"] == 1
    summary = report.summary()
    assert summary["rejected"] == 1 and summary["held"] == 1
