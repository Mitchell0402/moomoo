"""写死在代码里的风险护栏：不管目标比例来自 Claude 还是固定配置，下单前都要过这一关。

1. 趋势护栏：股票 ETF（默认 SCHB）的价格低于约 10 个月（210 个交易日）均线时，
   股票合计最多 stock_max_below_trend（默认 40%）。回测里这条把最坏回撤从 35.8% 降到 21.4%。
2. 回撤刹车：从高点回撤超过 drawdown_no_add（默认 15%，给 20% 的回撤上限留余量）时，股票合计不能比上一次执行的目标更高。
   （限制的是目标比例，不是实际股数：价格下跌后按原来的目标再平衡，仍可能买入少量股票。）

超出上限的股票比例按原有比例挪到债券（bond_codes，不会挪到黄金）；目标里没有债券时放进 bond_code。
config.yaml 里没有 guards 这一段时用下面的默认值，所以老的配置文件不用改也有护栏。
"""
from __future__ import annotations

DEFAULTS = {
    "enabled": True,
    "trend_code": "US.SCHB",
    "trend_days": 210,
    "stock_max_below_trend": 0.40,
    "drawdown_no_add": 0.15,
    "stock_codes": ["US.SCHB", "US.SCHF"],
    "bond_codes": ["US.SCHZ", "US.SCHO"],
    "bond_code": "US.SCHZ",
}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("guards") or {})}


def stock_total(targets: dict, stock_codes: list[str]) -> float:
    return sum(w for c, w in targets.items() if c in stock_codes)


def cap_stock(targets: dict, cap: float, stock_codes: list[str], bond_code: str,
              bond_codes: list[str] | None = None) -> dict:
    """股票合计超过 cap 时等比例压到 cap，多出来的按原有比例分给债券。"""
    total = stock_total(targets, stock_codes)
    if total <= cap + 1e-9:
        return dict(targets)
    out = dict(targets)
    for c in out:
        if c in stock_codes:
            out[c] = out[c] * cap / total
    excess = total - cap
    bond_codes = bond_codes or [bond_code]
    bonds = {c: w for c, w in out.items() if c in bond_codes and w > 0}
    if bonds:
        b_total = sum(bonds.values())
        for c, w in bonds.items():
            out[c] = w + excess * w / b_total
    else:
        out[bond_code] = out.get(bond_code, 0.0) + excess
    return {c: round(w, 6) for c, w in out.items()}


def trend_state(closes: list[float], days: int) -> dict | None:
    """最后一个价格和 days 日均线的关系；历史不够时返回 None（不启用趋势护栏）。"""
    if len(closes) < days:
        return None
    sma = sum(closes[-days:]) / days
    return {"price": round(closes[-1], 4), "sma": round(sma, 4), "below": closes[-1] < sma}


def stock_cap(trend: dict | None, drawdown: float, prev: dict, g: dict) -> float:
    """今天护栏允许的股票合计上限（护栏关闭时为 1）。"""
    if not g["enabled"]:
        return 1.0
    cap = g["stock_max_below_trend"] if trend and trend["below"] else 1.0
    if drawdown >= g["drawdown_no_add"]:
        cap = min(cap, stock_total(prev, g["stock_codes"]))
    return cap


def apply_guards(targets: dict, prev: dict, closes: list[float], drawdown: float, g: dict):
    """返回 (护栏后的目标, 备注, 给日志和 Claude 看的护栏状态)。"""
    info = {"trend": None, "stock_cap": 1.0, "drawdown_brake": False}
    if not g["enabled"]:
        return dict(targets), [], info
    stocks, notes, out = g["stock_codes"], [], dict(targets)
    trend = trend_state(closes, int(g["trend_days"]))
    info["trend"] = trend
    if trend is None:
        notes.append(f"{g['trend_code']} 历史价格不足 {g['trend_days']} 天，今天不启用趋势护栏")
    elif trend["below"]:
        cap = g["stock_max_below_trend"]
        info["stock_cap"] = cap
        if stock_total(out, stocks) > cap + 1e-9:
            notes.append(f"趋势护栏：{g['trend_code']} {trend['price']} 低于 {g['trend_days']} 日均线 "
                         f"{trend['sma']}，股票合计从 {stock_total(out, stocks):.0%} 降到 {cap:.0%}")
            out = cap_stock(out, cap, stocks, g["bond_code"], g["bond_codes"])
    if drawdown >= g["drawdown_no_add"]:
        info["drawdown_brake"] = True
        limit = stock_total(prev, stocks)
        if stock_total(out, stocks) > limit + 1e-9:
            notes.append(f"回撤刹车：已回撤 {drawdown:.1%}，股票合计不能高于上一次的 {limit:.0%}")
            out = cap_stock(out, limit, stocks, g["bond_code"], g["bond_codes"])
            info["stock_cap"] = min(info["stock_cap"], limit)
    return out, notes, info
