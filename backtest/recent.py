"""用最近一段时间的日度数据，回放所有"固定规则"的对照策略，结果和实盘里每天记的 logs/strategies.csv 同口径。

和程序共用同一套代码，不另写一套规则：
- 老的对照策略：autoinvest.main.update_shadows（调用 strategies.stock_weight）
- 规则基准：guards.trend_state + baseline.targets_for + main.update_baseline_shadow
- 日内对照：main.update_intraday_shadow
- 固定组合、行业动量：portfolios.update
- live_rules：规则基准加上实盘才有的整股、2% 现金缓冲、限价、趋势护栏和回撤刹车（guards.apply_guards + strategy.plan_rebalance）
Claude 选股、Claude 行业轮动、Claude 每日选股依赖 Claude 每天写的指令，历史上没有，不在回放里。

数据：先在装了 OpenD 的电脑上跑 python -m backtest.export_history，生成 backtest/data/recent_closes.csv。
用法（仓库根目录）：
    python -m backtest.recent                      # 最近 252 个交易日（约 12 个月）
    python -m backtest.recent --days 126 --out reports/recent-6m
价格是前复权收盘价（含分红）。每天按收盘价成交；实盘是上午 10:30 成交，所以这是近似。
"""
from __future__ import annotations

import argparse
import copy
import csv
import math
import random
import statistics
from pathlib import Path

from autoinvest import baseline, guards, portfolios, report, strategy
from autoinvest.main import update_baseline_shadow, update_intraday_shadow, update_shadows
from autoinvest.strategies import STRATEGIES

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "recent_closes.csv"
BUDGET = 2000.0
STOCK, BOND, GOLD = "US.SCHB", "US.SCHZ", "US.GLDM"
FALLBACK = {STOCK: 0.6, BOND: 0.4}   # config.example.yaml 里的 targets，趋势判断不了时用
SH_CFG = {"stock": STOCK, "bond": BOND, "band": 0.05, "strategies": list(STRATEGIES)}
FIXED_NAMES = list(portfolios.FIXED) + ["sector_momentum"]
HIST = 300   # 每天喂给规则的历史天数（和 config 里 shadows.history_days 一致）
GUARD = guards.DEFAULTS
LIVE = {"band": 0.05, "buffer": 0.02, "slippage": 0.002, "max_order": 1500.0, "max_daily": 2500.0}


def load(path: Path = DATA):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    dates = [r["date"] for r in rows]
    px = {c: [float(r[c]) for r in rows] for c in rows[0] if c != "date"}
    opens = px.pop("US.SCHB_open", None)
    return dates, px, opens


def _units(acct: dict):
    return tuple(sorted(acct["units"].items())) if "units" in acct else (acct.get("stock"), acct.get("bond"))


def _live_day(st: dict, prices: dict, trend_hist: list) -> None:
    """live_rules 一天：规则基准 -> 护栏 -> 整股再平衡（用实盘同一个 plan_rebalance）。"""
    led = strategy.Ledger(cash=st["cash"], holdings=st["hold"])
    value = led.cash + sum(q * prices[c] for c, q in led.holdings.items())
    st["peak"] = max(st["peak"], value)
    dd = 1 - value / st["peak"]
    trend = guards.trend_state(trend_hist, int(GUARD["trend_days"]))
    base, _ = baseline.targets_for(baseline.DEFAULTS, trend, FALLBACK)
    base = {c: float(base.get(c, 0.0)) for c in (STOCK, BOND, GOLD)}
    targets, _, _ = guards.apply_guards(base, st["prev"], trend_hist, dd, GUARD)
    plan = strategy.plan_rebalance(led, {c: prices[c] for c in targets}, targets, LIVE["band"], LIVE["buffer"],
                                   LIVE["slippage"], LIVE["max_order"], LIVE["max_daily"])
    for o in plan.orders:   # 先卖后买，限价单按限价成交（偏保守）
        if o.side == "SELL":
            st["cash"] += o.qty * o.price
            st["hold"][o.code] = st["hold"].get(o.code, 0.0) - o.qty
    for o in plan.orders:
        if o.side == "BUY":
            st["cash"] -= o.qty * o.price
            st["hold"][o.code] = st["hold"].get(o.code, 0.0) + o.qty
    if plan.orders:
        st["trades"] += 1
    st["prev"] = targets


