"""DW-TRUTH-FUNNEL-01 — fail-closed, grain-separated, deterministic."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "tf", ROOT / "scripts/truth_funnel.py")
tf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tf)


def run(tmp_path, *extra):
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/truth_funnel.py"),
         "--out-dir", str(tmp_path / "out")] + list(extra),
        capture_output=True, text=True, timeout=300)
    out = tmp_path / "out"
    j = out / "designwins_truth_funnel_v1.json"
    d = json.loads(j.read_text()) if j.exists() else None
    return r.returncode, d


def test_missing_required_source_is_unknown_and_fails_closed(tmp_path):
    rc, d = run(tmp_path, "--staging-root", str(tmp_path / "nope"),
                "--pin-batch-dir", str(tmp_path / "nope2"),
                "--cr-core", str(tmp_path / "nope3"),
                "--nas-root", str(tmp_path / "nope4"),
                "--export-root", str(tmp_path / "nope5"))
    assert d is not None
    stages = {s["stage"]: s for s in d["stages"]}
    for name in ("c.facet_parts", "d.staged_pin_rows"):
        assert stages[name]["count"] == "UNKNOWN"
        assert stages[name]["status"] == "UNKNOWN"
    assert d["errors"], "missing sources must be listed as errors"
    assert rc == 1, "fail-closed: missing sources -> nonzero exit"


def test_directory_sources_are_valid_not_errors(tmp_path):
    (tmp_path / "staging").mkdir()
    (tmp_path / "staging" / "a.json").write_text('{"pinout": {"pins": [1,2]}}')
    rc, d = run(tmp_path, "--staging-root", str(tmp_path / "staging"),
                "--pin-batch-dir", str(tmp_path / "nb"),
                "--cr-core", str(tmp_path / "cc"),
                "--nas-root", str(tmp_path / "nas"),
                "--export-root", str(tmp_path / "ex"))
    src = d["sources"]["staging_root"]
    assert src["available"] is True and src.get("is_dir") is True


def test_grains_never_summed_across_populations(tmp_path):
    rc, d = run(tmp_path, "--staging-root", str(tmp_path / "nope"),
                "--pin-batch-dir", str(tmp_path / "nb"),
                "--cr-core", str(tmp_path / "cc"),
                "--nas-root", str(tmp_path / "nas"),
                "--export-root", str(tmp_path / "ex"))
    grains = {s["grain"] for s in d["stages"]}
    assert grains <= set(tf.GRANS) | {"UNKNOWN"}
    # no stage may present a single percentage or combined count
    for s in d["stages"]:
        assert "%" not in str(s["count"])


def test_cas_counter_only_counts_64hex_stems(tmp_path):
    cas = tmp_path / "cas"
    for aa in ("00", "01"):
        (cas / aa / "bb").mkdir(parents=True)
    (cas / "00" / "bb" / ("a" * 64 + ".pdf")).write_bytes(b"x")
    (cas / "00" / "bb" / "not-a-sha.pdf").write_bytes(b"x")
    (cas / "01" / "bb" / ("b" * 64)).write_bytes(b"x")
    files, shas, _ = tf.count_cas_shas([str(cas)])
    assert files == 2 and shas == 2
