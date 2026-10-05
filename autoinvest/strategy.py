"""再平衡策略的纯逻辑部分：不连接券商，方便测试。

思路：
- 程序只管理一个"虚拟子账户"：预算 budget_usd，加上它自己下过的单（用 remark 标记）。
  这样模拟盘里默认的 100 万虚拟资金不会被动用，实盘里其它持仓也不会被碰。
- 持仓和剩余现金都从"本程序下过且已成交的订单"推算出来。
- 任一标的权重偏离目标超过 band，或者还没建仓，就把所有标的调回目标权重。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Order:
    code: str
    side: str  # "BUY" 或 "SELL"
    qty: int
    price: float

    @property
    def value(self) -> float:
        return self.qty * self.price


@dataclass
class Ledger:
    cash: float
    holdings: dict[str, float]


@dataclass
class Plan:
    managed_value: float
    weights: dict[str, float]
    max_drift: float
    needs_rebalance: bool
    orders: list[Order] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def build_ledger(budget: float, orders: list[dict], remark: str, codes: list[str]) -> Ledger:
    """根据本程序下过的订单推算现金和持仓。部分成交也按已成交数量计算。"""
    cash = float(budget)
    holdings = {c: 0.0 for c in codes}
    seen = set()
    for o in orders:
        if o.get("remark") != remark or o.get("code") not in holdings:
            continue
        if o["order_id"] in seen:
            continue
        seen.add(o["order_id"])
        qty = float(o.get("dealt_qty") or 0)
        px = float(o.get("dealt_avg_price") or 0)
        if qty <= 0:
            continue
        if o["trd_side"] == "BUY":
            holdings[o["code"]] += qty
            cash -= qty * px
        elif o["trd_side"] == "SELL":
            holdings[o["code"]] -= qty
            cash += qty * px
    return Ledger(cash=cash, holdings=holdings)


def limit_price(side: str, ref: float, slippage: float) -> float:
    """买单略高于参考价、卖单略低于参考价，保证大概率成交但不会离谱。美股 1 美元以上保留 2 位小数。"""
    if side == "BUY":
        return math.ceil(ref * (1 + slippage) * 100) / 100
    return math.floor(ref * (1 - slippage) * 100) / 100


def plan_rebalance(
    ledger: Ledger,
    prices: dict[str, float],
    targets: dict[str, float],
    band: float,
    cash_buffer: float,
    slippage: float,
    max_order_value: float,
    max_daily_value: float,
    account_cash: float | None = None,
) -> Plan:
    codes = list(targets)
    values = {c: ledger.holdings.get(c, 0.0) * prices[c] for c in codes}
    managed = ledger.cash + sum(values.values())
    weights = {c: (values[c] / managed if managed > 0 else 0.0) for c in codes}
    drifts = {c: weights[c] - targets[c] for c in codes}
    max_drift = max(abs(d) for d in drifts.values())
    empty = all(ledger.holdings.get(c, 0.0) <= 0 for c in codes)
    plan = Plan(managed_value=managed, weights=weights, max_drift=max_drift,
                needs_rebalance=empty or max_drift > band)
    if not plan.needs_rebalance:
        plan.notes.append(f"最大偏离 {max_drift:.1%}，未超过 {band:.0%}，今天不交易")
        return plan

    investable = managed * (1 - cash_buffer)
    deltas = {}
    for c in codes:
        target_qty = math.floor(investable * targets[c] / prices[c])
        deltas[c] = target_qty - int(ledger.holdings.get(c, 0.0))

    daily_left = max_daily_value
    # 先卖后买，卖出的钱用来买
    est_cash = ledger.cash
    for c in codes:
        if deltas[c] < 0:
            px = limit_price("SELL", prices[c], slippage)
            qty = _cap_qty(-deltas[c], px, min(max_order_value, daily_left), plan, c)
            if qty > 0:
                plan.orders.append(Order(c, "SELL", qty, px))
                daily_left -= qty * px
                est_cash += qty * px

    # 能花的钱 = 虚拟子账户现金；同时不能超过券商账户里真实可用的现金
    spendable = est_cash
    if account_cash is not None:
        sell_proceeds = est_cash - ledger.cash
        spendable = min(spendable, account_cash + sell_proceeds)
    for c in codes:
        if deltas[c] > 0:
            px = limit_price("BUY", prices[c], slippage)
            qty = _cap_qty(deltas[c], px, min(max_order_value, daily_left, spendable), plan, c)
            if qty > 0:
                plan.orders.append(Order(c, "BUY", qty, px))
                daily_left -= qty * px
                spendable -= qty * px
    if not plan.orders:
        plan.notes.append("需要再平衡，但按整股计算没有可下的单")
    return plan


def _cap_qty(qty: int, px: float, cap: float, plan: Plan, code: str) -> int:
    allowed = max(0, math.floor(cap / px))
    if qty > allowed:
        plan.notes.append(f"{code} 计划 {qty} 股，受金额上限限制改为 {allowed} 股")
        return allowed
    return qty
