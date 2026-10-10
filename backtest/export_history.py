"""把对照策略要用的 ETF 的日 K 线（前复权，含分红）导出成 CSV，给 backtest.recent 回放用。

只读：只查行情，不碰账户、不下单、不改任何配置。要在装了 OpenD 的电脑上，仓库根目录运行：
    python -m backtest.export_history            # 默认导出最近约 3 年
输出 backtest/data/recent_closes.csv：列是 date、各代码的收盘价、US.SCHB_open（SCHB 的开盘价，给日内对照用）。
"""
from __future__ import annotations

import csv
import datetime as dt
import sys
from pathlib import Path

from autoinvest import portfolios
from autoinvest.broker import BrokerError, MoomooBroker

OUT = Path(__file__).resolve().parent / "data" / "recent_closes.csv"
CODES = (["US.SCHB", "US.SCHZ", "US.SCHO", "US.SCHF", "US.GLDM"]
         + [c for t in portfolios.FIXED.values() for c in t] + portfolios.SECTORS)


def main(years: float = 3.0):
    codes = list(dict.fromkeys(CODES))
    broker = MoomooBroker()
    start = (dt.date.today() - dt.timedelta(days=int(365 * years))).isoformat()
    end = dt.date.today().isoformat()
    close, opens = {}, {}
    for c in codes:
        ret, df, _ = broker.quote.request_history_kline(c, start=start, end=end, max_count=1000)
        df = broker._check(ret, df, f"查询 {c} 日 K 线")
        close[c] = {str(t)[:10]: float(p) for t, p in zip(df["time_key"], df["close"])}
        if c == "US.SCHB":
            opens[c] = {str(t)[:10]: float(p) for t, p in zip(df["time_key"], df["open"])}
        print(f"{c}: {len(close[c])} 天，{min(close[c])} 至 {max(close[c])}")
    dates = sorted(set.intersection(*[set(v) for v in close.values()]))
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + codes + ["US.SCHB_open"])
        for d in dates:
            w.writerow([d] + [close[c][d] for c in codes] + [opens["US.SCHB"][d]])
    print(f"写入 {OUT}：{len(dates)} 个共同交易日，{dates[0]} 至 {dates[-1]}")


if __name__ == "__main__":
    try:
        main(float(sys.argv[1]) if len(sys.argv) > 1 else 3.0)
    except BrokerError as e:
        sys.exit(str(e))
