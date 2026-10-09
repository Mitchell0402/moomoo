"""状态文件（回撤高点、当前目标、对照账户）的读写。

写入先写临时文件再整体替换，同时留一份上一次的备份（.bak）；
读到损坏的文件时用备份，备份也不能用就报错，由调用方停止交易，而不是当作"第一次运行"悄悄清零。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


class StateError(RuntimeError):
    pass


def _read(path: Path):
    """返回 (内容, 错误原因)。内容必须是 JSON 对象。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "文件不存在"
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        return None, f"无法解析：{e}"
    if not isinstance(data, dict):
        return None, "内容不是一个 JSON 对象"
    return data, ""


def load_state(path: Path) -> tuple[dict, list[str]]:
    """返回 (状态, 备注)。文件和备份都不存在才算第一次运行，返回空状态。"""
    bak = path.with_name(path.name + ".bak")
    data, why = _read(path)
    if data is not None:
        return data, []
    if not path.exists():
        if bak.exists():
            data, _ = _read(bak)
            if data is not None:
                return data, [f"警告：{path.name} 不见了，已改用上一次的备份"]
            raise StateError(f"{path.name} 不见了，备份 {bak.name} 也无法读取")
        return {}, []
    data, bak_why = _read(bak)
    if data is not None:
        return data, [f"警告：{path.name} 已损坏（{why}），已改用上一次的备份"]
    raise StateError(f"{path.name} 已损坏（{why}），备份也不可用（{bak_why}）")


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(json.dumps(state, indent=2))
        f.flush()
        os.fsync(f.fileno())
    if _read(path)[0] is not None:
        # 只有当前文件是好的才把它留作备份，坏文件不能覆盖掉好备份
        bak = path.with_name(path.name + ".bak")
        bak_tmp = path.with_name(path.name + ".bak.tmp")
        bak_tmp.write_bytes(path.read_bytes())
        _replace(bak_tmp, bak)
    _replace(tmp, path)


def _replace(src: Path, dst: Path, tries: int = 10) -> None:
    # Windows 上别的程序（比如看板）刚好在读这个文件时，替换会被拒绝，稍等再试
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.1)
