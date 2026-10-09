import json

from autoinvest import portfolios as pf

STOCKS = {"US.AAPL": 0.2, "US.MSFT": 0.2, "US.NVDA": 0.2, "US.JPM": 0.2, "US.XOM": 0.2}


def test_validate_stock_picks():
    assert pf.validate_picks("claude_stocks", STOCKS) == []
    assert any("20%" in e for e in pf.validate_picks("claude_stocks", {**STOCKS, "US.AAPL": 0.3}))
    assert any("5 到 10" in e for e in pf.validate_picks("claude_stocks", {"US.AAPL": 0.2}))
    assert any("有效的美股代码" in e for e in pf.validate_picks("claude_stocks", {**STOCKS, "AAPL": 0.0001}))
    assert any("有效的美股代码" in e for e in pf.validate_picks("claude_stocks", {**STOCKS, "US.XLK": 0.0001}))


def test_validate_sector_picks():
    assert pf.validate_picks("claude_sectors", {"US.XLK": 0.5, "US.XLV": 0.5}) == []
    assert any("名单" in e for e in pf.validate_picks("claude_sectors", {"US.QQQ": 0.5, "US.XLV": 0.5}))
    assert any("50%" in e for e in pf.validate_picks("claude_sectors", {"US.XLK": 0.6, "US.XLV": 0.4}))
    assert any("超过 1" in e for e in pf.validate_picks("claude_sectors", {"US.XLK": 0.5, "US.XLV": 0.5,
                                                                           "US.XLE": 0.2}))


def test_load_picks_skips_only_the_bad_part(tmp_path):
    path = tmp_path / "shadows.json"
    path.write_text(json.dumps({"date": "2026-10-09", "claude_stocks": {"targets": STOCKS},
                                "claude_sectors": {"targets": {"US.XLK": 1.0}}}), encoding="utf-8")
    picks, notes = pf.load_picks(tmp_path)
    assert picks == {"claude_stocks": {"date": "2026-10-09", "targets": STOCKS}}
    assert len(notes) == 1 and "行业 ETF" in notes[0]
    assert pf.load_picks(tmp_path / "missing") == ({}, [])


def test_daily_picks_have_their_own_file_and_date(tmp_path):
    (tmp_path / "shadows.json").write_text(json.dumps({"date": "2026-10-05", "claude_stocks": {"targets": STOCKS}}),
                                           encoding="utf-8")
    (tmp_path / "shadows-daily.json").write_text(json.dumps({"date": "2026-10-09",
                                                             "claude_stocks_daily": {"targets": STOCKS}}),
                                                 encoding="utf-8")
    picks, _ = pf.load_picks(tmp_path)
    assert picks["claude_stocks"]["date"] == "2026-10-05" and picks["claude_stocks_daily"]["date"] == "2026-10-09"


def test_momentum_picks_top_three():
    closes = {c: [10.0] * 130 for c in pf.SECTORS}
    closes["US.XLE"] = [10.0] * 4 + [15.0] * 126   # 6 个月前 10，现在 15
    closes["US.XLK"] = [10.0] * 4 + [12.0] * 126
    closes["US.XLU"] = [10.0] * 4 + [11.0] * 126
    assert set(pf.momentum_top(closes)) == {"US.XLE", "US.XLK", "US.XLU"}
    assert pf.momentum_top({"US.XLE": [1.0] * 10}) is None


PRICES = {"US.QQQM": 200.0, "US.SSO": 90.0, "US.SCHB": 25.0, "US.SCHF": 22.0, "US.SCHZ": 23.0,
          "US.TLT": 85.0, "US.GLDM": 86.0, "US.SCHO": 24.0, "US.SCHD": 27.0, "US.DBMF": 28.0,
          "US.AAPL": 250.0, "US.MSFT": 500.0, "US.NVDA": 180.0, "US.JPM": 300.0, "US.XOM": 110.0}


def test_fixed_portfolios_buy_and_claude_waits_in_cash():
    state = {}
    v, notes = pf.update(state, PRICES, {}, None, "2026-10", 2000, "2026-10-09", 0.05, True)
    assert notes == [] and v["nasdaq_100"] == 1999.0 and v["claude_stocks"] == 2000.0  # 买入扣 0.05%
    assert state["portfolios"]["nasdaq_100"]["units"] == {"US.QQQM": 1999.0 / 200}
    assert state["portfolios"]["claude_stocks"]["units"] == {}
    # 纳指涨 10%：只估值，不是调仓日
    v, _ = pf.update(state, {**PRICES, "US.QQQM": 220.0}, {}, None, "2026-10", 2000, "2026-10-09", 0.05, False)
    assert v["nasdaq_100"] == round(1999.0 / 200 * 220, 2)


def test_claude_picks_rebalance_only_when_date_changes():
    state = {}
    picks = {"claude_stocks": {"date": "2026-10-09", "targets": STOCKS}}
    pf.update(state, PRICES, picks, None, "2026-10", 2000, "2026-10-09", 0.05, True)
    acct = state["portfolios"]["claude_stocks"]
    assert acct["tag"] == "2026-10-09" and acct["units"]["US.AAPL"] == 1999 * 0.2 / 250
    # AAPL 翻倍：同一份指令不再调仓，一直持有
    up = {**PRICES, "US.AAPL": 500.0}
    v, _ = pf.update(state, up, picks, None, "2026-10", 2000, "2026-10-10", 0.05, True)
    assert v["claude_stocks"] == round(1999 * 1.2, 2) and acct["units"]["US.AAPL"] == 1999 * 0.2 / 250
    # 新的一周有新指令：按新比例调
    new = {"claude_stocks": {"date": "2026-10-16", "targets": {**STOCKS, "US.AAPL": 0.1}}}
    pf.update(state, up, new, None, "2026-10", 2000, "2026-10-16", 0.05, True)
    acct = state["portfolios"]["claude_stocks"]
    value = pf.value(acct, up)
    assert acct["tag"] == "2026-10-16" and abs(acct["cash"] - 0.1 * value) < 1e-6
    assert value < 1999 * 1.2  # 换仓扣了成本


def test_missing_price_skips_rebalance_and_keeps_last_value():
    state = {}
    picks = {"claude_stocks": {"date": "2026-10-09", "targets": STOCKS}}
    pf.update(state, PRICES, picks, None, "2026-10", 2000, "2026-10-09", 0.05, True)
    no_xom = {c: p for c, p in PRICES.items() if c != "US.XOM"}
    new = {"claude_stocks": {"date": "2026-10-16", "targets": STOCKS}}
    v, notes = pf.update(state, no_xom, new, None, "2026-10", 2000, "2026-10-16", 0.05, True)
    assert v["claude_stocks"] == 1999.0 and state["portfolios"]["claude_stocks"]["tag"] == "2026-10-09"
    assert any("US.XOM" in n for n in notes)


def test_sector_momentum_rebalances_once_a_month():
    state = {}
    prices = {**PRICES, **{c: 50.0 for c in pf.SECTORS}}
    top = {"US.XLE": 1 / 3, "US.XLK": 1 / 3, "US.XLU": 1 / 3}
    pf.update(state, prices, {}, top, "2026-10", 2000, "2026-10-09", 0.05, True)
    acct = state["portfolios"]["sector_momentum"]
    assert acct["tag"] == "2026-10" and set(acct["units"]) == set(top)
    h = pf.holdings(state, prices)
    assert h["sector_momentum"]["since"] == "2026-10" and abs(h["sector_momentum"]["weights"]["US.XLE"] - 1 / 3) < 1e-3
    assert "nasdaq_100" not in h
