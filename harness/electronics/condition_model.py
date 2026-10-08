"""Typed condition model for power claims (fill-only, fail-closed).

The corpus stores test conditions as ``condition_verbatim`` strings
(``VGS = 0 V, VDS = 25 V, f = 1 MHz``). Discovery needs them typed
(``{vgs_v: 0, vds_v: 25, f_hz: 1e6}``) to answer, deterministically and
at query time, whether a claim's evidence condition covers a design
requirement's condition — the spec's "condition mismatch / insufficient
evidence, never FAIL" law. This module does that typing without ever
replacing the verbatim string, which stays the provenance anchor.

Contract (gold: tests/fixtures/gold/condition_model_v1.jsonl):

- ``parse_condition(text)`` -> dict, one of:
  {"reject": cls} for dash / no_anchors / units_absent / ambiguous_symbols
  {"keys": {...}, "mode"?, "approx"?, "sym"?, "bare"?, "comparators"?,
   "residue"?} — optional fields present only when non-empty.

- keys are canonical SI-typed anchors (``vgs_v``, ``id_a``, ``ta_c``,
  ``f_hz`` ...). A value is a float, or a two-element list for a printed
  range/list (``0 to 10 V``, ``0V to 480V``, ``0/18 V``,
  ``1.0V≦VOUT＜1.2V``).

Laws, per the corpus rules that raised them:

- Never invent units. A known symbol with a bare number and no unit
  (``VGS=0 ,VDS=80``) rejects as ``units_absent`` — it never becomes 0 V.
- Single-letter symbols (``V =12 V``, ``I =20 A``) are subscript-lost and
  ambiguous: ``ambiguous_symbols``, never typed.
- Bare number+V (``4.5V``) has no symbol and cannot be VGS vs VDS vs
  VIN: stays residue. Bare number+°C is unambiguous in kind and types as
  ``t_c`` flagged ``bare``.
- First parsable occurrence of a symbol wins; later duplicates stay in
  residue verbatim (``diode: body diode at VGS = 0 V``).
- Separators ``=`` / comparators (≤ ≥ < > ≦ ≧ ＜ ＞) / approx glyphs
  (≈ ⋍) all bind a value. Comparators type their number and record the
  verbatim segment in ``comparators``; approx flags the key in ``approx``;
  ``±`` types the magnitude and flags the key in ``sym``.
- Mode words {static, ac, dynamic} type to ``mode``; everything else
  unrecognized stays in ``residue`` verbatim.

``condition_covers(claim_keys, requirement_keys)`` compares two typed
key maps per the discovery elimination law:
  exact     — same keys, values agree, none strictly wider
  covers    — claim agrees on every required key and is a superset
              (extra keys, or a range strictly containing the requirement)
  partial   — shared keys agree, claim missing some required keys
  mismatch  — a shared key disagrees
  absent    — no shared keys (or claim untyped)
"""

from __future__ import annotations

import re
from typing import Any, Mapping

CONDITION_MODEL_SCHEMA = "harness.electronics-condition-model.v1"

_NUM = r"[-+]?\d+(?:\.\d+)?"
_SEP_CLASS = r"=≤≥≦≧<>＜＞"
_SEP_RE = re.compile(rf"\s*[{_SEP_CLASS}]\s*|\s*[≈⋍]\s*")
_RANGE_SEP = r"(?:to|/|~|–|—)"

_TEMP_UNIT = r"(?:°\s*C(?![A-Za-z])|℃|º\s*C(?![A-Za-z]))"

# --- unit scales ------------------------------------------------------------
_V_MULT = {"mv": 1e-3, "v": 1.0, "kv": 1e3}
_A_MULT = {"na": 1e-9, "µa": 1e-6, "ua": 1e-6, "ma": 1e-3, "ka": 1e3, "a": 1.0}
_HZ_MULT = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}
_OHM_MULT = {"mohm": 1e-3, "ohm": 1.0, "kohm": 1e3}
_H_MULT = {"nh": 1e-9, "µh": 1e-6, "uh": 1e-6, "mh": 1e-3, "h": 1.0}
_A_PER_S_MULT = {"µs": 1e6, "us": 1e6, "ns": 1e9, "ms": 1e3, "s": 1.0}

