#!/usr/bin/env python3
"""Extract the page-1 vendor one-liner for the power-description queue.

CR power-description-extraction-20260911: verbatim vendor copy from page 1
only — never invented, never paraphrased. One record per part, joined to the
pairs corpus by pdf_sha256 where available. A page whose best line does not
meet the stated floor (32+ chars with a digit, aisle_gold.valid_power_
description) records no_description_line honestly, but the raw best line is
kept so CR's locked validator can rule without re-opening the PDF.

    python3 scripts/extract_power_descriptions.py \
        --queue /Volumes/M5_4TB/exports/cr_requests/power-description-queue-20260911.json \
        --pdf-root /tmp/power-vendor-full/infineon \
        --pdf-root /tmp/power-vendor-full/rohm \
        --pairs /Volumes/M5_4TB/exports/cr_drops/power-pairs-20260909/pairs-infineon.jsonl \
        --pairs /Volumes/M5_4TB/exports/cr_drops/power-pairs-20260909/pairs-rohm.jsonl \
        --out descriptions.jsonl --missing-out missing.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pymupdf

DOMAIN_PREFIX = {
    "ti.com": "ti", "infineon.com": "infineon", "rohm.com": "rohm", "st.com": "st",
    "analog.com": "analog", "microchip.com": "microchip", "renesas.com": "renesas",
    "onsemi.com": "onsemi", "monolithicpower.com": "monolithicpower", "nxp.com": "nxp",
}

# Page-1 lines that are never the vendor one-liner.
BOILERPLATE = re.compile(
    r"datasheet|data\s*sheet|^\s*features\b|^\s*description$|general\s+description|product\s+description|"
    r"outline|revision|^\s*rev\b|\bwww\.|https?://|copyright|all\s+rights|"
    r"infineon\s+technologies|rohm\s+co|texas\s+instruments|product\s+structure|"
    r"monolithic\s+integrated|sourcing|interconnection|solderable|^\s*[•l○·]\s*|"
    r"product\s+validation|preliminary|engineering\s+sample|"
    r"rds\s*\(\s*on\s*\)|\bvdss\b|\bidm\b|\bvgs\b|"
    r"^\s*applications?\s*$|^\s*connection\s+diagrams?\s*$|^\s*pin\s+(configuration|assignments?)\s*$|"
    r"^\s*description\s*/\s*ordering\s+information|^\s*ordering\s+information|"
    r"^\s*(not\s+)?recommended\s+for\s+new\s+designs?\s*$|^\s*synchronization\s*$|"
    r"^\s*protection\s*$|^\s*amplifiers?\s*$|^\s*mobile\s+devices\s*$|"
    r"^\s*(simplified|typical|functional)\s+(schematic|application|applications|block\s+diagram|design)\b|"
    r"^\s*(application\s+example|block\s+diagram|output\s+voltage\s+ripple|typical\s+operating\s+circuit)\b|"
    r"^\s*\d+\s+(features?|description|applications?)\b|"
    r"^\s*(absolute\s+maximum\s+(ratings?|conditions?)|electrical\s+characteristics|thermal\s+(information|characteristics)|"
    r"package\s+(information|outline)|ordering\s+information|revision\s+history|"
    r"device\s+comparison|schematics?|applications?\s+information)\s*$|"
    r"^\s*qualified\s+for\s+automotive|^\s*related\s+literature\s*$",
    re.I,
)
MIN_LINE_CHARS = 10
FLOOR_CHARS = 32
TAGLINE_MIN_SIZE = 11.0
TAGLINE_MAX_GAP = 40.0


def meets_floor(line: str) -> bool:
    """CR's stated floor: 32+ chars with a digit (class labels without a
    digit fail). The locked validator is CR's; this is the stated reading."""
    return len(line) >= FLOOR_CHARS and any(c.isdigit() for c in line)


_CTRL_AS_SPACE = re.compile(r"[\x00-\x1f]")


