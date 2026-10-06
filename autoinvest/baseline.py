"""规则基准：按趋势算出每天的基准仓位，Claude 只能在基准上下 band（默认 10 个百分点）内调整。

- SCHB 在约 10 个月均线上方：70% 股票 / 20% 债券 / 10% 黄金
- 均线下方：40% 股票 / 50% 债券 / 10% 黄金
- 历史价格不够、判断不了趋势时：用 config.yaml 里的固定 targets

Claude 的指令缺失、过期或不合规时，直接用基准，这样 Claude 停了系统照样合理运转。
回测（1972 年起，月度）：基准本身年化约 10.8%、最大回撤约 17.7%；Claude 在边界里一直最激进时回撤约 19.9%。
config.yaml 里没有 baseline 这一段时用下面的默认值。
"""
from __future__ import annotations

DEFAULTS = {
    "enabled": True,
    "band": 0.10,
    "gold_codes": ["US.GLDM"],
    "above": {"US.SCHB": 0.70, "US.SCHZ": 0.20, "US.GLDM": 0.10},
    "below": {"US.SCHB": 0.40, "US.SCHZ": 0.50, "US.GLDM": 0.10},
}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("baseline") or {})}


def codes(b: dict) -> list[str]:
    return list(dict.fromkeys(list(b["above"]) + list(b["below"]) + list(b["gold_codes"])))


def targets_for(b: dict, trend: dict | None, fallback: dict) -> tuple[dict, str]:
    """返回 (今天的基准, 趋势状态 above / below / unknown)。"""
    if trend is None:
        return dict(fallback), "unknown"
    regime = "below" if trend["below"] else "above"
    return dict(b[regime]), regime


def ranges(base: dict, band: float, groups: dict) -> dict:
    """每一类资产（stock / bond / gold）今天允许的合计范围。"""
    out = {}
    for name, members in groups.items():
        w = sum(base.get(c, 0.0) for c in members)
        out[name] = [round(max(0.0, w - band), 4), round(min(1.0, w + band), 4)]
    return out
