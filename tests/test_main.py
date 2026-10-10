"""用假券商把整个流程跑一遍，不需要 OpenD。"""
import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

import autoinvest.main as m
from autoinvest.portfolios import SECTORS

ROOT = Path(__file__).resolve().parent.parent


class FakeBroker:
    state = "MORNING"
    placed = []
    orders = []
    closes = None
    bars = []
    bars_error = False
    held = {}
    cash = 1_000_000.0
    fill = "FILLED_ALL"
    price_override = {}
    funds_seq = []

    env = None

    def __init__(self, host, port, acc_id, env):
        self.acc_id = 123
        FakeBroker.env = env

    def orders_since(self, start):
        return list(FakeBroker.orders)

    def prices(self, codes):
        table = {"US.SCHB": 25.0, "US.SCHZ": 23.0, "US.SCHF": 22.0, "US.SCHO": 24.0, "US.GLDM": 86.0,
                 "US.QQQM": 200.0, "US.SSO": 90.0, "US.TLT": 85.0, "US.SCHD": 27.0, "US.DBMF": 28.0}
        table.update({c: 50.0 for c in SECTORS})
        table.update({"US.AAPL": 250.0, "US.MSFT": 500.0, "US.NVDA": 180.0, "US.JPM": 300.0, "US.XOM": 110.0})
        table.update(FakeBroker.price_override)
        missing = [c for c in codes if c not in table]
        if missing:
            raise m.BrokerError(f"没有拿到这些标的的价格：{missing}")
        return {c: table[c] for c in codes}

    def daily_closes(self, codes, days):
        if FakeBroker.closes:
            return {c: FakeBroker.closes.get(c, [["2026-10-02", 1.0]]) for c in codes}
        return {c: [["2026-10-02", 1.0]] for c in codes}

    def daily_bars(self, code, days):
        if FakeBroker.bars_error:
            raise m.BrokerError("K 线查询失败")
        return list(FakeBroker.bars)

    def funds(self):
        if len(FakeBroker.funds_seq) > 1:
            return FakeBroker.funds_seq.pop(0)
        if FakeBroker.funds_seq:
            return FakeBroker.funds_seq[0]
        c = FakeBroker.cash
        return {"cash": c, "us_cash": c, "usd_net_cash_power": c, "power": c, "total_assets": c}

    def positions(self):
        return dict(FakeBroker.held)

    def order_status(self, ids):
        return {i: FakeBroker.fill for i in ids}

    def market_state(self):
        return FakeBroker.state

    def place_limit(self, code, side, qty, price, remark):
        FakeBroker.placed.append((code, side, qty, price, remark))
        return f"oid{len(FakeBroker.placed)}"

    def close(self):
        pass


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    shutil.copy(ROOT / "config.example.yaml", root / "config.yaml")
    monkeypatch.setattr(m, "ROOT", root)
    monkeypatch.setattr(m, "MoomooBroker", FakeBroker)
    FakeBroker.placed, FakeBroker.orders, FakeBroker.state, FakeBroker.env = [], [], "MORNING", None
    FakeBroker.closes, FakeBroker.held, FakeBroker.cash, FakeBroker.fill = None, {}, 1_000_000.0, "FILLED_ALL"
    FakeBroker.price_override, FakeBroker.funds_seq = {}, []
    FakeBroker.bars, FakeBroker.bars_error = [], False
    return root


def run(root, *extra):
    return m.main(["run", *extra, "--config", str(root / "config.yaml")])


def last_log(root):
    folder = root / "logs" / "real" if (root / "logs" / "real").exists() else root / "logs"
    return json.loads(sorted(folder.glob("run-*.json"))[-1].read_text(encoding="utf-8"))


def test_dry_run_places_nothing(sandbox):
    assert run(sandbox) == 0
    assert FakeBroker.placed == []
    assert len(last_log(sandbox)["planned_orders"]) == 2


def test_execute_places_paper_orders(sandbox):
    assert run(sandbox, "--execute") == 0
    assert [(c, s, q) for c, s, q, _, _ in FakeBroker.placed] == [("US.SCHB", "BUY", 47), ("US.SCHZ", "BUY", 34)]
    assert all(r == "autoinvest-v1" for *_, r in FakeBroker.placed)


def test_execute_waits_when_market_closed(sandbox):
    FakeBroker.state = "CLOSED"
    run(sandbox, "--execute")
    assert FakeBroker.placed == []
    assert any("不在美股常规交易时段" in n for n in last_log(sandbox)["notes"])


def test_execute_skips_if_already_traded_today(sandbox):
    today = dt.date.today().isoformat()
    FakeBroker.orders = [{"order_id": "x", "code": "US.SCHB", "trd_side": "BUY", "dealt_qty": 0,
                          "dealt_avg_price": 0, "remark": "autoinvest-v1", "order_status": "CANCELLED_ALL",
                          "create_time": f"{today} 10:30:00"}]
    run(sandbox, "--execute")
    assert FakeBroker.placed == []