# --- symbol vocabulary (order matters) --------------------------------------
# (symbol regex WITHOUT separator, canonical key, kind)
_SYMBOLS: tuple[tuple[str, str, str], ...] = (
    (r"di\s*F\s*/\s*dt", "difdt_a_per_s", "a_per_s"),
    (r"V\s*_?\s*GS\s*\(\s*on\s*\)", "vgs_on_v", "v"),
    (r"V\s*_?\s*DS\s*,\s*peak", "vds_peak_v", "v"),
    (r"T\s*_?\s*vj\s*\(\s*start\s*\)", "tvj_start_c", "c"),
    (r"R\s*_?\s*(?:th|θ)?\s*_?\s*JA", "rthja_c_per_w", "c_per_w"),
    (r"R\s*_?\s*G\s*,\s*ext", "rg_ext_ohm", "ohm"),
    (r"L\s*σ", "lsigma_h", "h"),
    (r"\bSTBY", "stby_v", "v"),
    (r"\bI\s*_?\s*SINK", "isink_a", "a"),
    (r"\bI\s*_?\s*SD", "isd_a", "a"),
    (r"\bI\s*_?\s*REG", "ireg_a", "a"),
    (r"\bI\s*_?\s*AS", "i_as_a", "a"),
    (r"\bI\s*_?\s*OUT", "iout_a", "a"),
    (r"\bI\s*_?\s*DS", "id_a", "a"),
    (r"\bI\s*_?\s*O\b", "iout_a", "a"),
    (r"\bI\s*_?\s*F\b", "if_a", "a"),
    (r"\bI\s*_?\s*S\b", "is_a", "a"),
    (r"\bI\s*_?\s*D", "id_a", "a"),
    (r"\bV\s*_?\s*OUT", "vout_v", "v"),
    (r"\bV\s*_?\s*IN\b", "vin_v", "v"),
    (r"\bV\s*_?\s*CC", "vcc_v", "v"),
    (r"\bV\s*_?\s*DD", "vdd_v", "v"),
    (r"\bV\s*_?\s*EN\b", "ven_v", "v"),
    (r"\bV\s*_?\s*SD", "vsd_v", "v"),
    (r"\bV\s*_?\s*GS", "vgs_v", "v"),
    (r"\bV\s*_?\s*DS", "vds_v", "v"),
    (r"\bVR", "vr_v", "v"),
    (r"\bVO\b", "vout_v", "v"),
    (r"\bT\s*_?\s*STG", "tstg_c", "c"),
    (r"\bT\s*_?\s*vj", "tvj_c", "c"),
    (r"\bT\s*_?\s*A\b", "ta_c", "c"),
    (r"\bT\s*_?\s*C\b", "tc_c", "c"),
    (r"\bT\s*_?\s*J\b", "tj_c", "c"),
    (r"\bf\s*sw", "f_hz", "hz"),
    (r"\bf\s*osc", "f_hz", "hz"),
    (r"\bf\s*rr", "frr_hz", "hz"),
    (r"\b[fƒ]", "f_hz", "hz"),
    (r"\bR\s*_?\s*GS", "rgs_ohm", "ohm"),
    (r"\bR\s*_?\s*G\b", "rg_ohm", "ohm"),
    (r"\bL\b", "l_h", "h"),
    (r"\bR\b", "r_ohm", "ohm"),
)
_COMPILED_SYMBOLS = tuple((re.compile(p, re.I), k, kind)
                          for p, k, kind in _SYMBOLS)

_UNIT_RE = {
    "v": r"(?:mV|kV|V)\b",
    "a": r"(?:nA|µA|μA|uA|mA|kA|A)\b",
    "hz": r"(?:kHz|MHz|GHz|Hz)\b",
    "c": _TEMP_UNIT,
    "c_per_w": r"(?:(?:°\s*C|℃|º\s*C|K)\s*/\s*W)",
    "ohm": r"(?:mΩ|kΩ|MΩ|Ω|mohm|kohm|ohm)\b",
    "h": r"(?:nH|µH|μH|uH|mH|H)\b",
    "a_per_s": r"(?:A\s*/\s*(?:µs|μs|us|ns|ms|s))\b",
}
_PREFIX = {"v": "v", "a": "a", "hz": "hz", "c": "c",
           "c_per_w": "cw", "ohm": "o", "h": "ind", "a_per_s": "dds"}
_CMP_CHARS = "≤≥≦≧<>＜＞"


def _value_regex(kind: str) -> re.Pattern:
    p = _PREFIX[kind]
    u = _UNIT_RE[kind]
    # [±] NUM unit? (sep NUM unit?)? unit?  — unit may sit on lo, on hi,
    # or after both ("0V to 480V" / "0 to 10 V" / "0/18 V").
    # re.I: catalog condition_norm is lowercase-normalized ("tc = 25 °c");
    # case collapse already happened upstream, so mV/MV-class ambiguity
    # resolves to the milli reading (dominant in power tables).
    return re.compile(
        rf"[±]?\s*"
        rf"(?P<{p}_lo>{_NUM})\s*(?P<{p}_lu>{u})?"
        rf"(?:\s*(?P<{p}_sep>{_RANGE_SEP})\s*(?P<{p}_hi>{_NUM})\s*(?P<{p}_hu>{u})?)?"
        rf"(?:\s*(?P<{p}_unit>{u}))?",
        re.I,
    )


