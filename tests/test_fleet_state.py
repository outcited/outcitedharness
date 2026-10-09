from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fleet_state.py"
SPEC = importlib.util.spec_from_file_location("fleet_state", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
fleet_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fleet_state)


HEALTHZ = json.dumps(
    {
        "ready": True,
        "model": "bge-m3-cr-tapes-v1",
        "model_path": "/data/categoryrank/cr-finetune/models/bge-m3-cr-tapes-v1/latest",
        "device": "cuda",
        "routes": ["/encode", "/embed", "/v1/embeddings", "/semantic-search"],
        "uptime_sec": 21990.95,
    }
)


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def stub_http(monkeypatch, status, body):
    monkeypatch.setattr(fleet_state, "http_get", lambda url, timeout=8: (status, body))


def test_asus4_is_probed_directly_not_through_the_dead_fabric_relay() -> None:
    assert fleet_state.NODES["asus4"] == "samkimasus4@100.100.116.82"
    assert not hasattr(fleet_state, "ASUS4_RELAY")
    # asus4 gave its switch port to dgx1 on 2026-10-04, so 10.77.0.6 cannot route.
    # It must not survive as a probe target anywhere.
    targets = json.dumps(
        {"nodes": fleet_state.NODES, "endpoints": fleet_state.ENDPOINTS}, sort_keys=True)
    assert "10.77.0.6" not in targets


def test_ssh_docker_takes_a_single_host() -> None:
    import inspect

    assert list(inspect.signature(fleet_state.ssh_docker).parameters) == ["host"]


def test_embedder_is_probed_on_the_route_it_implements() -> None:
    url, kind = fleet_state.ENDPOINTS["spark_embedder"]
    assert url.endswith("/healthz")
    assert kind == "embedder"


def test_gci_is_probed() -> None:
    url, kind = fleet_state.ENDPOINTS["harness_gci"]
    assert url == "http://100.81.201.24:8810/health"
    assert kind == "gci"


def test_canon_is_probed_as_a_service() -> None:
    url, kind = fleet_state.ENDPOINTS["asus4_canon"]
    assert url == "http://100.100.116.82:8900/v1/models"
    assert kind == "models"


def test_both_canon_replicas_are_probed_on_their_own_ports() -> None:
    # asus4 serves :8900, the dgx1 replica serves :8901. A replica that is
    # running but not serving must be distinguishable from one that is absent.
    assert fleet_state.ENDPOINTS["asus4_canon"][0].endswith(":8900/v1/models")
    assert fleet_state.ENDPOINTS["dgx1_canon_replica"] == (
        "http://100.81.201.24:8901/v1/models", "models")


def test_http_get_splits_status_from_body(monkeypatch) -> None:
    monkeypatch.setattr(
        fleet_state.subprocess, "run",
        lambda *a, **k: _Result(stdout='{"ready": true}\n200'))
    assert fleet_state.http_get("http://x/healthz") == (200, '{"ready": true}')


def test_http_get_reports_zero_status_on_curl_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        fleet_state.subprocess, "run", lambda *a, **k: _Result(stdout="", returncode=7))
    assert fleet_state.http_get("http://x/healthz") == (0, "")


def test_http_get_reports_zero_status_on_timeout(monkeypatch) -> None:
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="curl", timeout=8)

    monkeypatch.setattr(fleet_state.subprocess, "run", boom)
    assert fleet_state.http_get("http://x/healthz") == (0, "")


def test_models_probe_lists_ids(monkeypatch) -> None:
    stub_http(monkeypatch, 200, json.dumps({"data": [{"id": "b"}, {"id": "a"}]}))
    assert fleet_state.probe_models("http://x/v1/models") == ["a", "b"]


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (0, "", ["UNREACHABLE"]),
        (404, '{"detail":"Not Found"}', ["HTTP_404"]),
        (500, "", ["HTTP_500"]),
        (200, "not json", ["HTTP_200_NON_JSON"]),
        (200, json.dumps({"data": []}), ["HTTP_200_NO_MODELS"]),
        (200, json.dumps({}), ["HTTP_200_NO_MODELS"]),
    ],
)
def test_models_probe_never_reports_an_empty_list(monkeypatch, status, body, expected) -> None:
    stub_http(monkeypatch, status, body)
    assert fleet_state.probe_models("http://x/v1/models") == expected


def test_embedder_probe_reports_identity(monkeypatch) -> None:
    stub_http(monkeypatch, 200, HEALTHZ)
    assert fleet_state.probe_embedder("http://x/healthz") == [
        "READY model=bge-m3-cr-tapes-v1 device=cuda routes=4"
    ]


