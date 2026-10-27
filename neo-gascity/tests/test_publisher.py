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


if __name__ == "__main__":
    unittest.main(verbosity=2)
