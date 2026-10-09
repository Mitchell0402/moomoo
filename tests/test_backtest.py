import pytest

from backtest.compare import simulate


def test_zero_stock_weight_is_kept():
    # 预热期股票一路下跌，趋势跟踪应该一开始就全仓债券（股票 0%）；之后股票跌 50% 不该有任何影响
    stock_r = [-0.05] * 13 + [-0.5, 0.0]
    bond_r = [0.0] * 15
    path, trades = simulate("trend_100", stock_r, bond_r, warmup=13)
    assert path == [pytest.approx(1.0), pytest.approx(1.0)]


def test_guardrail_baseline_numbers():
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "-m", "backtest.guardrail"], cwd=root, capture_output=True, text=True,
                         check=True).stdout
    line = next(l for l in out.splitlines() if l.startswith("建议核心"))
    _, cagr, mdd, *_ = line.split(",")
    assert float(cagr.strip("%")) == pytest.approx(10.8, abs=0.2)
    assert float(mdd.strip("%")) == pytest.approx(17.7, abs=0.5)
