from autoinvest.guards import DEFAULTS, apply_guards, cap_stock, trend_state

G = dict(DEFAULTS, trend_days=5)
UP = [10, 11, 12, 13, 14]
DOWN = [14, 13, 12, 11, 10]


def test_cap_moves_excess_to_bonds_in_proportion():
    t = {"US.SCHB": 0.60, "US.SCHF": 0.15, "US.SCHZ": 0.15, "US.SCHO": 0.05}
    out = cap_stock(t, 0.40, G["stock_codes"], G["bond_code"], G["bond_codes"])
    assert abs(out["US.SCHB"] + out["US.SCHF"] - 0.40) < 1e-6
    assert abs(out["US.SCHB"] / out["US.SCHF"] - 4) < 1e-6
    assert abs(sum(out.values()) - sum(t.values())) < 1e-6  # 现金比例不变
    assert abs(out["US.SCHZ"] / out["US.SCHO"] - 3) < 1e-6


def test_cap_without_bonds_uses_bond_code():
    out = cap_stock({"US.SCHB": 0.75}, 0.40, G["stock_codes"], G["bond_code"])
    assert out == {"US.SCHB": 0.40, "US.SCHZ": 0.35}


def test_trend_state_needs_enough_history():
    assert trend_state([1, 2, 3], 5) is None
    assert trend_state(DOWN, 5)["below"] and not trend_state(UP, 5)["below"]


def test_below_trend_caps_stocks():
    t, notes, info = apply_guards({"US.SCHB": 0.75, "US.SCHZ": 0.25}, {}, DOWN, 0.0, G)
    assert t == {"US.SCHB": 0.40, "US.SCHZ": 0.60}
    assert info["stock_cap"] == 0.40 and any("趋势护栏" in n for n in notes)


def test_above_trend_leaves_targets_alone():
    t, notes, info = apply_guards({"US.SCHB": 0.75, "US.SCHZ": 0.25}, {}, UP, 0.0, G)
    assert t == {"US.SCHB": 0.75, "US.SCHZ": 0.25} and notes == []


def test_drawdown_brake_blocks_adding_stocks():
    prev = {"US.SCHB": 0.55, "US.SCHZ": 0.45}
    t, notes, info = apply_guards({"US.SCHB": 0.65, "US.SCHZ": 0.35}, prev, UP, 0.22, G)
    assert t["US.SCHB"] == 0.55 and info["drawdown_brake"]
    # 减仓不受影响
    t, _, _ = apply_guards({"US.SCHB": 0.50, "US.SCHZ": 0.50}, prev, UP, 0.22, G)
    assert t["US.SCHB"] == 0.50


def test_disabled_guards_change_nothing():
    t, notes, _ = apply_guards({"US.SCHB": 0.75, "US.SCHZ": 0.25}, {}, DOWN, 0.5, dict(G, enabled=False))
    assert t == {"US.SCHB": 0.75, "US.SCHZ": 0.25} and notes == []


def test_cap_never_moves_stock_into_gold():
    t = {"US.SCHB": 0.70, "US.SCHZ": 0.20, "US.GLDM": 0.10}
    out = cap_stock(t, 0.40, G["stock_codes"], G["bond_code"], G["bond_codes"])
    assert out == {"US.SCHB": 0.40, "US.SCHZ": 0.50, "US.GLDM": 0.10}


def test_stock_cap_combines_trend_and_drawdown():
    from autoinvest.guards import stock_cap
    below, above = trend_state(DOWN, 5), trend_state(UP, 5)
    assert stock_cap(above, 0.0, {}, G) == 1.0
    assert stock_cap(below, 0.0, {}, G) == 0.40
    assert stock_cap(above, 0.25, {"US.SCHB": 0.55}, G) == 0.55
    assert stock_cap(below, 0.25, {"US.SCHB": 0.55}, G) == 0.40