_VALUE_RE = {kind: _value_regex(kind) for kind in _PREFIX}

# reversed bound/range: "2.5 V≤VOUT", "1.0V≦VOUT＜1.2V" — scanned BEFORE
# symbol anchors so the bound value is not stolen as a plain anchor
_REV_BOUND = re.compile(
    rf"(?P<rb_lo>{_NUM})\s*(?P<rb_lu>mV|kV|V)\s*[≤≥≦<＜]\s*"
    rf"(?P<rb_sym>V\s*_?\s*(?:OUT|O|IN|DD|CC|DS|GS))"
    rf"(?:\s*[≤≥≦<＜]\s*(?P<rb_hi>{_NUM})\s*(?P<rb_hu>mV|kV|V))?",
    re.I,
)
_REV_KEY = {"VOUT": "vout_v", "VO": "vout_v", "VIN": "vin_v", "VDD": "vdd_v",
            "VCC": "vcc_v", "VDS": "vds_v", "VGS": "vgs_v"}
_V_MULT_BY_TOKEN = {"mv": 1e-3, "v": 1.0, "kv": 1e3}

_BARE_TEMP = re.compile(
    rf"(?<![\w.])(?P<bt_lo>{_NUM})\s*(?:~|–|—|to)?\s*"
    rf"(?:(?P<bt_mid>{_NUM})\s*)?(?P<bt_u>{_TEMP_UNIT})(?![\w/])"
)

_DASH_ONLY = re.compile(r"^[\s\-–—―‒]+$")
_MODE_RE = re.compile(r"\b(static|ac|dynamic)\b", re.I)
_AMBIG_SINGLE = re.compile(rf"\b[VI]\s*[{_SEP_CLASS}]\s*[-+]?\d")
_PUNCT_ONLY = re.compile(r"^[\s\-–—.();:]*$")


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def _unit_token(text: str) -> str:
    return (re.sub(r"\s+", "", text or "")
            .replace("μ", "µ").replace("Ω", "ohm"))


def _scale(kind: str, unit_text: str | None) -> float | None:
    if unit_text is None:
        return None
    tok = _unit_token(unit_text).lower()
    if kind in ("c", "c_per_w"):
        return 1.0
    if kind == "a_per_s":
        m = re.match(r"a/(µs|us|ns|ms|s)", tok)
        return _A_PER_S_MULT[m.group(1)] if m else None
    table = {"v": _V_MULT, "a": _A_MULT, "hz": _HZ_MULT,
             "ohm": _OHM_MULT, "h": _H_MULT}
    return table[kind].get(tok)


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < e and end > b for b, e in spans)


