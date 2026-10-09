"""早上就能看到 Claude 当天的计划：定时 git fetch，直接读 GitHub 上最新的 signals/latest.json。

只 fetch，不 pull、不改电脑上的文件，交易程序照旧在 10:30 自己拉代码。
程序运行前后几分钟不 fetch，免得两边同时动 git 时程序的 git pull 失败。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from . import timeutil

log = logging.getLogger("dashboard")
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # pythonw 下调 git 不弹黑窗口


def git(root: Path, *args: str, timeout: int = 60) -> tuple[bool, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    try:
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=env, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def near_run(now: dt.datetime, schedule: list[str], before: int = 5, after: int = 10) -> bool:
    for s in schedule:
        at = dt.datetime.combine(now.date(), dt.time.fromisoformat(s))
        if at - dt.timedelta(minutes=before) <= now <= at + dt.timedelta(minutes=after):
            return True
    return False


class GitSignals:
    def __init__(self, root: Path, schedule: list[str], minutes: int = 15, enabled: bool = True):
        self.root, self.schedule, self.minutes, self.enabled = root, schedule, max(5, minutes), enabled
        self._lock = threading.Lock()
        self._data = {"ok": None, "fetched_at": None, "message": "", "signal": None, "halt": False}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="git", daemon=True)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._data)

    def start(self):
        if self.enabled and (self.root / ".git").exists():
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            now = timeutil.now_et()
            if not near_run(now, self.schedule):
                self.refresh(now)
            self._stop.wait(self.minutes * 60)

    def refresh(self, now: dt.datetime):
        ok, out = git(self.root, "fetch", "--quiet")
        sig, halt = None, False
        if ok:
            good, text = git(self.root, "show", "@{upstream}:signals/latest.json", timeout=20)
            if good:
                try:
                    sig = json.loads(text)
                except ValueError:
                    sig = None
            halt, _ = git(self.root, "cat-file", "-e", "@{upstream}:signals/HALT", timeout=20)
        else:
            log.info("git fetch 失败：%s", out[-300:])
        with self._lock:
            self._data = {"ok": ok, "fetched_at": now.isoformat(timespec="seconds"),
                          "message": "" if ok else out[-200:], "signal": sig if ok else self._data["signal"],
                          "halt": halt if ok else self._data["halt"]}
