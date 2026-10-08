"""Category taxonomy resolver for evidence units (facet breakdown, 2026-10-08).

Sam's framing: a retrieval layer cannot dwindle permutations until the
category is broken down. Measured state when this was written: 98.7% of
units carried no category and the catalog's family column was empty for
0/1,882 parts — so a facet UI would have nothing to narrow.

The classification signal already exists in the extracted text (62.4% of
front-matter units name their category in printed vocabulary: power 29,694,
mcu 11,209, connectors 4,864) plus the substrate path aisle and the
topology wave's selector aisles. This module propagates that printed signal
onto units, fail-closed: a unit whose text names no category stays
unclassified rather than guessed.

Subcategories reuse the selector-aisle vocabulary that already exists in the
platform (dc-dc-converters, gate-drivers, ldo-regulators, ...) so facet
values are the same names the extraction lanes use.
"""

from __future__ import annotations

import re

TAXONOMY_SCHEMA = "harness.search-taxonomy.v1"

# Ordered: first match wins. Each category lists (subcategory, pattern).
_CATEGORY_RULES: list[tuple[str, list[tuple[str, re.Pattern[str]]]]] = [
    ("power", [
        ("discrete-mosfet",
         re.compile(r"\b(mosfet|igbt|sic|gan|power transistor|"
                    r"superjunction|coolmos|strongirfet)\b", re.I)),
        ("gate-drivers",
         re.compile(r"\b(gate driv|low-?side driv|high-?side driv|"
                    r"half-?bridge driv|isolated driv)\w*", re.I)),
        ("ldo-regulators", re.compile(r"\b(ldo|low[- ]dropout|linear regulator)\b", re.I)),
        ("switching-regulators",
         re.compile(r"\b(buck|boost|buck-?boost|dc-?dc|switching regulator|"
                    r"step[- ]down|step[- ]up|converter)\w*", re.I)),
        ("ac-dc", re.compile(r"\b(ac-?dc|offline|flyback|pfc|rectifier|"
                             r"mains|bridge rectifier)\w*", re.I)),
        ("load-switches", re.compile(r"\b(load switch|hot[- ]swap|efuse|"
                                     r"e-?fuse|ideal diode)\b", re.I)),
        ("battery-management", re.compile(r"\b(battery|charger|fuel gauge|"
                                          r"bms|cell balanc)\w*", re.I)),
        ("poe-ics", re.compile(r"\b(power over ethernet|poe|pd controller)\b", re.I)),
        ("supervisors-reset", re.compile(r"\b(supervisor|reset ic|voltage monitor|"
                                         r"watchdog|por)\b", re.I)),
        ("voltage-references", re.compile(r"\b(voltage reference|shunt reference|"
                                          r"vref)\b", re.I)),
        ("power-management",
         re.compile(r"\b(power management|pmic|power supply|regulator|"
                    r"power module|dc-?dc|inverter)\w*", re.I)),
    ]),
    ("mcu", [
        ("wireless-soc",
         re.compile(r"\b(wifi|wi-?fi|bluetooth|ble|zigbee|lorawan|"
                    r"wireless soc|wlan)\b", re.I)),
        ("microcontrollers",
         re.compile(r"\b(microcontroller|mcu|arm cortex-?[am]\d|cortex-?m\d)\b", re.I)),
        ("processors",
         re.compile(r"\b(microprocessor|application processor|mpu|soc)\b", re.I)),
    ]),
    ("connectors", [
        ("connectors",
         re.compile(r"\b(connector|header|terminal block|receptacle|plug|"
                    r"socket|board-?to-?board|pitch|positions?)\b", re.I)),
    ]),
]

# Path-aisle hints (substrate source_path) -> category when text is silent.
_PATH_HINTS = (("/mcu/", "mcu"), ("mosfet", "power"), ("power", "power"),
               ("connector", "connectors"), ("exports/power", "power"))


def classify_text(text: str) -> tuple[str | None, str | None]:
    """(category, subcategory) from printed vocabulary; (None, None) if silent."""
    if not text:
        return None, None
    for category, subs in _CATEGORY_RULES:
        for subcategory, pattern in subs:
            if pattern.search(text):
                return category, subcategory
    return None, None


def classify(*, existing_category: str | None, text: str,
             source_path: str | None = None) -> tuple[str | None, str | None]:
    """Full priority: printed text wins, then path hint, then existing value.

    Never invents a family: family resolution is out of scope here (it is a
    catalog dimension, empty upstream today).
    """
    category, subcategory = classify_text(text)
    if category:
        return category, subcategory
    lowered = (source_path or "").lower()
    for hint, hinted in _PATH_HINTS:
        if hint in lowered:
            return hinted, None
    if existing_category:
        return existing_category, None
    return None, None