def _is_garbled(text: str) -> bool:
    """CID/Symbol-font pages extract as control-character soup. Broken-but-
    readable encodings use single control chars as spaces (Infineon maps the
    space to \\x03), so controls are normalised to spaces first; a line that
    is still not mostly letters and digits is not vendor copy."""
    cleaned = _CTRL_AS_SPACE.sub(" ", text)
    if not cleaned.strip():
        return True
    sane = sum(1 for c in cleaned if c.isalnum() or c.isspace() or c in ",.-–—/()™®%+±:;'\"&")
    return sane / len(cleaned) < 0.6


def _page_lines(page) -> list[dict]:
    lines = []
    height = page.rect.height
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = _CTRL_AS_SPACE.sub(" ", "".join(s["text"] for s in line["spans"])).strip()
            if not text or _is_garbled(text):
                continue
            y = line["bbox"][1]
            if y > height - 55:  # footer: page numbers, copyright, doc ids
                continue
            lines.append({"text": text, "y": y, "x": line["bbox"][0], "x1": line["bbox"][2], "size": max(s["size"] for s in line["spans"]), "h": height})
    lines.sort(key=lambda l: (l["y"], l["x"]))
    return lines


def _is_part_numberish(text: str, part_number: str) -> bool:
    """True when the line is just a device label, not vendor copy. Catches
    exact matches and near-variants that differ only by a package/suffix
    token (IRFS4227 row vs "IRFSL4227PbF" label: the letters of the row's
    part number appear in order inside a short line)."""
    bare = re.sub(r"[^a-z0-9]", "", text.lower())
    part = re.sub(r"[^a-z0-9]", "", part_number.lower())
    if not part:
        return False
    if part in bare and len(bare) <= len(part) + 4:
        return True
    # Subsequence match: part number's chars appear in order and the line is
    # short and has no lowercase prose words.
    it = iter(bare)
    if all(ch in it for ch in part) and len(bare) <= len(part) + 6:
        words = re.findall(r"[A-Za-z]{3,}", text)
        if len(words) <= 2:
            return True
    return False


_BULLET_START = re.compile(r"^\s*(?:[•l○·*\u2022\u25cf\u2023\u2043]|[-–—]\s)", re.I)
_FEATURES_BAND = re.compile(r"^\s*features?\b|^\s*key\s+features\b|^\s*applications?\b|^\s*benefits?\b", re.I)


def _alnum_density(text: str) -> float:
    t = text.strip()
    return (sum(1 for c in t if c.isalnum()) / len(t)) if t else 0.0


def _merge_tagline(lines: list[dict], start: int) -> str:
    """Merge same-size, vertically-adjacent tagline lines in the same column
    (ROHM prints '35V Voltage Resistance' / '1A LDO Regulators' as a two-line
    tagline). The x guard keeps two-column pages from merging across columns;
    the walk stops at boilerplate, bullet lines, and feature/application band
    headers so a tagline never splices into the FEATURES block below it."""
    first = lines[start]
    parts = [first["text"]]
    y, x = first["y"], first["x"]
    for nxt in lines[start + 1:]:
        if (
            0 <= nxt["y"] - y <= TAGLINE_MAX_GAP
            and abs(nxt["size"] - first["size"]) <= 1.0
            and abs(nxt["x"] - x) <= 30.0
            and len(nxt["text"]) >= MIN_LINE_CHARS
        ):
            if (
                BOILERPLATE.search(nxt["text"])
                or _BULLET_START.match(nxt["text"])
                or _FEATURES_BAND.match(nxt["text"])
                or len(" ".join(parts)) + len(nxt["text"]) > 240
            ):
                break
            parts.append(nxt["text"])
            y, x = nxt["y"], nxt["x"]
        else:
            break
    return " ".join(parts)


def _first_sentence(text: str) -> str:
    m = re.match(r".{20,}?\.(?=\s|$)", text, re.S)
    return (m.group(0) if m else text).strip()


