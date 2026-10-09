"""用 1953 年以来的月度数据回测 autoinvest/strategies.py 里的所有策略。

用法（在仓库根目录）：python -m backtest.compare
数据与近似方法见 backtest/backtest.py 开头的说明。
Claude 的动态调整没法用历史数据回测：Claude 知道历史上发生了什么，回测会"偷看答案"，只能在模拟盘里往前跑。
"""
from __future__ import annotations

from pathlib import Path

from autoinvest.strategies import STRATEGIES, stock_weight
from backtest.backtest import load, stats, window

HERE = Path(__file__).resolve().parent
BAND = 0.05


LAG = 1


def simulate(kind, stock_r, bond_r, warmup=13, lag=LAG):
    """月末根据截至当月的数据算目标，下个月按目标持有。前 warmup 个月只用来积累数据。

    数据是月均价，相邻月的涨跌会有人为的连续性，会让趋势、动量类策略看起来比实际好。
    所以默认再多滞后一个月（lag=1）才用信号，得到偏保守的结果；固定比例策略不受影响。
    """
    si, bi = [1.0], [1.0]
    for rs, rb in zip(stock_r, bond_r):
        si.append(si[-1] * (1 + rs))
        bi.append(bi[-1] * (1 + rb))
    w = stock_weight(kind, si[:warmup + 1], bi[:warmup + 1])
    w = 0.6 if w is None else w   # 0% 股票是合法的目标，不能当成"没有"
    s, b = w, 1 - w
    path, trades = [], 0
    for t in range(warmup, len(stock_r)):
        s *= 1 + stock_r[t]
        b *= 1 + bond_r[t]
        v = s + b
        target = stock_weight(kind, si[:t + 2 - lag], bi[:t + 2 - lag])
        if target is not None and abs(s / v - target) > BAND:
            s, b = v * target, v * (1 - target)
            trades += 1
        path.append(v)
    return path, trades


def table(months, stock_r, bond_r, title, warmup=13):
    print(f"\n{title}（{months[warmup]} 至 {months[-1]}）")
    print("策略,年化,最大回撤,最差12个月,最长水下月数,调仓次数,2000美元期末")
    for kind in STRATEGIES:
        path, trades = simulate(kind, stock_r, bond_r, warmup)
        st = stats(path, months[warmup:])
        print(f"{kind},{st['cagr']:.2%},{st['mdd']:.1%},{st['worst12']:.1%},"
              f"{st['longest_under_months']},{trades},{2000 * path[-1]:.0f}")


def main():
    months, stock_r, bond_r = load(HERE / "sp500.csv", HERE / "y10.csv")
    table(months, stock_r, bond_r, "全区间")
    for name, a in (("从 2000 年科网泡沫顶开始", "1998-12"), ("从 2007 年金融危机前开始", "2006-10"),
                    ("最近 10 年", "2015-08")):
        m2, s2, b2 = window(months, stock_r, bond_r, a, months[-1])
        table(m2, s2, b2, name)


if __name__ == "__main__":
    main()
