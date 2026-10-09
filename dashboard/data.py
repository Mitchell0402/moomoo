"""把交易程序写出的文件和实时行情拼成看板要显示的数据。只读，不改任何文件。

金额都只算程序管理的那笔钱（budget_usd），和 moomoo App 里整个模拟账户的数字不同。

今日盈亏的算法：现在的资产 − 上一个交易日收盘时的资产。
- 现在的资产 = 最近一次运行后的持仓（把那次下的单算作已成交）× 现价 + 现金
- 收盘时的资产 = 上一个交易日最后一次运行后的持仓 × 昨收价 + 现金
今天换过仓也不影响这个算法：换仓只是把一种 ETF 换成另一种，钱不会凭空多出来。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
from pathlib import Path

import yaml

from . import timeutil

NAMES = {
    "US.SCHB": "美国全市场股票", "US.SCHF": "海外发达市场股票", "US.SCHZ": "美国综合债券",
    "US.SCHO": "美国短期国债", "US.GLDM": "黄金",
    "US.SPY": "标普 500", "US.QQQ": "纳斯达克 100", "US.DIA": "道琼斯工业", "US.IWM": "罗素 2000",
    "US.TLT": "20 年以上美债", "US.GLD": "黄金", "US.USO": "原油", "US.UUP": "美元指数",
}
GROUP_LABELS = {"stock": "股票", "bond": "债券", "gold": "黄金", "cash": "现金"}
DEFAULT_GROUPS = {"stock": ["US.SCHB", "US.SCHF"], "bond": ["US.SCHZ", "US.SCHO"], "gold": ["US.GLDM"]}

STRATEGY_NAMES = {
    "actual": "我的账户",
    "baseline": "规则基准",
    "fixed_100": "100% 股票",
    "fixed_80_20": "股债 80/20",
    "fixed_60_40": "股债 60/40",
    "fixed_50_50": "股债 50/50",
    "trend_100": "趋势跟踪",
    "trend_80_30": "温和趋势",
    "dual_momentum": "双动量",
    "vol_target": "波动率目标",
    "risk_parity": "风险平价",
    "intraday": "日内交易",
}
STRATEGY_DESC = {
    "actual": "Claude 每天在规则基准上下 10 个百分点内微调，程序真实下单",
    "baseline": "SCHB 在 10 个月均线上方 70/20/10（股/债/金），下方 40/50/10，不含 Claude 的调整",
    "intraday": "每天开盘买入 SCHB、收盘全部卖出，晚上拿现金",
}
try:  # 对照策略的说明和交易程序共用一份
    from autoinvest.strategies import STRATEGIES as _S
    STRATEGY_DESC.update(_S)
    from autoinvest.portfolios import DESCRIPTIONS as _D, TITLES as _T
    STRATEGY_DESC.update(_D)
    STRATEGY_NAMES.update(_T)
except Exception:  # noqa: BLE001 看板不能因为这个起不来
    pass

DEFAULT_MARKET = [
    ["US.SPY", "标普 500"], ["US.QQQ", "纳斯达克 100"], ["US.DIA", "道琼斯工业"], ["US.IWM", "罗素 2000"],
    ["US.TLT", "20 年以上美债"], ["US.GLD", "黄金"], ["US.USO", "原油"], ["US.UUP", "美元指数"],
]
DEFAULT_SCHEDULE = ["10:30", "12:30", "14:30", "16:10"]
BENCH = "US.SPY"


# ---------------------------------------------------------------- 读文件

def load_config(root: Path) -> dict:
    for name in ("config.yaml", "config.example.yaml"):
        p = root / name
        if p.exists():
            try:
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
    return {}


def settings(cfg: dict) -> dict:
    d = cfg.get("dashboard") or {}
    market = d.get("market") or DEFAULT_MARKET
    market = [m if isinstance(m, (list, tuple)) else [m, NAMES.get(m, m)] for m in market]
    if BENCH not in [m[0] for m in market]:
        market = [[BENCH, "标普 500"]] + market
    return {
        "host": "127.0.0.1",
        "port": int(d.get("port", 8080)),
        "up_color": "red" if str(d.get("up_color", "green")).lower() == "red" else "green",
        "market": [[str(c), str(n)] for c, n in market],
        "schedule": [str(s) for s in d.get("schedule", DEFAULT_SCHEDULE)],
        "git_fetch_minutes": int(d.get("git_fetch_minutes", 15)),
    }


class Files:
    """按修改时间缓存的文件读取。电脑上的文件优先；没有时（比如云端测试）退回到 backup/ 里的副本。"""

    def __init__(self, root: Path):
        self.root = root
        self._cache: dict[str, tuple[float, object]] = {}

    def _read(self, path: Path, parse):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        key = str(path)
        hit = self._cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            value = parse(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return hit[1] if hit else None
        self._cache[key] = (mtime, value)
        return value

    def json(self, rel: str, fallback: str | None = None):
        v = self._read(self.root / rel, json.loads)
        if v is None and fallback:
            v = self._read(self.root / fallback, json.loads)
        return v

    def csv_rows(self, rel: str) -> list[dict]:
        def parse(text):
            return [r for r in csv.DictReader(io.StringIO(text)) if r.get("time")]
        return self._read(self.root / rel, parse) or []

    def run_records(self, log_dir: str) -> list[dict]:
        folder = self.root / log_dir
        files = sorted(folder.glob("run-*.json"))
        if not files:
            files = sorted((self.root / "backup" / "logs").glob("run-*.json"))
        out = []
        for f in files:
            rec = self._read(f, json.loads)
            if isinstance(rec, dict) and rec.get("time"):
                out.append(rec)
        out.sort(key=lambda r: r["time"])
        return out

    def signals(self) -> dict[str, dict]:
        out = {}
        for f in sorted((self.root / "signals").glob("*.json")):
            sig = self._read(f, json.loads)
            if isinstance(sig, dict) and sig.get("date"):
                if f.stem == "latest":
                    out.setdefault(str(sig["date"]), sig)
                else:
                    out[str(sig["date"])] = sig
        return out


# ---------------------------------------------------------------- 账本

def book_after(rec: dict) -> dict | None:
    """一次运行之后的持仓和现金。运行记录里的持仓是下单前的，这次下的单按限价算作已成交。"""
    if not isinstance(rec.get("holdings"), dict) or rec.get("ledger_cash") is None:
        return None
    holdings = {c: float(q) for c, q in rec["holdings"].items()}
    cash = float(rec["ledger_cash"])
    if rec.get("mode") == "execute":
        for o in rec.get("orders") or []:
            q, p = float(o["qty"]), float(o["price"])
            sign = 1 if o["side"] == "BUY" else -1
            holdings[o["code"]] = holdings.get(o["code"], 0.0) + sign * q
            cash -= sign * q * p
    return {"time": rec["time"], "holdings": holdings, "cash": cash}


class Books:
    def __init__(self, records: list[dict], budget: float):
        self.items = [b for b in (book_after(r) for r in records) if b]
        self.initial = {"time": "", "holdings": {}, "cash": float(budget)}

    def at(self, when: str) -> dict:
        """when（"YYYY-MM-DDTHH:MM:SS"）时点的持仓。"""
        cur = self.initial
        for b in self.items:
            if b["time"] <= when:
                cur = b
            else:
                break
        return cur

    def latest(self) -> dict:
        return self.items[-1] if self.items else self.initial

    def before_day(self, day: str) -> dict:
        return self.at(day + "T00:00:00")


def value_of(book: dict, prices: dict, fallback: dict | None = None) -> float | None:
    total = book["cash"]
    for c, q in book["holdings"].items():
        if abs(q) < 1e-9:
            continue
        p = prices.get(c) or (fallback or {}).get(c)
        if not p:
            return None
        total += q * p
    return total


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b - 1


def r2(x, n=2):
    return None if x is None else round(x, n)


# ---------------------------------------------------------------- 行情

def offline_quotes(records: list[dict], status: dict | None) -> tuple[dict, str | None]:
    """OpenD 没连上时：用最近一次运行记下的价格，昨收价从 status.json 的日 K 线里找。"""
    rec = next((r for r in reversed(records) if isinstance(r.get("prices"), dict)), None)
    if not rec:
        return {}, None
    day = rec["time"][:10]
    closes = (status or {}).get("daily_closes") or {}
    quotes = {}
    for c, p in rec["prices"].items():
        prev = [v for d, v in closes.get(c, []) if d < day]
        quotes[c] = {"last": float(p), "prev_close": float(prev[-1]) if prev else None}
    return quotes, day


# ---------------------------------------------------------------- 组装

def assemble(root: Path, files: Files, feed: dict, git: dict, now: dt.datetime) -> dict:
    """feed：行情线程的快照，见 quotes.py。git：从 GitHub 读到的最新指令，见 gitsync.py。now：美东时间。"""
    cfg = load_config(root)
    st = settings(cfg)
    budget = float(cfg.get("budget_usd", 2000))
    env = cfg.get("trd_env", "SIMULATE")
    log_dir = cfg.get("log_dir", "logs")
    state_name = "state.json" if env == "SIMULATE" else "state-real.json"

    records = files.run_records(log_dir)
    status = files.json("data/status.json") or {}
    state = files.json(state_name, f"backup/{state_name}") or {}
    csv_rows = files.csv_rows(f"{log_dir}/strategies.csv")
    signals = files.signals()
    books = Books(records, budget)

    live = feed.get("status") in ("live", "demo") and bool(feed.get("quotes"))
    if live:
        quotes = feed["quotes"]
        qdate = feed.get("quote_date") or now.date().isoformat()
    else:
        quotes, qdate = offline_quotes(records, status)
        qdate = qdate or now.date().isoformat()
    last = {c: q["last"] for c, q in quotes.items() if q.get("last")}
    prev = {c: q["prev_close"] for c, q in quotes.items() if q.get("prev_close")}

    phase = timeutil.PHASES.get(str(feed.get("market_state") or ""), None) if live else None
    phase = phase or timeutil.session_phase(now)
    if live and qdate != now.date().isoformat() and phase in ("pre", "closed"):
        phase_note = f"显示的是 {qdate} 的数据"
    else:
        phase_note = ""

    # ---- 账户
    cur_book = books.latest()
    prev_book = books.before_day(qdate)
    rec_prices = next((r["prices"] for r in reversed(records) if isinstance(r.get("prices"), dict)), {})
    value_now = value_of(cur_book, last, rec_prices)
    if value_now is None:
        value_now = status.get("managed_value")
    value_prev = value_of(prev_book, prev)
    if value_prev is None and csv_rows:
        older = [r for r in csv_rows if r["time"][:10] < qdate and r.get("actual")]
        value_prev = float(older[-1]["actual"]) if older else None
    peak = max([float(state.get("peak_value") or 0), value_now or 0]
               + [float(r["actual"]) for r in csv_rows if r.get("actual")])
    day_pnl = None if value_now is None or value_prev is None else value_now - value_prev

    # ---- 对照策略：用 state.json 里的虚拟账户 × 现价，算现在的价值和今天的涨跌
    sh_cfg = cfg.get("shadows") or {}
    s_code, b_code = sh_cfg.get("stock", "US.SCHB"), sh_cfg.get("bond", "US.SCHZ")
    shadows = state.get("shadows") or {}

    def shadow_value(acct: dict, px: dict) -> float | None:
        if "units" in acct:
            return value_of({"cash": acct.get("cash", 0.0), "holdings": acct["units"]}, px)
        if "stock" in acct:
            if s_code not in px or b_code not in px:
                return None
            return acct["stock"] * px[s_code] + acct["bond"] * px[b_code]
        return None

    strat_now, strat_prev = {}, {}
    for key, acct in shadows.items():
        if key == "intraday":
            # 日内对照晚上拿现金：今天开盘前的价值 = 上一个交易日最后记下的价值
            older = [r for r in csv_rows if r["time"][:10] < qdate and r.get(key)]
            base = float(older[-1][key]) if older else float(acct.get("value", budget))
            q = quotes.get(s_code) or {}
            opened = q.get("open") and qdate >= acct.get("start", "") and live and phase != "pre"
            factor = q["last"] / q["open"] * (1 - float(sh_cfg.get("intraday_cost", 0.0005))) if opened else 1.0
            strat_now[key], strat_prev[key] = base * factor, base
            continue
        strat_now[key] = shadow_value(acct, last)
        strat_prev[key] = shadow_value(acct, prev)

    csv_last = csv_rows[-1] if csv_rows else {}
    names = [c for c in (csv_rows[0].keys() if csv_rows else []) if c not in ("time", "actual")]
    for k in list(shadows) + names:
        if strat_now.get(k) is None and csv_last.get(k):
            strat_now[k] = float(csv_last[k])

    spy_q = quotes.get(BENCH) or {}
    spy_pct = pct(spy_q.get("last"), spy_q.get("prev_close"))
    acct_pct = pct(value_now, value_prev)

    def day_pct_of(k):
        return pct(strat_now.get(k), strat_prev.get(k))

    compare = [
        {"key": "actual", "label": "我的账户", "pct": r2(acct_pct, 5)},
        {"key": "spy", "label": "标普 500", "pct": r2(spy_pct, 5)},
        {"key": "fixed_60_40", "label": "股债 60/40", "pct": r2(day_pct_of("fixed_60_40"), 5)},
        {"key": "baseline", "label": "规则基准", "pct": r2(day_pct_of("baseline"), 5)},
    ]

    # ---- 持仓
    groups = (status.get("signal_rules") or {}).get("groups") or (cfg.get("signal") or {}).get("groups") \
        or DEFAULT_GROUPS
    group_of = {c: g for g, cs in groups.items() for c in cs}
    targets = state.get("targets") or status.get("current_targets") or status.get("targets") or cfg.get("targets") or {}
    codes = [c for c in dict.fromkeys(list(cur_book["holdings"]) + list(targets))
             if abs(cur_book["holdings"].get(c, 0)) > 1e-9 or targets.get(c, 0) > 0]
    holdings = []
    for c in codes:
        q = cur_book["holdings"].get(c, 0.0)
        p = last.get(c) or rec_prices.get(c)
        pc = prev.get(c)
        val = q * p if p else None
        w = val / value_now if val is not None and value_now else None
        t = float(targets.get(c, 0.0))
        holdings.append({
            "code": c, "symbol": c.split(".", 1)[-1], "name": NAMES.get(c, c), "group": group_of.get(c, "stock"),
            "qty": q, "price": r2(p, 4), "prev_close": r2(pc, 4), "day_pct": r2(pct(p, pc), 5),
            "value": r2(val), "day_pnl": r2(q * (p - pc)) if p and pc else None,
            "weight": r2(w, 4), "target": r2(t, 4), "drift": r2(w - t, 4) if w is not None else None,
        })
    holdings.sort(key=lambda h: -(h["value"] or 0))
    cash_w = cur_book["cash"] / value_now if value_now else None

    # ---- 今日计划
    plan = build_plan(signals, git, records, status, holdings, cash_w, groups, targets, now, st["schedule"])

    # ---- 当天分时
    feed = {**feed, "records_today": [r for r in records if r["time"][:10] == qdate and r.get("mode") == "execute"]}
    intraday = build_intraday(feed, books, qdate, prev, value_prev, shadows, s_code, b_code,
                              strat_prev.get("fixed_60_40")) if live else None

    # ---- 历史
    history, daily = build_history(csv_rows, feed.get("spy_daily") or [], records, signals, groups,
                                   budget, qdate if live else None,
                                   {"actual": value_now, **strat_now} if live else None,
                                   spy_q.get("last") if live else None)

    if live and daily and daily[0]["time"] == "实时":
        daily[0]["stock_weight"] = sum(h["weight"] or 0 for h in holdings if h["group"] == "stock")

    # ---- 策略排行
    strategies = []
    for k in ["actual"] + [k for k in dict.fromkeys(list(shadows) + names)]:
        v = value_now if k == "actual" else strat_now.get(k)
        if v is None:
            continue
        start = budget
        strategies.append({
            "key": k, "name": STRATEGY_NAMES.get(k, k), "desc": STRATEGY_DESC.get(k, ""),
            "value": r2(v), "return": r2(v / start - 1, 5),
            "day_pct": r2(acct_pct if k == "actual" else day_pct_of(k), 5),
        })
    strategies.sort(key=lambda s: -s["value"])

    # ---- 市场
    market = []
    for code, label in st["market"]:
        q = quotes.get(code)
        if not q or not q.get("last"):
            continue
        bars = (feed.get("bars") or {}).get(code) or []
        spark = [round(p, 4) for _, p in bars]
        if len(spark) > 90:
            step = len(spark) / 90
            spark = [spark[int(i * step)] for i in range(90)] + [spark[-1]]
        market.append({
            "code": code, "symbol": code.split(".", 1)[-1], "label": label,
            "price": r2(q["last"], 4), "prev_close": r2(q.get("prev_close"), 4),
            "change": r2(q["last"] - q["prev_close"], 4) if q.get("prev_close") else None,
            "pct": r2(pct(q["last"], q.get("prev_close")), 5),
            "high52": r2(q.get("high52"), 4), "low52": r2(q.get("low52"), 4), "spark": spark,
        })

    g = status.get("guards") or {}
    trend = dict(g.get("trend") or {})
    trend_code = (status.get("guard_rules") or {}).get("trend_code", "US.SCHB")
    if trend.get("sma"):
        price = last.get(trend_code) or trend.get("price")
        trend.update({"code": trend_code, "symbol": trend_code.split(".", 1)[-1], "price": r2(price, 4),
                      "pct_above": r2(price / trend["sma"] - 1, 5), "below": price < trend["sma"]})
    trend.update({"stock_cap": g.get("stock_cap"), "drawdown_brake": g.get("drawdown_brake"),
                  "regime": (status.get("baseline") or {}).get("regime")})

    # ---- 运行和系统
    runs = [{
        "time": r["time"], "mode": r.get("mode"), "value": r.get("managed_value"),
        "orders": len(r.get("orders") or []), "notes": r.get("notes") or [],
        "ok": not any(str(n).startswith("错误") for n in r.get("notes") or []),
    } for r in reversed(records[-30:])]
    orders = []
    for r in reversed(records):
        if r.get("mode") != "execute":
            continue
        for o in r.get("orders") or []:
            orders.append({"time": r["time"], "code": o["code"], "symbol": o["code"].split(".", 1)[-1],
                           "name": NAMES.get(o["code"], o["code"]), "side": o["side"], "qty": o["qty"],
                           "price": o["price"], "amount": r2(float(o["qty"]) * float(o["price"]))})
    today = now.date().isoformat()
    runs_today = [r for r in records if r["time"][:10] == today]
    nxt = timeutil.next_run(now, st["schedule"])
    halt = (root / "signals" / "HALT").exists() or bool(git.get("halt"))
    stop = (root / "STOP").exists() or cfg.get("enabled", True) is False

    alerts = []
    if stop:
        alerts.append({"level": "warn", "text": "总开关已关闭（STOP 文件或 enabled: false），程序不会交易"})
    if halt:
        alerts.append({"level": "warn", "text": "远程急停已打开（signals/HALT），程序不会交易"})
    if records and runs[0]["ok"] is False:
        alerts.append({"level": "error", "text": "最近一次运行出错：" + next(
            (n for n in runs[0]["notes"] if str(n).startswith("错误")), "")})
    first_run = min((dt.time.fromisoformat(s) for s in st["schedule"]), default=None)
    if (timeutil.is_weekday(now.date()) and first_run and not runs_today
            and now.time() > (dt.datetime.combine(now.date(), first_run) + dt.timedelta(minutes=20)).time()
            and phase != "closed"):
        alerts.append({"level": "warn", "text": f"今天 {first_run:%H:%M} 应该运行一次，但还没有运行记录（电脑睡眠、OpenD 没开或计划任务没跑）"})
    if feed.get("status") == "offline":
        alerts.append({"level": "info", "text": "没有连上 OpenD，显示的是最近一次程序运行时的数据"})
    dd = 1 - value_now / peak if value_now and peak else 0.0

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "settings": {"up_color": st["up_color"], "budget": budget, "start_date": str(cfg.get("start_date", "")),
                     "schedule": st["schedule"]},
        "env": env,
        "market_phase": {"phase": phase, "label": timeutil.PHASE_LABELS[phase], "note": phase_note,
                         "quote_date": qdate, "state": feed.get("market_state")},
        "feed": {k: feed.get(k) for k in ("status", "message", "updated_at", "source")},
        "account": {
            "value": r2(value_now), "prev_value": r2(value_prev), "day_pnl": r2(day_pnl), "day_pct": r2(acct_pct, 5),
            "total_pnl": r2(value_now - budget) if value_now else None,
            "total_return": r2(value_now / budget - 1, 5) if value_now else None,
            "peak": r2(peak), "drawdown": r2(dd, 5), "cash": r2(cur_book["cash"]), "cash_weight": r2(cash_w, 4),
            "live": live, "as_of": feed.get("updated_at") if live else (records[-1]["time"] if records else None),
            "stock_weight": r2(sum(h["weight"] or 0 for h in holdings if h["group"] == "stock"), 4),
        },
        "compare": compare,
        "intraday": intraday,
        "plan": plan,
        "holdings": holdings,
        "market": market,
        "trend": trend,
        "history": history,
        "daily": daily,
        "strategies": strategies,
        "orders": orders[:60],
        "runs": runs,
        "schedule": {"next": nxt.isoformat(timespec="minutes") if nxt else None, "today_count": len(runs_today),
                     "last": records[-1]["time"] if records else None},
        "system": {"stop": stop, "halt": halt, "git": {k: git.get(k) for k in ("ok", "fetched_at", "message")},
                   "ledger_mode": status.get("ledger_mode")},
        "alerts": alerts,
    }


def build_plan(signals, git, records, status, holdings, cash_w, groups, targets, now, schedule) -> dict:
    sig = None
    cands = [s for s in [git.get("signal")] + [signals[d] for d in sorted(signals)[-1:]] if s]
    for s in cands:
        if sig is None or str(s.get("date", "")) > str(sig.get("date", "")):
            sig = s
    today = now.date().isoformat()
    todays = [r for r in records if r["time"][:10] == today]
    latest = todays[-1] if todays else (records[-1] if records else {})
    base = (latest.get("baseline") or status.get("baseline") or {})
    base_t = base.get("targets") or {}
    ranges = base.get("ranges") or (status.get("signal_rules") or {}).get("allowed_ranges") or {}
    sig_t = (sig or {}).get("targets") or {}
    # 程序最终用的目标（护栏之后）；今天还没运行时就是指令里的
    applied_t = (todays[-1].get("targets") if todays else None) or sig_t or targets

    cur = {h["code"]: h["weight"] or 0 for h in holdings}
    group_rows = []
    for g in ("stock", "bond", "gold"):
        cs = groups.get(g, [])
        if not cs:
            continue
        group_rows.append({
            "key": g, "label": GROUP_LABELS[g],
            "target": r2(sum(float(applied_t.get(c, 0)) for c in cs), 4),
            "baseline": r2(sum(float(base_t.get(c, 0)) for c in cs), 4) if base_t else None,
            "range": ranges.get(g),
            "current": r2(sum(cur.get(c, 0) for c in cs), 4),
        })
    t_sum = sum(float(v) for v in applied_t.values())
    group_rows.append({"key": "cash", "label": "现金", "target": r2(max(0.0, 1 - t_sum), 4),
                       "baseline": r2(max(0.0, 1 - sum(base_t.values())), 4) if base_t else None,
                       "range": None, "current": r2(cash_w, 4)})
    etfs = []
    for c in dict.fromkeys([c for cs in groups.values() for c in cs]):
        t, b, w = float(applied_t.get(c, 0)), float(base_t.get(c, 0)), cur.get(c, 0)
        if t or b or w:
            etfs.append({"code": c, "symbol": c.split(".", 1)[-1], "name": NAMES.get(c, c), "target": r2(t, 4),
                         "baseline": r2(b, 4), "current": r2(w, 4)})

    sdate = str((sig or {}).get("date", ""))
    used = [r for r in todays if (r.get("signal") or {}).get("date") == sdate]
    if not sig:
        state, label = "none", "还没有 Claude 的计划"
    elif sdate != today:
        state, label = "old", f"今天还没有新计划，显示的是 {sdate} 的"
    elif used:
        state, label = "applied", f"程序已采用（{used[0]['time'][11:16]}）"
    elif todays:
        state, label = "rejected", "程序今天没有采用这份计划，见下方说明"
    else:
        first = min(schedule) if schedule else "10:30"
        state, label = "waiting", f"等待 {first} 程序运行时执行"
    orders_today = [{"time": r["time"], "code": o["code"], "symbol": o["code"].split(".", 1)[-1],
                     "side": o["side"], "qty": o["qty"], "price": o["price"]}
                    for r in todays if r.get("mode") == "execute" for o in r.get("orders") or []]
    shown = todays or ([r for r in records if r["time"][:10] == records[-1]["time"][:10]] if records else [])
    timeline = [{"time": r["time"], "mode": r.get("mode"), "notes": r.get("notes") or [],
                 "orders": len(r.get("orders") or []) if r.get("mode") == "execute" else 0} for r in shown]
    return {
        "date": sdate or None, "state": state, "state_label": label, "timeline": timeline,
        "rationale": (sig or {}).get("rationale", ""), "sources": (sig or {}).get("sources") or [],
        "regime": base.get("regime"), "band": base.get("band"),
        "groups": group_rows, "etfs": etfs, "orders_today": orders_today,
        "notes": latest.get("notes") or [], "notes_time": latest.get("time"),
        "rebalance_band": status.get("rebalance_band", 0.05),
        "max_drift": latest.get("max_drift"),
        "from_github": bool(git.get("signal") and sig is git.get("signal")),
    }


def build_intraday(feed, books, qdate, prev, value_prev, shadows, s_code, b_code, bal_prev) -> dict | None:
    """当天每分钟：账户、标普 500、股债 60/40 相对昨收的涨跌。"""
    bars = feed.get("bars") or {}
    source = "kline"
    if not bars.get(BENCH):
        samples = feed.get("samples") or []
        if len(samples) < 2:
            return None
        source = "samples"
        bars = {}
        for t, px in samples:
            for c, p in px.items():
                bars.setdefault(c, []).append((t, p))
    times = sorted({t for series in bars.values() for t, _ in series if timeutil.in_session(t, qdate)})
    if not times or not value_prev:
        return None
    lookup = {c: dict(s) for c, s in bars.items()}
    px = dict(prev)
    bal = shadows.get("fixed_60_40") or {}
    out_t, acct, spy, balanced = [], [], [], []
    for t in times:
        for c, m in lookup.items():
            if t in m:
                px[c] = m[t]
        v = value_of(books.at(t.replace(" ", "T")), px)
        out_t.append(t[11:16])
        acct.append(r2(pct(v, value_prev), 6))
        spy.append(r2(pct(px.get(BENCH), prev.get(BENCH)), 6))
        if bal and bal_prev and s_code in px and b_code in px:
            balanced.append(r2(pct(bal["stock"] * px[s_code] + bal["bond"] * px[b_code], bal_prev), 6))
    marks = [{"t": r["time"][11:16], "orders": len(r.get("orders") or [])}
             for r in feed.get("records_today", [])]
    return {"t": out_t, "account": acct, "spy": spy, "balanced": balanced or None, "source": source,
            "marks": marks}


def build_history(rows, spy_daily, records, signals, groups, budget, live_day, live_vals, spy_live):
    """每天一个点（当天最后一次运行），今天用实时数。返回走势图数据和每日记录表。"""
    by_day: dict[str, dict] = {}
    for r in rows:
        by_day[r["time"][:10]] = r
    if live_day and live_vals and live_vals.get("actual") is not None:
        row = dict(by_day.get(live_day, {}))
        row.update({k: v for k, v in live_vals.items() if v is not None})
        row["time"] = live_day + "T99"  # 实时
        by_day[live_day] = row
    days = sorted(by_day)
    if not days:
        return {"dates": [], "series": {}, "spy_label": ""}, []
    keys = []
    for d in days:
        for k in by_day[d]:
            if k != "time" and k not in keys:
                keys.append(k)

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    series = {k: [r2(num(by_day[d].get(k))) for d in days] for k in keys}
    spy = dict((str(d)[:10], float(p)) for d, p in spy_daily)
    if spy_live and live_day:
        spy[live_day] = float(spy_live)
    spy_label = "标普 500"
    spy_days = sorted(spy)
    if spy and any(d in spy for d in days):
        base_day = next(d for d in days if d in spy)
        base = spy[base_day]
        # 第一天按 2000 美元起点对齐，之后跟着标普 500 的收盘价走
        series["spy"] = [r2(budget * spy[d] / base) if d in spy else None for d in days]
    elif "fixed_100" in series:
        series["spy"] = series["fixed_100"]
        spy_label = "100% 美股（SCHB）"

    # 每日记录
    daily = []
    day_recs: dict[str, list] = {}
    for r in records:
        day_recs.setdefault(r["time"][:10], []).append(r)
    stock_codes = set(groups.get("stock", []))
    for i, d in enumerate(days):
        v = num(by_day[d].get("actual"))
        pv = num(by_day[days[i - 1]].get("actual")) if i else budget
        prev_spy = [x for x in spy_days if x < d]
        s_pct = pct(spy.get(d), spy[prev_spy[-1]]) if d in spy and prev_spy else None
        bal = num(by_day[d].get("fixed_60_40"))
        bal_prev = num(by_day[days[i - 1]].get("fixed_60_40")) if i else budget
        recs = day_recs.get(d, [])
        last = next((r for r in reversed(recs) if book_after(r) and isinstance(r.get("prices"), dict)), None)
        stock_w = None
        if last:  # 当天最后一次运行、下完单之后的股票占比
            b = book_after(last)
            total = value_of(b, last["prices"])
            if total:
                stock_w = sum(q * last["prices"].get(c, 0) for c, q in b["holdings"].items() if c in stock_codes) / total
        sig = signals.get(d) or {}
        t = by_day[d]["time"]
        a_pct = pct(v, pv)
        daily.append({
            "date": d, "value": r2(v), "change": r2(v - pv) if v is not None and pv else None,
            "pct": r2(a_pct, 5), "spy_pct": r2(s_pct, 5), "balanced_pct": r2(pct(bal, bal_prev), 5),
            "diff": r2(a_pct - s_pct, 5) if a_pct is not None and s_pct is not None else None,
            "stock_weight": r2(stock_w, 4),
            "orders": sum(len(r.get("orders") or []) for r in recs if r.get("mode") == "execute"),
            "runs": len(recs), "rationale": sig.get("rationale", ""),
            "time": "实时" if t.endswith("T99") else t[11:16], "partial": not t.endswith("T99") and t[11:16] < "16:00",
        })
    daily.reverse()
    return {"dates": days, "series": series, "spy_label": spy_label}, daily