def _visual_segments(lines: list[dict]) -> list[dict]:
    """Reconstruct visual text segments: y-bucket the extraction entries,
    sort by x, and split where the whitespace between printed extents
    exceeds 25pt (a column gap, not a word space). Justified text extracts
    as word-level fragments; this restores the printed line per column."""
    buckets: list[dict] = []
    for ln in lines:
        if buckets and abs(ln["y"] - buckets[-1]["y"]) <= 2.5:
            buckets[-1]["parts"].append(ln)
        else:
            buckets.append({"y": ln["y"], "parts": [ln]})
    segments = []
    for b in buckets:
        parts = sorted(b["parts"], key=lambda p: p["x"])
        seg_parts = [parts[0]]
        for part in parts[1:]:
            if part["x"] - seg_parts[-1]["x1"] > 25.0:
                segments.append({"y": b["y"], "x": seg_parts[0]["x"], "text": " ".join(p["text"] for p in seg_parts)})
                seg_parts = [part]
            else:
                seg_parts.append(part)
        segments.append({"y": b["y"], "x": seg_parts[0]["x"], "text": " ".join(p["text"] for p in seg_parts)})
    segments.sort(key=lambda s: (s["y"], s["x"]))
    return segments


def _paragraph_from_segments(segments: list[dict], anchor_x: float, start_y: float) -> str | None:
    """First sentence from the column anchored at anchor_x, beginning at
    start_y (the header line, or the opener line itself). Stops at
    bullet-like fragments, boilerplate, and visual paragraph breaks."""
    paragraph = []
    prev_y = None
    for seg in segments:
        if seg["y"] < start_y - 5.0 or abs(seg["x"] - anchor_x) > 30.0:
            continue
        text = seg["text"]
        if prev_y is not None and seg["y"] - prev_y > 60.0:
            break
        if len(text) < 12 or BOILERPLATE.search(text) or text.lstrip()[:1] in "−•▪◦l·":
            break
        paragraph.append(text)
        prev_y = seg["y"]
        if text.endswith("."):
            break
        if len(" ".join(paragraph)) > 400:
            break
    if not paragraph:
        return None
    return _first_sentence(" ".join(paragraph))


DESC_HEADERS = re.compile(
    r"^(?:[●•▪◦\u2022\u25cf\u2023\u2043\x84l]\s*)?"
    r"(?:general\s+|product\s+|device\s+)?description"
    r"(?:\s*/\s*ordering\s+information)?\s*$",
    re.I,
)
# Headerless covers: the description paragraph sits under the title with no
# "Description" heading, in the upper part of page 1. Two openers are common —
# "The RAA210130 is a fully PMBus ..." (Renesas) and "CoolMOS™ is a
# revolutionary technology ..." / "Advanced HEXFET® Power MOSFETs from
# International Rectifier utilize ..." (Infineon/IR). The opener must be a
# full sentence start (capitalised, ends its clause with a verb), which is why
# the bare "first short line in the header" heuristic is not used.
PARAGRAPH_OPENER = re.compile(
    r"^(?:The\s+[A-Za-z0-9][\w./-]*\s+(?:is|are|provides|offers|features|delivers|combines)\b"
    r"|[A-Z][\w®™ª-]*\s+(?:is|are|provides|offers|combines|delivers|features|utilizes|utilise)\b"
    r"|[A-Z][\w®™ª-]*(?:\s+[\w®™ª,-]+){1,5}\s+(?:from\s+[A-Z][\w&.-]+)?\s*(?:utilizes|utilise|provides|offers|combines|are|is)\b)",
    re.I,
)
HEADER_REGION_FRAC = 0.45

# A section heading we must not walk past while collecting a Description
# paragraph (matches the existing BOILERPLATE capture of section names, plus
# the common vendor headings that follow a Description block).
SECTION_STOP = re.compile(
    r"^\s*(?:absolute\s+maximum\s+(?:ratings?|conditions?)|electrical\s+characteristics|"
    r"thermal\s+(?:information|characteristics|resistance)|package\s+(?:information|outline|type)|"
    r"ordering\s+information|revision\s+history|pin\s+(?:configuration|assignments?)|"
    r"connection\s+diagram|block\s+diagram|typical\s+(?:application|operating)|"
    r"application\s+(?:information|examples?|circuit)|features?|key\s+features|applications?|"
    r"benefits?|general\s+description|product\s+description|description|"
    r"qualification\s+information|related\s+literature|references?|notes?|"
    r"functional\s+description|overview)\s*:?\s*$",
    re.I,
)

