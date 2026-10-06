"""命令行入口。

    python -m autoinvest status            只看账户和偏离情况，不下单
    python -m autoinvest run               演练：算出要下的单，但不下单
    python -m autoinvest run --execute     真正下单（模拟盘还是实盘看 config.yaml 的 trd_env）

config.yaml 里 signal.enabled 为 true 时，目标比例来自 Claude 每天写的 signals/latest.json，
并且必须落在 signal 里设的边界内；否则用固定的 targets。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

from .broker import OPEN_ORDER_STATUSES, OPEN_STATES, BrokerError, MoomooBroker
from . import baseline, guards
from .claude_signal import resolve_targets, whitelist
from .strategies import stock_weight
from .strategy import Ledger, build_ledger, plan_rebalance

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    env = cfg.setdefault("trd_env", "SIMULATE")
    if env not in ("SIMULATE", "REAL"):
        sys.exit(f"trd_env 只能是 SIMULATE 或 REAL，现在是 {env}")
    if env == "REAL" and cfg.get("real_money_confirmed") is not True:
        sys.exit("要用真实资金，请在 config.yaml 里同时把 real_money_confirmed 改成 true")
    total = sum(cfg["targets"].values())
    if abs(total - 1) > 1e-6:
        sys.exit(f"targets 权重加起来应为 1，现在是 {total}")
    return cfg


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_log(log_dir: Path, record: dict):
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    (log_dir / f"run-{stamp}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = log_dir / "summary.csv"
    new = not summary.exists()
    with summary.open("a", encoding="utf-8") as f:
        if new:
            f.write("time,mode,managed_value,benchmark_value,drawdown,max_drift,orders,note\n")
        note = " | ".join(record.get("notes", [])).replace(",", "，")
        f.write(f"{record['time']},{record['mode']},{record.get('managed_value', '')},"
                f"{record.get('benchmark_value', '')},{record.get('drawdown', '')},"
                f"{record.get('max_drift', '')},{len(record.get('orders', []))},{note}\n")


def write_strategies(log_dir: Path, record: dict):
    """每天一行：实际账户（Claude 或固定目标）和每个对照策略的虚拟账户价值。
    同一天跑好几次时用最后一次覆盖当天那行，所以收盘后那次运行跑过的话，记的就是收盘价。"""
    path = log_dir / "strategies.csv"
    names = list(record["strategies"])
    header = ",".join(["time", "actual"] + names)
    lines = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0].strip() != header:
            # 对照策略的列变了（比如新加了 baseline），旧文件改名保存，新开一个
            path.rename(log_dir / f"strategies-until-{record['time'][:10]}.csv")
            lines = []
    if not lines:
        lines = [header]
    elif len(lines) > 1 and lines[-1].startswith(record["time"][:10]):
        lines.pop()
    lines.append(",".join([record["time"], str(record.get("managed_value", ""))]
                          + [str(record["strategies"][n]) for n in names]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_shadows(state: dict, sh_cfg: dict, closes: dict, prices: dict, budget: float, today: str) -> dict:
    """对照策略：每个策略一个虚拟账户（同样的预算、允许碎股、按当时价格成交），只用来和实际账户比较。"""
    stock, bond = sh_cfg["stock"], sh_cfg["bond"]
    # 盘中拿到的 K 线可能已含今天这根，去掉后再接上实时价，避免重复
    s_hist = [p for d, p in closes.get(stock, []) if d < today] + [prices[stock]]
    b_hist = [p for d, p in closes.get(bond, []) if d < today] + [prices[bond]]
    book = state.setdefault("shadows", {})
    values = {}
    for kind in sh_cfg["strategies"]:
        target = stock_weight(kind, s_hist, b_hist, "daily")
        acct = book.get(kind)
        if acct is None:
            w = 0.6 if target is None else target
            acct = {"start": today, "stock": budget * w / prices[stock], "bond": budget * (1 - w) / prices[bond]}
        value = acct["stock"] * prices[stock] + acct["bond"] * prices[bond]
        if target is not None and abs(acct["stock"] * prices[stock] / value - target) > sh_cfg.get("band", 0.05):
            acct["stock"], acct["bond"] = value * target / prices[stock], value * (1 - target) / prices[bond]
        book[kind] = acct
        values[kind] = round(value, 2)
    return values


def update_baseline_shadow(state: dict, base: dict, prices: dict, budget: float, band: float, today: str) -> float:
    """规则基准的虚拟账户（不含 Claude 的调整），用来单独衡量 Claude 的调整帮了多少。"""
    book = state.setdefault("shadows", {})
    acct = book.get("baseline")
    if acct is None:
        acct = {"start": today, "cash": 0.0,
                "units": {c: budget * w / prices[c] for c, w in base.items() if w > 0}}
    value = acct["cash"] + sum(q * prices[c] for c, q in acct["units"].items())
    drift = max(abs(acct["units"].get(c, 0.0) * prices[c] / value - base.get(c, 0.0))
                for c in set(base) | set(acct["units"]))
    if drift > band:
        acct["units"] = {c: value * w / prices[c] for c, w in base.items() if w > 0}
        acct["cash"] = value * (1 - sum(base.values()))
    book["baseline"] = acct
    return round(value, 2)


def value_shadows(state: dict, sh_cfg: dict, prices: dict, kinds: list) -> dict:
    """按现在的价格给对照账户估值，不调仓。收盘后那次运行用的是收盘价，和实际账户在同一时点比较。"""
    book = state.get("shadows") or {}
    values = {}
    for kind in kinds:
        acct = book.get(kind)
        if acct is None:
            continue
        if "units" in acct:
            value = acct["cash"] + sum(q * prices[c] for c, q in acct["units"].items())
        else:
            value = acct["stock"] * prices[sh_cfg["stock"]] + acct["bond"] * prices[sh_cfg["bond"]]
        values[kind] = round(value, 2)
    return values


def ledger_mode(cfg: dict) -> str:
    """orders：按本程序下过的单推算持仓和现金（模拟盘里有大量虚拟资金时用）；
    account：直接以账户真实持仓和现金为准，分红自动计入（实盘账户只放这笔钱时用）。"""
    mode = cfg.get("ledger", "auto")
    if mode == "auto":
        return "account" if cfg["trd_env"] == "REAL" else "orders"
    if mode not in ("orders", "account"):
        sys.exit(f"ledger 只能是 auto、orders 或 account，现在是 {mode}")
    return mode


FILLED = "FILLED_ALL"


def wait_filled(broker, order_ids: list[str], timeout: float, interval: float = 5) -> bool:
    """等卖单全部成交。超时、或订单被撤销/失败，返回 False。"""
    deadline = time.monotonic() + timeout
    while True:
        status = broker.order_status(order_ids)
        if all(status.get(i) == FILLED for i in order_ids):
            return True
        if any(status.get(i) not in OPEN_ORDER_STATUSES | {FILLED, None} for i in order_ids):
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def git(*args: str) -> tuple[bool, str]:
    try:
        p = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def main(argv=None):
    p = argparse.ArgumentParser(prog="autoinvest")
    p.add_argument("command", choices=["run", "status"])
    p.add_argument("--execute", action="store_true", help="真正下单（默认只演练）")
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = p.parse_args(argv)

    cfg = load_config(Path(args.config))
    mode = "execute" if (args.command == "run" and args.execute) else ("dry-run" if args.command == "run" else "status")
    record = {"time": dt.datetime.now().isoformat(timespec="seconds"), "mode": mode, "notes": [], "orders": []}
    log_dir = ROOT / cfg.get("log_dir", "logs")
    # 模拟盘和实盘各记各的高点，切换时回撤不会串
    state_path = ROOT / ("state.json" if cfg["trd_env"] == "SIMULATE" else "state-real.json")
    record["trd_env"] = cfg["trd_env"]
    sig_cfg = cfg.get("signal") or {}
    use_signal = bool(sig_cfg.get("enabled"))
    sync = use_signal and mode == "execute" and bool(sig_cfg.get("git_sync", True))

    if (ROOT / "STOP").exists() or not cfg.get("enabled", True):
        record["notes"].append("总开关已关闭（存在 STOP 文件或 enabled: false），不做任何操作")
        print(record["notes"][-1])
        write_log(log_dir, record)
        return 0

    if sync:
        ok, out = git("pull", "--rebase", "--autostash")
        if not ok:
            record["notes"].append(f"拉取 Claude 指令失败（git pull）：{out[-200:]}")

    if (ROOT / "signals" / "HALT").exists():
        # 远程急停：Mitchell 在项目里说"停"，Claude 往仓库写 signals/HALT，下一次运行就不再交易
        record["notes"].append("远程急停已打开（signals/HALT），不做任何操作")
        print(record["notes"][-1])
        write_log(log_dir, record)
        return 0

    state = load_state(state_path)
    bench_targets = cfg["targets"]
    g = guards.settings(cfg)
    b = baseline.settings(cfg)
    if use_signal and b["enabled"] and "gold" not in sig_cfg["groups"]:
        # 老的 config.yaml 白名单里没有黄金，基准模式下自动加上
        sig_cfg = {**sig_cfg, "groups": {**sig_cfg["groups"], "gold": list(b["gold_codes"])}}
    codes = list(bench_targets)
    if use_signal:
        codes += whitelist(sig_cfg)
    if b["enabled"]:
        codes += baseline.codes(b)
    if g["enabled"]:
        codes.append(g["bond_code"])
    codes = list(dict.fromkeys(codes))
    prev_targets = state.get("targets") or bench_targets
    targets = {c: float(bench_targets.get(c, 0.0)) for c in codes}
    record["targets"] = targets

    remark = cfg.get("remark", "autoinvest-v1")
    broker = None
    try:
        broker = MoomooBroker(cfg.get("opend_host", "127.0.0.1"), int(cfg.get("opend_port", 11111)),
                              int(cfg.get("acc_id", 0)), cfg["trd_env"])
        record["acc_id"] = broker.acc_id
        orders = broker.orders_since(dt.date.fromisoformat(str(cfg["start_date"])))
        ours = [o for o in orders if o.get("remark") == remark]
        prices = broker.prices(codes)
        account_cash = broker.account_cash()
        mode_ledger = ledger_mode(cfg)
        record["ledger_mode"] = mode_ledger
        if mode_ledger == "account":
            actual = broker.positions()
            ledger = Ledger(cash=account_cash - float(cfg.get("cash_reserve_usd", 0)),
                            holdings={c: actual.get(c, 0.0) for c in codes})
        else:
            ledger = build_ledger(cfg["budget_usd"], ours, remark, codes)
        sh_cfg = cfg.get("shadows") or {}
        close_codes = list(dict.fromkeys(codes + [c for c in (sh_cfg.get("stock"), sh_cfg.get("bond"),
                                                              g["trend_code"]) if c]))
        hist_days = max(int(sh_cfg.get("history_days", 300)), int(g["trend_days"]) + 10)
        closes = broker.daily_closes(close_codes, hist_days)
        extra = [c for c in close_codes if c not in prices]
        if extra:
            prices.update(broker.prices(extra))

        today = dt.date.today().isoformat()
        value_now = ledger.cash + sum(ledger.holdings[c] * prices[c] for c in codes)
        peak = max(float(state.get("peak_value", 0)), value_now)
        drawdown = 1 - value_now / peak if peak else 0.0
        state["peak_value"] = round(peak, 2)

        # 趋势：盘中拿到的 K 线可能已含今天这根，去掉后再接上实时价
        trend_hist = [p for d, p in closes.get(g["trend_code"], []) if d < today] + [prices[g["trend_code"]]]
        trend = guards.trend_state(trend_hist, int(g["trend_days"]))

        # 今天的规则基准，以及 Claude 能调整的范围
        base, ranges = None, None
        if b["enabled"]:
            base, regime = baseline.targets_for(b, trend, bench_targets)
            base = {c: float(base.get(c, 0.0)) for c in codes}
            groups = sig_cfg["groups"] if use_signal else {"stock": g["stock_codes"], "bond": g["bond_codes"],
                                                            "gold": b["gold_codes"]}
            ranges = baseline.ranges(base, float(b["band"]), groups)
            # 护栏压得更低时，Claude 的股票范围跟着降，免得合规的减仓指令被拒
            cap = guards.stock_cap(trend, drawdown, prev_targets, g)
            if "stock" in ranges:
                ranges["stock"] = [min(ranges["stock"][0], cap), min(ranges["stock"][1], cap)]
            record["baseline"] = {"regime": regime, "targets": base, "band": b["band"], "ranges": ranges}
            targets = dict(base)

        if use_signal:
            sig_targets, sig_notes, accepted = resolve_targets(
                ROOT / sig_cfg.get("path", "signals/latest.json"), dt.date.today(), sig_cfg, prev_targets,
                base, ranges)
            targets = {c: sig_targets.get(c, 0.0) for c in codes}
            record["notes"] += sig_notes
            if accepted:
                record["signal"] = {"date": accepted["date"], "rationale": accepted.get("rationale", "")}

        targets, guard_notes, guard_info = guards.apply_guards(targets, prev_targets, trend_hist, drawdown, g)
        record["notes"] += guard_notes
        record["guards"] = guard_info
        record["targets"] = targets

        plan = plan_rebalance(
            ledger, prices, targets, band=cfg["rebalance_band"], cash_buffer=cfg["cash_buffer"],
            slippage=cfg["limit_slippage"], max_order_value=cfg["max_order_value_usd"],
            max_daily_value=cfg["max_daily_value_usd"], account_cash=account_cash)

        record.update({
            "prices": prices, "ledger_cash": round(ledger.cash, 2), "holdings": ledger.holdings,
            "account_cash": account_cash, "managed_value": round(plan.managed_value, 2),
            "weights": {c: round(w, 4) for c, w in plan.weights.items()},
            "max_drift": round(plan.max_drift, 4), "drawdown": round(drawdown, 4),
        })
        record["notes"] += plan.notes
        if drawdown >= cfg["alert_drawdown"]:
            record["notes"].append(f"提醒：已从高点回撤 {drawdown:.1%}，超过提醒线 {cfg['alert_drawdown']:.0%}（程序不会因此卖出）")

        actual = broker.positions()
        for c in codes if mode_ledger == "orders" else []:
            if actual.get(c, 0.0) + 1e-9 < ledger.holdings[c]:
                record["notes"].append(f"警告：{c} 实际持仓 {actual.get(c, 0)} 少于程序记录 {ledger.holdings[c]}，本次不下单")
                plan.orders = []

        pending = [o for o in ours if o.get("order_status") in OPEN_ORDER_STATUSES]
        # 当天只卖不买的情况（卖单没及时成交、买单留到下次）允许当天再跑一次把买单补上
        traded_today = [o for o in ours if str(o.get("create_time", "")).startswith(today)
                        and o.get("trd_side") == "BUY"]
        applied = False
        if mode == "execute":
            state_now = broker.market_state()
            record["market_state"] = state_now
            market_open = state_now in OPEN_STATES or not cfg.get("require_market_open", True)
            if not plan.orders:
                applied = market_open
            elif pending:
                record["notes"].append(f"还有 {len(pending)} 笔未完成订单，本次不再下单")
            elif traded_today:
                record["notes"].append("今天已经买过，本次不再下单")
            elif not market_open:
                record["notes"].append(f"现在不在美股常规交易时段（市场状态 {state_now}），本次不下单")
            else:
                # 先卖后买：卖单成交、钱回到账户后再下买单，避免实盘买单因资金不足被拒
                sells = [o for o in plan.orders if o.side == "SELL"]
                buys = [o for o in plan.orders if o.side == "BUY"]
                sell_ids = []
                for o in sells:
                    oid = broker.place_limit(o.code, o.side, o.qty, o.price, remark)
                    sell_ids.append(oid)
                    record["orders"].append({"order_id": oid, "code": o.code, "side": o.side,
                                             "qty": o.qty, "price": o.price})
                timeout = float(cfg.get("sell_fill_timeout_sec", 90))
                if buys and sell_ids and not wait_filled(broker, sell_ids, timeout):
                    record["notes"].append(f"卖单 {timeout:.0f} 秒内没有全部成交，买单留到下一次运行")
                else:
                    for o in buys:
                        oid = broker.place_limit(o.code, o.side, o.qty, o.price, remark)
                        record["orders"].append({"order_id": oid, "code": o.code, "side": o.side,
                                                 "qty": o.qty, "price": o.price})
                applied = True
            if applied and market_open:
                # 只有真正在交易时段执行过，才把今天的目标记为"当前目标"，也才推进对照线
                state["targets"] = targets
                # 一天可能运行好几次，对照账户每天只在第一次调仓
                if sh_cfg.get("strategies") and state.get("shadow_day") != today:
                    update_shadows(state, sh_cfg, closes, prices, cfg["budget_usd"], today)
                    if base is not None:
                        update_baseline_shadow(state, base, prices, cfg["budget_usd"],
                                               sh_cfg.get("band", 0.05), today)
                    state["shadow_day"] = today
            if sh_cfg.get("strategies") and state.get("shadows"):
                # 每次运行都按现价重新估值（不调仓），收盘后那次就是收盘价
                kinds = list(sh_cfg["strategies"]) + (["baseline"] if base is not None else [])
                values = value_shadows(state, sh_cfg, prices, kinds)
                record["strategies"] = values
                record["benchmark_value"] = values.get(sh_cfg.get("benchmark", "fixed_60_40"))
        else:
            record["planned_orders"] = [{"code": o.code, "side": o.side, "qty": o.qty, "price": o.price}
                                        for o in plan.orders]
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

        if use_signal:
            status = {k: record.get(k) for k in ("time", "trd_env", "managed_value", "benchmark_value",
                                                 "drawdown", "holdings", "weights", "targets", "notes", "orders",
                                                 "strategies", "guards", "ledger_mode", "baseline")}
            status["current_targets"] = state.get("targets") or bench_targets
            status["signal_rules"] = {k: sig_cfg[k] for k in ("groups", "stock_min", "stock_max",
                                                              "max_daily_change", "max_age_days")}
            status["signal_rules"]["mode"] = "baseline" if record.get("baseline") else "legacy"
            if record.get("baseline"):
                # Claude 今天能用的范围：基准 ±band，已叠加护栏的股票上限
                status["signal_rules"]["allowed_ranges"] = record["baseline"]["ranges"]
            status["rebalance_band"] = cfg["rebalance_band"]
            status["guard_rules"] = {k: g[k] for k in ("enabled", "trend_code", "trend_days",
                                                        "stock_max_below_trend", "drawdown_no_add", "stock_codes")}
            n = int(sig_cfg.get("history_days", 120))
            status["daily_closes"] = {c: v[-n:] for c, v in closes.items()}
            data_dir = ROOT / "data"
            data_dir.mkdir(exist_ok=True)
            (data_dir / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    except BrokerError as e:
        record["notes"].append(f"错误：{e}")
    finally:
        if broker:
            broker.close()

    print(json.dumps(record, ensure_ascii=False, indent=2))
    write_log(log_dir, record)
    if record.get("strategies") and mode == "execute":
        write_strategies(log_dir, record)

    if sync:
        git("add", "data/status.json", *[str((log_dir / f).relative_to(ROOT)) for f in ("summary.csv", "strategies.csv")
                                          if (log_dir / f).exists()])
        git("commit", "-m", f"daily status {record['time']}")
        ok, out = git("pull", "--rebase", "--autostash")
        ok2, out2 = git("push")
        if not (ok and ok2):
            print(f"推送日志到 GitHub 失败：{(out + out2)[-300:]}")
    return 1 if any(n.startswith("错误") for n in record["notes"]) else 0


if __name__ == "__main__":
    sys.exit(main())
