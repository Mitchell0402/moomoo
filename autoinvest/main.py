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
from pathlib import Path

import yaml

from .broker import OPEN_ORDER_STATUSES, OPEN_STATES, BrokerError, MoomooBroker
from .claude_signal import resolve_targets, whitelist
from .strategies import stock_weight
from .strategy import build_ledger, plan_rebalance

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
    """每次真正执行后记一行：实际账户（Claude 或固定目标）和每个对照策略的虚拟账户价值。"""
    path = log_dir / "strategies.csv"
    names = list(record["strategies"])
    new = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if new:
            f.write(",".join(["time", "actual"] + names) + "\n")
        f.write(",".join([record["time"], str(record.get("managed_value", ""))]
                         + [str(record["strategies"][n]) for n in names]) + "\n")


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
        ok, out = git("pull", "--rebase")
        if not ok:
            record["notes"].append(f"拉取 Claude 指令失败（git pull）：{out[-200:]}")

    state = load_state(state_path)
    bench_targets = cfg["targets"]
    if use_signal:
        codes = list(dict.fromkeys(whitelist(sig_cfg) + list(bench_targets)))
        prev = state.get("targets") or bench_targets
        targets, sig_notes, accepted = resolve_targets(
            ROOT / sig_cfg.get("path", "signals/latest.json"), dt.date.today(), sig_cfg, prev)
        targets = {c: targets.get(c, 0.0) for c in codes}
        record["notes"] += sig_notes
        if accepted:
            record["signal"] = {"date": accepted["date"], "rationale": accepted.get("rationale", "")}
    else:
        codes = list(bench_targets)
        targets = dict(bench_targets)
    record["targets"] = targets

    remark = cfg.get("remark", "autoinvest-v1")
    broker = None
    try:
        broker = MoomooBroker(cfg.get("opend_host", "127.0.0.1"), int(cfg.get("opend_port", 11111)),
                              int(cfg.get("acc_id", 0)), cfg["trd_env"])
        record["acc_id"] = broker.acc_id
        orders = broker.orders_since(dt.date.fromisoformat(str(cfg["start_date"])))
        ours = [o for o in orders if o.get("remark") == remark]
        ledger = build_ledger(cfg["budget_usd"], ours, remark, codes)
        prices = broker.prices(codes)
        account_cash = broker.account_cash()
        sh_cfg = cfg.get("shadows") or {}
        close_codes = list(dict.fromkeys(codes + [c for c in (sh_cfg.get("stock"), sh_cfg.get("bond")) if c]))
        closes = broker.daily_closes(close_codes, int(sh_cfg.get("history_days", 300)))
        extra = [c for c in close_codes if c not in prices]
        if extra:
            prices.update(broker.prices(extra))
        plan = plan_rebalance(
            ledger, prices, targets, band=cfg["rebalance_band"], cash_buffer=cfg["cash_buffer"],
            slippage=cfg["limit_slippage"], max_order_value=cfg["max_order_value_usd"],
            max_daily_value=cfg["max_daily_value_usd"], account_cash=account_cash)

        peak = max(float(state.get("peak_value", 0)), plan.managed_value)
        drawdown = 1 - plan.managed_value / peak if peak else 0.0
        state["peak_value"] = round(peak, 2)

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
        for c in codes:
            if actual.get(c, 0.0) + 1e-9 < ledger.holdings[c]:
                record["notes"].append(f"警告：{c} 实际持仓 {actual.get(c, 0)} 少于程序记录 {ledger.holdings[c]}，本次不下单")
                plan.orders = []

        today = dt.date.today().isoformat()
        pending = [o for o in ours if o.get("order_status") in OPEN_ORDER_STATUSES]
        traded_today = [o for o in ours if str(o.get("create_time", "")).startswith(today)]
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
                record["notes"].append("今天已经下过单，本次不再下单")
            elif not market_open:
                record["notes"].append(f"现在不在美股常规交易时段（市场状态 {state_now}），本次不下单")
            else:
                for o in plan.orders:
                    oid = broker.place_limit(o.code, o.side, o.qty, o.price, remark)
                    record["orders"].append({"order_id": oid, "code": o.code, "side": o.side,
                                             "qty": o.qty, "price": o.price})
                applied = True
            if applied and market_open:
                # 只有真正在交易时段执行过，才把今天的目标记为"当前目标"，也才推进对照线
                state["targets"] = targets
                if sh_cfg.get("strategies"):
                    values = update_shadows(state, sh_cfg, closes, prices, cfg["budget_usd"], today)
                    record["strategies"] = values
                    record["benchmark_value"] = values.get(sh_cfg.get("benchmark", "fixed_60_40"))
        else:
            record["planned_orders"] = [{"code": o.code, "side": o.side, "qty": o.qty, "price": o.price}
                                        for o in plan.orders]
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

        if use_signal:
            status = {k: record.get(k) for k in ("time", "trd_env", "managed_value", "benchmark_value",
                                                 "drawdown", "holdings", "weights", "targets", "notes", "orders",
                                                 "strategies")}
            status["current_targets"] = state.get("targets") or bench_targets
            status["signal_rules"] = {k: sig_cfg[k] for k in ("groups", "stock_min", "stock_max",
                                                              "max_daily_change", "max_age_days")}
            status["rebalance_band"] = cfg["rebalance_band"]
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
    if record.get("strategies"):
        write_strategies(log_dir, record)

    if sync:
        git("add", "data/status.json", *[str((log_dir / f).relative_to(ROOT)) for f in ("summary.csv", "strategies.csv")
                                          if (log_dir / f).exists()])
        git("commit", "-m", f"daily status {record['time']}")
        ok, out = git("pull", "--rebase")
        ok2, out2 = git("push")
        if not (ok and ok2):
            print(f"推送日志到 GitHub 失败：{(out + out2)[-300:]}")
    return 1 if any(n.startswith("错误") for n in record["notes"]) else 0


if __name__ == "__main__":
    sys.exit(main())