def test_embedder_probe_ignores_uptime_so_a_healthy_service_is_quiet(monkeypatch) -> None:
    a = json.loads(HEALTHZ)
    b = dict(a, uptime_sec=a["uptime_sec"] + 300)
    stub_http(monkeypatch, 200, json.dumps(a))
    first = fleet_state.probe_embedder("http://x/healthz")
    stub_http(monkeypatch, 200, json.dumps(b))
    assert fleet_state.probe_embedder("http://x/healthz") == first


def test_embedder_probe_flags_a_stock_bge_m3_impostor(monkeypatch) -> None:
    # asus2's ~/embedder/serve.py exposes /health, not /healthz.
    stub_http(monkeypatch, 404, '{"detail":"Not Found"}')
    assert fleet_state.probe_embedder("http://x/healthz") == ["HTTP_404"]


def test_embedder_probe_flags_not_ready(monkeypatch) -> None:
    stub_http(monkeypatch, 200, json.dumps({"ready": False, "model": "bge-m3-cr-tapes-v1",
                                            "device": "cuda", "routes": []}))
    assert fleet_state.probe_embedder("http://x/healthz") == [
        "NOT_READY model=bge-m3-cr-tapes-v1 device=cuda routes=0"
    ]


def test_gci_probe_treats_401_as_alive(monkeypatch) -> None:
    stub_http(monkeypatch, 401, '{"error":"unauthorized"}')
    assert fleet_state.probe_gci("http://x/health") == ["ALIVE_AUTH_REQUIRED"]


def test_gci_probe_reads_queue_when_authenticated(monkeypatch) -> None:
    stub_http(monkeypatch, 200, json.dumps({"ready": True, "queue_depth": 3, "paused": False}))
    assert fleet_state.probe_gci("http://x/health") == ["READY queue_depth=3 paused=False"]


def test_gci_probe_reports_unreachable(monkeypatch) -> None:
    stub_http(monkeypatch, 0, "")
    assert fleet_state.probe_gci("http://x/health") == ["UNREACHABLE"]


def test_gci_probe_never_sends_a_bearer_token(monkeypatch) -> None:
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _Result(stdout="{}\n401")

    monkeypatch.setenv("HARNESS_GCI_TOKEN", "super-secret-value")
    monkeypatch.setattr(fleet_state.subprocess, "run", fake_run)
    fleet_state.probe_gci("http://x/health")
    assert "super-secret-value" not in " ".join(seen["cmd"])
    assert not any("Authorization" in str(part) for part in seen["cmd"])


def test_probe_endpoints_covers_every_endpoint_with_a_non_empty_result(monkeypatch) -> None:
    stub_http(monkeypatch, 0, "")
    state = fleet_state.probe_endpoints()
    assert set(state) == set(fleet_state.ENDPOINTS)
    assert all(isinstance(v, list) and v for v in state.values())


def test_probe_nodes_covers_asus4(monkeypatch) -> None:
    monkeypatch.setattr(fleet_state, "ssh_docker", lambda host: [host.split("@")[0]])
    state = fleet_state.probe_nodes()
    assert set(state) == set(fleet_state.NODES)
    assert state["asus4"] == ["samkimasus4"]


def test_ssh_docker_distinguishes_failure_from_idle(monkeypatch) -> None:
    monkeypatch.setattr(fleet_state.subprocess, "run",
                        lambda *a, **k: _Result(stdout="", returncode=255))
    assert fleet_state.ssh_docker("x@y") == ["SSH_FAIL"]
    monkeypatch.setattr(fleet_state.subprocess, "run", lambda *a, **k: _Result(stdout="\n"))
    assert fleet_state.ssh_docker("x@y") == ["NO_CONTAINERS"]
    monkeypatch.setattr(fleet_state.subprocess, "run",
                        lambda *a, **k: _Result(stdout="canon-qwen38-27b\n"))
    assert fleet_state.ssh_docker("x@y") == ["canon-qwen38-27b"]


def test_diff_reports_added_and_changed_keys() -> None:
    old = {"asus4": ["SSH_FAIL"], "spark_embedder": []}
    new = {"asus4": ["canon-qwen38-27b"], "spark_embedder": ["READY"], "harness_gci": ["ALIVE"]}
    assert fleet_state.diff(old, new) == [
        "asus4: ['SSH_FAIL'] -> ['canon-qwen38-27b']",
        "harness_gci: None -> ['ALIVE']",
        "spark_embedder: [] -> ['READY']",
    ]
