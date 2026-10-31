import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_curve_queue.py"), *argv],
        capture_output=True,
        text=True,
    )


def _write_bundle(root: Path, rows: list[dict]) -> Path:
    bundle = root / "page-evidence"
    bundle.mkdir(parents=True, exist_ok=True)
    with (bundle / "page-evidence.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return bundle


def _row(sha: str, page: int, lanes: list[str]) -> dict:
    return {
        "schema": "harness.electronics-page-evidence.v2",
        "document_sha256": sha,
        "source_path": f"/corpus/{sha[:12]}.pdf",
        "page_1based": page,
        "lanes": lanes,
        "evidence_sha256": hashlib.sha256(f"{sha}:{page}".encode()).hexdigest(),
    }


def test_evidence_mode_selects_typical_characteristics_rows(tmp_path):
    sha = "a" * 64
    bundle = _write_bundle(
        tmp_path,
        [
            _row(sha, 29, ["typical_characteristics"]),
            _row(sha, 15, ["typical_characteristics"]),
            _row(sha, 3, ["pin_or_ball"]),
            _row("b" * 64, 7, []),
        ],
    )
    out = tmp_path / "queue.json"
    result = _run("--page-evidence", str(bundle), "--out", str(out))
    assert result.returncode == 0, result.stderr
    work = json.loads(out.read_text())["work"]
    assert {item["page_1based"] for item in work} == {15, 29}
    for item in work:
        assert item["schema"] == "harness.electronics-local-model-work.v1"
        assert item["capability"] == "typical_characteristics"
        assert item["document_sha256"] == sha
        expected = "curve-" + hashlib.sha256(
            f"{sha}:{item['page_1based']}".encode()
        ).hexdigest()[:32]
        assert item["work_id"] == expected


def test_evidence_mode_missing_bundle_fails_closed(tmp_path):
    result = _run(
        "--page-evidence", str(tmp_path / "nope"), "--out", str(tmp_path / "o.json")
    )
    assert result.returncode != 0


def test_evidence_and_queue_are_mutually_exclusive(tmp_path):
    result = _run(
        "--page-evidence", str(tmp_path),
        "--queue", str(tmp_path / "q.json"),
        "--out", str(tmp_path / "o.json"),
    )
    assert result.returncode != 0
