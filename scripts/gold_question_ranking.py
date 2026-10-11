#!/usr/bin/env python3
"""Run the LIVE reference `discovery.service.next_question` over a fixture
population and print its output as JSON — the gold for the Rust port.

The reference is the spec (D8); this script generates fixtures FROM it, live,
never from memory. `design_id` is arbitrary: the fixture db has no atoms, so
`pinned_axes` are inserted as active atoms to exercise the answered-axis skip.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.discovery import service  # noqa: E402
from harness.discovery import store  # noqa: E402


def main() -> None:
    fixture_path = sys.argv[1]
    fixture = json.loads(Path(fixture_path).read_text())
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "discovery.db")
        con = store.connect(db_path)
        design_id = "fixture-design"
        con.execute(
            "INSERT INTO designs (design_id, aisle, name, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            (design_id, "power", "fixture", 0.0, 0.0),
        )
        for axis in fixture.get("pinned_axes", []):
            con.execute(
                "INSERT INTO atoms (design_id, axis, op, value_json, hard,"
                " margin_pct, required_condition_json, state, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (design_id, axis, "min_rating", "1.0", 1, None, None, "active", 0.0),
            )
        con.commit()
        con.close()
        out = service.next_question(
            db_path, design_id, population=fixture["population"]
        )
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    print()


if __name__ == "__main__":
    main()
