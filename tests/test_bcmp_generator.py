"""Offline (hermetic) tests for the BCMP generator's pure validation layer.

No network: `call_tp4` is never exercised here. The law these pin:
- proposals outside the knife declaration are parked, never guessed in;
- value bands mirror the engine-side declaration;
- canonical dedupe keys ignore float noise and wording.
"""

import importlib.util
import json
import os
import tempfile

TOOL = os.path.join(
    os.path.dirname(__file__),
    "..",
    "designwins",
    "crates",
    "dw-bcmp",
    "tools",
    "bcmp_generate.py",
)

SPEC = {
    "version": "bcmp-knives-v1",
    "knives": [
        {"axis": "flash_kb", "op": "min_rating", "unit": "kb", "value_min": 1, "value_max": 8192},
        {"axis": "freq_mhz", "op": "min_rating", "unit": "mhz", "value_min": 0.032, "value_max": 1000},
        {"axis": "has_can", "op": "min_rating", "unit": "count", "value_min": 1, "value_max": 8},
        {"axis": "has_usb", "op": "min_rating", "unit": "count", "value_min": 1, "value_max": 4},
    ],
    "parked_axes": {"temp_range_c": "range evidence unsupported", "sram_kb": "no axis"},
}


def load_tool():
    spec = importlib.util.spec_from_file_location("bcmp_generate", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def setup():
    mod = load_tool()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as h:
        json.dump(SPEC, h)
        path = h.name
    mod.load_spec(path)
    return mod


def test_unsupported_axis_parked_not_guessed():
    mod = setup()
    honored, parked = mod.validate_atoms(
        {"atoms": [{"axis": "sram_kb", "value": 64, "hard": True}]}
    )
    assert honored == []
    assert parked[0]["code"] in ("unsupported_axis", "undeclared_knife")


def test_out_of_band_value_parked():
    mod = setup()
    honored, parked = mod.validate_atoms(
        {"atoms": [{"axis": "has_can", "value": 734, "hard": True}]}
    )
    assert honored == []
    assert parked[0]["code"] == "value_out_of_range"


def test_soft_atom_parked_and_valid_honored():
    mod = setup()
    honored, parked = mod.validate_atoms(
        {
            "atoms": [
                {"axis": "flash_kb", "value": 256, "hard": True},
                {"axis": "freq_mhz", "value": 170, "hard": False},
            ]
        }
    )
    assert [a["axis"] for a in honored] == ["flash_kb"]
    assert parked[0]["code"] == "soft_atom"
    assert honored[0]["op"] == "min_rating" and honored[0]["unit"] == "kb"


def test_canonical_key_ignores_float_noise():
    mod = setup()
    a = [{"axis": "flash_kb", "value": 128, "hard": True}]
    b = [{"axis": "flash_kb", "value": 128.000004, "hard": True}]
    assert mod.canonical_key(a) == mod.canonical_key(b)


def test_canonical_key_orders_axes():
    mod = setup()
    a = [
        {"axis": "flash_kb", "value": 256, "hard": True},
        {"axis": "has_can", "value": 1, "hard": True},
    ]
    b = list(reversed(a))
    assert mod.canonical_key(a) == mod.canonical_key(b)


def test_existing_keys_restart_safety(tmp_path):
    mod = setup()
    sdir = tmp_path / "scenarios"
    sdir.mkdir()
    row = {
        "scenario_id": "x1",
        "question": "q",
        "proposed_atoms": [{"axis": "flash_kb", "value": 64, "hard": True}],
    }
    (sdir / "a.jsonl").write_text(json.dumps(row) + "\n")
    keys = mod.existing_keys(str(tmp_path))
    assert len(keys) == 1
    assert list(keys)[0] == mod.canonical_key(row["proposed_atoms"])