def replay(dates, px, opens, first: int, last: int):
    """从第 first 天到第 last 天（含）逐日回放。返回 ({策略: [每天收盘价值]}, {策略: 调仓次数}, 日期列表)。"""
    state: dict = {}
    series: dict[str, list] = {}
    trades: dict[str, int] = {}
    live = {"cash": BUDGET, "hold": {}, "peak": BUDGET, "prev": dict(FALLBACK), "trades": 0}
    for t in range(first, last + 1):
        today = dates[t]
        prices = {c: v[t] for c, v in px.items()}
        lo = max(0, t - HIST)
        closes = {c: list(zip(dates[lo:t], px[c][lo:t])) for c in (STOCK, BOND)}
        before = {k: {n: _units(a) for n, a in (state.get(k) or {}).items()} for k in ("shadows", "portfolios")}
        values = update_shadows(state, SH_CFG, closes, prices, BUDGET, today)

        trend_hist = list(px[STOCK][max(0, t - HIST):t]) + [prices[STOCK]]
        trend = guards.trend_state(trend_hist, int(GUARD["trend_days"]))
        base, _ = baseline.targets_for(baseline.DEFAULTS, trend, FALLBACK)
        base = {c: float(base.get(c, 0.0)) for c in (STOCK, BOND, GOLD)}
        values["baseline"] = update_baseline_shadow(state, base, prices, BUDGET, baseline.DEFAULTS["band"], today)

        month = today[:7]
        acct = (state.get("portfolios") or {}).get("sector_momentum")
        momentum = None
        if acct is None or acct.get("tag") != month:
            momentum = portfolios.momentum_top({c: px[c][:t + 1] for c in portfolios.SECTORS})
        pv, _ = portfolios.update(state, prices, {}, momentum, month, BUDGET, today, SH_CFG["band"], True)
        values.update({k: v for k, v in pv.items() if k in FIXED_NAMES})

        if opens is not None:
            bars = [[dates[i], opens[i], px[STOCK][i]] for i in range(max(0, t - 5), t + 1)]
            values["intraday"] = update_intraday_shadow(state, bars, prices[STOCK], BUDGET, today, 0.0005)

        _live_day(live, prices, trend_hist)
        values["live_rules"] = round(live["cash"] + sum(q * prices[c] for c, q in live["hold"].items()), 2)

        after = {k: {n: _units(a) for n, a in (state.get(k) or {}).items()} for k in ("shadows", "portfolios")}
        for k in after:
            for n, u in after[k].items():
                if t > first and before[k].get(n) != u:
                    trades[n] = trades.get(n, 0) + 1
        for n, v in values.items():
            series.setdefault(n, []).append(v)
    trades["live_rules"] = live["trades"] - 1   # 第一次是建仓
    return series, trades, dates[first:last + 1]


def stats(vals: list[float]) -> dict:
    peak, mdd = BUDGET, 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    rets = [b / a - 1 for a, b in zip(vals, vals[1:])]
    vol = statistics.pstdev(rets) * math.sqrt(252) if len(rets) > 1 else 0.0
    return {"total": vals[-1] / BUDGET - 1, "mdd": mdd, "vol": vol, "end": vals[-1]}


def block_bootstrap_diff(a: list[float], b: list[float], n: int = 2000, block: int = 21, seed: int = 7):
    """两条价值曲线的日收益做配对的块自助法，估计"累计收益之差"的 90% 区间，以及差为正的比例。"""
    ra = [math.log(y / x) for x, y in zip(a, a[1:])]
    rb = [math.log(y / x) for x, y in zip(b, b[1:])]
    d = [x - y for x, y in zip(ra, rb)]
    rng = random.Random(seed)
    m = len(d)
    out = []
    for _ in range(n):
        s = []
        while len(s) < m:
            i = rng.randrange(m)
            s += d[i:i + block]
        out.append(sum(s[:m]))
    out.sort()
    lo, hi = out[int(0.05 * n)], out[int(0.95 * n)]
    return math.expm1(lo), math.expm1(hi), sum(1 for x in out if x > 0) / n


def rolling(dates, px, opens, first_ok: int, last: int, length: int, step: int):
    """长度 length 的窗口，起点每隔 step 天一个：每个策略的区间收益，以及每个窗口里的名次。"""
    res = []
    for s in range(first_ok, last - length + 2, step):
        ser, _, _ = replay(dates, px, opens, s, s + length - 1)
        res.append({n: v[-1] / BUDGET - 1 for n, v in ser.items()})
    return res


