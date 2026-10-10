"""单位净值：往账户里加钱或取钱时，回撤和收益不被这笔钱带偏。

做法和基金一样：账户价值 = 单位净值 × 份数。第一次运行时份数是 1，单位净值就等于账户价值（美元）。
入金时按入金前的单位净值买入新的份数，单位净值不变；取钱时反过来。之后的涨跌只改变单位净值。
回撤刹车、提醒线和 strategies.csv 里的 actual 都用单位净值，所以加钱不会掩盖亏损，取钱也不会被当成回撤。

怎么发现入金：和上一次运行比，现金的变化减去"本程序买卖花掉 / 收回的钱"（持仓股数的变化 × 上次的价格）。
剩下的就是外部现金流。分红、利息和成交价差也会留下几块到几十块的差额，所以小于 min_flow（默认 100 美元）
的差额当作收益，不当作入金。只在 account 模式（按券商真实现金记账，实盘用）里检测；orders 模式不会有入金。
"""
from __future__ import annotations

DEFAULT_MIN_FLOW = 100.0


def external_flow(snap: dict | None, cash: float, holdings: dict, prices: dict, min_flow: float) -> float:
    """上一次运行到现在，账户里进出的外部资金（入金为正、取钱为负）；没有或太小时返回 0。"""
    if not snap:
        return 0.0
    prev_hold, prev_px = snap.get("holdings") or {}, snap.get("prices") or {}
    traded = 0.0
    for c in set(holdings) | set(prev_hold):
        dq = holdings.get(c, 0.0) - prev_hold.get(c, 0.0)
        px = prev_px.get(c, prices.get(c))  # 上次没有记价格（新加的标的）时用现价
        if dq and px is None:
            return 0.0  # 算不清楚这笔买卖花了多少钱，宁可不认作入金
        traded += dq * (px or 0.0)
    flow = (cash - float(snap.get("cash", 0.0))) + traded
    return round(flow, 2) if abs(flow) >= min_flow else 0.0


def update(state: dict, value_now: float, cash: float, holdings: dict, prices: dict,
           detect_flows: bool, min_flow: float = DEFAULT_MIN_FLOW, settled: bool = True) -> dict:
    """更新 state 里的份数、净值高点和现金流快照，返回这次的单位净值、回撤和外部现金流。

    state 用到的键：nav_units（份数，没有时为 1）、peak_value（单位净值的高点；没有入金时就是账户价值的高点）、
    net_deposits（累计净入金）、flow_snap（上次运行开始时的现金、持仓和价格）。
    settled=False（还有没成交完的单）时这次不判断入金，也不更新快照：挂单可能已经冻结了现金却还没变成持仓，
    这时比较会把它误当成取钱；等单子成交后的下一次运行再一起算。
    """
    units = float(state.get("nav_units", 1.0))
    flow = external_flow(state.get("flow_snap"), cash, holdings, prices, min_flow) if detect_flows and settled else 0.0
    nav_before = (value_now - flow) / units if units > 0 else 0.0
    if flow and nav_before > 0:
        units += flow / nav_before
        state["net_deposits"] = round(float(state.get("net_deposits", 0.0)) + flow, 2)
    else:
        flow = 0.0
    nav = value_now / units if units > 0 else 0.0
    peak = max(float(state.get("peak_value", 0)), nav)
    state["nav_units"] = round(units, 8)
    state["peak_value"] = round(peak, 2)
    if settled or "flow_snap" not in state:
        state["flow_snap"] = {"cash": round(cash, 2), "holdings": dict(holdings),
                              "prices": {c: prices[c] for c in holdings if c in prices}}
    return {"nav": round(nav, 2), "units": round(units, 8), "flow": flow, "peak": round(peak, 2),
            "drawdown": 1 - nav / peak if peak else 0.0}
