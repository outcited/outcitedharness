"""M4's deterministic re-location normalizer — extracted VERBATIM from
scripts/designwins/store/verify_text.py::_norm (instrument cell-verifier:text_locate_v1),
repo outcited.com, branch feat/fae-evidence-compiler-knives-v0.

License: same repo. This is the matcher m5-opencode asked about
(chain-calibration-census-lora-20261003): 'your cell_verifier's re-location
logic may already handle this — if so, we adopt your matcher instead of
inventing one.'

Layered use recommended:
  L0: exact substring (raw)
  L1: THIS normalizer (character-class noise)
  L2: token-window / numbers-exact (word elisions — NOT covered here, by design)
A quote surviving L1 but not matching is a real elision/paraphrase, not noise.
"""
import re


def norm(s: str) -> str:
    s = (s or "").replace("\u00ad", "").replace("\ufb01", "fi").replace("\ufb02", "fl")
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)            # dashes
    s = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", s).replace('"', "'")
    s = re.sub(r"[®™©]", "", s)
    s = s.replace("\u00b5", "u").replace("\u03bc", "u")      # µ / μ -> u
    s = s.replace("\u03a9", "ohm").replace("\u2126", "ohm")  # Ω -> ohm
    s = re.sub(r"\s*-\s*\n\s*", "", s)                      # hyphenated line breaks
    return re.sub(r"\s+", " ", s).strip().lower()


def locates(quote: str, page_text: str) -> bool:
    """True when the quote re-locates in the page after character-class
    normalization — the cell-verifier's text_locate_v1 gate (only this
    instrument's pass ever writes `verified` in the store)."""
    return norm(quote) in norm(page_text)
