"""Power topology reader: printed topology + integration class (fill-only).

Discovery above the OPN needs the architecture layer: which topology a
part implements (buck, boost, flyback, ...) and its integration class
(integrated converter / controller + external FET / power module / power
stage). Neither is a selector axis; both are printed in titles, features,
and descriptions. This reader scans substrate page text (front pages) and
emits evidence-backed rows. Nothing is inferred from part-number
prefixes or vendor taxonomy — printed evidence only, fail-closed: an
answer that is not printed is ``absent``, never guessed.

Contract (gold: tests/fixtures/gold/power_topology_v1.jsonl):

``read_topology(pages, part_number=None)`` -> dict:
  topologies        list[str] — canonical names, possibly several
  integration_class "integrated" | "controller" | "module" |
                    "power_stage" | None
  isolated          True | False | None
  evidence          [{field, value, quote, page}]
  topology_absent / integration_absent — first-class answers

Rules, each named by the corpus trap that raised it:

- A topology word matches only with a head noun (converter, controller,
  regulator, module, switcher, topology, power stage, gate/LED driver,
  DC/DC) within a short window AFTER it, or a topology introducer
  ("Supports Topologies such as ...") shortly BEFORE it. Figure text
  like a stray ``boost`` or a CoolMOS application note's ``LLC`` must
  not flip the answer (LM2673, IPA60R120C7).
- Compound rule: a ``buck-boost`` print claims buck, boost AND
  buck-boost — a four-switch buck-boost does both. Documented
  derivation, applied uniformly.
- Integration class reads TITLE WINDOWS ONLY (text anchored after the
  part number on pages 1-3, plus the page-1 opening): body words
  "controller"/"module" are applications/figure context (TLV61070A
  "Remote controller", TPS54240-Q1 "GSM Modules", CSD97376 figure
  label, TPS53830 "DIMM module"). Priority:
  power_stage > module > controller > integrated.
- integrated additionally requires FET evidence: (integrated|internal)
  directly adjacent to (power)? FET/MOSFET/switch(es)/load switch, or a
  longer window backed by a power qualifier (power, high/low-side,
  output, N-channel, NFET, DMOS, GaN, Ω). "Integrated BOOT recharge
  FET" and "integrated VIN bypass capacitors" never claim (TPS54561,
  LMQ66420). "Incorporates ... MOSFET" is not integrated/internal
  printed evidence and stays absent (TPS2067).
- isolated: True when isolation prints (isolated / isolation barrier /
  kVrms isolation); False when ``non-isolated`` prints; None otherwise.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

TOPOLOGY_SCHEMA = "harness.electronics-power-topology.v1"

_SCAN_PAGES = 3
_WINDOW = 48
_INTRODUCER_BACK = 100
_TITLE_WINDOW = 250

_TOPO_VOCAB: tuple[tuple[str, str], ...] = (
    ("buck-boost", r"buck[\s\-/]?boost"),
    ("sepic", r"\bsepic\b"),
    ("flyback", r"fly\s?back"),
    ("forward", r"\bforward\b"),
    ("push-pull", r"push[\s\-]?pull"),
    ("half-bridge", r"half[\s\-]?bridge"),
    ("full-bridge", r"full[\s\-]?bridge"),
    ("llc", r"\bllc\b"),
    ("resonant", r"\bresonant\b"),
    ("charge-pump", r"charge[\s\-]?pump"),
    ("inverting", r"\binverting\b"),
    ("buck", r"\bbuck\b|step[\s\-]?down"),
    ("boost", r"\bboost\b|step[\s\-]?up"),
    ("ldo", r"low[\s\-]?dropout|\bldo(?:s)?\b"),
)
_HEAD_NOUN = (
    r"converters?|controllers?|regulators?|modules?|switchers?|topolog\w*|"
    r"power\s+stages?|conversion|gate\s+drivers?|led\s+drivers?|dc[\s\-]?dc"
)
_INTRODUCER = r"topolog\w*|supports\b|ideal\s+for\b|configur\w*|implement\w*"

_COMPILED_TOPO = tuple((re.compile(v, re.I), name) for name, v in _TOPO_VOCAB)
_HEAD_RE = re.compile(_HEAD_NOUN, re.I)
_INTRO_RE = re.compile(_INTRODUCER, re.I)

# "NON-ISOLATED" must not satisfy isolation (PTH04T241W)
_ISO_RE = re.compile(
    r"(?<!non-)(?<!non )isolated|"
    r"isolation\s+(?:barrier|rating|voltage)|"
    r"\d[\d,.]*\s*-?\s*(?:kv|vrms)\s+isolation", re.I)
_NON_ISO_RE = re.compile(r"non[\s\-]?isolated", re.I)

# class nouns inside title windows. Title prose has no articles: "the
# power stage", "the controller" is body/figure context (UCC28881), and
# application-list "GSM Modules" / "DIMM module" / "camera modules" /
# "HEV Power Modules" never set class (TPS54240-Q1, TPS53830, BU11TD3WG).
_TITLE_CLASS_WINDOW = 250
_CLASS_ARTICLE = re.compile(r"\b(?:the|an|a)\s*$")  # lowercase body articles
_CLASS_NEGATIVE = {
    "controller": re.compile(
        r"remote|motor|transmitter|most\b|multi[\s\-]phase", re.I),
    "module": re.compile(
        r"dimm|gsm|gprs|camera|motor|server|hev\b|ev\b|led\s*$", re.I),
    "power_stage": re.compile(r"form\b", re.I),
}
_TITLE_CLASS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"power\s+stages?", re.I), "power_stage"),
    (re.compile(r"\bmodules?\b|power\s+module", re.I), "module"),
    (re.compile(r"\bcontrollers?\b", re.I), "controller"),
)

# bare LDO(s) needs a head noun, a topology introducer, or a title
# qualifier before it (Ultra-Low-Noise LDO..., Three ... LDOs); pin
# labels "LDO BB" / "LDO5V" never claim (LMG3411R150)
_LDO_QUALIFIER = re.compile(
    r"\b(?:dual|triple|three|two|single|cmos|ultra|micropower|variable|"
    r"fixed|general|noise|iq|psrr|low|high|precision)\b|\d\s*$|\d$", re.I)

# FET integration evidence (anywhere in scanned pages). Direct adjacency
# for FET/MOSFET and power/load switches; bare switches need the window
# path plus a power qualifier (TPIC74100 "integrated switches for
# voltage-mode control" never claims).
_FET_DIRECT = re.compile(
    r"(?:integrated|internal)\s+(?:power\s+|single[\s\-]channel\s+)?"
    r"(?:fets?|mosfets?|load\s+switch|power\s+switch)\b", re.I)
_FET_WINDOW = re.compile(
    r"(?:integrated|internal)\s+[a-z0-9,\s\-ΩΩωµμ°%.]{0,30}"
    r"(?:fets?|mosfets?|switch(?:es)?)\b", re.I)
_POWER_WORD = re.compile(
    r"power|high[\s\-]?side|low[\s\-]?side|output|n[\s\-]?channel|"
    r"nfet|dmos|gan|ω|Ω|\d+(?:\.\d+)?\s*-?\s*a\b", re.I)


def _flatten(text: str) -> str:
    return " ".join(str(text or "").split())


def _title_spans(text: str, page_no: Any, part_number: str | None,
                 first_page: bool) -> list[tuple[int, int]]:
    """(start, end) spans in `text` where title nouns are trusted."""
    spans: list[tuple[int, int]] = []
    if first_page:
        spans.append((0, min(_TITLE_WINDOW, len(text))))
    if part_number:
        stem = re.escape(part_number)
        # family stems: trailing digits/letters often generalized (TPS56224x)
        family = re.sub(r"[0-9](?:[0-9A-Za-z]*)$", "", part_number)
        for pat in {stem, re.escape(family) if len(family) >= 4 else stem}:
            for m in re.finditer(pat, text, re.I):
                spans.append((m.end(), m.end() + _TITLE_WINDOW))
    return spans


def read_topology(pages: Sequence[Mapping[str, Any]],
                  part_number: str | None = None) -> dict[str, Any]:
    """Read topology/integration/isolation evidence from front page texts."""
    flat_pages = [(_flatten(p.get("text")), p.get("page"))
                  for p in pages[:_SCAN_PAGES]]
    evidence: list[dict[str, Any]] = []
    topologies: list[str] = []
    derived: set[str] = set()
    integration: str | None = None
    isolated: bool | None = None

    for idx, (text, page_no) in enumerate(flat_pages):
        compound_spans: list[tuple[int, int]] = []
        for rx, name in _COMPILED_TOPO:
            if name in topologies:
                continue
            for m in rx.finditer(text):
                # bare LDO(s) needs a qualifier before it; "low-dropout"
                # phrases and head-noun matches take the noun path below
                qualified_ldo = (
                    name == "ldo"
                    and m.group(0).upper() in ("LDO", "LDOS")
                    and _LDO_QUALIFIER.search(
                        text[max(0, m.start() - 30):m.start()]))
                if qualified_ldo or _HEAD_RE.search(
                        text[m.end():m.end() + _WINDOW]) or (
                        _INTRO_RE.search(
                            text[max(0, m.start() - _INTRODUCER_BACK):m.start()])):
                    # quote includes the justifying noun/introducer context
                    quote = text[m.start():m.end() + _WINDOW]
                    evidence.append({
                        "field": "topology", "value": name,
                        "quote": quote[:120], "page": page_no,
                    })
                    if name == "buck-boost":
                        compound_spans.append(m.span())
                    elif name in ("buck", "boost"):
                        for b, e in compound_spans:
                            if m.start() < e and m.end() > b:
                                derived.add(name)
                                break
                    topologies.append(name)
                    break

    # compound rule: buck/boost claimed only via a "Buck-Boost" print are
    # DERIVED components (Class-2), kept in topologies but flagged so a
    # consumer can tell a printed buck from a derived-from-buck-boost buck
    topologies_derived = sorted(derived & set(topologies))

    # integration class from title windows only (title prose has no
    # articles; application-list nouns never set class)
    for idx, (text, page_no) in enumerate(flat_pages):
        for b, e in _title_spans(text, page_no, part_number, idx == 0):
            head = text[b:min(b + _TITLE_CLASS_WINDOW, e)]
            for rx, value in _TITLE_CLASS:
                m = rx.search(head)
                if not m:
                    continue
                before = head[max(0, m.start() - 24):m.start()]
                if _CLASS_ARTICLE.search(before):
                    continue
                if _CLASS_NEGATIVE.get(value) and _CLASS_NEGATIVE[value].search(
                        before):
                    continue
                evidence.append({
                    "field": "integration_class", "value": value,
                    "quote": text[max(0, b + m.start() - 30):
                                  b + m.end() + 30][:120],
                    "page": page_no,
                })

    classes = [e["value"] for e in evidence if e["field"] == "integration_class"]
    for cls in ("power_stage", "module", "controller"):
        if cls in classes:
            integration = cls
            break
    if integration is None and classes:
        integration = classes[0]

    # integrated via FET evidence anywhere in the scanned pages
    if integration is None:
        for text, page_no in flat_pages:
            m = _FET_DIRECT.search(text)
            if m:
                integration = "integrated"
                evidence.append({
                    "field": "integration_class", "value": "integrated",
                    "quote": text[max(0, m.start() - 30):m.end() + 30][:120],
                    "page": page_no,
                })
                break
            m = _FET_WINDOW.search(text)
            if m and _POWER_WORD.search(m.group(0)):
                integration = "integrated"
                evidence.append({
                    "field": "integration_class", "value": "integrated",
                    "quote": text[max(0, m.start() - 30):m.end() + 30][:120],
                    "page": page_no,
                })
                break

    # isolation
    for text, page_no in flat_pages:
        if isolated is None:
            m = _ISO_RE.search(text)
            if m:
                isolated = True
                evidence.append({
                    "field": "isolated", "value": True,
                    "quote": text[max(0, m.start() - 40):m.end() + 40][:120],
                    "page": page_no,
                })
                continue
            m = _NON_ISO_RE.search(text)
            if m:
                isolated = False
                evidence.append({
                    "field": "isolated", "value": False,
                    "quote": text[max(0, m.start() - 40):m.end() + 40][:120],
                    "page": page_no,
                })

    return {
        "topologies": topologies,
        "topologies_derived": topologies_derived,
        "integration_class": integration,
        "isolated": isolated,
        "evidence": evidence,
        "topology_absent": not topologies,
        "integration_absent": integration is None,
    }


__all__ = ["TOPOLOGY_SCHEMA", "read_topology"]
