from autoinvest.strategies import STRATEGIES, stock_weight


def test_fixed_weights():
    assert stock_weight("fixed_100", [], []) == 1.0
    assert stock_weight("fixed_60_40", [], []) == 0.6


def test_not_enough_history_returns_none():
    for kind in ("trend_100", "dual_momentum", "vol_target", "risk_parity"):
        assert stock_weight(kind, [1.0] * 5, [1.0] * 5) is None


def test_trend_and_momentum_follow_direction():
    up = [100 + i for i in range(20)]
    down = [100 - i for i in range(20)]
    bond = [100.0 + 0.1 * i for i in range(20)]
    assert stock_weight("trend_100", up, bond) == 1.0
    assert stock_weight("trend_100", down, bond) == 0.0
    assert stock_weight("trend_80_30", down, bond) == 0.3
    assert stock_weight("dual_momentum", up, bond) == 1.0
    assert stock_weight("dual_momentum", down, bond) == 0.0


def test_risk_parity_gives_less_to_the_volatile_asset():
    wild = [100 * (1.1 if i % 2 else 0.95) ** (i % 3) for i in range(20)]
    calm = [100 * (1.01 if i % 2 else 0.995) ** (i % 3) for i in range(20)]
    assert stock_weight("risk_parity", wild, calm) < 0.5
    assert 0 < stock_weight("vol_target", wild, calm) <= 1


def test_every_listed_strategy_runs():
    s = [100 * 1.01 ** i * (1.02 if i % 2 else 1) for i in range(300)]
    for kind in STRATEGIES:
        w = stock_weight(kind, s, s, "daily")
        assert w is not None and 0 <= w <= 1