# Feature bullets: the fallback layout. A bullet block is contiguous lines at
# similar size/x under a Features-style band, each a short phrase. The lead
# bullet carrying the product claim (usually has a digit: "Delivers up to
# 250W per Channel into 4Ω with No Heat sink").
_BULLET_MARK = re.compile(r"^\s*(?:[•▪◦\u2022\u25cf\u2023\u2043]|[-–—]\s|l\s|○\s)", re.I)
_BULLET_BAND = re.compile(r"^\s*(?:features?|key\s+features|product\s+features|"
                          r"applications?|benefits?|highlights?|general\s+features?)\s*:?\s*$", re.I)


def _column_lines(lines: list[dict], start: int, x: float) -> list[dict]:
    return [l for l in lines[start:] if abs(l["x"] - x) <= 30.0]


# Vendor annotation blocks that can sit inside a Description section on SiC
# covers: a pin-definition note is not the product description.
NOTE_BLOCK = re.compile(r"^\s*(note|pin\s+definition|caution|warning)s?\b", re.I)


def _paragraph_from_segments(segments: list[dict], anchor_x: float, start_y: float,
                             stop_at_section: bool = False) -> str | None:
    """First sentence from the column anchored at anchor_x, beginning at
    start_y (the header line, or the opener line itself). Stops at
    bullet-like fragments, boilerplate, and visual paragraph breaks. When
    stop_at_section is set, a section heading also ends the paragraph."""
    paragraph = []
    prev_y = None
    for seg in segments:
        if seg["y"] < start_y - 5.0 or abs(seg["x"] - anchor_x) > 30.0:
            continue
        text = seg["text"]
        if prev_y is not None and seg["y"] - prev_y > 60.0:
            break
        if stop_at_section and paragraph and SECTION_STOP.match(text):
            break
        if NOTE_BLOCK.match(text):
            break
        if len(text) < 12 or text.lstrip()[:1] in "−•▪◦l·":
            if paragraph:
                break
            continue
        if BOILERPLATE.search(text):
            break
        paragraph.append(text)
        prev_y = seg["y"]
        if text.endswith("."):
            break
        if len(" ".join(paragraph)) > 400:
            break
    if not paragraph:
        return None
    return _first_sentence(" ".join(paragraph))


def _looks_like_prose(text: str) -> bool:
    """A Description section that holds only a figure label or a heading
    fragment is not vendor prose. Require a verb-bearing clause: at least 5
    words and a lowercase function word, which every real description
    paragraph has and a bare label like "HEXFET® Power MOSFET" does not."""
    words = text.split()
    if len(words) < 5:
        return False
    return any(w.lower() in {"is", "are", "provides", "offers", "combines", "delivers",
                             "utilizes", "utilizes", "of", "with", "for", "and", "the",
                             "a", "an", "to", "that", "which", "designed", "used"}
               for w in words)


def _description_section_paragraph(lines: list[dict]) -> str | None:
    """CR spec 2026-09-17: anchor on a Description / General Description /
    Product Description heading and take the paragraph(s) following it, up to
    the next section heading. The heading's own segment must not enter the
    walk (it is short and would end it), so the walk starts below the
    header's y."""
    segments = _visual_segments(lines)
    for ln in lines:
        if not DESC_HEADERS.match(ln["text"]):
            continue
        # The heading's own segment must not enter the walk: it matches
        # BOILERPLATE and would end the paragraph at once. Everything else in
        # the heading's column follows.
        remaining = [s for s in segments if not DESC_HEADERS.match(s["text"])]
        sentence = _paragraph_from_segments(remaining, ln["x"], ln["y"] + 1.0, stop_at_section=True)
        if sentence and _looks_like_prose(sentence):
            return sentence
    # Headerless cover: a paragraph opener under the title, no heading. Only
    # in the page's header region — a mid-document sentence that happens to
    # read like an opener is not a cover description. The paragraph is one
    # contiguous same-column run of segments; interleaved figure glyphs in
    # other columns are ignored by working on segments, not raw lines.
    if not lines:
        return None
    top = lines[0]["h"] * HEADER_REGION_FRAC
    body = [s for s in segments if s["y"] <= top and len(s["text"]) >= 12]
    for idx, seg in enumerate(body):
        if not (PARAGRAPH_OPENER.match(seg["text"]) and not BOILERPLATE.search(seg["text"])):
            continue
        start = idx
        while start > 0:
            prev, cur = body[start - 1], body[start]
            if abs(prev["x"] - cur["x"]) > 30.0:
                break
            if not (0 < cur["y"] - prev["y"] <= 20.0):
                break
            if SECTION_STOP.match(prev["text"]) or BOILERPLATE.search(prev["text"]):
                break
            start -= 1
        opener = body[start]
        sentence = _paragraph_from_segments(segments, opener["x"], opener["y"] - 5.0, stop_at_section=True)
        if sentence and _looks_like_prose(sentence):
            return sentence
    return None
    return None


