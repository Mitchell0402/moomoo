"""用假券商把整个流程跑一遍，不需要 OpenD。"""
import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

import autoinvest.main as m

ROOT = Path(__file__).resolve().parent.parent


class FakeBroker:
    state = "MORNING"
    placed = []
    orders = []

    env = None

    def __init__(self, host, port, acc_id, env):
        self.acc_id = 123
        FakeBroker.env = env

    def orders_since(self, start):
        return list(FakeBroker.orders)

    def prices(self, codes):
        table = {"US.SCHB": 25.0, "US.SCHZ": 23.0, "US.SCHF": 22.0, "US.SCHO": 24.0}
        return {c: table[c] for c in codes}

    def daily_closes(self, codes, days):
        return {c: [["2026-10-02", 1.0]] for c in codes}

    def account_cash(self):
        return 1_000_000.0

    def positions(self):
        return {}

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
    return root


def run(root, *extra):
    return m.main(["run", *extra, "--config", str(root / "config.yaml")])


def last_log(root):
    return json.loads(sorted((root / "logs").glob("run-*.json"))[-1].read_text(encoding="utf-8"))


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
