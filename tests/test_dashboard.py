"""看板：用临时文件夹里的假数据检查盈亏算法、各种缺数据的情况和只读接口。不需要 OpenD。"""
import datetime as dt
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from dashboard import data, gitsync, quotes, server, timeutil

ROOT = Path(__file__).resolve().parent.parent


def write(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")


def run(time, holdings, cash, prices, orders=(), mode="execute", **kw):
    return {"time": time, "mode": mode, "holdings": holdings, "ledger_cash": cash, "prices": prices,
            "orders": list(orders), "notes": kw.pop("notes", []), **kw}


@pytest.fixture
def folder(tmp_path):
    write(tmp_path / "config.yaml", "trd_env: SIMULATE\nbudget_usd: 2000\nstart_date: 2026-10-05\n"
                                    "shadows:\n  stock: US.SCHB\n  bond: US.SCHZ\n")
    # 10-07 收盘后：40 SCHB + 30 SCHZ + 现金 140
    write(tmp_path / "logs" / "run-20261007-161000.json",
          run("2026-10-07T16:10:00", {"US.SCHB": 40, "US.SCHZ": 30}, 140.0, {"US.SCHB": 30.0, "US.SCHZ": 22.0}))
    # 10-08 10:30：卖 10 SCHZ、买 7 SCHB
    write(tmp_path / "logs" / "run-20261008-103000.json",
          run("2026-10-08T10:30:00", {"US.SCHB": 40, "US.SCHZ": 30}, 140.0, {"US.SCHB": 30.2, "US.SCHZ": 21.9},
              orders=[{"code": "US.SCHZ", "side": "SELL", "qty": 10, "price": 21.9},
                      {"code": "US.SCHB", "side": "BUY", "qty": 7, "price": 30.2}],
              signal={"date": "2026-10-08", "rationale": "x"}, targets={"US.SCHB": 0.7, "US.SCHZ": 0.3}))
    write(tmp_path / "logs" / "strategies.csv",
          "time,actual,fixed_60_40\n2026-10-06T16:10:00,1990.0,1995.0\n2026-10-07T16:10:00,2000.0,2001.0\n")
    write(tmp_path / "state.json", {"peak_value": 2010.0, "targets": {"US.SCHB": 0.7, "US.SCHZ": 0.3},
                                    "shadows": {"fixed_60_40": {"start": "2026-10-05", "stock": 40.0, "bond": 36.0}}})
    write(tmp_path / "signals" / "2026-10-08.json", {"date": "2026-10-08", "targets": {"US.SCHB": 0.7},
                                                      "rationale": "今天的理由"})
    return tmp_path


def live_feed(quotes_):
    return {"status": "live", "quotes": quotes_, "quote_date": "2026-10-08", "market_state": "AFTERNOON",
            "bars": {}, "samples": [], "spy_daily": [], "updated_at": "2026-10-08T13:00:00"}


NOW = dt.datetime(2026, 10, 8, 13, 0)
Q = {"US.SCHB": {"last": 31.0, "prev_close": 30.0}, "US.SCHZ": {"last": 22.0, "prev_close": 22.0},
     "US.SPY": {"last": 606.0, "prev_close": 600.0}}


def test_book_after_applies_orders():
    rec = run("t", {"US.A": 10}, 100.0, {}, orders=[{"code": "US.A", "side": "SELL", "qty": 4, "price": 5.0},
                                                   {"code": "US.B", "side": "BUY", "qty": 2, "price": 10.0}])
    b = data.book_after(rec)
    assert b["holdings"] == {"US.A": 6.0, "US.B": 2.0}
    assert b["cash"] == pytest.approx(100 + 20 - 20)
    rec["mode"] = "dry-run"
    assert data.book_after(rec)["holdings"] == {"US.A": 10.0}


def test_day_pnl_counts_trades_made_today(folder):
    d = data.assemble(folder, data.Files(folder), live_feed(Q), {}, NOW)
    a = d["account"]
    # 昨收：40×30 + 30×22 + 140 = 2000；现在：47×31 + 20×22 + (140 + 219 − 211.4)
    assert a["prev_value"] == pytest.approx(2000.0)
    assert a["value"] == pytest.approx(47 * 31 + 20 * 22 + 140 + 219 - 211.4, abs=0.01)
    assert a["day_pnl"] == pytest.approx(a["value"] - 2000.0, abs=0.01)
    spy = next(c for c in d["compare"] if c["key"] == "spy")
    assert spy["pct"] == pytest.approx(0.01)
    # 对照策略按 state.json 里的份额现价估值
    bal = next(s for s in d["strategies"] if s["key"] == "fixed_60_40")
    assert bal["value"] == pytest.approx(40 * 31 + 36 * 22)
    assert d["plan"]["state"] == "applied"
    assert d["plan"]["orders_today"][0]["code"] == "US.SCHZ"
    assert d["history"]["dates"][-1] == "2026-10-08"  # 今天用实时数补上
    assert d["daily"][0]["time"] == "实时"


def test_offline_uses_last_run(folder):
    feed = quotes.OfflineFeed("没连上").snapshot()
    d = data.assemble(folder, data.Files(folder), feed, {}, NOW)
    # 用最近一次运行记下的价格：47×30.2 + 20×21.9 + 147.6
    assert d["account"]["value"] == pytest.approx(47 * 30.2 + 20 * 21.9 + 147.6, abs=0.01)
    assert d["account"]["live"] is False
    assert d["intraday"] is None
    assert d["market"] == []
    assert any("OpenD" in a["text"] for a in d["alerts"])


def test_plan_waiting_and_missed_run(folder):
    # 第二天早上：Claude 已经写了计划，程序还没运行
    write(folder / "signals" / "latest.json", {"date": "2026-10-09", "targets": {}, "rationale": "新的"})
    morning = dt.datetime(2026, 10, 9, 9, 0)
    d = data.assemble(folder, data.Files(folder), quotes.OfflineFeed("").snapshot(), {}, morning)
    assert d["plan"]["state"] == "waiting" and d["plan"]["rationale"] == "新的"
    assert not any("还没有运行记录" in a["text"] for a in d["alerts"])
    late = dt.datetime(2026, 10, 9, 11, 30)
    d = data.assemble(folder, data.Files(folder), quotes.OfflineFeed("").snapshot(), {}, late)
    assert any("还没有运行记录" in a["text"] for a in d["alerts"])


def test_plan_prefers_newer_signal_from_github(folder):
    git = {"signal": {"date": "2026-10-09", "targets": {}, "rationale": "GitHub 上的"}}
    d = data.assemble(folder, data.Files(folder), quotes.OfflineFeed("").snapshot(), git,
                      dt.datetime(2026, 10, 9, 9, 0))
    assert d["plan"]["rationale"] == "GitHub 上的" and d["plan"]["from_github"]


def test_empty_folder_does_not_crash(tmp_path):
    d = data.assemble(tmp_path, data.Files(tmp_path), quotes.OfflineFeed("").snapshot(), {}, NOW)
    assert d["account"]["value"] in (None, 2000.0)
    assert d["plan"]["state"] == "none"


def test_repo_demo_data_assembles():
    cfg = data.load_config(ROOT)
    feed = server.build_feed(ROOT, cfg, data.settings(cfg), demo=True)
    d = server.App(ROOT, feed, None).dashboard()
    assert d["account"]["value"] and d["intraday"]["t"]
    json.dumps(d, allow_nan=False)


def test_et_offset_and_schedule():
    assert timeutil.et_offset(dt.datetime(2026, 7, 1, 12)) == dt.timedelta(hours=-4)
    assert timeutil.et_offset(dt.datetime(2026, 12, 1, 12)) == dt.timedelta(hours=-5)
    assert timeutil.et_offset(dt.datetime(2026, 11, 1, 5, 59)) == dt.timedelta(hours=-4)
    assert timeutil.et_offset(dt.datetime(2026, 11, 1, 6, 0)) == dt.timedelta(hours=-5)
    fri = dt.datetime(2026, 10, 9, 17, 0)
    assert timeutil.next_run(fri, ["10:30", "16:10"]) == dt.datetime(2026, 10, 12, 10, 30)
    assert gitsync.near_run(dt.datetime(2026, 10, 9, 10, 33), ["10:30"])
    assert not gitsync.near_run(dt.datetime(2026, 10, 9, 9, 0), ["10:30"])


def test_http_is_read_only(folder):
    app = server.App(folder, quotes.OfflineFeed(""), None)
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        body = json.loads(urllib.request.urlopen(base + "/api/dashboard").read())
        assert body["env"] == "SIMULATE"
        assert b"<title>" in urllib.request.urlopen(base + "/").read()
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(base + "/api/dashboard", data=b"{}", method="POST"))
        assert e.value.code == 405
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/static/..%2f..%2fconfig.yaml")
        assert e.value.code == 404
    finally:
        httpd.shutdown()


