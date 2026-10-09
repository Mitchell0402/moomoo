"""同一台电脑上只允许一个程序在跑，以及发现另一台电脑今天也跑过时停止交易。

锁是操作系统级的文件锁：程序退出或崩溃时系统自动释放，不会留下"死锁"文件。
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path


class LockBusy(RuntimeError):
    pass


@contextlib.contextmanager
def run_lock(path: Path):
    f = open(path, "a+b")
    try:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise LockBusy(str(e)) from e
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                with contextlib.suppress(OSError):
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        f.close()


def other_host(status: dict, host: str, today: str) -> str | None:
    """status.json 是每次运行后推到 GitHub 的。今天的记录来自另一台电脑，就返回那台电脑的名字。"""
    other = status.get("host")
    if other and other != host and str(status.get("time", "")).startswith(today):
        return str(other)
    return None
