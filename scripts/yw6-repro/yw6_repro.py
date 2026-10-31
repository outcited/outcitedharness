"""hw-yw6 revision round: independent actual-path synthetic reproductions
against PR66 head 61e95297. Synthetic fixtures only; no corpus, no PG.
"""
import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from pathlib import Path
ROOT = str(Path(__file__).resolve().parents[2])

RESULTS = {}

# ---- R1: shared-SHA K6-only curve requested as K4 -> HTTP200 stamped K4 ----
sys.path.insert(0, ROOT)
import os
os.environ["CR_API_KEYS"] = "testkey123"
os.environ["DISCOVERY_CATALOG"] = str(Path(__file__).resolve().parent / "empty_catalog.db")
import sqlite3
con = sqlite3.connect(str(Path(__file__).resolve().parent / "empty_catalog.db"))
con.executescript("CREATE TABLE IF NOT EXISTS parts(opn TEXT, vendor TEXT, family TEXT, package TEXT);"
                  "CREATE TABLE IF NOT EXISTS part_sources(opn TEXT, doc_sha256 TEXT);"
                  "CREATE TABLE IF NOT EXISTS claims_canonical(opn TEXT, symbol TEXT, value REAL,"
                  " condition_norm TEXT, unit TEXT, provenance TEXT, doc_sha256 TEXT);")
con.commit(); con.close()

import harness.extraction_api as xapi
import hashlib
SHA = hashlib.sha256(b"synthetic").hexdigest()
xapi.PART_SHAS["STM32F051K4"].append(SHA)  # stem-join from shared manifest
xapi.CURVES_BY_SHA[SHA].append({
    "row_id": "curve-synth-p10-f1", "atom_class": "curve",
    "curve_type": "idd_run_vs_vdd", "unit": "mA",
    "page": 10, "figure_index": 1,
    "pdf_sha": SHA, "render_sha": None,
    "extraction_model": "synthetic", "burn_id": "synthetic",
})

from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), xapi.Handler)
t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
port = srv.server_address[1]
req = urllib.request.Request(
    f"http://127.0.0.1:{port}/api/extraction/v1/parts/STM32F051K4?atom_class=curve",
    headers={"Authorization": "Bearer testkey123"})
with urllib.request.urlopen(req) as r:
    body = json.loads(r.read())
    RESULTS["R1_status"] = r.status
    RESULTS["R1_part_number"] = body["part_number"]
    RESULTS["R1_row_count"] = body["row_count"]
    RESULTS["R1_row_pdf_sha"] = body["rows"][0]["pdf_sha"][:8] if body["rows"] else None
srv.shutdown()

# ---- R3a: adapter accepts K6U7 via opn_default, validation errors [] ----
from harness.normserve.adapter import adapt_power_axes_row
from harness.normserve import schema as schema_mod
raw = {"pdf_sha": SHA, "field": "idd_run_current", "value": 22.0,
       "unit": "mA", "value_role": "typ",
       "condition_verbatim": "TA = 25 °C (per §6.1.2)",
       "verdict": "printed_row", "page_1based": 50,
       "verbatim": "22 mA typ @ TA = 25 °C", "rule_version": "power-axes-v2", "schema": "harness.electronics-power-axes-v2.v1", "method": "synthetic", "evidence_ref": "synthetic"}
typed, reason = adapt_power_axes_row(raw, opn_default="STM32F051K6U7")
RESULTS["R3a_typed_opn"] = typed["opn"] if typed else reason
RESULTS["R3a_validation_errors"] = schema_mod.validate_typed_row(typed) if typed else None
# and that opn serves under the unrelated default, not any evidence-bound OPN

# ---- R3b: loader same-key rows 22@p50 then 23@p51 -> only 22 retained ----
from harness.normserve.loader import Store, load_rows
tmpdb = tempfile.mkdtemp() + "/yw6.db"
store = Store(tmpdb)
vault = tempfile.mkdtemp()
blob = Path(vault) / "blobs" / SHA[:2] / SHA[2:4] / f"{SHA}.pdf"
blob.parent.mkdir(parents=True, exist_ok=True)
blob.write_bytes(b"synthetic")
row22 = dict(raw, opn="STM32F051K6", page_1based=50, value=22.0)
# SAME-KEY collision: identical (opn,doc_sha,field,value_role,condition_verbatim),
# different page + value -> UNIQUE key identical -> INSERT OR IGNORE drops row 2.
row23 = dict(raw, opn="STM32F051K6", page_1based=51, value=23.8,
              verbatim="23.8 mA typ @ TA = 25 °C")
rep = load_rows(store, [row22, row23], vault_root=vault)
RESULTS["R3b_summary"] = rep.summary()
stored = store.claims_for_opn("STM32F051K6")
RESULTS["R3b_stored"] = [(r["value"], r["page"]) for r in stored]
RESULTS["R3b_groupby_having"] = store.con.execute(
    "SELECT opn, doc_sha, field, COUNT(*) c FROM norm_claims"
    " GROUP BY 1,2,3 HAVING c > 1").fetchall()

# ---- Ra: ordering_codes — X is a legitimate ST pin letter ----
from harness.electronics.ordering_codes import decode
try:
    RESULTS["Ra_stm32f051k6ux"] = str(decode("STM32F051K6UX"))
except Exception as e:
    RESULTS["Ra_stm32f051k6ux"] = f"{type(e).__name__}: {e}"
try:
    RESULTS["Ra_pic32mx"] = str(decode("PIC32MX795F512L"))
except Exception as e:
    RESULTS["Ra_pic32mx"] = f"{type(e).__name__}: {e}"

print(json.dumps(RESULTS, indent=2, ensure_ascii=False, default=str))
