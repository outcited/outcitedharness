from __future__ import annotations

import json

import pytest

from harness.model_result import (
    EMPTY,
    FAILED,
    OK,
    ModelResult,
    failed,
    ok,
    parse_json_array,
)


def test_parse_ok_returns_rows():
    res = parse_json_array('[{"symbol": "RDS(on)", "value": 1.2}]', attempts=1, model="m")
    assert res.status == OK
    assert res.ok and not res.failed
    assert res.rows == [{"symbol": "RDS(on)", "value": 1.2}]
    assert res.attempts == 1
    assert res.model == "m"
    assert res.reason is None


def test_parse_empty_array_is_empty_not_failed():
    res = parse_json_array("no specs printed here: []", attempts=1)
    assert res.status == EMPTY
    assert res.rows == []
    assert res.reason is None


def test_unparseable_json_is_failed_and_never_repaired():
    truncated = '[{"symbol": "IDD", "value": 1.0}, {"symbol": "IQ"'
    res = parse_json_array(truncated, attempts=2, model="m")
    assert res.status == FAILED
    assert res.reason.startswith("unparseable_json")
    assert res.rows == []
    assert res.attempts == 2
    assert res.raw_excerpt == truncated
    receipt = res.as_receipt()
    assert receipt["status"] == FAILED
    assert receipt["rows"] == 0
    json.dumps(receipt)


def test_finish_reason_length_is_failed_even_if_a_prefix_parses():
    res = parse_json_array('[{"a": 1}]', attempts=1, finish_reason="length")
    assert res.status == FAILED
    assert res.reason == "truncated_output"


def test_no_array_and_empty_content_are_distinct_failures():
    assert parse_json_array("I could not find any tables.").reason == "no_json_array"
    assert parse_json_array("").reason == "empty_content"
    assert parse_json_array(None).reason == "empty_content"


def test_non_list_payload_is_failed():
    res = parse_json_array('{"rows": []}')
    assert res.status == FAILED
    assert res.reason.startswith("not_a_list")


def test_model_result_enforces_status_invariants():
    with pytest.raises(ValueError):
        ModelResult("BROKEN")
    with pytest.raises(ValueError):
        ModelResult(OK, [])
    with pytest.raises(ValueError):
        ModelResult(EMPTY, [{"a": 1}])
    with pytest.raises(ValueError):
        ModelResult(FAILED, [])


def test_helpers_map_empty_rows_to_empty_and_keep_failure_reason():
    assert ok([]).status == EMPTY
    assert ok([{"a": 1}]).status == OK
    res = failed("request_error: timeout", attempts=2)
    assert res.failed
    assert res.reason == "request_error: timeout"
    assert res.attempts == 2
