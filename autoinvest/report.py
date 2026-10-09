"""策略对照报告：从 logs/strategies.csv 生成排行表（Markdown）和价值曲线图（SVG），给每天的收盘日报用。

    python -m autoinvest.report                          打印排行表
    python -m autoinvest.report --svg reports/curves.svg  同时画曲线图

只读日志，不连券商，云端的 Claude 定时任务也能直接跑。
"""
from __future__ import annotations

import argparse
import csv
from html import escape
from pathlib import Path

from .portfolios import TITLES

ROOT = Path(__file__).resolve().parent.parent

NAMES = {
    "actual": "我的账户", "baseline": "规则基准", "fixed_100": "100% 股票", "fixed_80_20": "股债 80/20",
    "fixed_60_40": "股债 60/40", "fixed_50_50": "股债 50/50", "trend_100": "趋势跟踪", "trend_80_30": "温和趋势",
    "dual_momentum": "双动量", "vol_target": "波动率目标", "risk_parity": "风险平价", "intraday": "日内交易",
    **TITLES,
}
# 图上用颜色标出的线（最多 8 条，颜色按这个顺序固定，不随排名变）；其余画成细灰线
HIGHLIGHT = ["actual", "baseline", "fixed_60_40", "fixed_100", "claude_stocks", "claude_stocks_daily",
             "claude_sectors", "sector_momentum"]
LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]


def default_csv(root: Path = ROOT) -> Path:
    """实盘开始后用 logs/real/strategies.csv，没有就用模拟盘的 logs/strategies.csv。"""
    real = root / "logs" / "real" / "strategies.csv"
    return real if real.exists() else root / "logs" / "strategies.csv"


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("time")]


def series(rows: list[dict]) -> dict[str, list[tuple[str, float]]]:
    """每个策略的 [(日期, 价值)]，跳过还没开始记的日子。"""
    out: dict[str, list] = {}
    for r in rows:
        for k, v in r.items():
            if k == "time" or v in ("", None):
                continue
            try:
                out.setdefault(k, []).append((r["time"][:10], float(v)))
            except ValueError:
                continue
    return out


def summary(data: dict, budget: float = 2000.0) -> list[dict]:
    """按累计收益排序的排行。每个账户都从 budget（2000 美元）开始，累计收益和回撤都从它算起。"""
    out = []
    for k, pts in data.items():
        start, now = budget, pts[-1][1]
        peak, mdd = budget, 0.0
        for _, v in pts:
            peak = max(peak, v)
            mdd = max(mdd, 1 - v / peak if peak else 0.0)
        day = now / pts[-2][1] - 1 if len(pts) > 1 and pts[-2][1] else None
        out.append({"key": k, "name": NAMES.get(k, k), "since": pts[0][0], "value": now,
                    "total": now / start - 1 if start else 0.0, "day": day, "max_dd": mdd})
    return sorted(out, key=lambda s: -s["total"])


