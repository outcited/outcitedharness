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
c1 = {"pdf_sha": "aa11", "lane": "pillars", "field": "flash_kb", "value_raw": 64,
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
    def __init__(self, pages, sha="aa11"): self.pages = pages; self.sha = sha
    def page_text(self, path, pg, expect_sha=None):
        if path == "missing.pdf":
            return "missing", None, None
        if expect_sha and expect_sha != self.sha:
            return "sha_mismatch", None, self.sha
        if not isinstance(pg, int) or pg < 1 or pg not in self.pages:
            return "no_page", None, self.sha
        return "ok", {0: self.pages[pg]}, self.sha

claim = dict(c1, claim_id="x1", source_path="real.pdf")
PAGE_OK = "Flash memory 64 kB program flash size"
pages = {3: PAGE_OK}
r = spine.l2_verify_claim(claim, FakeCache(pages))
check("verbatim on exact page verifies", r["verdict"] == "verified", str(r))
r = spine.l2_verify_claim(claim, FakeCache({4: PAGE_OK}))
check("adjacent page does NOT verify (exact-page law)",
      r["verdict"] != "verified", str(r))
r = spine.l2_verify_claim(claim, FakeCache({3: "table of contents only", 4: PAGE_OK}))
check("content on neighbor, cited page empty -> not verified",
      r["verdict"] == "held", str(r))
r = spine.l2_verify_claim(dict(claim, pdf_sha="deadbeef"), FakeCache(pages))
check("sha mismatch -> rejected", r["verdict"] == "rejected" and "source_sha_mismatch" in r["reasons"], str(r))
r = spine.l2_verify_claim(dict(claim, value_raw=64, unit_raw="kB"),
                          FakeCache({3: "Flash memory 64 kB program flash"}))
check("value present in evidence", r["verdict"] == "verified", str(r))
r = spine.l2_verify_claim(dict(claim, value_raw=64, unit_raw="kB"),
                          FakeCache({3: "Flash memory 65 kB program flash size"}))
check("value absent from page -> held", r["verdict"] == "held" and "value_not_in_evidence" in r["reasons"], str(r))
r = spine.l2_verify_claim(claim, FakeCache({3: "nothing relevant here"}))
check("verbatim not on cited page -> held",
      r["verdict"] == "held" and "verbatim_not_on_cited_page" in r["reasons"], str(r))
r = spine.l2_verify_claim(claim, FakeCache({5: PAGE_OK}))
check("cited page absent from doc -> rejected (evidence broken)",
      r["verdict"] == "rejected", str(r))
r = spine.l2_verify_claim(dict(claim, source_path="missing.pdf"), FakeCache(pages))
check("missing source -> rejected", r["verdict"] == "rejected")
r = spine.l2_verify_claim(dict(claim, value_raw=7290, field="n_gpio", unit_raw="pins",
                               verbatim="7290 pins GPIO count"),
                          FakeCache({3: "GPIO count 7290 pins printed here"}))
check("range violation held (7290 gpio)", r["verdict"] == "held" and "value_out_of_range" in r["reasons"], str(r))

# value None -> held
r = spine.l2_verify_claim(dict(claim, value_raw=None), FakeCache(pages))
check("no value -> held", r["verdict"] == "held" and "no_value" in r["reasons"])

# categorical fields verify on verbatim alone
r = spine.l2_verify_claim(dict(claim, field="package", value_raw="QFN30", unit_raw=None,
                               verbatim="QFN30 package marking"),
                          FakeCache({3: "device QFN30 package marking top view"}))
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

# ---- L5 axis gating (hardened: exact identity, roles, empty-condition law)
EX = {"tier": "exact_opn"}
a = {"unit_canon": "A", "mode_canon": "Run", "tier": "exact_opn",
     "conditions_typed": {"vin": 3.3}, "conditions_raw": "VIN=3.3V", "value_role": "typ"}
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
s, q = spine.axis_status(dict(a, tier="family"), b_same, "mode")
check("identity not exact -> noncomparable", s == "noncomparable" and "identity" in q)
s, q = spine.axis_status(dict(a, conditions_typed={}, conditions_raw=None), b_same, "mode")
check("empty conditions NEVER comparable (mode)",
      s == "qualified" and "unspecified" in q, f"{s} {q}")
s, q = spine.axis_status(dict(a, value_role="max"), b_same, "mode")
check("role mismatch qualified", s == "qualified" and "role" in q)
ta = {"unit_canon": "C/W", "tier": "exact_opn", "conditions_raw": "2-layer board", "value_role": "typ"}
tb = dict(ta, conditions_raw="4-layer board")
s, q = spine.axis_status(ta, tb, "thermal")
check("thermal board difference qualified", s == "qualified")
s, q = spine.axis_status(dict(ta, conditions_raw=None), tb, "thermal")
check("thermal unspecified condition qualified", s == "qualified")
pa = {"unit_canon": "kB", "tier": "exact_opn", "value_role": None}
check("pillars {} conditions are inherent, comparable",
      spine.axis_status(pa, dict(pa), "pillars")[0] == "comparable")

print()
if FAILS:
    print(f"{len(FAILS)} FAILURES: {FAILS}")
    sys.exit(1)
print("ALL GATES PASS")
