"""把电脑上不进仓库的文件备份到 backup/，随每天的数据一起推到 GitHub。

重装电脑后，按 docs/restore.md 从 backup/ 拷回去就能接着跑：
- config.yaml（密码类字段会清空，现在的配置里没有这类字段）
- state.json / state-real.json（账户高点、当前目标、对照策略的虚拟账户）
- logs/run-*.json（每次运行的完整记录）
- moomoo-autoinvest.xml（Windows 计划任务的定义，只在 Windows 上导出）
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

TASK_NAME = "moomoo-autoinvest"
SECRET_KEY = re.compile(r"pass|pwd|secret|token|unlock|cookie", re.I)
LINE = re.compile(r"^(\s*)([A-Za-z0-9_.-]+)(\s*):(\s*)(\S.*)$")


def sanitize(text: str) -> str:
    """密码、口令一类的字段清空，其余原样保留。"""
    out = []
    for line in text.splitlines():
        m = LINE.match(line)
        if m and SECRET_KEY.search(m.group(2)) and not line.lstrip().startswith("#"):
            line = f'{m.group(1)}{m.group(2)}: ""  # 备份时已清空，恢复后手动填'
        out.append(line)
    return "\n".join(out) + "\n"


def export_task(dest: Path) -> bool:
    """导出 Windows 计划任务的 XML。只读操作，不改任务本身。"""
    if sys.platform != "win32":
        return False
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", f"Export-ScheduledTask -TaskName '{TASK_NAME}'"],
                           capture_output=True, text=True, errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if p.returncode != 0 or "<Task" not in p.stdout:
        return False
    (dest / f"{TASK_NAME}.xml").write_text(p.stdout, encoding="utf-8")
    return True


def snapshot(root: Path, log_dir: Path, with_task: bool = True) -> Path:
    """把要备份的文件复制到 root/backup，返回这个目录。"""
    dest = root / "backup"
    dest.mkdir(exist_ok=True)
    cfg = root / "config.yaml"
    if cfg.exists():
        (dest / "config.yaml").write_text(sanitize(cfg.read_text(encoding="utf-8")), encoding="utf-8")
    for name in ("state.json", "state-real.json"):
        if (root / name).exists():
            shutil.copy2(root / name, dest / name)
    logs = dest / "logs"
    logs.mkdir(exist_ok=True)
    for f in log_dir.glob("run-*.json"):
        if not (logs / f.name).exists():
            shutil.copy2(f, logs / f.name)
    if with_task:
        export_task(dest)
    return dest
