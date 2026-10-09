"""实盘和模拟盘的记录放在不同的文件夹里，曲线和排行不会接在一起。"""
from __future__ import annotations


def log_dir_name(cfg: dict) -> str:
    """模拟盘用 log_dir（默认 logs），实盘用它下面的 real 子文件夹。"""
    base = str(cfg.get("log_dir", "logs")).rstrip("/\\")
    return base if cfg.get("trd_env", "SIMULATE") == "SIMULATE" else f"{base}/real"
