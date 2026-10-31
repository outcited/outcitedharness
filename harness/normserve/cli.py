"""CLI: dry-run loader + serve.

usage:
  python -m harness.normserve --load rows.jsonl --opn ST1S15 \
      --db norm.db [--vault-root V] [--dry-run]
  python -m harness.normserve --serve --db norm.db [--port 8801]
"""

import argparse
import json

from .loader import Store, load_jsonl, load_rows
from .serve import serve


def main():
    ap = argparse.ArgumentParser(prog="harness.normserve")
    ap.add_argument("--load")
    ap.add_argument("--rows", help="inline JSON array (for tests)")
    ap.add_argument("--opn", help="operator-supplied OPN default (samples "
                                 "without an opn field)")
    ap.add_argument("--db", default="norm.dryrun.db")
    ap.add_argument("--vault-root")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8801)
    args = ap.parse_args()

    store = Store(args.db)
    if args.serve:
        serve(store, host=args.host, port=args.port)
        return
    if not (args.load or args.rows):
        ap.error("one of --load/--rows/--serve required")
    if args.load:
        report = load_jsonl(store, args.load, vault_root=args.vault_root,
                            dry_run=args.dry_run, opn_default=args.opn)
    else:
        rows = json.loads(args.rows)
        report = load_rows(store, rows, vault_root=args.vault_root,
                           dry_run=args.dry_run, opn_default=args.opn)
    print(json.dumps(report.summary(), indent=2))


if __name__ == "__main__":
    main()
