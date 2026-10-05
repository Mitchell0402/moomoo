"""读取并校验 Claude 每天写的指令文件 signals/latest.json。

指令格式：
    {"date": "2026-10-06",
     "targets": {"US.SCHB": 0.50, "US.SCHF": 0.10, "US.SCHZ": 0.30, "US.SCHO": 0.10},
     "rationale": "为什么这样调"}

权重之和可以小于 1，差额留作现金。任何一条不符合 config.yaml 里的 signal 边界，整份指令作废，
继续沿用上一次接受的目标。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


def whitelist(sig_cfg: dict) -> list[str]:
    return [c for group in sig_cfg["groups"].values() for c in group]


def load_signal(path: Path, today: dt.date, max_age_days: int) -> tuple[dict | None, str]:
    try:
        sig = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "没有找到 Claude 的指令文件"
    except json.JSONDecodeError as e:
        return None, f"指令文件格式错误：{e}"
    try:
        date = dt.date.fromisoformat(str(sig["date"]))
    except (KeyError, ValueError):
        return None, "指令文件缺少有效的 date"
    age = (today - date).days
    if age < 0 or age > max_age_days:
        return None, f"指令日期 {date} 已过期（超过 {max_age_days} 天）"
    if not isinstance(sig.get("targets"), dict):
        return None, "指令文件缺少 targets"
    return sig, ""


def validate(targets: dict, sig_cfg: dict, prev: dict) -> list[str]:
    """返回所有不合规的原因；空列表表示通过。"""
    errors = []
    allowed = whitelist(sig_cfg)
    for c, w in targets.items():
        if c not in allowed:
            errors.append(f"{c} 不在白名单里")
        elif not isinstance(w, (int, float)) or w < 0:
            errors.append(f"{c} 的权重 {w} 无效")
    if errors:
        return errors
    total = sum(targets.values())
    if total > 1 + 1e-9:
        errors.append(f"权重合计 {total:.2f} 超过 1")
    stock = sum(targets.get(c, 0) for c in sig_cfg["groups"]["stock"])
    lo, hi = sig_cfg["stock_min"], sig_cfg["stock_max"]
    if not lo - 1e-9 <= stock <= hi + 1e-9:
        errors.append(f"股票合计 {stock:.0%} 不在 {lo:.0%}–{hi:.0%} 之间")
    cap = sig_cfg["max_daily_change"]
    prev_stock = sum(prev.get(c, 0) for c in sig_cfg["groups"]["stock"])
    if abs(stock - prev_stock) > cap + 1e-9:
        errors.append(f"股票比例一天变动 {abs(stock - prev_stock):.0%}，超过 {cap:.0%}")
    for c in allowed:
        change = abs(targets.get(c, 0) - prev.get(c, 0))
        if change > cap + 1e-9:
            errors.append(f"{c} 一天变动 {change:.0%}，超过 {cap:.0%}")
    return errors


def resolve_targets(path: Path, today: dt.date, sig_cfg: dict, prev: dict) -> tuple[dict, list[str], dict | None]:
    """返回 (今天使用的目标, 备注, 被接受的指令或 None)。所有白名单代码都会出现在目标里，没写的为 0。"""
    full = lambda t: {c: float(t.get(c, 0)) for c in whitelist(sig_cfg)}
    sig, why = load_signal(path, today, sig_cfg["max_age_days"])
    if sig is None:
        return full(prev), [f"{why}，沿用上一次的目标"], None
    errors = validate(sig["targets"], sig_cfg, prev)
    if errors:
        return full(prev), ["Claude 的指令被拒绝：" + "；".join(errors) + "。沿用上一次的目标"], None
    return full(sig["targets"]), [f"采用 Claude {sig['date']} 的指令"], sig