def _feature_bullet_block(lines: list[dict], part_number: str) -> str | None:
    """Fallback layout (CR spec 2026-09-17, step 2): the contiguous
    feature-bullet block, verbatim and whitespace-normalized. The bullet
    glyphs are stripped (they are markup, not copy) and the block's lines
    joined in reading order. Returns None when there is no genuine
    multi-line bullet block."""
    n = len(lines)
    for i, ln in enumerate(lines):
        if not (_BULLET_BAND.match(ln["text"]) or _BULLET_MARK.match(ln["text"])):
            continue
        block, size, x = [], ln["size"], ln["x"]
        for nxt in lines[i + 1:]:
            # Column guard first: a table cell in the other column must not
            # end the block (it belongs to a different reading column).
            if abs(nxt["size"] - size) > 2.0 or abs(nxt["x"] - x) > 30.0:
                continue
            if nxt["text"].lstrip()[:1] in "0123456789":
                break
            if not (MIN_LINE_CHARS <= len(nxt["text"]) <= 160):
                break
            if SECTION_STOP.match(nxt["text"]) or _BULLET_BAND.match(nxt["text"]):
                break
            if not _BULLET_MARK.match(nxt["text"]) and block:
                break
            block.append(re.sub(r"^\s*(?:[•▪◦\u2022\u25cf\u2023\u2043]|[-–—]\s|l\s|○\s)", "", nxt["text"], flags=re.I).strip())
        if len(block) >= 3:
            return " ".join(block)
    return None


def _text_artifacts_present(lines: list[dict]) -> bool:
    """The text layer splits words mid-token on some vendor PDFs ("packag
    ing", "compatib le"). CR decides at ingest; the extractor only flags.
    Heuristic: a line ending in a lowercase fragment followed by a line
    starting with a lowercase fragment, where joining yields a real word."""
    from itertools import pairwise
    for a, b in pairwise(lines):
        ta, tb = a["text"].rstrip(), b["text"].lstrip()
        if not ta or not tb:
            continue
        if ta[-1].islower() and tb[0].islower() and ta.endswith(("g", "e", "n", "t", "c", "l", "r", "s", "d")):
            return True
    return False


