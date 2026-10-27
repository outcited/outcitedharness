"""Publisher tests — review finding 3 (stability, sanitization, checkout guard).

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "status_publish", ROOT / "ops" / "status_publish.py")
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

from bridge import neo_status as NS  # noqa: E402


class TestChangeDetection(unittest.TestCase):
    def test_stable_digest_ignores_volatile_fields(self):
        base = {"city": "c", "supervisor": {"status": "ok", "uptime_sec": 1},
                "city_info": {"work": {"ready": 5}}}
        a = json.loads(json.dumps(base))
        b = json.loads(json.dumps(base))
        a["captured_at"] = "T1"; a["supervisor"]["uptime_sec"] = 10
        b["captured_at"] = "T2"; b["supervisor"]["uptime_sec"] = 9999
        b["recent_events"] = [{"seq": 5}]; b["last_event_seq"] = 5
        self.assertEqual(json.dumps(sp._stable(a), sort_keys=True),
                         json.dumps(sp._stable(b), sort_keys=True))

    def test_real_state_change_detected(self):
        a = {"city": "c", "city_info": {"work": {"ready": 5}}}
        b = {"city": "c", "city_info": {"work": {"ready": 6}}}
        self.assertNotEqual(json.dumps(sp._stable(a), sort_keys=True),
                            json.dumps(sp._stable(b), sort_keys=True))

    def test_digest_file_roundtrip(self):
        with TemporaryDirectory() as tmp:
            sd = Path(tmp) / "status"
            sd.mkdir()
            self.assertTrue(sp._digest_changed(None, sd, "d1"))     # first run
            self.assertFalse(sp._digest_changed(None, sd, "d1"))    # same state
            self.assertTrue(sp._digest_changed(None, sd, "d2"))     # real change

    def test_heartbeat_due(self):
        with TemporaryDirectory() as tmp:
            import time as _t
            sd = Path(tmp) / "status" / "history"
            sd.mkdir(parents=True)
            self.assertTrue(sp._heartbeat_due(sd))
            now = _t.strftime("%Y%m%dT%H%M%SZ", _t.gmtime())
            (sd / f"{now}.json").write_text("{}")
            self.assertFalse(sp._heartbeat_due(sd))


class TestCheckoutGuard(unittest.TestCase):
    def test_marker_required(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            sp.WORKTREE = repo  # point publisher at an unmarked checkout
            os.system(f"git -C {repo} init -q")
            os.system(f"git -C {repo} config user.email t@t; git -C {repo} config user.name t")
            (repo / "f.txt").write_text("user work")
            os.system(f"git -C {repo} add f.txt && git -C {repo} commit -qm wip")
            with self.assertRaises(RuntimeError):
                sp.git(["git", "reset", "--hard", "HEAD"], repo)
            # marker present -> allowed (still the dedicated dir)
            (repo / sp.MARKER).write_text("")
            sp.git(["git", "reset", "--hard", "HEAD"], repo)

    def test_foreign_worktree_refused(self):
        with TemporaryDirectory() as tmp:
            other = Path(tmp) / "user-worktree"
            other.mkdir()
            os.system(f"git -C {other} init -q && git -C {other} config user.email t@t "
                      f"&& git -C {other} config user.name t && "
                      f"touch {other}/wip && git -C {other} add wip "
                      f"&& git -C {other} commit -qm wip")
            sp.WORKTREE = Path(tmp) / "elsewhere"  # publisher's root is elsewhere
            (other / sp.MARKER).write_text("")     # even with a forged marker
            with self.assertRaises(RuntimeError):
                sp.git(["git", "reset", "--hard", "HEAD"], other)


class TestPublisherSanitizeIntegration(unittest.TestCase):
    def test_snapshot_files_are_sanitized(self):
        # the exact object publish() writes must contain no prompt/description
        snap = NS.sanitize_snapshot({
            "pending_interactions": [{"prompt": "SECRET"}],
            "findings": [{"description": "SECRET"}],
            "recent_events": [{"payload": {"SECRET": 1}}],
        })
        self.assertNotIn("SECRET", json.dumps(snap))


class TestFreshnessHeartbeat(unittest.TestCase):
    """P3 — freshness committed every run; history only on change."""

    def test_freshness_logic(self):
        # decision rule: history when changed, no history yet, or heartbeat
        # due; FRESHNESS is written every run regardless
        from pathlib import Path
        import tempfile, time
        with tempfile.TemporaryDirectory() as tmp:
            sd = Path(tmp) / "status"
            (sd / "history").mkdir(parents=True)
            # no history at all -> first entry due
            self.assertTrue(sp._heartbeat_due(sd / "history"))
            now = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            (sd / "history" / f"{now}.json").write_text("{}")
            # fresh entry exists, state unchanged -> no new history entry;
            # freshness digest file is still (re)written every run
            self.assertFalse(sp._heartbeat_due(sd / "history"))
            self.assertTrue(sp._digest_changed(None, sd, "d1"))    # first run
            self.assertFalse(sp._digest_changed(None, sd, "d1"))   # unchanged
            self.assertTrue(sp._digest_changed(None, sd, "d2"))     # real change

    def test_main_fails_visibly_on_publish_error(self):
        from unittest import mock
        with mock.patch.object(sp, "publish", side_effect=RuntimeError("tunnel down")):
            rc = sp.main.__wrapped__ if hasattr(sp.main, "__wrapped__") else None
        # call main with patched publish and argparse args
        import contextlib, io
        argv = sys.argv
        sys.argv = ["status_publish.py"]
        try:
            with mock.patch.object(sp, "publish", side_effect=RuntimeError("tunnel down")):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = sp.main()
        finally:
            sys.argv = argv
        self.assertEqual(code, 1)
        self.assertIn("tunnel down", err.getvalue())

    def test_freshness_file_fields(self):
        # NEO must detect staleness from GitHub alone: ts + interval + max age
        import time as _t
        ts = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
        from bridge import neo_status as NS
        snap = {"captured_at": ts, "city": {"work": {}}, "agents": [],
                "findings": [], "pending_interactions": []}
        rules = [a["rule"] for a in NS.evaluate(snap)]
        self.assertNotIn("snapshot-stale", rules)
        old = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(_t.time() - 40 * 60))
        snap["captured_at"] = old
        rules = [a["rule"] for a in NS.evaluate(snap)]
        self.assertIn("snapshot-stale", rules)


if __name__ == "__main__":
    unittest.main(verbosity=2)
