import pytest

from autoinvest.nav import external_flow, update

PX = {"A": 10.0, "B": 20.0}


def snap(cash, a, b, px=PX):
    return {"cash": cash, "holdings": {"A": a, "B": b}, "prices": dict(px)}


def test_first_run_starts_at_account_value():
    st = {}
    nv = update(st, 2000.0, 100.0, {"A": 100, "B": 50}, PX, True)
    assert nv == {"nav": 2000.0, "units": 1.0, "flow": 0.0, "peak": 2000.0, "drawdown": 0.0}
    assert st["flow_snap"]["cash"] == 100.0


def test_old_state_keeps_its_peak():
    # 加这个功能以前的 state.json 只有 peak_value：份数按 1 算，原来的高点照样有效
    st = {"peak_value": 2011.98}
    nv = update(st, 1900.0, 0.0, {"A": 190}, PX, True)
    assert nv["nav"] == 1900.0 and nv["peak"] == 2011.98
    assert nv["drawdown"] == pytest.approx(1 - 1900 / 2011.98)


def test_deposit_does_not_hide_a_loss():
    st = {}
    update(st, 2000.0, 0.0, {"A": 100, "B": 50}, PX, True)
    # 股价跌 20%，同时存进 1000 美元：账户 2600 美元比高点还多，但单位净值仍然回撤 20%
    px = {"A": 8.0, "B": 16.0}
    nv = update(st, 1600.0 + 1000.0, 1000.0, {"A": 100, "B": 50}, px, True)
    assert nv["flow"] == 1000.0
    assert nv["nav"] == pytest.approx(1600.0)
    assert nv["drawdown"] == pytest.approx(0.20)
    assert st["net_deposits"] == 1000.0
    # 之后再涨 10%，单位净值也只涨 10%
    nv = update(st, 2600.0 * 1.1, 1000.0, {"A": 100, "B": 50}, px, True)
    assert nv["flow"] == 0.0 and nv["nav"] == pytest.approx(1760.0)


def test_withdrawal_is_not_a_drawdown():
    st = {}
    update(st, 2000.0, 1000.0, {"A": 100}, PX, True)
    nv = update(st, 1500.0, 500.0, {"A": 100}, PX, True)
    assert nv["flow"] == -500.0 and nv["drawdown"] == pytest.approx(0.0)
    assert st["net_deposits"] == -500.0


def test_own_trades_are_not_deposits():
    # 上次运行时用 500 美元按 10 元买了 50 股 A；这次 A 涨到 10.5：现金少了 500、多了 50 股，不是取钱
    assert external_flow(snap(1000.0, 100, 50), 500.0, {"A": 150, "B": 50}, {"A": 10.5, "B": 20}, 100) == 0.0
    # 卖出也一样
    assert external_flow(snap(0.0, 100, 50), 400.0, {"A": 100, "B": 30}, PX, 100) == 0.0


def test_small_cash_changes_count_as_returns():
    # 分红、利息几十美元：当作收益，不当作入金
    assert external_flow(snap(100.0, 100, 50), 160.0, {"A": 100, "B": 50}, PX, 100) == 0.0
    st = {}
    update(st, 2100.0, 100.0, {"A": 100, "B": 50}, PX, True)
    nv = update(st, 2160.0, 160.0, {"A": 100, "B": 50}, PX, True)
    assert nv["flow"] == 0.0 and nv["nav"] == 2160.0


def test_new_code_uses_current_price():
    s = snap(1000.0, 100, 50)
    assert external_flow(s, 700.0, {"A": 100, "B": 50, "C": 10}, {**PX, "C": 30.0}, 100) == 0.0
    assert external_flow(s, 1700.0, {"A": 100, "B": 50, "C": 10}, {**PX, "C": 30.0}, 100) == 1000.0


def test_orders_ledger_never_detects_flows():
    st = {}
    update(st, 2000.0, 0.0, {"A": 200}, PX, False)
    nv = update(st, 3000.0, 1000.0, {"A": 200}, PX, False)
    assert nv["flow"] == 0.0 and nv["units"] == 1.0


def test_pending_orders_postpone_flow_detection():
    st = {}
    update(st, 2000.0, 1000.0, {"A": 100}, PX, True)
    # 挂着一笔没成交的买单：现金被冻结 500 但持仓没变，不能当成取钱，快照也不动
    nv = update(st, 1500.0, 500.0, {"A": 100}, PX, True, settled=False)
    assert nv["flow"] == 0.0 and st["flow_snap"]["cash"] == 1000.0
    # 成交后：多了 50 股、现金少 500，和原快照比也不是取钱
    nv = update(st, 2000.0, 500.0, {"A": 150}, PX, True)
    assert nv["flow"] == 0.0 and nv["drawdown"] == pytest.approx(0.0)