def test_stop_file_disables_everything(sandbox):
    (sandbox / "STOP").touch()
    run(sandbox, "--execute")
    assert FakeBroker.placed == []


def edit_config(root, old, new):
    cfg = root / "config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def test_default_config_is_paper(sandbox):
    run(sandbox, "--execute")
    assert FakeBroker.env == "SIMULATE"


def test_real_env_without_confirmation_is_refused(sandbox):
    edit_config(sandbox, "trd_env: SIMULATE", "trd_env: REAL")
    with pytest.raises(SystemExit):
        run(sandbox, "--execute")
    assert FakeBroker.env is None and FakeBroker.placed == []


def test_confirmation_alone_stays_on_paper(sandbox):
    edit_config(sandbox, "real_money_confirmed: false", "real_money_confirmed: true")
    run(sandbox, "--execute")
    assert FakeBroker.env == "SIMULATE"


def test_real_env_with_confirmation_uses_real_account(sandbox):
    edit_config(sandbox, "trd_env: SIMULATE", "trd_env: REAL")
    edit_config(sandbox, "real_money_confirmed: false", "real_money_confirmed: true")
    assert run(sandbox, "--execute") == 0
    assert FakeBroker.env == "REAL"
    assert len(FakeBroker.placed) == 2
    assert (sandbox / "state-real.json").exists() and not (sandbox / "state.json").exists()


def enable_signal(root, targets, date=None):
    edit_config(root, "  enabled: false         # 改成 true", "  enabled: true         # 改成 true")
    edit_config(root, "  git_sync: true", "  git_sync: false")
    (root / "signals").mkdir(exist_ok=True)
    (root / "signals" / "latest.json").write_text(json.dumps(
        {"date": date or dt.date.today().isoformat(), "targets": targets, "rationale": "测试"}), encoding="utf-8")