def markdown(rows: list[dict]) -> str:
    lines = ["| 排名 | 策略 | 现在 | 累计 | 今天 | 最大回撤 | 开始 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for i, s in enumerate(rows, 1):
        name = f"**{s['name']}**" if s["key"] == "actual" else s["name"]
        day = "—" if s["day"] is None else f"{s['day']:+.2%}"
        lines.append(f"| {i} | {name} | ${s['value']:,.2f} | {s['total']:+.2%} | {day} | "
                     f"{s['max_dd']:.1%} | {s['since'][5:]} |")
    return "\n".join(lines)


def svg(data: dict, width: int = 720, height: int = 360) -> str:
    """价值曲线：横轴日期，纵轴美元。重点策略彩色，其余灰色细线；每条线悬停显示名字和现值。"""
    dates = sorted({d for pts in data.values() for d, _ in pts})
    if not dates:
        return ""
    vals = [v for pts in data.values() for _, v in pts]
    lo, hi = min(vals), max(vals)
    pad = max((hi - lo) * 0.08, 5.0)
    lo, hi = lo - pad, hi + pad
    left, right, top, bottom = 64, 16, 16, 64
    pw, ph = width - left - right, height - top - bottom
    xi = {d: left + (pw * i / (len(dates) - 1) if len(dates) > 1 else pw / 2) for i, d in enumerate(dates)}

    def y(v):
        return top + ph * (hi - v) / (hi - lo)

    style = "".join(f".s{i}{{stroke:{c}}}.f{i}{{fill:{c}}}" for i, c in enumerate(LIGHT))
    dark = "".join(f".s{i}{{stroke:{c}}}.f{i}{{fill:{c}}}" for i, c in enumerate(DARK))
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
             f'height="{height}" font-family="system-ui,sans-serif" font-size="11">',
             f"<style>.bg{{fill:#fcfcfb}}.t1{{fill:#0b0b0b}}.t2{{fill:#52514e}}.grid{{stroke:#e4e3de}}"
             f".muted{{stroke:#b5b4ad}}{style}"
             f"@media (prefers-color-scheme: dark){{.bg{{fill:#1a1a19}}.t1{{fill:#ffffff}}.t2{{fill:#c3c2b7}}"
             f".grid{{stroke:#3a3a37}}.muted{{stroke:#5e5d58}}{dark}}}</style>",
             f'<rect class="bg" width="{width}" height="{height}"/>']
    for i in range(5):   # 横向网格和纵轴刻度
        v = lo + (hi - lo) * i / 4
        parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>')
        parts.append(f'<text class="t2" x="{left - 6}" y="{y(v) + 4:.1f}" text-anchor="end">${v:,.0f}</text>')
    step = max(1, len(dates) // 6)
    for d in dates[::step] + ([dates[-1]] if (len(dates) - 1) % step else []):
        parts.append(f'<text class="t2" x="{xi[d]:.1f}" y="{top + ph + 16}" text-anchor="middle">{d[5:]}</text>')

    def line(k, pts, cls, w):
        title = f"<title>{escape(NAMES.get(k, k))}：${pts[-1][1]:,.2f}</title>"
        if len(pts) == 1:
            d, v = pts[0]
            fill = cls.replace("s", "f", 1) if cls.startswith("s") else "t2"
            return f'<circle class="{fill}" cx="{xi[d]:.1f}" cy="{y(v):.1f}" r="4">{title}</circle>'
        path = " ".join(f"{xi[d]:.1f},{y(v):.1f}" for d, v in pts)
        return (f'<polyline class="{cls}" points="{path}" fill="none" stroke-width="{w}" '
                f'stroke-linejoin="round" stroke-linecap="round">{title}</polyline>')

    for k, pts in data.items():   # 灰线先画，彩线压在上面
        if k not in HIGHLIGHT:
            parts.append(line(k, pts, "muted", 1))
    shown = [k for k in HIGHLIGHT if k in data]
    for k in shown:
        parts.append(line(k, data[k], f"s{HIGHLIGHT.index(k)}", 2.5 if k == "actual" else 2))
    # 图例：两行，每行最多 5 个；灰线的数字在排行表里
    for n, k in enumerate(shown + ["_other"]):
        lx, ly = left + (n % 5) * 128, height - 34 + (n // 5) * 16
        cls = "muted" if k == "_other" else f"s{HIGHLIGHT.index(k)}"
        label = "其他策略" if k == "_other" else NAMES.get(k, k)
        parts.append(f'<line class="{cls}" x1="{lx}" x2="{lx + 16}" y1="{ly - 4}" y2="{ly - 4}" stroke-width="2"/>')
        parts.append(f'<text class="t1" x="{lx + 22}" y="{ly}">{escape(label)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def main(argv=None):
    p = argparse.ArgumentParser(prog="autoinvest.report")
    p.add_argument("--csv", default=str(default_csv()))
    p.add_argument("--svg", help="把曲线图写到这个文件")
    p.add_argument("--budget", type=float, default=2000.0, help="每个账户的起始金额")
    args = p.parse_args(argv)
    data = series(load(Path(args.csv)))
    print(markdown(summary(data, args.budget)))
    if args.svg:
        out = Path(args.svg)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(svg(data), encoding="utf-8")
        print(f"\n曲线图：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
