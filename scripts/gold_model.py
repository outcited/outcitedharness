#!/usr/bin/env python3
"""Generate dw-model gold from the live `harness/electronics/local_model.py`.

Every case is computed by the reference: `local_prompt` cases capture the
exact instruction string, `focused_page_context` cases capture the projected
evidence dict, `validate_response` cases capture success or the exact
`ValueError` message, and `parse_json_response` cases capture the parsed
object or error.

Cases cover the traps: code-fence stripping in `parse_json_response`,
required-key and type errors in `validate_response`, region-based
block/table filtering and `project_pin_table` in `focused_page_context`,
the 80 000-byte evidence cap in `local_prompt`, Python `repr()` in the
scope rule, and the two distinct JSON encodings (compact for evidence,
default-separator for the schema).

Usage: python3 scripts/gold_model.py > tests/fixtures/gold/model_v1.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.electronics.local_model import (  # noqa: E402
    focused_page_context,
    local_prompt,
    parse_json_response,
    validate_response,
    RESPONSE_SCHEMAS,
)


def _page_evidence(**overrides):
    base = {
        "document_sha256": "a" * 64,
        "page_1based": 1,
        "blocks": [],
        "tables": [],
        "digital_text": None,
        "structural_evidence": None,
    }
    base.update(overrides)
    return base


def _prompt_cases():
    cases = []
    specs = [
        ("pin_or_ball_no_evidence", "pin_or_ball", _page_evidence(), False),
        ("pin_or_ball_with_evidence", "pin_or_ball", _page_evidence(
            blocks=[{"bbox": [0, 0, 100, 50], "text": "VCC"}],
            tables=[{"table_index": 0, "rows": [["1", "VCC"]]}],
            digital_text={"text": "VCC GND"},
            structural_evidence={"regions": []},
        ), True),
        ("pin_semantics_scoped", "pin_semantics", _page_evidence(
            structural_evidence={
                "regions": [{"bbox": [0, 0, 200, 100], "table_index": 0}],
                "package_scope": {"package": "QFN-32", "column_header": "Pin No."},
            },
            blocks=[{"bbox": [10, 10, 50, 50], "text": "pin block"}],
            tables=[{"table_index": 0, "rows": [["1", "VCC"]]}],
        ), True),
        ("parametrics_simple", "parametrics", _page_evidence(
            blocks=[{"bbox": [0, 0, 100, 50], "text": "Rds(on) 120 mOhm"}],
            tables=[{"table_index": 0, "rows": [["Rds(on)", "120", "mOhm"]]}],
        ), True),
        ("power_modes_regions", "power_modes", _page_evidence(
            structural_evidence={"regions": [{"bbox": [0, 0, 200, 100], "table_index": 0}]},
            blocks=[
                {"bbox": [10, 10, 50, 50], "text": "in region"},
                {"bbox": [300, 300, 400, 400], "text": "outside region"},
            ],
            tables=[
                {"table_index": 0, "rows": [["Active", "10", "mA"]]},
                {"table_index": 1, "rows": [["Sleep", "1", "uA"]]},
            ],
        ), True),
        ("typical_characteristics_minimal", "typical_characteristics", _page_evidence(), True),
        ("series_summary_minimal", "series_summary", _page_evidence(), True),
        ("opn_decoder_minimal", "opn_decoder", _page_evidence(), True),
        ("pin_or_ball_no_column_header", "pin_or_ball", _page_evidence(
            structural_evidence={
                "regions": [],
                "package_scope": {"package": "SOIC-8", "column_header": None},
            },
        ), True),
    ]
    for name, cap, pe, include in specs:
        result = local_prompt(cap, pe, include_page_evidence=include)
        cases.append({
            "kind": "local_prompt",
            "name": name,
            "input": {"capability": cap, "page_evidence": pe, "include_page_evidence": include},
            "expected": result,
        })
    return cases


def _focused_cases():
    cases = []
    specs = [
        ("no_regions_passthrough", "pin_or_ball", _page_evidence(
            blocks=[{"bbox": [0, 0, 10, 10], "text": "a"}],
            tables=[{"table_index": 0}],
        )),
        ("region_filtering", "pin_or_ball", _page_evidence(
            structural_evidence={"regions": [{"bbox": [0, 0, 100, 100], "table_index": 0}]},
            blocks=[
                {"bbox": [10, 10, 20, 20], "text": "in"},
                {"bbox": [500, 500, 600, 600], "text": "out"},
            ],
            tables=[
                {"table_index": 0},
                {"table_index": 1},
            ],
        )),
        ("digital_text_truncation", "parametrics", _page_evidence(
            digital_text={"text": "x" * 35_000, "extra": "kept"},
        )),
    ]
    for name, cap, pe in specs:
        result = focused_page_context(cap, pe)
        cases.append({
            "kind": "focused_page_context",
            "name": name,
            "input": {"capability": cap, "page_evidence": pe},
            "expected": result,
        })
    return cases


def _validate_cases():
    cases = []
    schema = RESPONSE_SCHEMAS["pin_or_ball"]
    specs = [
        ("valid", {"package": "QFN", "pins": [{"pin_no": "1", "name": "VCC"}]}, schema),
        ("missing_required", {"pins": []}, schema),
        ("wrong_type_root", "not a dict", schema),
        ("wrong_type_nested", {"package": 42, "pins": []}, schema),
        ("nested_array_type", {"package": None, "pins": [{"pin_no": 1, "name": 2}]}, schema),
    ]
    for name, value, sch in specs:
        try:
            validate_response(value, sch)
            expected = None
        except (ValueError, TypeError) as exc:
            expected = str(exc)
        cases.append({
            "kind": "validate_response",
            "name": name,
            "input": {"value": value, "schema": sch},
            "expected": expected,
        })
    return cases


def _parse_cases():
    cases = []
    specs = [
        ("clean_object", '{"a": 1}'),
        ("fenced_json", '```json\n{"a": 1}\n```'),
        ("fenced_no_lang", '```\n{"a": 1}\n```'),
        ("trailing_text", '{"a": 1} extra'),
        ("no_object", "hello world"),
        ("array_not_object", "[1, 2]"),
        ("leading_prose", 'Here is the result: {"a": 1}'),
    ]
    for name, text in specs:
        try:
            expected = parse_json_response(text)
        except (ValueError, TypeError) as exc:
            expected = str(exc)
        cases.append({
            "kind": "parse_json_response",
            "name": name,
            "input": {"text": text},
            "expected": expected,
        })
    return cases


def main() -> int:
    cases = []
    cases.extend(_prompt_cases())
    cases.extend(_focused_cases())
    cases.extend(_validate_cases())
    cases.extend(_parse_cases())
    out = {"cases": cases}
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
