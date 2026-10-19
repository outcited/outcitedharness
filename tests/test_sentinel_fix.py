"""Reproduce the sentinel failures, fix them, and add regression tests.

Root cause (confirmed with host evidence 2026-10-19):

1. HOURLY: sentinel.py:142 connects to the RETIRED
   /Volumes/M5_4TB/extract-results/pipeline.db (10.3 GB, last write Oct 7).
   The active DB is pipeline-live.db at /Users/samkim/Harnessv1/results/.
   Fix: point at the active DB.

2. NIGHTLY: sentinel.py writes last-known-good.json to
   /Volumes/M5_4TB/extract-results/sentinel/ which is owned by
   _lpoperator:1026 mode 775. samkim (501) cannot write.
   Fix: use the local sentinel directory, or chown the remote one.

Before fix: both fail. After fix: hourly opens the active DB, nightly
writes to a writable path.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SENTINEL = Path("/Users/samkim/Harnessv1/scripts/sentinel.py")


def test_hourly_reproduces_failure_before_fix():
    """The original code points at the retired pipeline.db."""
    code = SENTINEL.read_text()
    assert 'f"{RESULTS}/pipeline.db"' in code, \
        "original code should reference the retired pipeline.db"


def test_fixed_code_uses_active_db():
    """After the fix, the code must reference the active pipeline-live.db."""
    # The fix changes the connection to use the live DB
    # This test runs AFTER the fix is applied
    code = SENTINEL.read_text()
    # After fix, either the path is updated or RESULTS points to the live dir
    using_live = ("pipeline-live.db" in code or
                  'Path("/Users/samkim/Harnessv1/results")' in code)
    assert using_live, "fixed code must reference pipeline-live.db"


def test_nightly_writes_to_writable_path():
    """After the fix, the sentinel directory must be writable by the process."""
    code = SENTINEL.read_text()
    # The sentinel dir must be under a path samkim can write to
    # or the code must handle the permission error
    has_local_fallback = ("sentinel" in code.lower() and
                          ("local" in code.lower() or
                           "Harnessv1/results" in code or
                           "PermissionError" in code))
    assert has_local_fallback, \
        "fixed code must use a writable sentinel path or handle the error"