def test_signal_mode_follows_claude_targets(sandbox):
    enable_signal(sandbox, {"US.SCHB": 0.50, "US.SCHF": 0.10, "US.SCHZ": 0.40})
    assert run(sandbox, "--execute") == 0
    bought = {c: q for c, s, q, _, _ in FakeBroker.placed}
    assert set(bought) == {"US.SCHB", "US.SCHF", "US.SCHZ"}
    assert bought["US.SCHF"] == int(2000 * 0.98 * 0.10 // 22.0)
    status = json.loads((sandbox / "data" / "status.json").read_text(encoding="utf-8"))
    assert status["current_targets"]["US.SCHF"] == 0.10
    assert "daily_closes" in status and status["benchmark_value"] == 2000.0
    assert set(status["strategies"]) >= {"fixed_60_40", "trend_100", "risk_parity"}
    lines = (sandbox / "logs" / "strategies.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("time,actual,fixed_100") and len(lines) == 2


def test_signal_mode_rejects_out_of_bounds_and_uses_previous(sandbox):
    enable_signal(sandbox, {"US.SCHB": 0.95, "US.SCHZ": 0.05})
    run(sandbox, "--execute")
    bought = {c: q for c, s, q, _, _ in FakeBroker.placed}
    assert bought == {"US.SCHB": 47, "US.SCHZ": 34}  # 回到固定 60/40
    assert any("被拒绝" in n for n in last_log(sandbox)["notes"])


def test_signal_not_applied_when_market_closed(sandbox):
    enable_signal(sandbox, {"US.SCHB": 0.55, "US.SCHZ": 0.45})
    FakeBroker.state = "CLOSED"
    run(sandbox, "--execute")
    state = json.loads((sandbox / "state.json").read_text(encoding="utf-8"))
    assert "targets" not in state and FakeBroker.placed == []


def test_update_shadows_tracks_and_rebalances():
    from autoinvest.main import update_shadows
    sh = {"stock": "S", "bond": "B", "band": 0.05, "strategies": ["fixed_60_40", "trend_100"]}
    up = [["2026-01-%02d" % (i % 28 + 1), 10 + i * 0.01] for i in range(260)]
    flat = [["2026-01-01", 20.0]] * 260
    state = {}
    v = update_shadows(state, sh, {"S": up, "B": flat}, {"S": 13.0, "B": 20.0}, 2000, "2026-10-05")
    assert v == {"fixed_60_40": 2000.0, "trend_100": 2000.0}
    assert state["shadows"]["trend_100"]["bond"] == 0  # 股价在均线上方，全仓股票
    # 股票翻倍：60/40 变成 75/25，超出阈值后调回 60/40，价值不变
    v = update_shadows(state, sh, {"S": up, "B": flat}, {"S": 26.0, "B": 20.0}, 2000, "2026-10-06")
    assert v["fixed_60_40"] == 2000 * 0.6 * 2 + 800
    a = state["shadows"]["fixed_60_40"]
    assert abs(a["stock"] * 26.0 / (a["stock"] * 26.0 + a["bond"] * 20.0) - 0.6) < 1e-9
    assert v["trend_100"] == 4000.0


def history(price_from, price_to, n=230):
    """n 个交易日、从 price_from 线性走到 price_to 的日 K 线（都早于今天）。"""
    start = dt.date.today() - dt.timedelta(days=n + 5)
    return [[(start + dt.timedelta(days=i)).isoformat(), price_from + (price_to - price_from) * i / (n - 1)]
            for i in range(n)]


def legacy_mode(root):
    edit_config(root, "  enabled: true          # 规则基准", "  enabled: false         # 规则基准")


def test_trend_guard_caps_stocks_when_below_average(sandbox):
    legacy_mode(sandbox)  # 旧模式里 Claude 能写到 65%，护栏要把它压到 40%
    FakeBroker.closes = {"US.SCHB": history(35.0, 30.0)}  # 现价 25 远低于均线
    enable_signal(sandbox, {"US.SCHB": 0.55, "US.SCHF": 0.05, "US.SCHZ": 0.40})
    run(sandbox, "--execute")
    log = last_log(sandbox)
    assert log["guards"]["stock_cap"] == 0.40 and log["guards"]["trend"]["below"]
    assert abs(log["targets"]["US.SCHB"] + log["targets"]["US.SCHF"] - 0.40) < 1e-6
    assert any("趋势护栏" in n for n in log["notes"])
    status = json.loads((sandbox / "data" / "status.json").read_text(encoding="utf-8"))
    assert status["guards"]["stock_cap"] == 0.40 and status["guard_rules"]["stock_max_below_trend"] == 0.40
    assert abs(status["current_targets"]["US.SCHB"] + status["current_targets"]["US.SCHF"] - 0.40) < 1e-6


def test_trend_guard_quiet_above_average(sandbox):
    legacy_mode(sandbox)
    FakeBroker.closes = {"US.SCHB": history(20.0, 24.0)}
    run(sandbox, "--execute")
    log = last_log(sandbox)
    assert log["targets"]["US.SCHB"] == 0.60 and log["guards"]["stock_cap"] == 1.0


def test_baseline_above_trend_buys_70_20_10(sandbox):
    FakeBroker.closes = {"US.SCHB": history(20.0, 24.0)}
    run(sandbox, "--execute")
    log = last_log(sandbox)
    assert log["baseline"]["regime"] == "above"
    assert {c: w for c, w in log["targets"].items() if w} == {"US.SCHB": 0.70, "US.SCHZ": 0.20, "US.GLDM": 0.10}
    assert {c for c, *_ in FakeBroker.placed} == {"US.SCHB", "US.SCHZ", "US.GLDM"}


def test_baseline_below_trend_is_40_50_10(sandbox):
    FakeBroker.closes = {"US.SCHB": history(35.0, 30.0)}
    run(sandbox, "--execute")
    log = last_log(sandbox)
    assert log["baseline"]["regime"] == "below"
    assert {c: w for c, w in log["targets"].items() if w} == {"US.SCHB": 0.40, "US.SCHZ": 0.50, "US.GLDM": 0.10}
    assert not any("趋势护栏" in n for n in log["notes"])  # 基准本身已经在护栏以内


def test_claude_tilt_within_band_is_used_without_daily_limit(sandbox):
    FakeBroker.closes = {"US.SCHB": history(20.0, 24.0)}
    # 上一次是 60/40，今天直接写 75%：基准 70 ±5 以内，不受旧的每天 10 个百分点限制
    enable_signal(sandbox, {"US.SCHB": 0.70, "US.SCHF": 0.05, "US.SCHZ": 0.15, "US.GLDM": 0.10})
    run(sandbox, "--execute")
    log = last_log(sandbox)
    assert log["signal"] and log["targets"]["US.SCHF"] == 0.05
    status = json.loads((sandbox / "data" / "status.json").read_text(encoding="utf-8"))
    assert status["signal_rules"]["mode"] == "baseline"
    assert status["signal_rules"]["allowed_ranges"]["stock"] == [0.65, 0.75]
    assert status["signal_rules"]["groups"]["gold"] == ["US.GLDM"]
    assert status["baseline"]["targets"]["US.GLDM"] == 0.10


def test_claude_outside_band_falls_back_to_baseline(sandbox):
    FakeBroker.closes = {"US.SCHB": history(35.0, 30.0)}  # 均线下方，股票只能 35–40%
    enable_signal(sandbox, {"US.SCHB": 0.60, "US.SCHZ": 0.30, "US.GLDM": 0.10})
    run(sandbox, "--execute")
    log = last_log(sandbox)
    assert "signal" not in log
    assert any("被拒绝" in n and "规则基准" in n for n in log["notes"])
    assert log["targets"]["US.SCHB"] == 0.40 and log["targets"]["US.SCHZ"] == 0.50
    status = json.loads((sandbox / "data" / "status.json").read_text(encoding="utf-8"))
    assert status["signal_rules"]["allowed_ranges"]["stock"] == [0.35, 0.4]


def test_old_config_without_baseline_section_gets_defaults(sandbox):
    cfg = sandbox / "config.yaml"
    text = cfg.read_text(encoding="utf-8")
    start, end = text.index("# ---- 规则基准"), text.index("# ---- 风险护栏")
    cfg.write_text(text[:start] + text[end:].replace("    gold:\n      - US.GLDM", ""), encoding="utf-8")
    FakeBroker.closes = {"US.SCHB": history(20.0, 24.0)}
    enable_signal(sandbox, {"US.SCHB": 0.70, "US.SCHZ": 0.20, "US.GLDM": 0.10})
    run(sandbox, "--execute")
    log = last_log(sandbox)
    assert log["signal"] and log["baseline"]["regime"] == "above"


def test_baseline_shadow_is_logged(sandbox):
    FakeBroker.closes = {"US.SCHB": history(20.0, 24.0)}
    run(sandbox, "--execute")
    header = (sandbox / "logs" / "strategies.csv").read_text(encoding="utf-8").splitlines()[0]
    names = header.split(",")
    assert {"baseline", "intraday", "claude_stocks", "sector_momentum"} <= set(names)


def test_strategies_csv_keeps_old_rows_when_columns_change(sandbox):
    logs = sandbox / "logs"
    logs.mkdir()
    (logs / "strategies.csv").write_text("time,actual,fixed_100\n2026-10-01T16:10:00,1990,2010\n",
                                         encoding="utf-8")
    FakeBroker.closes = {"US.SCHB": history(20.0, 24.0)}
    run(sandbox, "--execute")
    assert list(logs.glob("strategies-until-*.csv")) == []
    lines = (logs / "strategies.csv").read_text(encoding="utf-8").splitlines()
    names = lines[0].split(",")
    assert {"baseline", "intraday", "nasdaq_100"} <= set(names) and len(lines) == 3
    old = dict(zip(names, lines[1].split(",")))
    assert old["actual"] == "1990" and old["fixed_100"] == "2010" and old["intraday"] == ""
    new = dict(zip(names, lines[2].split(",")))
    assert new["intraday"] == "2000.0" and new["fixed_100"] == "2000.0"


def held_orders(schb, schz):
    return [{"order_id": "a", "code": "US.SCHB", "trd_side": "BUY", "dealt_qty": schb, "dealt_avg_price": 25.0,
             "remark": "autoinvest-v1", "order_status": "FILLED_ALL", "create_time": "2026-09-01 10:30:00"},
            {"order_id": "b", "code": "US.SCHZ", "trd_side": "BUY", "dealt_qty": schz, "dealt_avg_price": 23.0,
             "remark": "autoinvest-v1", "order_status": "FILLED_ALL", "create_time": "2026-09-01 10:30:00"}]


def test_sells_fill_before_buys(sandbox):
    FakeBroker.orders = held_orders(70, 10)  # 股票太多，要先卖 SCHB 再买 SCHZ
    FakeBroker.held = {"US.SCHB": 70, "US.SCHZ": 10}
    run(sandbox, "--execute")
    assert [s for _, s, *_ in FakeBroker.placed] == ["SELL", "BUY"]


def test_buys_wait_when_sells_not_filled(sandbox):
    FakeBroker.orders = held_orders(70, 10)
    FakeBroker.held = {"US.SCHB": 70, "US.SCHZ": 10}
    FakeBroker.fill = "SUBMITTED"
    edit_config(sandbox, "sell_fill_timeout_sec: 90", "sell_fill_timeout_sec: 0")
    run(sandbox, "--execute")
    assert [s for _, s, *_ in FakeBroker.placed] == ["SELL"]
    assert any("买单留到下一次" in n for n in last_log(sandbox)["notes"])


def test_sell_only_day_allows_a_later_run_to_buy(sandbox):
    today = dt.date.today().isoformat()
    FakeBroker.orders = held_orders(47, 0) + [
        {"order_id": "s", "code": "US.SCHB", "trd_side": "SELL", "dealt_qty": 0, "dealt_avg_price": 0,
         "remark": "autoinvest-v1", "order_status": "FILLED_ALL", "create_time": f"{today} 10:30:00"}]
    FakeBroker.held = {"US.SCHB": 47}
    run(sandbox, "--execute")
    assert [(c, s) for c, s, *_ in FakeBroker.placed] == [("US.SCHZ", "BUY")]


def test_remote_halt_stops_trading(sandbox):
    (sandbox / "signals").mkdir()
    (sandbox / "signals" / "HALT").touch()
    run(sandbox, "--execute")
    assert FakeBroker.placed == []
    assert any("远程急停" in n for n in last_log(sandbox)["notes"])


def test_real_account_ledger_counts_dividend_cash(sandbox):
    edit_config(sandbox, "trd_env: SIMULATE", "trd_env: REAL")
    edit_config(sandbox, "real_money_confirmed: false", "real_money_confirmed: true")
    FakeBroker.held = {"US.SCHB": 48, "US.SCHZ": 34}   # 1200 + 782
    FakeBroker.cash = 118.0                            # 含分红
    run(sandbox, "--execute")
    log = last_log(sandbox)
    assert log["ledger_mode"] == "account"
    assert log["managed_value"] == 48 * 25 + 34 * 23 + 118
    assert FakeBroker.placed == []  # 偏离不到 5 个百分点


def test_shadows_logged_once_per_day(sandbox):
    run(sandbox, "--execute")
    FakeBroker.placed = []
    FakeBroker.orders = held_orders(47, 34)
    run(sandbox, "--execute")
    lines = (sandbox / "logs" / "strategies.csv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert last_log(sandbox)["benchmark_value"] == 2000.0


def test_after_close_run_values_shadows_at_close(sandbox):
    run(sandbox, "--execute")
    state = json.loads((sandbox / "state.json").read_text(encoding="utf-8"))
    before = state["shadows"]["fixed_60_40"]
    FakeBroker.placed = []
    FakeBroker.orders = held_orders(47, 34)
    FakeBroker.state = "CLOSED"
    FakeBroker.price_override = {"US.SCHB": 27.5}  # 收盘时股票涨了 10%
    run(sandbox, "--execute")
    assert FakeBroker.placed == []
    state = json.loads((sandbox / "state.json").read_text(encoding="utf-8"))
    assert state["shadows"]["fixed_60_40"] == before  # 收盘后只估值，不调仓
    expected = round(before["stock"] * 27.5 + before["bond"] * 23.0, 2)
    log = last_log(sandbox)
    assert log["benchmark_value"] == expected and log["strategies"]["fixed_60_40"] == expected
    lines = (sandbox / "logs" / "strategies.csv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # 当天那行被收盘后的估值覆盖
    names = lines[0].split(",")
    assert float(lines[1].split(",")[names.index("fixed_60_40")]) == expected


def test_intraday_shadow_books_open_to_close_once_per_day():
    from autoinvest.main import update_intraday_shadow
    state = {}
    # 第一次在 10-05 盘中运行：今天开盘 10，现价 10.5，按开盘买、现价估值
    v = update_intraday_shadow(state, [["2026-10-02", 9, 9.5], ["2026-10-05", 10.0, 10.4]], 10.5, 2000,
                               "2026-10-05", 0.0005)
    assert v == round(2000 * 1.05 * 0.9995, 2)
    assert state["shadows"]["intraday"]["value"] == 2000  # 今天还没收完，不记账；建立前的日子不算
    # 第二天：10-05 按日 K 线开盘 10、收盘 10.4 记一次账；今天还没开盘的话就只有昨天的
    bars = [["2026-10-05", 10.0, 10.4]]
    v = update_intraday_shadow(state, bars, 11.0, 2000, "2026-10-06", 0.0005)
    assert v == round(2000 * 1.04 * 0.9995, 2)
    # 同一天再跑一次，不会重复记账
    assert update_intraday_shadow(state, bars, 11.0, 2000, "2026-10-06", 0.0005) == v
    assert state["shadows"]["intraday"]["last_day"] == "2026-10-05"


def test_intraday_shadow_logged_and_survives_kline_error(sandbox):
    today = dt.date.today().isoformat()
    FakeBroker.bars = [[today, 24.0, 25.5]]
    run(sandbox, "--execute")
    assert last_log(sandbox)["strategies"]["intraday"] == round(2000 * 25.0 / 24.0 * 0.9995, 2)
    FakeBroker.bars_error = True
    FakeBroker.placed, FakeBroker.orders = [], held_orders(47, 34)
    assert run(sandbox, "--execute") == 0
    log = last_log(sandbox)
    assert "intraday" not in log["strategies"] and any("日内对照这次没有更新" in n for n in log["notes"])


def test_intraday_shadow_can_be_turned_off(sandbox):
    edit_config(sandbox, "  intraday: true ", "  intraday: false ")
    run(sandbox, "--execute")
    assert "intraday" not in last_log(sandbox)["strategies"]


def test_unexpected_error_is_logged(sandbox, monkeypatch):
    monkeypatch.setattr(FakeBroker, "positions", lambda self: 1 / 0)
    assert run(sandbox, "--execute") == 1
    log = last_log(sandbox)
    assert any(n.startswith("错误：程序异常 ZeroDivisionError") for n in log["notes"])
    assert "Traceback" in log["traceback"]


def test_position_mismatch_does_not_mark_targets_applied(sandbox):
    FakeBroker.orders = held_orders(47, 34)
    FakeBroker.held = {"US.SCHB": 10, "US.SCHZ": 34}  # 实际持仓比程序记录的少
    run(sandbox, "--execute")
    assert FakeBroker.placed == []
    state = json.loads((sandbox / "state.json").read_text(encoding="utf-8"))
    assert "targets" not in state and any("警告" in n for n in last_log(sandbox)["notes"])


def test_drawdown_brake_lets_claude_move_the_cut_into_bonds(sandbox):
    FakeBroker.closes = {"US.SCHB": history(20.0, 24.0)}  # 均线上方，基准 70/20/10
    (sandbox / "state.json").write_text(json.dumps({"peak_value": 3000.0, "targets": {
        "US.SCHB": 0.5, "US.SCHZ": 0.4, "US.GLDM": 0.1}}), encoding="utf-8")  # 回撤 33%，上次股票 50%
    enable_signal(sandbox, {"US.SCHB": 0.5, "US.SCHZ": 0.4, "US.GLDM": 0.1})
    run(sandbox, "--execute")
    log = last_log(sandbox)
    assert log["baseline"]["ranges"]["stock"] == [0.5, 0.5]
    assert log["baseline"]["ranges"]["bond"] == [0.15, 0.45]
    assert log["signal"] and log["targets"]["US.SCHZ"] == 0.4


def write_picks(root, picks):
    (root / "signals").mkdir(exist_ok=True)
    (root / "signals" / "shadows.json").write_text(json.dumps(picks), encoding="utf-8")


def test_claude_stock_picks_are_tracked_and_reported(sandbox):
    stocks = {"US.AAPL": 0.2, "US.MSFT": 0.2, "US.NVDA": 0.2, "US.JPM": 0.2, "US.XOM": 0.2}
    write_picks(sandbox, {"date": dt.date.today().isoformat(), "claude_stocks": {"targets": stocks},
                          "claude_sectors": {"targets": {"US.XLK": 0.5, "US.XLV": 0.5}}})
    FakeBroker.closes = {"US.SCHB": history(20.0, 24.0)}
    enable_signal(sandbox, {"US.SCHB": 0.70, "US.SCHZ": 0.20, "US.GLDM": 0.10})
    run(sandbox, "--execute")
    log = last_log(sandbox)
    assert log["strategies"]["claude_stocks"] == 1999.0 and log["strategies"]["claude_sectors"] == 1999.0
    assert log["portfolios"]["claude_stocks"]["weights"]["US.AAPL"] == 0.2
    status = json.loads((sandbox / "data" / "status.json").read_text(encoding="utf-8"))
    assert status["portfolios"]["claude_sectors"]["weights"] == {"US.XLK": 0.5, "US.XLV": 0.5}
    assert status["strategies"]["nasdaq_100"] == 1999.0
    # 第二次运行（同一天收盘后）AAPL 涨 10%：只估值
    FakeBroker.placed, FakeBroker.orders = [], held_orders(47, 34)
    FakeBroker.state = "CLOSED"
    FakeBroker.price_override = {"US.AAPL": 275.0}
    run(sandbox, "--execute")
    assert last_log(sandbox)["strategies"]["claude_stocks"] == round(1999 + 1999 * 0.2 / 250 * 25, 2)


def test_unknown_stock_code_only_skips_that_portfolio(sandbox):
    stocks = {"US.AAPL": 0.2, "US.MSFT": 0.2, "US.NVDA": 0.2, "US.JPM": 0.2, "US.ZZZZ": 0.2}
    write_picks(sandbox, {"date": dt.date.today().isoformat(), "claude_stocks": {"targets": stocks}})
    assert run(sandbox, "--execute") == 0
    log = last_log(sandbox)
    assert log["strategies"]["claude_stocks"] == 2000.0 and log["portfolios"]["claude_stocks"]["weights"] == {}
    assert log["strategies"]["nasdaq_100"] == 1999.0
    assert any("US.ZZZZ" in n for n in log["notes"])
    assert {c for c, *_ in FakeBroker.placed} <= {"US.SCHB", "US.SCHZ", "US.GLDM"}  # 实际账户照常


def test_sector_momentum_uses_six_month_returns(sandbox):
    up = history(10.0, 20.0, 140)
    FakeBroker.closes = {"US.XLE": up, "US.XLK": history(10.0, 15.0, 140), "US.XLU": history(10.0, 12.0, 140),
                         **{c: history(10.0, 10.0, 140) for c in SECTORS if c not in ("US.XLE", "US.XLK", "US.XLU")}}
    run(sandbox, "--execute")
    log = last_log(sandbox)
    assert set(log["portfolios"]["sector_momentum"]["weights"]) == {"US.XLE", "US.XLK", "US.XLU"}


def test_portfolios_can_be_turned_off(sandbox):
    edit_config(sandbox, "  portfolios: true ", "  portfolios: false ")
    run(sandbox, "--execute")
    assert "nasdaq_100" not in last_log(sandbox)["strategies"]


def test_corrupt_state_stops_trading(sandbox):
    (sandbox / "state.json").write_text('{"peak', encoding="utf-8")
    assert run(sandbox, "--execute") == 1
    assert FakeBroker.placed == []
    notes = last_log(sandbox)["notes"]
    assert any(n.startswith("错误") and "状态文件" in n for n in notes)
    assert (sandbox / "state.json").read_text(encoding="utf-8") == '{"peak'


def test_corrupt_state_recovers_from_backup(sandbox):
    (sandbox / "state.json").write_text('{"peak', encoding="utf-8")
    (sandbox / "state.json.bak").write_text(json.dumps({"peak_value": 3000}), encoding="utf-8")
    assert run(sandbox, "--execute") == 0
    log = last_log(sandbox)
    assert log["drawdown"] > 0.3
    assert any(n.startswith("警告") and "备份" in n for n in log["notes"])


def test_config_rejects_negative_weight(sandbox):
    edit_config(sandbox, "US.SCHB: 0.60", "US.SCHB: -0.20")
    edit_config(sandbox, "US.SCHZ: 0.40", "US.SCHZ: 1.20")
    with pytest.raises(SystemExit):
        run(sandbox)


@pytest.mark.parametrize("old,new", [("US.SCHB: 0.60", "US.SCHB: true"), ("budget_usd: 2000", "budget_usd: 0"),
                                     ("max_daily_value_usd: 2500", "max_daily_value_usd: -1"),
                                     ("cash_reserve_usd: 0", "cash_reserve_usd: -5")])
def test_config_rejects_bad_numbers(sandbox, old, new):
    edit_config(sandbox, old, new)
    with pytest.raises(SystemExit):
        run(sandbox)


def test_daily_cap_spans_runs(sandbox):
    edit_config(sandbox, "max_daily_value_usd: 2500", "max_daily_value_usd: 575")
    today = dt.date.today().isoformat()
    FakeBroker.orders = [{"order_id": "s1", "code": "US.SCHB", "trd_side": "SELL", "dealt_qty": 23,
                          "dealt_avg_price": 24.95, "qty": 23, "price": 24.95, "remark": "autoinvest-v1",
                          "order_status": "FILLED_ALL", "create_time": f"{today} 10:30:00"}]
    run(sandbox, "--execute")
    spent = sum(q * p for _, _, q, p, _ in FakeBroker.placed)
    assert spent <= 575 - 573.85 + 0.01
    log = last_log(sandbox)
    assert log["daily_used"] == pytest.approx(573.85)
    assert any("受金额上限限制" in n for n in log["notes"])


def test_busy_lock_skips_run(sandbox):
    from autoinvest.runlock import run_lock
    with run_lock(sandbox / ".run.lock"):
        assert run(sandbox, "--execute") == 0
    assert FakeBroker.placed == []
    assert any("另一个程序正在运行" in n for n in last_log(sandbox)["notes"])


def test_other_pc_blocks_trading(sandbox, monkeypatch):
    (sandbox / "data").mkdir()
    (sandbox / "data" / "status.json").write_text(
        json.dumps({"host": "OLD-PC", "time": dt.datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    monkeypatch.setattr(m.socket, "gethostname", lambda: "NEW-PC")
    assert run(sandbox, "--execute") == 1
    assert FakeBroker.placed == []
    assert any(n.startswith("错误") and "OLD-PC" in n for n in last_log(sandbox)["notes"])


def test_same_pc_status_does_not_block(sandbox, monkeypatch):
    (sandbox / "data").mkdir()
    (sandbox / "data" / "status.json").write_text(
        json.dumps({"host": "NEW-PC", "time": dt.datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    monkeypatch.setattr(m.socket, "gethostname", lambda: "NEW-PC")
    assert run(sandbox, "--execute") == 0
    assert FakeBroker.placed


def test_run_log_records_all_fund_fields(sandbox):
    run(sandbox, "--execute")
    assert set(last_log(sandbox)["funds"]) == {"cash", "us_cash", "usd_net_cash_power", "power", "total_assets"}


def test_buys_rechecked_after_sells_fill(sandbox):
    FakeBroker.orders = held_orders(70, 10)
    FakeBroker.held = {"US.SCHB": 70, "US.SCHZ": 10}
    big = {"cash": 1e6, "us_cash": 1e6, "usd_net_cash_power": 1e6, "power": 1e6, "total_assets": 1e6}
    FakeBroker.funds_seq = [big, {**big, "usd_net_cash_power": 100.0}]  # 卖单成交后可用资金只有 100
    run(sandbox, "--execute")
    buys = [(q, p) for _, s, q, p, _ in FakeBroker.placed if s == "BUY"]
    assert sum(q * p for q, p in buys) <= 100
    assert any("可用资金" in n for n in last_log(sandbox)["notes"])


def test_buying_power_uses_smallest_known_field(sandbox):
    FakeBroker.funds_seq = [{"cash": 2000.0, "us_cash": 100.0, "usd_net_cash_power": 50.0, "power": 4000.0,
                             "total_assets": 2000.0}]
    run(sandbox, "--execute")
    assert sum(q * p for _, s, q, p, _ in FakeBroker.placed if s == "BUY") <= 50


def test_shadow_crash_still_saves_state(sandbox, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(m, "run_portfolios", boom)
    assert run(sandbox, "--execute") == 0
    assert len(FakeBroker.placed) == 2
    state = json.loads((sandbox / "state.json").read_text(encoding="utf-8"))
    assert state["targets"]
    assert any(n.startswith("警告") and "boom" in n for n in last_log(sandbox)["notes"])


def test_bad_pick_file_does_not_touch_real_account(sandbox):
    (sandbox / "signals").mkdir()
    (sandbox / "signals" / "shadows.json").write_text("[]", encoding="utf-8")
    assert run(sandbox, "--execute") == 0
    assert len(FakeBroker.placed) == 2
    assert (sandbox / "state.json").exists()


def test_stop_during_sell_wait_blocks_buys(sandbox, monkeypatch):
    FakeBroker.orders = held_orders(70, 10)   # 先卖 SCHB 再买 SCHZ
    FakeBroker.held = {"US.SCHB": 70, "US.SCHZ": 10}

    def stop_while_waiting(broker, ids, timeout, interval=5):
        (sandbox / "STOP").touch()
        return True
    monkeypatch.setattr(m, "wait_filled", stop_while_waiting)
    run(sandbox, "--execute")
    assert [s for _, s, *_ in FakeBroker.placed] == ["SELL"]
    assert any("STOP" in n and "不再下" in n for n in last_log(sandbox)["notes"])


def test_halt_file_blocks_orders_placed_after_start(sandbox, monkeypatch):
    FakeBroker.orders = held_orders(70, 10)
    FakeBroker.held = {"US.SCHB": 70, "US.SCHZ": 10}

    def halt_while_waiting(broker, ids, timeout, interval=5):
        (sandbox / "signals").mkdir(exist_ok=True)
        (sandbox / "signals" / "HALT").touch()
        return True
    monkeypatch.setattr(m, "wait_filled", halt_while_waiting)
    run(sandbox, "--execute")
    assert [s for _, s, *_ in FakeBroker.placed] == ["SELL"]


def test_zero_buying_power_is_reported_as_error(sandbox):
    # SDK 把不支持的资金字段报成 0 而不是 N/A 时，买单会被悄悄削成 0：要在日志里报"错误"，不能当作正常运行
    FakeBroker.funds_seq = [{"cash": 2000.0, "us_cash": 0.0, "usd_net_cash_power": 0.0, "power": 0.0,
                             "total_assets": 2000.0}]
    assert run(sandbox, "--execute") == 1
    assert FakeBroker.placed == []
    assert any(n.startswith("错误") and "可用资金" in n for n in last_log(sandbox)["notes"])


def test_real_env_writes_to_logs_real(sandbox):
    edit_config(sandbox, "trd_env: SIMULATE", "trd_env: REAL")
    edit_config(sandbox, "real_money_confirmed: false", "real_money_confirmed: true")
    run(sandbox)
    assert list((sandbox / "logs" / "real").glob("run-*.json"))
    assert not list((sandbox / "logs").glob("run-*.json"))
    assert FakeBroker.env == "REAL"


def test_paper_env_keeps_logs_dir(sandbox):
    run(sandbox)
    assert list((sandbox / "logs").glob("run-*.json"))
    assert not (sandbox / "logs" / "real").exists()


def test_two_runs_in_the_same_second_keep_both_logs(sandbox, monkeypatch):
    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 12, 10, 30, 0)
    monkeypatch.setattr(m.dt, "datetime", Frozen)
    run(sandbox)
    run(sandbox)
    assert len(list((sandbox / "logs").glob("run-*.json"))) == 2
