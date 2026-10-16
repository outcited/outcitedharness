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

    ver = sub.add_parser("verify")
    ver.add_argument("--workspace", required=True)

    a = ap.parse_args(argv)
    if a.cmd == "verify":
        from .state import DirectorState
        st = DirectorState(Path(a.workspace))
        ok, broken = st.verify_chain()
        print(json.dumps({"chain_ok": ok, "broken_at": broken}))
        return 0 if ok else 1

    feeds = load_feeds(Path(a.feeds)) if a.feeds else {}
    llm = None
    if not a.no_llm:
        from .llm import OpsBrainClient
        llm = OpsBrainClient(enabled=True)
    c = DirectorConsumer(Path(a.workspace), Path(a.deps), feeds=feeds, llm=llm)
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
