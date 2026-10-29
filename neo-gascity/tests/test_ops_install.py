"""Ops packaging tests — the activation defect found 2026-10-27 must not recur.

The publisher plist shipped with a stale absolute path (pre-merge layout) and
exited 2 on first launch. Now: plists carry __NEO_GC_ROOT__ placeholders and
install-m5.sh substitutes the resolved checkout root into the INSTALLED copy.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPS = ROOT / "ops"


class TestOpsPackaging(unittest.TestCase):
    def test_publisher_plist_uses_placeholder(self):
        raw = (OPS / "com.neogascity.publisher.plist").read_text()
        self.assertIn("__NEO_GC_ROOT__/neo-gascity/ops/status_publish.py", raw)
        self.assertNotIn("/Users/samkim", raw, "no stale absolute home paths")

    def test_tunnel_plist_has_no_checkout_paths(self):
        raw = (OPS / "com.neogascity.tunnel.plist").read_text()
        self.assertNotIn("__NEO_GC_ROOT__", raw)   # tunnel needs no script path
        self.assertNotIn("/Users/samkim", raw)

    def test_install_script_resolves_root_and_substitutes(self):
        sh = (OPS / "install-m5.sh").read_text()
        self.assertIn('ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"',
                      sh)
        self.assertIn('s|__NEO_GC_ROOT__|$ROOT|g', sh)
        self.assertIn("plutil -lint", sh)          # installed plist must be valid
        self.assertIn("__NEO_GC_ROOT__", sh)        # guards the placeholder

    def test_install_script_fails_closed_on_bad_layout(self):
        sh = (OPS / "install-m5.sh").read_text()
        self.assertIn("lacks template placeholder", sh)
        self.assertIn("status_publish.py not found", sh)

    def test_script_target_exists_relative_to_packaged_root(self):
        # the path the installed plist will reference after substitution
        self.assertTrue((ROOT / "ops" / "status_publish.py").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