def minutes(day, start, n, price=1.0):
    t0 = dt.datetime.fromisoformat(f"{day} {start}")
    return [((t0 + dt.timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"), price) for i in range(n)]


def test_minute_bars_keep_only_regular_session(monkeypatch):
    # 晚上 23:20 打开看板：SPY 最近 1000 根分钟线里一大半是盘后和夜盘的，白天只剩 15:40 以后的
    import sys
    import types
    fake = types.SimpleNamespace(RET_OK=0, KLType=types.SimpleNamespace(K_1M="K_1M"),
                                 SubType=types.SimpleNamespace(K_1M="K_1M"))
    monkeypatch.setitem(sys.modules, "moomoo", fake)
    monkeypatch.setattr(timeutil, "now_et", lambda: dt.datetime(2026, 10, 8, 23, 20))
    day = "2026-10-08"
    cur = {"US.SPY": minutes(day, "15:41:00", 20, 600.0) + minutes(day, "16:01:00", 440, 601.0),
           "US.SCHB": minutes(day, "09:31:00", 390, 30.0) + minutes(day, "16:01:00", 60, 30.5)}
    full = minutes(day, "04:01:00", 330, 599.0) + minutes(day, "09:31:00", 390, 600.0)

    class Ctx:
        history_calls = []

        def subscribe(self, *a, **kw):
            return 0, None

        def get_cur_kline(self, code, num, ktype):
            rows = cur[code][-num:]
            return 0, {"time_key": [t for t, _ in rows], "close": [p for _, p in rows]}

        def request_history_kline(self, code, start, end, ktype, max_count):
            self.history_calls.append(code)
            return 0, {"time_key": [t for t, _ in full], "close": [p for _, p in full]}, None

    feed = quotes.OpenDFeed("127.0.0.1", 11111, ["US.SPY", "US.SCHB"])
    feed.ctx = Ctx()
    bars = feed._klines(day)
    assert [t[11:16] for t, _ in bars["US.SPY"]][:1] + [bars["US.SPY"][-1][0][11:16]] == ["09:31", "16:00"]
    assert len(bars["US.SPY"]) == 390
    assert len(bars["US.SCHB"]) == 390
    assert Ctx.history_calls == ["US.SPY"]  # SCHB 白天的数据是全的，不用补取
    feed._klines(day)
    assert Ctx.history_calls == ["US.SPY"]  # 补取过的当天不再取


def test_intraday_chart_ignores_after_hours_bars(folder):
    feed = live_feed(Q)
    day = "2026-10-08"
    feed["bars"] = {"US.SPY": minutes(day, "09:31:00", 3, 603.0) + minutes(day, "16:01:00", 2, 590.0),
                    "US.SCHB": minutes(day, "09:31:00", 3, 30.5)}
    d = data.assemble(folder, data.Files(folder), feed, {}, NOW)
    assert d["intraday"]["t"] == ["09:31", "09:32", "09:33"]
    assert d["intraday"]["spy"][-1] == pytest.approx(0.005)


def test_real_env_reads_only_real_logs(folder):
    # 配置改成实盘，logs/real/ 里还没有记录：看板不能把模拟盘的持仓显示成实盘的
    cfg = (folder / "config.yaml").read_text(encoding="utf-8").replace("SIMULATE", "REAL")
    write(folder / "config.yaml", cfg)
    d = data.assemble(folder, data.Files(folder), quotes.OfflineFeed("").snapshot(), {}, NOW)
    assert d["account"]["value"] == pytest.approx(2000.0)


def test_shadow_pick_files_do_not_replace_plan(tmp_path):
    day = "2026-10-09"
    write(tmp_path / "signals" / f"{day}.json", {"date": day, "targets": {"US.SCHB": 0.7}, "rationale": "主计划"})
    write(tmp_path / "signals" / "latest.json", {"date": day, "targets": {"US.SCHB": 0.7}, "rationale": "主计划"})
    write(tmp_path / "signals" / "shadows.json", {"date": day, "claude_stocks": {"targets": {"US.JPM": 0.2}}})
    write(tmp_path / "signals" / "shadows-daily.json", {"date": day, "claude_stocks_daily": {"targets": {}}})
    write(tmp_path / "signals" / "notes.json", {"date": "2026-10-10", "targets": "oops"})
    sigs = data.Files(tmp_path).signals()
    assert list(sigs) == [day]
    assert sigs[day]["targets"] == {"US.SCHB": 0.7} and "claude_stocks" not in sigs[day]
