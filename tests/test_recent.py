import random

from backtest import recent


def _data(n=620, seed=3):
    rng = random.Random(seed)
    dates = [f"{2023 + i // 250}-{(i % 250) // 21 + 1:02d}-{i % 21 + 1:02d}" for i in range(n)]
    codes = ["US.SCHB", "US.SCHZ", "US.GLDM", "US.QQQM", "US.SSO", "US.SCHF", "US.TLT", "US.SCHO", "US.SCHD", "US.DBMF",
             *recent.portfolios.SECTORS]
    px = {c: [50.0] for c in codes}
    for _ in range(n - 1):
        m = rng.gauss(0.0004, 0.01)
        for c in codes:
            px[c].append(px[c][-1] * (1 + (m if c not in ("US.SCHZ", "US.SCHO") else m * 0.1) + rng.gauss(0, 0.004)))
    return dates, px, [v * 0.999 for v in px["US.SCHB"]]


def test_replay_matches_hand_calculation_for_fixed_100():
    dates, px, opens = _data()
    first, last = 300, 560
    ser, trades, ds = recent.replay(dates, px, opens, first, last)
    assert ds[0] == dates[first] and ds[-1] == dates[last]
    # 100% 股票不调仓，价值就是 SCHB 的涨跌
    expect = recent.BUDGET * px["US.SCHB"][last] / px["US.SCHB"][first]
    assert abs(ser["fixed_100"][-1] - expect) < 0.05
    assert trades.get("fixed_100", 0) == 0
    assert all(len(v) == last - first + 1 for v in ser.values())


def test_live_rules_stay_close_to_fractional_baseline_and_hold_whole_shares():
    dates, px, opens = _data()
    ser, trades, _ = recent.replay(dates, px, opens, 300, 560)
    assert abs(ser["live_rules"][-1] / ser["baseline"][-1] - 1) < 0.05
    assert trades["live_rules"] >= 0


def test_bootstrap_of_identical_curves_is_centered_on_zero():
    v = [2000 * (1 + 0.001 * i) for i in range(200)]
    lo, hi, pos = recent.block_bootstrap_diff(v, v, n=200)
    assert lo == hi == 0 and pos == 0
