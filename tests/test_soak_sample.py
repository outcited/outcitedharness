from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "soak_sample.py"
SPEC = importlib.util.spec_from_file_location("soak_sample", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
soak = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(soak)


def opener_for(*dead_urls):
    def fake(url, timeout=8):
        if url in dead_urls:
            raise OSError("connection refused")
        return None

    return fake


def test_every_endpoint_column_exists_in_the_csv_schema() -> None:
    vitals = soak.endpoint_vitals([], opener=opener_for())
    assert set(vitals) == {"v41", "canon_up", "student_up"}
    assert set(vitals) <= set(soak.FIELDS)


def test_csv_schema_is_frozen_so_old_rows_stay_aligned() -> None:
    # soak.csv has carried this header since 2026-10; a new column would
    # misalign every historical row.
    assert soak.FIELDS == [
        "ts", "pending", "claimed", "done", "dead", "done_delta",
        "frontier_today", "canon_today", "v41", "canon_up", "student_up", "alerts",
    ]


def test_all_up_reports_ones_and_no_alerts() -> None:
    alerts: list[str] = []
    vitals = soak.endpoint_vitals(alerts, opener=opener_for())
    assert vitals == {"v41": 1, "canon_up": 1, "student_up": 1}
    assert alerts == []


def test_canon_down_alerts() -> None:
    """The regression: canon was down for 24h and the soak said nothing."""
    alerts: list[str] = []
    vitals = soak.endpoint_vitals(alerts, opener=opener_for(soak.ENDPOINTS["canon"]))
    assert vitals["canon_up"] == 0
    assert "canon_down" in alerts


def test_canon_replica_down_alerts_without_adding_a_column() -> None:
    alerts: list[str] = []
    vitals = soak.endpoint_vitals(
        alerts, opener=opener_for(soak.EXTRA_ALERTS["canon_replica"]))
    assert "canon_replica_down" in alerts
    assert set(vitals) == {"v41", "canon_up", "student_up"}


def test_v41_down_still_alerts() -> None:
    alerts: list[str] = []
    soak.endpoint_vitals(alerts, opener=opener_for(soak.ENDPOINTS["v41_tp4"]))
    assert alerts == ["v41_down"]


def test_student_down_is_recorded_but_not_an_alert() -> None:
    # Dark by choice while the gate verdict is unresolved; a permanent alert
    # line would be noise.
    alerts: list[str] = []
    vitals = soak.endpoint_vitals(alerts, opener=opener_for(soak.ENDPOINTS["student"]))
    assert vitals["student_up"] == 0
    assert alerts == []


def test_everything_down_alerts_each_must_have_once() -> None:
    alerts: list[str] = []
    dead = list(soak.ENDPOINTS.values()) + list(soak.EXTRA_ALERTS.values())
    soak.endpoint_vitals(alerts, opener=opener_for(*dead))
    assert sorted(alerts) == ["canon_down", "canon_replica_down", "v41_down"]


def test_student_lane_is_probed_on_dgx1_where_it_actually_serves() -> None:
    assert soak.ENDPOINTS["student"] == "http://100.81.201.24:8950/v1/models"


def test_canon_endpoints_match_the_live_replica_ports() -> None:
    assert soak.ENDPOINTS["canon"] == "http://100.100.116.82:8900/v1/models"
    assert soak.EXTRA_ALERTS["canon_replica"] == "http://100.81.201.24:8901/v1/models"
