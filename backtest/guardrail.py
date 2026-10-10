"""评估用回测：在仓库根目录运行 python -m backtest.guardrail，输出和 backtest/guardrail_results.txt 一致。
README 里“规则基准”的年化和最大回撤来自这里。比较：现有边界的最坏情况、趋势护栏、加 10% 黄金。月度数据，信号滞后 1 个月，偏离 5pp 再平衡。
黄金数据：github.com/datasets/gold-prices 月度价格。"""
import csv
from pathlib import Path

from backtest.backtest import load, stats
HERE = Path(__file__).resolve().parent
months, sr, br = load(HERE / "sp500.csv", HERE / "y10.csv")
gold = {}
for r in csv.DictReader(open(HERE / "gold_monthly.csv")):
    gold[r["Date"][:7]] = float(r["Price"])
gm = sorted(gold)
gr = {}
for a, b in zip(gm, gm[1:]):
    gr[b] = gold[b] / gold[a] - 1
BAND, LAG, SMA = 0.05, 1, 10

def run(fn, start, end="2026-08"):
    idx = [i for i, m in enumerate(months) if start <= m <= end and m in gr]
    ms = [months[i] for i in idx]
    s_r = [sr[i] for i in idx]; b_r = [br[i] for i in idx]; g_r = [gr[months[i]] for i in idx]
    # stock index history including pre-window for SMA
    si = [1.0]
    for x in sr: si.append(si[-1] * (1 + x))
    w = fn(si[:idx[0] + 1 - LAG], None)
    hold = list(w); path = []; trades = 0; dd_peak = 1; v = 1
    for k, i in enumerate(idx):
        hold = [hold[0] * (1 + s_r[k]), hold[1] * (1 + b_r[k]), hold[2] * (1 + g_r[k])]
        v = sum(hold)
        tgt = fn(si[:i + 2 - LAG], v)
        if max(abs(h / v - t) for h, t in zip(hold, tgt)) > BAND:
            hold = [v * t for t in tgt]; trades += 1
        path.append(v)
    return stats(path, ms), trades, ms

def above(si):
    return si[-1] > sum(si[-SMA:]) / SMA
S = {
 "固定 60/40": lambda si, v: (0.6, 0.4, 0),
 "Claude 一直顶格 75/25（现有上限）": lambda si, v: (0.75, 0.25, 0),
 "Claude 一直最保守 40/60（现有下限）": lambda si, v: (0.40, 0.60, 0),
 "加趋势护栏后顶格（均线上 75% / 下 50%）": lambda si, v: (0.75, 0.25, 0) if above(si) else (0.5, 0.5, 0),
 "加趋势护栏后顶格（均线上 75% / 下 40%）": lambda si, v: (0.75, 0.25, 0) if above(si) else (0.4, 0.6, 0),
 "温和趋势 80/30": lambda si, v: (0.8, 0.2, 0) if above(si) else (0.3, 0.7, 0),
 "60/30/10 黄金": lambda si, v: (0.6, 0.3, 0.1),
 "建议核心：均线上 70/20/10，下 40/50/10": lambda si, v: (0.7, 0.2, 0.1) if above(si) else (0.4, 0.5, 0.1),
 "Claude ±10 一直顶格（均线上 80/10/10）": lambda si, v: (0.8, 0.1, 0.1) if above(si) else (0.4, 0.5, 0.1),
 "Claude ±5 一直顶格（均线上 75/15/10，现有边界）": lambda si, v: (0.75, 0.15, 0.1) if above(si) else (0.4, 0.5, 0.1),
}
for title, start in (("1972-01 至 2026-08（黄金自由浮动以来）", "1972-01"), ("2000-01 起（科网泡沫顶）", "2000-01"), ("2007-11 起（金融危机前）", "2007-11"), ("最近 10 年", "2016-09")):
    print("\n" + title)
    print("策略,年化,最大回撤,最差12个月,最长水下月数,调仓次数")
    for name, fn in S.items():
        st, t, ms = run(fn, start)
        print(f"{name},{st['cagr']:.2%},{st['mdd']:.1%},{st['worst12']:.1%},{st['longest_under_months']},{t}")
