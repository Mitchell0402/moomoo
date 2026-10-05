from autoinvest.strategy import Ledger, build_ledger, limit_price, plan_rebalance

TARGETS = {"US.SCHB": 0.6, "US.SCHZ": 0.4}
KW = dict(band=0.05, cash_buffer=0.02, slippage=0.002, max_order_value=1500, max_daily_value=2500)


def order(oid, code, side, qty, px, remark="autoinvest-v1"):
    return {"order_id": oid, "code": code, "trd_side": side, "dealt_qty": qty,
            "dealt_avg_price": px, "remark": remark}


def test_ledger_counts_only_our_filled_orders():
    orders = [
        order("1", "US.SCHB", "BUY", 40, 25.0),
        order("2", "US.SCHZ", "BUY", 30, 23.0),
        order("3", "US.SCHB", "SELL", 5, 26.0),
        order("4", "US.SCHB", "BUY", 100, 25.0, remark="manual"),  # 不是本程序的单
        order("5", "US.AAPL", "BUY", 1, 200.0),                     # 不在目标里
        order("1", "US.SCHB", "BUY", 40, 25.0),                     # 重复
        order("6", "US.SCHZ", "BUY", 0, 0),                         # 未成交
    ]
    led = build_ledger(2000, orders, "autoinvest-v1", list(TARGETS))
    assert led.holdings == {"US.SCHB": 35, "US.SCHZ": 30}
    assert abs(led.cash - (2000 - 1000 - 690 + 130)) < 1e-9


def test_initial_buy_uses_whole_shares_and_keeps_buffer():
    led = Ledger(cash=2000, holdings={"US.SCHB": 0, "US.SCHZ": 0})
    plan = plan_rebalance(led, {"US.SCHB": 25.0, "US.SCHZ": 23.0}, TARGETS, **KW)
    assert plan.needs_rebalance
    got = {o.code: (o.side, o.qty) for o in plan.orders}
    assert got == {"US.SCHB": ("BUY", 47), "US.SCHZ": ("BUY", 34)}  # 1960*0.6/25, 1960*0.4/23
    assert sum(o.value for o in plan.orders) <= 2000


def test_no_trade_inside_band():
    led = Ledger(cash=40, holdings={"US.SCHB": 47, "US.SCHZ": 34})
    plan = plan_rebalance(led, {"US.SCHB": 26.0, "US.SCHZ": 23.0}, TARGETS, **KW)
    assert not plan.needs_rebalance and plan.orders == []


def test_rebalance_sells_overweight_then_buys_underweight():
    led = Ledger(cash=40, holdings={"US.SCHB": 47, "US.SCHZ": 34})
    plan = plan_rebalance(led, {"US.SCHB": 35.0, "US.SCHZ": 23.0}, TARGETS, **KW)
    assert plan.needs_rebalance
    assert [o.side for o in plan.orders] == ["SELL", "BUY"]
    sell, buy = plan.orders
    assert sell.code == "US.SCHB" and buy.code == "US.SCHZ"
    assert buy.value <= led.cash + sell.value


def test_order_cap_clips_quantity():
    led = Ledger(cash=2000, holdings={"US.SCHB": 0, "US.SCHZ": 0})
    plan = plan_rebalance(led, {"US.SCHB": 25.0, "US.SCHZ": 23.0}, TARGETS,
                          **{**KW, "max_order_value": 500})
    assert all(o.value <= 500 for o in plan.orders)
    assert any("金额上限" in n for n in plan.notes)


def test_buys_never_exceed_real_account_cash():
    led = Ledger(cash=2000, holdings={"US.SCHB": 0, "US.SCHZ": 0})
    plan = plan_rebalance(led, {"US.SCHB": 25.0, "US.SCHZ": 23.0}, TARGETS, **KW, account_cash=800)
    assert sum(o.value for o in plan.orders) <= 800


def test_limit_price_rounding():
    assert limit_price("BUY", 25.0, 0.002) == 25.05
    assert limit_price("SELL", 25.0, 0.002) == 24.95
