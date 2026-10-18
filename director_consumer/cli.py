"""CLI: run (once/loop), replay-check, verify journal chain.

Examples:
  python -m director_consumer.cli run --workspace /tmp/dw --once
  python -m director_consumer.cli verify --workspace /tmp/dw
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .consumer import DirectorConsumer, load_feeds

DEFAULT_DEPS = Path(__file__).with_name("deps.yaml")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser("director-consumer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run")
    run.add_argument("--workspace", required=True)
    run.add_argument("--deps", default=str(DEFAULT_DEPS))
    run.add_argument("--feeds", help="feeds.yaml (paths per deploy)")
    run.add_argument("--once", action="store_true")
    run.add_argument("--interval", type=float, default=600)
    run.add_argument("--no-llm", action="store_true")
    run.add_argument("--project", help="project config dir (e.g. projects/designwins)")
    run.add_argument("--fleet-yaml")
    run.add_argument("--agent-network")
    run.add_argument("--snap-dir")
    run.add_argument("--factory-db")
    run.add_argument("--pipeline-db")
    run.add_argument("--facet-root")
    run.add_argument("--intake-log")
    run.add_argument("--l0-index")
    run.add_argument("--backlog-paths")
    run.add_argument("--class-costs", help="json file: {class: {hours_per_doc}}")
    run.add_argument("--sessions-db")
    run.add_argument("--mailbox", help="shared-docs-queue root (ownership hints)")
    run.add_argument("--machine-id", default="unknown")
    run.add_argument("--repo", help="git repo for ownership claims")
    run.add_argument("--github-repo", action="append", default=[],
                     help="explicitly opt into polling one allowlisted repo's "
                          "open issues once per Director tick (repeatable)")
    run.add_argument("--github-project-id",
                     help="already-registered project id to scope GitHub tasks")

    ver = sub.add_parser("verify")
    ver.add_argument("--workspace", required=True)

    reg = sub.add_parser("register-project")
    reg.add_argument("--workspace", required=True)
    reg.add_argument("--project-dir", required=True)

    reh = sub.add_parser("rehearse")
    reh.add_argument("--workspace", required=True)

    a = ap.parse_args(argv)
    if a.cmd == "rehearse":
        from .rehearse import run_rehearsal
        rep = run_rehearsal(Path(a.workspace))
        print(json.dumps(rep["summary"], indent=1))
        failed = [k for k, v in rep["cases"].items() if not v.get("pass")]
        if failed:
            print("FAILED CASES:", failed, file=sys.stderr)
            return 1
        return 0
    if a.cmd == "register-project":
        from .projects import ProjectRegistry, load_spec_from_config
        from .state import DirectorState
        st = DirectorState(Path(a.workspace))
        spec = load_spec_from_config(Path(a.project_dir))
        ProjectRegistry(st.con).register(spec)
        ok, broken = st.verify_chain()
        st.close()
        print(json.dumps({"registered": spec.project_id,
                          "policy_version": spec.policy_version,
                          "config_digest": spec.config_digest,
                          "chain_ok": ok}))
        return 0
    if a.cmd == "verify":
        from .state import DirectorState
        st = DirectorState(Path(a.workspace))
        ok, broken = st.verify_chain()
        print(json.dumps({"chain_ok": ok, "broken_at": broken}))
        return 0 if ok else 1

    if a.github_repo and not a.github_project_id:
        ap.error("--github-project-id is required when using --github-repo")
    if a.github_project_id and not a.github_repo:
        ap.error("--github-repo is required when using --github-project-id")

    feeds = load_feeds(Path(a.feeds)) if a.feeds else {}
    llm = None
    if not a.no_llm:
        from .llm import OpsBrainClient
        llm = OpsBrainClient(enabled=True)
    continuity = None
    if any((a.factory_db, a.pipeline_db, a.facet_root, a.intake_log,
            a.l0_index, a.backlog_paths)):
        from .continuity import FactoryContinuity
        thresholds = {}
        if a.project:
            import yaml as _y
            fy = Path(a.project) / "fleet.yaml"
            if fy.exists():
                thresholds = (_y.safe_load(fy.read_text()) or {}) \
                    .get("factory_continuity", {})
        class_costs = {}
        if a.class_costs:
            import json as _j
            class_costs = _j.loads(Path(a.class_costs).read_text())
        continuity = FactoryContinuity(
            factory_db=a.factory_db, pipeline_db=a.pipeline_db,
            facet_root=a.facet_root, intake_log=a.intake_log,
            l0_index=a.l0_index, backlog_paths=a.backlog_paths,
            class_costs=class_costs, thresholds=thresholds)
    c = DirectorConsumer(
        Path(a.workspace), Path(a.deps), feeds=feeds, llm=llm,
        project_dir=Path(a.project) if a.project else None,
        fleet_yaml=a.fleet_yaml, agent_network=a.agent_network,
        snap_dir=a.snap_dir, continuity=continuity,
        sessions_db=a.sessions_db, mailbox_root=a.mailbox,
        repo_path=a.repo, machine_id=a.machine_id,
        github_repos=tuple(a.github_repo),
        github_project_id=a.github_project_id or "")
    while True:
        r = c.run_once()
        print(json.dumps(r.__dict__, default=str), flush=True)
        if a.once:
            break
        time.sleep(a.interval)
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
