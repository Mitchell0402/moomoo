"""几种公认的股债配置规则。回测（月度数据）和实盘对照（日度数据）共用同一套代码。

每个规则只看"到今天为止"的价格，给出股票的目标比例，其余放债券。
实际持仓偏离目标超过 5 个百分点时才调仓，所以连续变化的目标不会天天交易。
lookback 用"期数"表示：月度回测里 10 期 = 10 个月，日度运行时用对应的交易日数。
"""
from __future__ import annotations

import math

# 每种规则在月度 / 日度数据上的回看期数
LOOKBACKS = {
    "monthly": {"sma": 10, "momentum": 12, "vol": 12, "per_year": 12},
    "daily": {"sma": 210, "momentum": 252, "vol": 126, "per_year": 252},
}

STRATEGIES = {
    "fixed_100": "100% 股票，不调整",
    "fixed_80_20": "固定 80/20",
    "fixed_60_40": "固定 60/40",
    "fixed_50_50": "固定 50/50",
    "trend_100": "趋势跟踪：股价在 10 个月均线上方全仓股票，下方全仓债券",
    "trend_80_30": "趋势跟踪（温和版）：均线上方 80% 股票，下方 30% 股票",
    "dual_momentum": "双动量：过去 12 个月股票跑赢债券就全仓股票，否则全仓债券",
    "vol_target": "波动率目标：按股票近 12 个月波动把整体波动控制在约 10%，股票最多 100%",
    "risk_parity": "风险平价：按股票、债券各自波动的倒数分配，让两者贡献的风险相近",
}


def _ret(a: float, b: float) -> float:
    return b / a - 1


def _vol(closes: list[float], n: int, per_year: int) -> float | None:
    if len(closes) < n + 1:
        return None
    rs = [_ret(closes[i - 1], closes[i]) for i in range(len(closes) - n, len(closes))]
    mean = sum(rs) / n
    var = sum((r - mean) ** 2 for r in rs) / (n - 1)
    return math.sqrt(var * per_year)


def stock_weight(kind: str, stock: list[float], bond: list[float], freq: str = "monthly") -> float | None:
    """返回股票目标比例；历史数据不够时返回 None（调用方沿用默认）。stock/bond 是总回报指数或价格序列。"""
    lb = LOOKBACKS[freq]
    if kind == "fixed_100":
        return 1.0
    if kind.startswith("fixed_"):
        return int(kind.split("_")[1]) / 100
    if kind in ("trend_100", "trend_80_30"):
        n = lb["sma"]
        if len(stock) < n:
            return None
        above = stock[-1] > sum(stock[-n:]) / n
        if kind == "trend_100":
            return 1.0 if above else 0.0
        return 0.8 if above else 0.3
    if kind == "dual_momentum":
        n = lb["momentum"]
        if len(stock) <= n or len(bond) <= n:
            return None
        return 1.0 if _ret(stock[-n - 1], stock[-1]) > _ret(bond[-n - 1], bond[-1]) else 0.0
    if kind == "vol_target":
        v = _vol(stock, lb["vol"], lb["per_year"])
        if v is None:
            return None
        return max(0.0, min(1.0, 0.10 / v)) if v > 0 else 1.0
    if kind == "risk_parity":
        vs = _vol(stock, lb["vol"], lb["per_year"])
        vb = _vol(bond, lb["vol"], lb["per_year"])
        if not vs or not vb:
            return None
        return (1 / vs) / (1 / vs + 1 / vb)
    raise ValueError(f"未知策略 {kind}")

