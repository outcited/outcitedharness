"""Canonical vendor identity registry (PRD-FACET-02 R3).

Deterministic alias resolution for manufacturer identities. The corpus
carries the same vendor under several printed spellings (`infineon` vs
`infineon.com`, `ti` vs `ti.com` vs `texas_instruments`), which fragments
any vendor facet. This registry resolves those aliases to a canonical
identity while preserving the original value and the transformation
provenance on every resolution.

Fail-closed: placeholders (`unknown`, empty, null) and ambiguous tokens
stay UNRESOLVED rather than guessed. Adding an alias is a reviewed act —
the registry is the source of truth, not a fuzzy matcher.
"""

from __future__ import annotations

import re

VENDOR_REGISTRY_SCHEMA = "harness.search-vendor-registry.v1"

# canonical -> printed aliases (lowercased). Aliases must be unambiguous:
# a token that could name two manufacturers is never listed.
CANONICAL_VENDORS: dict[str, tuple[str, ...]] = {
    "infineon": ("infineon", "infineon.com", "infineon technologies"),
    "texas-instruments": ("ti", "ti.com", "texas instruments",
                          "texas_instruments", "texasinstruments"),
    "stmicroelectronics": ("st", "st.com", "stmicro", "stmicroelectronics",
                           "stmicro.com"),
    "microchip": ("microchip", "microchip.com", "microchip technology"),
    "nxp": ("nxp", "nxp.com", "nxp semiconductors"),
    "renesas": ("renesas", "renesas.com", "renesas electronics"),
    "rohm": ("rohm", "rohm.com", "rohm semiconductor"),
    "vishay": ("vishay", "vishay.com", "vishay intertechnology"),
    "analog-devices": ("adi", "analog", "analog.com", "analog devices",
                       "analog-devices"),
    "onsemi": ("on", "onsemi", "on semiconductor", "onsemi.com"),
    "silicon-labs": ("silabs", "silabs.com", "silicon labs",
                     "silicon-labs", "silicon_labs"),
    "espressif": ("esp", "espressif", "espressif.com", "espressif systems"),
    "gigadevice": ("gigadevice", "gigadevice.com", "gd"),
    "toshiba": ("toshiba", "toshiba.com"),
    "maxim": ("maxim", "maxim integrated", "maxim-ic"),
    "diodes-inc": ("diodes", "diodes.com", "diodes incorporated"),
    "wurth": ("wurth", "wuerth", "wurth.com", "wurth elektronik"),
    "molex": ("molex", "molex.com"),
    "te-connectivity": ("te", "te.com", "te connectivity",
                        "te_connectivity"),
    "amphenol": ("amphenol", "amphenol.com"),
    "jst": ("jst", "jst.com", "jst-mfg"),
    "samtec": ("samtec", "samtec.com"),
    "phoenix-contact": ("phoenix", "phoenixcontact", "phoenix contact",
                        "phoenix-contact"),
}

# Tokens that are explicitly NOT manufacturers — never resolved.
_UNRESOLVABLE = frozenset({"unknown", "n/a", "na", "none", "null", "-", ""})

_ALIAS_INDEX: dict[str, str] = {}
for _canonical, _aliases in CANONICAL_VENDORS.items():
    for _alias in _aliases:
        _ALIAS_INDEX.setdefault(_alias, _canonical)

# Ambiguity guard: any alias claimed by two canonicals is dropped entirely.
_seen: dict[str, int] = {}
for _aliases in CANONICAL_VENDORS.values():
    for _alias in _aliases:
        _seen[_alias] = _seen.get(_alias, 0) + 1
_AMBIGUOUS = {a for a, n in _seen.items() if n > 1}
for _alias in _AMBIGUOUS:
    _ALIAS_INDEX.pop(_alias, None)

_WS = re.compile(r"\s+")


def _norm(value: str | None) -> str:
    if value is None:
        return ""
    return _WS.sub(" ", str(value).strip().lower())


def resolve(value: str | None) -> dict:
    """Resolve one printed vendor value.

    Returns {original, canonical, resolved, rule}. `resolved` is False for
    placeholders, ambiguity, and unknowns — never guessed.
    """
    original = value
    token = _norm(value)
    if token in _UNRESOLVABLE:
        return {"original": original, "canonical": None, "resolved": False,
                "rule": "placeholder"}
    if token in _AMBIGUOUS:
        return {"original": original, "canonical": None, "resolved": False,
                "rule": "ambiguous"}
    if token in _ALIAS_INDEX:
        canonical = _ALIAS_INDEX[token]
        rule = "exact" if token == canonical else "alias"
        return {"original": original, "canonical": canonical,
                "resolved": True, "rule": rule}
    # A bare domain that matches no alias stays unresolved (do not strip
    # TLDs and guess — that would invent identities).
    return {"original": original, "canonical": None, "resolved": False,
            "rule": "unregistered"}


def canonical_or_none(value: str | None) -> str | None:
    return resolve(value)["canonical"]


def registry() -> dict:
    return {
        "schema": VENDOR_REGISTRY_SCHEMA,
        "canonical_vendors": {k: list(v) for k, v in CANONICAL_VENDORS.items()},
        "unresolvable_tokens": sorted(_UNRESOLVABLE),
        "ambiguous_tokens": sorted(_AMBIGUOUS),
    }