def main(argv=None):
    p = argparse.ArgumentParser(prog="backtest.recent")
    p.add_argument("--csv", default=str(DATA))
    p.add_argument("--days", type=int, default=252, help="回放最近多少个交易日")
    p.add_argument("--out", default=str(HERE / "recent"), help="输出文件前缀（.csv / .svg / .md）")
    p.add_argument("--no-rolling", action="store_true")
    a = p.parse_args(argv)
    dates, px, opens = load(Path(a.csv))
    last = len(dates) - 1
    first = last - a.days + 1
    warm = 252 + 1   # 双动量要回看 252 天
    if first < warm:
        raise SystemExit(f"数据只有 {len(dates)} 天，回放 {a.days} 天还需要 {warm} 天热身，一共至少 {a.days + warm} 天")
    ser, trades, ds = replay(dates, px, opens, first, last)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    names = list(ser)
    with open(out.with_suffix(".csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time"] + names)
        for i, d in enumerate(ds):
            w.writerow([d] + [ser[n][i] for n in names])
    data = report.series(report.load(out.with_suffix(".csv")))
    out.with_suffix(".svg").write_text(report.svg(data), encoding="utf-8")

    lines = [f"回放区间 {ds[0]} 至 {ds[-1]}（{len(ds)} 个交易日），每个策略从 {BUDGET:.0f} 美元起步", "",
             "| 策略 | 累计收益 | 最大回撤 | 年化波动 | 调仓次数 | 期末 |", "| --- | --- | --- | --- | --- | --- |"]
    rows = sorted(((n, stats(v)) for n, v in ser.items()), key=lambda r: -r[1]["total"])
    for n, s in rows:
        lines.append(f"| {report.NAMES.get(n, n)} | {s['total']:+.1%} | {s['mdd']:.1%} | {s['vol']:.1%} | "
                     f"{trades.get(n, 0)} | ${s['end']:,.0f} |")
    lines += ["", "市场背景（SCHB 在这段时间）："]
    sb = px[STOCK][first:last + 1]
    sma = [sum(px[STOCK][t - 209:t + 1]) / 210 for t in range(first, last + 1)]
    flips = sum(1 for i in range(1, len(sb)) if (sb[i] > sma[i]) != (sb[i - 1] > sma[i - 1]))
    below = sum(1 for x, m in zip(sb, sma) if x < m)
    s_st = stats([BUDGET * x / sb[0] for x in sb])
    lines.append(f"区间收益 {s_st['total']:+.1%}，最大回撤 {s_st['mdd']:.1%}，跌破 210 日均线的天数 {below}，"
                 f"穿越均线的次数 {flips}")

    ref = "fixed_60_40"
    lines += ["", f"相对 {report.NAMES[ref]} 的累计收益差，块自助法 90% 区间（把这段日收益打乱重抽 2000 次）：", "",
              "| 策略 | 实际差 | 90% 区间 | 差为正的比例 |", "| --- | --- | --- | --- |"]
    for n, s in rows:
        if n == ref:
            continue
        lo, hi, pos = block_bootstrap_diff(ser[n], ser[ref])
        lines.append(f"| {report.NAMES.get(n, n)} | {s['total'] - stats(ser[ref])['total']:+.1%} | "
                     f"{lo:+.1%} ~ {hi:+.1%} | {pos:.0%} |")

    if not a.no_rolling and first - warm >= 0:
        length = 126
        wins = rolling(dates, px, opens, warm, last, length, 21)
        lines += ["", f"换起点再测：{len(wins)} 个 {length} 个交易日（约 6 个月）的窗口，起点每隔约 1 个月，"
                  "看排名稳不稳：", "",
                  "| 策略 | 最好 | 最差 | 名次中位数 | 最好名次 | 最差名次 |", "| --- | --- | --- | --- | --- | --- |"]
        keys = [n for n in wins[0] if n in ser]
        ranks = {n: [] for n in keys}
        for w_ in wins:
            order = sorted(keys, key=lambda n: -w_[n])
            for i, n in enumerate(order, 1):
                ranks[n].append(i)
        for n in sorted(keys, key=lambda n: statistics.median(ranks[n])):
            rs = [w_[n] for w_ in wins]
            lines.append(f"| {report.NAMES.get(n, n)} | {max(rs):+.1%} | {min(rs):+.1%} | "
                         f"{statistics.median(ranks[n]):.0f} | {min(ranks[n])} | {max(ranks[n])} |")
    text = "\n".join(lines)
    out.with_suffix(".md").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
