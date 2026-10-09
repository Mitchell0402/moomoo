"""读取并校验 Claude 每天写的指令文件 signals/latest.json。

指令格式：
    {"date": "2026-10-06",
     "targets": {"US.SCHB": 0.50, "US.SCHF": 0.10, "US.SCHZ": 0.30, "US.SCHO": 0.10},
     "rationale": "为什么这样调"}

权重之和可以小于 1，差额留作现金。任何一条不符合边界，整份指令作废。

两种边界：
- 规则基准模式（默认，见 baseline.py）：股票、债券、黄金各自的合计只能在今天基准的上下 band 以内；
  指令作废时用今天的基准。
- 旧模式（没有基准）：股票合计在 stock_min–stock_max 之间，每天变动不超过 max_daily_change；
  指令作废时沿用上一次接受的目标。
"""
from __future__ import annotations

import datetime as dt
import json
import math
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
    if not isinstance(sig, dict):
        return None, "指令文件的内容不是一个对象"
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


def validate(targets: dict, sig_cfg: dict, prev: dict, allowed_ranges: dict | None = None) -> list[str]:
    """返回所有不合规的原因；空列表表示通过。allowed_ranges 是基准模式下每类资产的 [下限, 上限]。"""
    errors = []
    allowed = whitelist(sig_cfg)
    for c, w in targets.items():
        if c not in allowed:
            errors.append(f"{c} 不在白名单里")
        elif not isinstance(w, (int, float)) or isinstance(w, bool) or not math.isfinite(w) or w < 0:
            errors.append(f"{c} 的权重 {w} 无效")
    if errors:
        return errors
    total = sum(targets.values())
    if total > 1 + 1e-9:
        errors.append(f"权重合计 {total:.2f} 超过 1")
    if allowed_ranges is not None:
        names = {"stock": "股票", "bond": "债券", "gold": "黄金"}
        for group, (lo, hi) in allowed_ranges.items():
            w = sum(targets.get(c, 0) for c in sig_cfg["groups"].get(group, []))
            if not lo - 1e-9 <= w <= hi + 1e-9:
                errors.append(f"{names.get(group, group)}合计 {w:.0%} 不在今天基准允许的 {lo:.0%}–{hi:.0%} 之间")
        return errors
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


def resolve_targets(path: Path, today: dt.date, sig_cfg: dict, prev: dict, base: dict | None = None,
                    allowed_ranges: dict | None = None) -> tuple[dict, list[str], dict | None]:
    """返回 (今天使用的目标, 备注, 被接受的指令或 None)。所有白名单代码都会出现在目标里，没写的为 0。
    给了 base（今天的规则基准）时，指令不能用就用基准；否则沿用上一次的目标。"""
    full = lambda t: {c: float(t.get(c, 0)) for c in whitelist(sig_cfg)}
    fallback, what = (base, "今天的规则基准") if base is not None else (prev, "上一次的目标")
    sig, why = load_signal(path, today, sig_cfg["max_age_days"])
    if sig is None:
        return full(fallback), [f"{why}，用{what}"], None
    errors = validate(sig["targets"], sig_cfg, prev, allowed_ranges)
    if errors:
        return full(fallback), ["Claude 的指令被拒绝：" + "；".join(errors) + f"。用{what}"], None
    return full(sig["targets"]), [f"采用 Claude {sig['date']} 的指令"], sig
