import datetime as dt
import json

from autoinvest.claude_signal import resolve_targets, validate

CFG = {"groups": {"stock": ["US.SCHB", "US.SCHF"], "bond": ["US.SCHZ", "US.SCHO"]},
       "stock_min": 0.40, "stock_max": 0.75, "max_daily_change": 0.10, "max_age_days": 3}
PREV = {"US.SCHB": 0.6, "US.SCHZ": 0.4}
TODAY = dt.date(2026, 10, 6)


def write(tmp_path, targets, date="2026-10-06"):
    p = tmp_path / "latest.json"
    p.write_text(json.dumps({"date": date, "targets": targets, "rationale": "test"}), encoding="utf-8")
    return p


def test_valid_tilt_is_accepted(tmp_path):
    t = {"US.SCHB": 0.55, "US.SCHF": 0.05, "US.SCHZ": 0.35, "US.SCHO": 0.05}
    targets, notes, sig = resolve_targets(write(tmp_path, t), TODAY, CFG, PREV)
    assert sig is not None and targets == t


def test_cash_allowed_when_weights_sum_below_one(tmp_path):
    t = {"US.SCHB": 0.55, "US.SCHZ": 0.35}
    assert validate(t, CFG, PREV) == []


def test_rejects_unknown_ticker():
    assert any("白名单" in e for e in validate({"US.TSLA": 0.6, "US.SCHZ": 0.4}, CFG, PREV))


def test_rejects_stock_share_outside_bounds():
    prev = {"US.SCHB": 0.72, "US.SCHZ": 0.28}
    assert any("股票合计" in e for e in validate({"US.SCHB": 0.80, "US.SCHZ": 0.20}, CFG, prev))


def test_rejects_too_big_daily_move():
    assert any("一天变动" in e for e in validate({"US.SCHB": 0.45, "US.SCHZ": 0.55}, CFG, PREV))


def test_rejects_weights_over_one():
    assert any("超过 1" in e for e in validate({"US.SCHB": 0.6, "US.SCHZ": 0.5}, CFG, PREV))


def test_rejected_signal_keeps_previous_targets(tmp_path):
    targets, notes, sig = resolve_targets(write(tmp_path, {"US.SCHB": 1.0}), TODAY, CFG, PREV)
    assert sig is None
    assert targets["US.SCHB"] == 0.6 and targets["US.SCHZ"] == 0.4 and targets["US.SCHF"] == 0
    assert "被拒绝" in notes[0]


def test_stale_or_missing_signal_keeps_previous(tmp_path):
    targets, notes, sig = resolve_targets(write(tmp_path, {"US.SCHB": 0.55, "US.SCHZ": 0.45}, "2026-10-01"),
                                          TODAY, CFG, PREV)
    assert sig is None and "过期" in notes[0]
    targets, notes, sig = resolve_targets(tmp_path / "nope.json", TODAY, CFG, PREV)
    assert sig is None and targets["US.SCHB"] == 0.6
