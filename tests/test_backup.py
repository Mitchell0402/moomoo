"""备份：不进仓库的文件复制到 backup/，密码类字段清空。"""
import subprocess
from pathlib import Path

from autoinvest.backup import sanitize, snapshot

ROOT = Path(__file__).resolve().parent.parent


def test_sanitize_blanks_secrets_only():
    text = "acc_id: 123\nunlock_password: abc123\n  token: xyz\n# password: keep-comment\ntrd_env: SIMULATE\n"
    out = sanitize(text)
    assert "abc123" not in out and "xyz" not in out
    assert "acc_id: 123" in out and "trd_env: SIMULATE" in out
    assert "# password: keep-comment" in out


def test_snapshot_copies_local_files(tmp_path):
    (tmp_path / "config.yaml").write_text("acc_id: 1\n", encoding="utf-8")
    (tmp_path / "state.json").write_text('{"peak_value": 2010}', encoding="utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "run-20261008-103000.json").write_text("{}", encoding="utf-8")
    dest = snapshot(tmp_path, logs, with_task=False)
    assert (dest / "config.yaml").read_text(encoding="utf-8") == "acc_id: 1\n"
    assert (dest / "state.json").exists() and not (dest / "state-real.json").exists()
    assert (dest / "logs" / "run-20261008-103000.json").exists()


def test_backup_copies_are_not_gitignored():
    paths = ["backup/config.yaml", "backup/state.json", "backup/state-real.json", "backup/logs/run-1.json"]
    p = subprocess.run(["git", "-C", str(ROOT), "check-ignore", *paths], capture_output=True, text=True)
    assert p.stdout.strip() == ""
    p = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "config.yaml", "state.json", "logs/run-1.json"],
                       capture_output=True, text=True)
    assert len(p.stdout.split()) == 3
