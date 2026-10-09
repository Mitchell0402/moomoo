import subprocess
import sys
import time
from pathlib import Path

import pytest

from autoinvest.runlock import LockBusy, other_host, run_lock

ROOT = Path(__file__).resolve().parent.parent


def test_second_holder_is_refused(tmp_path):
    lock = tmp_path / ".run.lock"
    code = ("import sys, time; from pathlib import Path; from autoinvest.runlock import run_lock\n"
            "with run_lock(Path(sys.argv[1])):\n    print('held', flush=True); time.sleep(3)")
    child = subprocess.Popen([sys.executable, "-c", code, str(lock)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        with pytest.raises(LockBusy):
            with run_lock(lock):
                pass
    finally:
        child.wait(timeout=10)
    with run_lock(lock):
        pass


def test_lock_is_reusable_in_one_process(tmp_path):
    lock = tmp_path / ".run.lock"
    with run_lock(lock):
        pass
    with run_lock(lock):
        pass


def test_other_host():
    st = {"host": "OLD-PC", "time": "2026-10-12T10:30:01"}
    assert other_host(st, "NEW-PC", "2026-10-12") == "OLD-PC"
    assert other_host(st, "OLD-PC", "2026-10-12") is None
    assert other_host(st, "NEW-PC", "2026-10-13") is None
    assert other_host({"time": "2026-10-12T10:30:01"}, "NEW-PC", "2026-10-12") is None
