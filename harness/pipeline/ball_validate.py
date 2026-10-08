"""Ball-map geometry validators — the vision lane's deterministic conscience.

Checks (per the external review, 2026-10-03):
  1. grid coordinates valid (JEDEC letters skip I, O, Q...; numbers 1-N)
  2. no ball assigned twice
  3. ball count matches package designation (with depopulation tolerance)
  4. power/ground counts plausible
  5. cross-check against pin table when both exist
"""

import re

BALL_LETTERS = [c for c in "ABCDEFGHJKLMNPRTUVWXY" if c not in "IOQ"]
BALL_RE = re.compile(r"^([A-Y])\s*[-_]?\s*(\d{1,2})$", re.IGNORECASE)
POWER_RE = re.compile(r"^(vcc|vdd|vddio|vddcore|vdda|vbat|avdd|pvdd)", re.IGNORECASE)
GROUND_RE = re.compile(r"^(gnd|vss|vssa|vssio|avss)", re.IGNORECASE)

PACKAGE_BALL_COUNTS = {
    "0.5": {"a4": 84, "a5": 100, "a6": 144, "a8": 164, "a9": 196, "a10": 256, "a11": 289,
            "a12": 324, "a13": 400, "a14": 484, "a15": 576, "a16": 676, "bga": None},
    "0.8": {"a4": 84, "a5": 100, "a6": 144, "a8": 160, "a9": 196, "a10": 256},
    "1.0": {"a4": 81, "a5": 100, "a6": 144, "a8": 169, "a10": 256},
}


def parse_balls(rows):
    """rows: [{ball: 'A1', name: 'VDDIO', ...}] -> (balls, issues)"""
    issues = []
    balls = {}
    for r in rows:
        if isinstance(r, (tuple, list)) and len(r) == 2:
            designator, name = str(r[0]).strip(), str(r[1]).strip()
        elif isinstance(r, dict):
            designator = str(r.get("ball") or r.get("designator") or "").strip()
            name = str(r.get("name") or r.get("pin") or "").strip()
        else:
            issues.append({"severity": "P1", "check": "invalid_row", "detail": str(r)[:40]})
            continue
        m = BALL_RE.match(designator)
        if not m:
            issues.append({"severity": "P1", "check": "invalid_designator",
                           "detail": designator or "<missing>"})
            continue
        letter, num = m.group(1).upper(), int(m.group(2))
        if letter not in BALL_LETTERS:
            issues.append({"severity": "P1", "check": "invalid_letter", "detail": designator})
        if designator.upper() in balls:
            issues.append({"severity": "P0", "check": "duplicate_ball",
                           "detail": f"{designator}: {balls[designator.upper()]} vs {name}"})
            continue
        balls[designator.upper()] = name
    return balls, issues


def check_counts(balls, package=None):
    issues = []
    n = len(balls)
    if package:
        m = re.search(r"(\d+(?:\.\d+)?)\s*mm", package) or re.search(r"(\d+)\s*(bga|ball)", package, re.IGNORECASE)
        expected = None
        if m:
            key = package.lower().replace(" ", "")
            for pitch, table in PACKAGE_BALL_COUNTS.items():
                for k, v in table.items():
                    if k in key and v:
                        expected = v
        if expected and abs(n - expected) > max(4, int(expected * 0.12)):
            issues.append({"severity": "P1", "check": "count_mismatch",
                           "detail": f"{n} balls vs package {package} (~{expected})"})
    if n < 16:
        issues.append({"severity": "P1", "check": "implausibly_few_balls", "detail": str(n)})
    power = sum(1 for v in balls.values() if POWER_RE.match(v))
    ground = sum(1 for v in balls.values() if GROUND_RE.match(v))
    if n >= 100:
        if power < 2 or ground < 2:
            issues.append({"severity": "P1", "check": "implausible_power_ground",
                           "detail": f"pwr={power} gnd={ground} of {n}"})
    if power + ground > n * 0.6:
        issues.append({"severity": "P1", "check": "power_ground_saturation",
                       "detail": f"pwr+gnd={power + ground}/{n}"})
    return issues


def cross_check_pins(balls, pin_rows):
    """pin_rows: [{name, function/direction}] — every named ball should appear
    in the pin table (or its group prefix)."""
    issues = []
    pin_names = {str(p.get("name") or "").strip().upper() for p in pin_rows if p.get("name")}
    for designator, name in balls.items():
        base = re.split(r"[_ ]?\d+$", name)[0] if name else name
        if name and name.upper() not in pin_names and base.upper() not in pin_names:
            issues.append({"severity": "P1", "check": "ball_not_in_pin_table",
                           "detail": f"{designator}={name}"})
    return issues


def validate_ballmap(rows, package=None, pin_rows=None):
    balls, issues = parse_balls(rows)
    issues += check_counts(balls, package)
    if pin_rows:
        issues += cross_check_pins(balls, pin_rows)
    return {
        "n_balls": len(balls),
        "n_issues": len(issues),
        "p0": sum(1 for i in issues if i["severity"] == "P0"),
        "p1": sum(1 for i in issues if i["severity"] == "P1"),
        "issues": issues,
        "pass": not any(i["severity"] == "P0" for i in issues),
    }
