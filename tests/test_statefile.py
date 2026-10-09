import json
import os
import shutil
from pathlib import Path

import pytest

from autoinvest.statefile import StateError, load_state, save_state

ROOT = Path(__file__).resolve().parent.parent


def test_missing_file_is_empty(tmp_path):
    assert load_state(tmp_path / "state.json") == ({}, [])


def test_roundtrip_and_backup(tmp_path):
    p = tmp_path / "state.json"
    save_state(p, {"peak_value": 1})
    save_state(p, {"peak_value": 2})
    assert json.loads(p.read_text(encoding="utf-8"))["peak_value"] == 2
    assert json.loads((tmp_path / "state.json.bak").read_text(encoding="utf-8"))["peak_value"] == 1
    assert load_state(p)[0]["peak_value"] == 2


def test_truncated_file_falls_back_to_backup(tmp_path):
    p = tmp_path / "state.json"
    p.write_text('{"peak_value": 30', encoding="utf-8")
    (tmp_path / "state.json.bak").write_text('{"peak_value": 3000}', encoding="utf-8")
    state, notes = load_state(p)
    assert state["peak_value"] == 3000
    assert notes and notes[0].startswith("警告")


def test_corrupt_without_backup_raises(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(StateError):
        load_state(p)


def test_corrupt_does_not_become_the_backup(tmp_path):
    p = tmp_path / "state.json"
    save_state(p, {"peak_value": 4})
    save_state(p, {"peak_value": 5})
    p.write_text('{"peak', encoding="utf-8")
    save_state(p, {"peak_value": 6})
    assert json.loads((tmp_path / "state.json.bak").read_text(encoding="utf-8"))["peak_value"] == 4


def test_replace_retries_on_permission_error(tmp_path, monkeypatch):
    real = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("in use")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    monkeypatch.setattr("autoinvest.statefile.time.sleep", lambda s: None)
    p = tmp_path / "state.json"
    save_state(p, {"peak_value": 7})
    assert json.loads(p.read_text(encoding="utf-8"))["peak_value"] == 7
    assert calls["n"] == 3


def test_current_pc_state_loads_unchanged(tmp_path):
    src = ROOT / "backup" / "state.json"
    shutil.copy(src, tmp_path / "state.json")
    state, notes = load_state(tmp_path / "state.json")
    assert state == json.loads(src.read_text(encoding="utf-8"))
    assert notes == []