def page1_description(pdf_path: Path, part_number: str) -> dict:
    """Deterministic page-1 candidates, verbatim only.

    Priority per CR ruling 2026-09-17 (am-mu57vf5v-4f3c):
      1. Description / General Description / Product Description section
         paragraph (anchor on the heading, take what follows to the next
         section heading).
      2. Contiguous feature-bullet block (verbatim).
      3. Large-font tagline in the header region (legacy layout).
    Everything is recorded verbatim; candidate_kind names which rule fired
    and the floor verdict is reported separately so CR's locked validator
    rules. Also flags text_artifacts when the text layer splits words
    mid-token; CR decides at ingest, we never repair editorially.
    """
    try:
        doc = pymupdf.open(pdf_path)
    except Exception:
        return {"error": "pdf_unreadable"}
    try:
        lines = _page_lines(doc[0])
    finally:
        doc.close()
    artifacts = _text_artifacts_present(lines)
    out_extra = {"text_artifacts": True} if artifacts else {}

    # 1. Description section paragraph.
    sentence = _description_section_paragraph(lines)
    if sentence and not _is_part_numberish(sentence, part_number):
        return {"description_verbatim": sentence, "candidate_kind": "section_paragraph", **out_extra}

    # 2. Feature-bullet block.
    bullets = _feature_bullet_block(lines, part_number)
    if bullets and not _is_part_numberish(bullets, part_number):
        return {"description_verbatim": bullets, "candidate_kind": "bullet_block", **out_extra}

    # 3. Legacy tagline (header region, largest type).
    candidates = []
    for i, ln in enumerate(lines):
        if ln["size"] < TAGLINE_MIN_SIZE or BOILERPLATE.search(ln["text"]) or _is_part_numberish(ln["text"], part_number):
            continue
        if len(ln["text"]) < MIN_LINE_CHARS:
            continue
        tagline = _merge_tagline(lines, i)
        if not BOILERPLATE.search(tagline) and not _is_garbled(tagline) and _alnum_density(tagline) >= 0.5:
            candidates.append((ln["size"], ln["y"], tagline))
    if candidates:
        best = max(candidates, key=lambda c: (c[0], -c[1]))
        return {"description_verbatim": best[2], "candidate_kind": "tagline", **out_extra}
    return {"no_description_line": True, "candidate_kind": "none", **out_extra}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--pdf-root", action="append", default=[], type=Path)
    ap.add_argument("--pairs", action="append", default=[], type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--missing-out", type=Path, required=True)
    args = ap.parse_args()

    queue = json.loads(args.queue.read_text())
    local: dict[str, Path] = {}
    for root in args.pdf_root:
        for pdf in root.glob("*.pdf"):
            local.setdefault(pdf.name, pdf)
    pairs: dict[tuple[str, str], dict] = {}
    for path in args.pairs:
        for line in path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                pairs[(row["vendor"], row["part_number"])] = row

    out_rows, missing = [], []
    tally: Counter = Counter()
    for item in queue:
        domain, part = item["domain"], item["part_number"]
        filename = f"{DOMAIN_PREFIX[domain]}-{part}.pdf"
        pdf = local.get(filename)
        if pdf is None:
            missing.append({**item, "reason": "pdf_not_local"})
            tally[("missing_pdf", domain)] += 1
            continue
        result = page1_description(pdf, part)
        pair = pairs.get((domain, part))
        local_sha = None
        sha_source, sha_matches = "local", None
        if pair is not None:
            local_sha = local_sha or sha256_of(pdf)
            sha_source = "pairs"
            sha_matches = local_sha == pair.get("pdf_sha256")
        row = {
            "part_number": part,
            "domain": domain,
            "source_url": item.get("datasheet_url"),
            "pdf_sha": (pair or {}).get("pdf_sha256") or local_sha or sha256_of(pdf),
            "pdf_sha_source": sha_source,
            "pdf_sha_matches_pairs": sha_matches,
        }
        if "description_verbatim" in result:
            line = result["description_verbatim"]
            row["candidate_kind"] = result.get("candidate_kind", "tagline")
            if result.get("text_artifacts"):
                row["text_artifacts"] = True
            row["page1_line_verbatim"] = line
            if meets_floor(line):
                row["description_verbatim"] = line
                tally[("extracted", domain)] += 1
            else:
                row["no_description_line"] = True
                row["floor_failure_reason"] = "under_32_chars" if len(line) < FLOOR_CHARS else "no_digit"
                tally[("floor_fail", domain)] += 1
        else:
            row.update(result)
            tally[(result.get("error", "no_line"), domain)] += 1
        out_rows.append(row)

    args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out_rows))
    args.missing_out.write_text(json.dumps(missing, ensure_ascii=False, indent=1))
    print(f"parts: {len(queue)}  records: {len(out_rows)}  missing-pdf: {len(missing)}")
    for key in sorted(tally):
        print(f"  {key[0]:12} {key[1]:18} {tally[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
