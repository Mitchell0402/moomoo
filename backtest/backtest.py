"""用历史月度数据回测不同股债比例。

数据（公开、免费）：
- 股票：标普 500 指数月度价格和股息（Shiller 数据，github.com/datasets/s-and-p-500）
- 债券：美国 10 年期国债月度收益率（github.com/datasets/bond-yields-us-10y），
  按"每月买入新的 10 年期平价国债"换算成月度总回报。10 年期国债比 SCHZ 这类综合债券波动更大，
  所以债券部分的结果偏保守。

近似处理：
- 2023 年 7 月之后的数据集没有股息，按年化 1.3% 的股息率补上（近年标普 500 股息率大约在这个水平）。
- 不计交易费用和税；ETF 的管理费（SCHB、SCHZ 每年约 0.03%）忽略不计。

用法：python backtest.py sp500.csv y10.csv
"""
from __future__ import annotations

import csv
import sys

FALLBACK_DIV_YIELD = 0.013


def load(sp_path, y_path):
    yields = {}
    with open(y_path) as f:
        for r in csv.DictReader(f):
            yields[r["Date"][:7]] = float(r["Rate"]) / 100
    rows = []
    with open(sp_path) as f:
        for r in csv.DictReader(f):
            m = r["Date"][:7]
            if m in yields and float(r["SP500"]) > 0:
                rows.append((m, float(r["SP500"]), float(r["Dividend"]), yields[m]))
    months, stock, bond = [], [], []
    for (m0, p0, d0, y0), (m1, p1, d1, y1) in zip(rows, rows[1:]):
        div = d1 if d1 > 0 else p0 * FALLBACK_DIV_YIELD
        stock.append(p1 / p0 - 1 + div / 12 / p0)
        bond.append(bond_return(y0, y1))
        months.append(m1)
    return months, stock, bond


def bond_return(y0, y1, n=10):
    """上月按收益率 y0 买入 n 年期平价债券，本月按 y1 估值的总回报。"""
    t = n - 1 / 12
    pv = y0 / y1 * (1 - (1 + y1 / 2) ** (-2 * t)) + (1 + y1 / 2) ** (-2 * t)
    return pv - 1 + y0 / 12


def simulate(stock, bond, w, rule, band=0.05):
    """rule: 'band' 每月检查、偏离超过 band 就调回；'annual' 每 12 个月调一次；'none' 不调。"""
    s, b = w, 1 - w
    path = []
    trades = 0
    for i, (rs, rb) in enumerate(zip(stock, bond)):
        s *= 1 + rs
        b *= 1 + rb
        v = s + b
        if (rule == "band" and abs(s / v - w) > band) or (rule == "annual" and (i + 1) % 12 == 0):
            if abs(s / v - w) > 1e-9:
                trades += 1
            s, b = v * w, v * (1 - w)
        path.append(s + b)
    return path, trades


def stats(path, months):
    n = len(path)
    cagr = path[-1] ** (12 / n) - 1
    peak, mdd, mdd_at, recover = 1.0, 0.0, None, 0
    under, longest = 0, 0
    for v, m in zip(path, months):
        if v >= peak:
            peak, under = v, 0
        else:
            under += 1
            longest = max(longest, under)
        dd = 1 - v / peak
        if dd > mdd:
            mdd, mdd_at = dd, m
    full = [1.0] + path
    worst12 = min(full[i + 12] / full[i] - 1 for i in range(n - 11))
    r10 = [full[i + 120] / full[i] for i in range(n - 119)]
    return {"cagr": cagr, "mdd": mdd, "mdd_at": mdd_at, "worst12": worst12,
            "longest_under_months": longest, "pct10y_loss": (sum(x < 1 for x in r10) / len(r10)) if r10 else None}


def window(months, stock, bond, start, end):
    idx = [i for i, m in enumerate(months) if start <= m <= end]
    return [months[i] for i in idx], [stock[i] for i in idx], [bond[i] for i in idx]


def main(sp_path, y_path):
    months, stock, bond = load(sp_path, y_path)
    print(f"数据区间 {months[0]} 至 {months[-1]}，共 {len(months)} 个月")
    print("\n全区间，$2000 起步")
    print("配置,规则,年化,最大回撤,回撤最深月份,最差12个月,最长水下月数,10年亏损概率,调仓次数,期末金额")
    for w in (1.0, 0.8, 0.7, 0.6, 0.5, 0.4):
        for rule in (("band", "annual", "none") if w not in (1.0,) else ("none",)):
            path, trades = simulate(stock, bond, w, rule)
            s = stats(path, months)
            print(f"{int(w*100)}/{int(round((1-w)*100))},{rule},{s['cagr']:.2%},{s['mdd']:.1%},{s['mdd_at']},"
                  f"{s['worst12']:.1%},{s['longest_under_months']},{s['pct10y_loss']:.1%},{trades},{2000*path[-1]:.0f}")
    print("\n60/40 不同调仓阈值")
    for band in (0.03, 0.05, 0.10):
        path, trades = simulate(stock, bond, 0.6, "band", band)
        s = stats(path, months)
        print(f"阈值 {band:.0%}: 年化 {s['cagr']:.2%}，最大回撤 {s['mdd']:.1%}，调仓 {trades} 次")
    for name, a, b in (("2007-11 金融危机前开始", "2007-11", "2026-08"),
                       ("2000-01 科网泡沫顶开始", "2000-01", "2026-08"),
                       ("2021-12 加息前开始", "2021-12", "2026-08"),
                       ("最近 10 年", "2016-09", "2026-08")):
        m2, s2, b2 = window(months, stock, bond, a, b)
        print(f"\n{name}（{m2[0]} 至 {m2[-1]}）")
        for w in (1.0, 0.8, 0.7, 0.6):
            path, _ = simulate(s2, b2, w, "band")
            s = stats(path, m2)
            print(f"{int(w*100)}/{int(round((1-w)*100))}: 年化 {s['cagr']:.2%}，最大回撤 {s['mdd']:.1%}（{s['mdd_at']}），"
                  f"期末 ${2000*path[-1]:,.0f}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
