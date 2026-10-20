"""Spine proof gates — run before scaling beyond the pilot (PLAN-NORMALIZATION-SPINE §6)."""
import json, subprocess, sys, os, tempfile
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1/scripts")
import spine

TMP = Path(tempfile.mkdtemp(prefix="spine-tests-"))
FAILS = []

def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)

# ---- claim identity: deterministic + dedupe
c1 = {"pdf_sha": "aa", "lane": "pillars", "field": "flash_kb", "value_raw": 64,
      "unit_raw": "KB", "value_role": None, "page_1based": 3, "verbatim": "64 kB"}
c2 = dict(c1, source_file="other-lane.jsonl")
id1, id2 = spine.claim_id(c1), spine.claim_id(c2)
check("claim_id deterministic (content-addressed)", id1 == id2)
check("claim_id distinguishes values",
      id1 != spine.claim_id(dict(c1, value_raw=128)))

# ---- unit canon: never merge meaning
check("µA canonicalizes", spine.canon_unit("µA") == "uA")
check("nA canonicalizes", spine.canon_unit("nA") == "nA")
check("uA/MHz stays distinct from uA", spine.canon_unit("µA/MHz") == "uA/MHz")

# ---- mode canon: case variants merge, distinct modes NEVER merge
check("RUN->Run", spine.canon_mode("RUN") == "Run")
check("EM1 stays EM1", spine.canon_mode("EM1") == "EM1")
check("Stop 1 stays distinct", spine.canon_mode("Stop 1") == "Stop 1"
      and spine.canon_mode("Stop 2") == "Stop 2")

# ---- conditions typed parse
cond = spine.parse_conditions("VDD = 1.8 V to 3.6 V; VIN = 12 V; f = 500 kHz")
check("conditions parse vin", cond.get("vin") == 12.0)
check("conditions parse freq", cond.get("freq") == 500.0)

# ---- L2 fail-closed paths (fake PDF via monkeypatched cache)
class FakeCache:
    def __init__(self, pages): self.pages = pages
    def page_text(self, path, pg):
        if path == "missing.pdf":
            return "missing", None
        return "ok", self.pages

claim = dict(c1, claim_id="x1", source_path="real.pdf")
pages = {0: "Flash memory 64 kB program flash size", 1: "", -1: ""}
r = spine.l2_verify_claim(claim, FakeCache(pages))
check("verbatim on page verifies", r["verdict"] == "verified", str(r))
r = spine.l2_verify_claim(claim, FakeCache({0: "nothing relevant here"}))
check("verbatim not on page -> held", r["verdict"] == "held" and "verbatim_not_on_page" in r["reasons"])
r = spine.l2_verify_claim(claim, FakeCache({5: "Flash memory 64 kB"}))
check("no cited page -> held", r["verdict"] == "held")
r = spine.l2_verify_claim(dict(claim, source_path="missing.pdf"), FakeCache(pages))
check("missing source -> rejected", r["verdict"] == "rejected")
r = spine.l2_verify_claim(dict(claim, value_raw=7290, field="n_gpio", unit_raw="pins"),
                          FakeCache({0: "GPIO count 7290 pins printed here"}))
check("range violation held (7290 gpio)", r["verdict"] == "held" and "value_out_of_range" in r["reasons"], str(r))

# value None -> held
r = spine.l2_verify_claim(dict(claim, value_raw=None), FakeCache(pages))
check("no value -> held", r["verdict"] == "held" and "no_value" in r["reasons"])

# categorical fields verify on verbatim alone
r = spine.l2_verify_claim(dict(claim, field="package", value_raw="QFN30", unit_raw=None,
                               verbatim="QFN30 package marking"),
                          FakeCache({0: "device QFN30 package marking top view"}))
check("categorical package verifies", r["verdict"] == "verified", str(r))

# ---- L3 purity + preservation
v = {"claim_id": "x1", "verdict": "verified", "reasons": []}
claim_mode = dict(c1, lane="mode", field="RUN", value_raw=160, unit_raw="µA",
                  conditions_raw="VIN = 3.3 V")
n = spine.l3_normalize_claim(v, claim_mode)
check("L3 converts uA->A", n["value_canon"] == 0.00016 and n["unit_canon"] == "A")
check("L3 preserves original in claim chain", claim_mode["value_raw"] == 160)
check("L3 records transform rule", "canon-units-v1" in (n.get("transform") or ""))
check("L3 mode canon applied", n.get("mode_canon") == "Run")
n2 = spine.l3_normalize_claim(v, claim_mode)
check("L3 deterministic replay", n == n2)

# ---- L4 identity tiers
cat = {"STM32F103C8T6", "TPS54260DRBR"}
t = spine.identity_tier("STM32F103C8T6", cat)
check("exact opn binds", t["tier"] == "exact_opn")
t = spine.identity_tier("stm32f103c8t6", cat)
check("canonical alias binds", t["tier"] == "exact_opn" and t["opn"] == "STM32F103C8T6")
t = spine.identity_tier("STM32F103-family", cat)
check("family stays family", t["tier"] == "family")
t = spine.identity_tier("zzz-unknown-999", cat)
check("unknown slug -> document_slug tier (never OPN)", t["tier"] == "document_slug")
t = spine.identity_tier("STM32F103* family", cat)
check("wildcard/family marker stays family", t["tier"] == "family")
t = spine.identity_tier(None, cat)
check("no slug unresolved", t["tier"] == "unresolved")

# ---- L5 axis gating
a = {"unit_canon": "A", "mode_canon": "Run", "conditions_typed": {"vin": 3.3}, "conditions_raw": "VIN=3.3V"}
b_same = dict(a)
b_diff_cond = dict(a, conditions_typed={"vin": 5.0}, conditions_raw="VIN=5V")
b_diff_mode = dict(a, mode_canon="Sleep")
check("same conditions comparable", spine.axis_status(a, b_same, "mode")[0] == "comparable")
s, q = spine.axis_status(a, b_diff_cond, "mode")
check("vin difference qualified", s == "qualified" and "VIN" in q)
s, q = spine.axis_status(a, b_diff_mode, "mode")
check("different modes noncomparable", s == "noncomparable")
s, q = spine.axis_status(dict(a, unit_canon="W"), b_same, "mode")
check("unit mismatch noncomparable", s == "noncomparable")
ta = {"unit_canon": "C/W", "conditions_raw": "2-layer board"}
tb = {"unit_canon": "C/W", "conditions_raw": "4-layer board"}
s, q = spine.axis_status(ta, tb, "thermal")
check("thermal board difference qualified", s == "qualified")

print()
if FAILS:
    print(f"{len(FAILS)} FAILURES: {FAILS}")
    sys.exit(1)
print("ALL GATES PASS")