def parse_condition(text: str) -> dict[str, Any]:
    """Parse a printed test condition into typed anchors, fail-closed."""
    s = _clean(text)
    if not s or _DASH_ONLY.match(s):
        return {"reject": "dash"}

    spans: list[tuple[int, int]] = []
    keys: dict[str, Any] = {}
    approx: list[str] = []
    sym: list[str] = []
    comparators: list[str] = []
    mode: str | None = None
    bare: list[str] = []
    unitless_hit = False

    mode_m = _MODE_RE.search(s)
    if mode_m:
        mode = mode_m.group(1).lower()
        spans.append(mode_m.span())

    # reversed bound/range first, so symbol anchors cannot steal their values
    for m in _REV_BOUND.finditer(s):
        sym_norm = re.sub(r"[\s_]+", "", m.group("rb_sym")).upper()
        key = _REV_KEY.get(sym_norm)
        if not key or key in keys or _overlaps(m.start(), m.end(), spans):
            continue
        lo = float(m.group("rb_lo")) * _V_MULT_BY_TOKEN[
            m.group("rb_lu").lower()]
        hi = m.group("rb_hi")
        if hi is not None:
            keys[key] = [lo, float(hi) * _V_MULT_BY_TOKEN[
                m.group("rb_hu").lower()]]
        else:
            keys[key] = lo
        comparators.append(_clean(m.group(0)))
        spans.append(m.span())

    # symbol-anchored scans
    for anchor, key, kind in _COMPILED_SYMBOLS:
        if key in keys:
            continue
        p = _PREFIX[kind]
        for m in anchor.finditer(s):
            tail = s[m.end():]
            sm = _SEP_RE.match(tail)
            if not sm:
                continue
            vm = _VALUE_RE[kind].match(tail, sm.end())
            if vm is None:
                continue
            lo_mult = _scale(kind, vm.group(f"{p}_lu") or vm.group(f"{p}_unit"))
            if vm.group(f"{p}_lu") is None and vm.group(f"{p}_unit") is None \
                    and vm.group(f"{p}_hu") is None:
                if re.match(rf"\s*{_NUM}\s*(?:[,;]|$)",
                            tail[sm.end():]):
                    unitless_hit = True
                continue
            if lo_mult is None:  # hi-unit-only range: lo shares hi's scale
                lo_mult = _scale(kind, vm.group(f"{p}_hu"))
            if lo_mult is None:
                continue
            lo = float(vm.group(f"{p}_lo"))
            hi_g = vm.group(f"{p}_hi")
            hi_mult = _scale(kind, vm.group(f"{p}_hu") or vm.group(f"{p}_unit")) \
                or lo_mult
            end_abs = m.end() + vm.end()  # vm.end() is absolute within tail
            if _overlaps(m.start(), end_abs, spans):
                continue
            value: float | list[float] = (
                [lo * lo_mult, float(hi_g) * hi_mult] if hi_g is not None
                else lo * lo_mult
            )
            seg = s[m.start():end_abs]
            sep_txt = sm.group(0).strip()
            if sep_txt and sep_txt[0] in _CMP_CHARS:
                comparators.append(seg)
            if sep_txt and ("≈" in sep_txt or "⋍" in sep_txt):
                approx.append(key)
            if "±" in seg:
                sym.append(key)
            keys[key] = value
            spans.append((m.start(), end_abs))
            break  # first parsable occurrence wins

    # bare temperatures: unambiguous in kind -> t_c, flagged bare
    for m in _BARE_TEMP.finditer(s):
        if "t_c" in keys or _overlaps(m.start(), m.end(), spans):
            continue
        lo = float(m.group("bt_lo"))
        mid = m.group("bt_mid")
        keys["t_c"] = [lo, float(mid)] if mid is not None else lo
        bare.append("t_c")
        spans.append(m.span())

    if keys or mode:
        out: dict[str, Any] = {"keys": keys}
        if mode:
            out["mode"] = mode
        for name, val in (("approx", approx), ("sym", sym), ("bare", bare),
                          ("comparators", comparators),
                          ("residue", _residue(s, spans))):
            if val:
                out[name] = val
        return out

    if unitless_hit:
        return {"reject": "units_absent"}
    if _AMBIG_SINGLE.search(s):
        return {"reject": "ambiguous_symbols"}
    return {"reject": "no_anchors"}


def _residue(s: str, spans: list[tuple[int, int]]) -> list[str]:
    dead = bytearray(len(s))
    for b, e in spans:
        for i in range(b, min(e, len(s))):
            dead[i] = 1
    chunks: list[str] = []
    cur: list[str] = []
    for i, ch in enumerate(s):
        if dead[i]:
            if cur:
                chunks.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        chunks.append("".join(cur))
    out: list[str] = []
    for chunk in chunks:
        for part in re.split(r"[,;]+", chunk):
            p = _clean(part)
            if p and not _PUNCT_ONLY.fullmatch(p):
                out.append(p)
    return out


# --- comparator ---------------------------------------------------------------

def _as_range(v: Any) -> tuple[float, float]:
    if isinstance(v, list):
        return (float(min(v)), float(max(v)))
    return (float(v), float(v))


def _eps() -> float:
    return 1e-9


def _values_agree(claim_val: Any, req_val: Any) -> bool:
    cb, ce = _as_range(claim_val)
    rb, re_ = _as_range(req_val)
    e = _eps() * max(1.0, abs(cb), abs(ce), abs(rb), abs(re_))
    return cb <= re_ + e and rb <= ce + e  # overlap


def _claim_strictly_wider(claim_val: Any, req_val: Any) -> bool:
    cb, ce = _as_range(claim_val)
    rb, re_ = _as_range(req_val)
    e = _eps() * max(1.0, abs(cb), abs(ce), abs(rb), abs(re_))
    wider = cb <= rb + e and ce >= re_ - e
    strictly = cb < rb - e or ce > re_ + e
    return wider and strictly


def condition_covers(claim_keys: Mapping[str, Any] | None,
                     requirement_keys: Mapping[str, Any] | None) -> str:
    """How a claim's typed condition stands against a requirement's.

    exact | covers | partial | mismatch | absent (never invented).
    """
    claim = dict(claim_keys or {})
    req = dict(requirement_keys or {})
    if not claim:
        return "absent"
    if not req:
        return "exact"
    shared = set(claim) & set(req)
    if not shared:
        return "absent"
    for k in shared:
        if not _values_agree(claim[k], req[k]):
            return "mismatch"
    if shared != set(req):
        return "partial"
    if set(claim) == set(req) and not any(
            _claim_strictly_wider(claim[k], req[k]) for k in shared):
        return "exact"
    return "covers"


__all__ = [
    "CONDITION_MODEL_SCHEMA",
    "condition_covers",
    "parse_condition",
]
