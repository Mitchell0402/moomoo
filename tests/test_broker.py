from autoinvest.broker import _num


def test_num_parses_sdk_values():
    assert _num("N/A") is None
    assert _num(float("nan")) is None
    assert _num(None) is None
    assert _num("12.5") == 12.5
    assert _num(3) == 3.0
