"""更多对照账户：任意 ETF 或股票的组合，各有一个虚拟的 2000 美元账户，不下单，只用来比较。

三类：
- 固定组合（FIXED）：按固定比例持有，偏离超过 band 时调回。
- 行业动量（sector_momentum）：每月第一次运行时，买过去约 6 个月涨得最多的 3 个行业 ETF，各 1/3。
- Claude 组合（claude_stocks、claude_sectors）：Claude 每周写一次 signals/shadows.json，
  选股票或行业 ETF 和比例；指令日期一变就按新指令调仓，之前一直持有。还没有指令时拿着现金。

账户格式：{"start": 建立日期, "cash": 现金, "units": {代码: 份数}, "last": {代码: 最近一次价格}, "tag": 上次调仓依据}
允许碎股，按当时价格成交，不算手续费和分红，所以和其它对照账户一样只适合互相比较。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SECTORS = ["US.XLK", "US.XLV", "US.XLF", "US.XLE", "US.XLY", "US.XLP",
           "US.XLI", "US.XLU", "US.XLB", "US.XLRE", "US.XLC"]

FIXED = {
    "nasdaq_100": {"US.QQQM": 1.0},                       # 纳斯达克 100，科技股比重高
    "sp500_2x": {"US.SSO": 1.0},                          # 2 倍杠杆标普 500，看杠杆的回撤有多大
    "three_fund": {"US.SCHB": 0.5, "US.SCHF": 0.2, "US.SCHZ": 0.3},   # 三基金组合（美股、海外股、债券）
    "permanent": {"US.SCHB": 0.25, "US.TLT": 0.25, "US.GLDM": 0.25, "US.SCHO": 0.25},  # 永久组合
    "dividend": {"US.SCHD": 1.0},                         # 高股息股票
    "managed_futures": {"US.SCHB": 0.5, "US.SCHZ": 0.3, "US.DBMF": 0.2},  # 股债 + 20% 管理期货（趋势跟踪）
}
CLAUDE = {"claude_stocks": "个股", "claude_sectors": "行业 ETF"}
NAMES = list(FIXED) + ["sector_momentum"] + list(CLAUDE)

TITLES = {
    "nasdaq_100": "纳斯达克 100", "sp500_2x": "2 倍杠杆标普", "three_fund": "三基金组合",
    "permanent": "永久组合", "dividend": "高股息", "managed_futures": "股债+管理期货",
    "sector_momentum": "行业动量", "claude_stocks": "Claude 选股", "claude_sectors": "Claude 行业轮动",
}
DESCRIPTIONS = {
    "nasdaq_100": "100% QQQM（纳斯达克 100），科技股比重高，比全市场更激进",
    "sp500_2x": "100% SSO（2 倍杠杆标普 500），用来看杠杆在大跌时的回撤",
    "three_fund": "SCHB 50% / SCHF 20% / SCHZ 30%：美股、海外股、债券",
    "permanent": "SCHB、TLT（长期国债）、GLDM、SCHO 各 25%，各种经济环境都有一块能扛",
    "dividend": "100% SCHD（高股息美股）",
    "managed_futures": "SCHB 50% / SCHZ 30% / DBMF 20%（管理期货，股债一起跌时常常上涨）",
    "sector_momentum": "每月买过去约 6 个月涨得最多的 3 个行业 ETF，各 1/3",
    "claude_stocks": "Claude 每周挑 5 到 10 只大盘股，单只最多 20%",
    "claude_sectors": "Claude 每周在 11 个行业 ETF 里选配，单个行业最多 50%",
}

MOMENTUM_DAYS = 126   # 约 6 个月
MOMENTUM_TOP = 3
STOCK_CODE = re.compile(r"^US\.[A-Z][A-Z0-9.]{0,9}$")


def validate_picks(kind: str, targets) -> list[str]:
    """检查 Claude 写的一个组合；返回不合规的原因，空列表表示通过。"""
    if not isinstance(targets, dict) or not targets:
        return ["没有 targets"]
    errors = []
    for c, w in targets.items():
        if not isinstance(w, (int, float)) or w <= 0:
            errors.append(f"{c} 的权重 {w} 无效")
        elif kind == "claude_stocks" and (not STOCK_CODE.match(str(c)) or c in SECTORS):
            errors.append(f"{c} 不是有效的美股代码")
        elif kind == "claude_stocks" and w > 0.20 + 1e-9:
            errors.append(f"{c} 占 {w:.0%}，单只股票最多 20%")
        elif kind == "claude_sectors" and c not in SECTORS:
            errors.append(f"{c} 不在行业 ETF 名单里")
        elif kind == "claude_sectors" and w > 0.50 + 1e-9:
            errors.append(f"{c} 占 {w:.0%}，单个行业最多 50%")
    if errors:
        return errors
    if kind == "claude_stocks" and not 5 <= len(targets) <= 10:
        errors.append(f"选了 {len(targets)} 只股票，要 5 到 10 只")
    if kind == "claude_sectors" and len(targets) < 2:
        errors.append("至少要选 2 个行业")
    total = sum(targets.values())
    if total > 1 + 1e-9:
        errors.append(f"权重合计 {total:.2f} 超过 1")
    return errors


def load_picks(path: Path) -> tuple[dict, list[str]]:
    """读取 signals/shadows.json，返回 ({组合名: {"date", "targets"}}, 备注)。不合规的组合会被跳过。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, []
    except json.JSONDecodeError as e:
        return {}, [f"选股对照指令文件格式错误：{e}"]
    date = str(data.get("date", ""))
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return {}, ["选股对照指令文件缺少有效的 date"]
    picks, notes = {}, []
    for kind, label in CLAUDE.items():
        part = data.get(kind)
        if part is None:
            continue
        targets = part.get("targets") if isinstance(part, dict) else None
        errors = validate_picks(kind, targets)
        if errors:
            notes.append(f"Claude 的{label}对照指令被拒绝，继续持有原来的：" + "；".join(errors))
        else:
            picks[kind] = {"date": date, "targets": {c: float(w) for c, w in targets.items()}}
    return picks, notes


def momentum_top(closes: dict, days: int = MOMENTUM_DAYS, top: int = MOMENTUM_TOP) -> dict | None:
    """过去 days 个交易日涨得最多的 top 个代码，各占 1/top；数据不够时返回 None。"""
    rets = {c: v[-1] / v[-days - 1] - 1 for c, v in closes.items() if len(v) > days and v[-days - 1] > 0}
    if len(rets) < top:
        return None
    best = sorted(rets, key=rets.get, reverse=True)[:top]
    return {c: 1 / top for c in best}


def new_account(budget: float, today: str) -> dict:
    return {"start": today, "cash": float(budget), "units": {}, "last": {}, "tag": None}


def value(acct: dict, prices: dict) -> float:
    """按现价估值；这次没拿到价格的代码用最近一次的价格。"""
    for c in acct["units"]:
        if c in prices:
            acct["last"][c] = prices[c]
    return acct["cash"] + sum(q * acct["last"].get(c, 0.0) for c, q in acct["units"].items())


def drift(acct: dict, targets: dict, prices: dict) -> float:
    v = value(acct, prices)
    if v <= 0:
        return 0.0
    codes = set(targets) | set(acct["units"])
    return max(abs(acct["units"].get(c, 0.0) * prices.get(c, acct["last"].get(c, 0.0)) / v - targets.get(c, 0.0))
               for c in codes)


def rebalance(acct: dict, targets: dict, prices: dict) -> None:
    """按现价把账户调成 targets，没分配的部分留作现金。调用前要确保持仓和目标的价格都在。"""
    v = value(acct, prices)
    acct["units"] = {c: v * w / prices[c] for c, w in targets.items() if w > 0}
    acct["cash"] = v - sum(q * prices[c] for c, q in acct["units"].items())
    acct["last"] = {c: prices[c] for c in acct["units"]}


def needed_codes(state: dict, picks: dict) -> list[str]:
    """估值和调仓要用到价格的所有代码。"""
    book = state.get("portfolios") or {}
    codes = [c for t in FIXED.values() for c in t]
    codes += [c for p in picks.values() for c in p["targets"]]
    codes += [c for a in book.values() for c in a["units"]]
    return list(dict.fromkeys(codes))


def update(state: dict, prices: dict, picks: dict, momentum: dict | None, month: str, budget: float,
           today: str, band: float, rebalance_today: bool) -> tuple[dict, list[str]]:
    """更新所有组合对照账户，返回 ({名字: 价值}, 备注)。rebalance_today 为 False 时只估值。"""
    book = state.setdefault("portfolios", {})
    values, notes = {}, []
    for name in NAMES:
        acct = book.setdefault(name, new_account(budget, today))
        targets, tag = None, acct["tag"]
        if name in FIXED:
            targets = FIXED[name]
        elif name == "sector_momentum" and momentum and acct["tag"] != month:
            targets, tag = momentum, month
        elif name in picks and acct["tag"] != picks[name]["date"]:
            targets, tag = picks[name]["targets"], picks[name]["date"]
        if rebalance_today and targets:
            missing = [c for c in set(targets) | set(acct["units"]) if c not in prices]
            due = tag != acct["tag"] or not acct["units"] or drift(acct, targets, prices) > band
            if missing and due:
                notes.append(f"对照账户 {name} 缺少 {sorted(missing)} 的价格，这次不调仓")
            elif due:
                rebalance(acct, targets, prices)
                acct["tag"] = tag
        values[name] = round(value(acct, prices), 2)
    return values, notes


def holdings(state: dict, prices: dict) -> dict:
    """给 status.json 用：每个非固定组合当前的持仓比例和调仓依据，方便 Claude 保持连续性。"""
    out = {}
    for name, acct in (state.get("portfolios") or {}).items():
        if name in FIXED:
            continue
        v = value(acct, prices)
        w = {c: round(q * acct["last"].get(c, 0.0) / v, 4) for c, q in acct["units"].items()} if v > 0 else {}
        out[name] = {"since": acct["tag"], "weights": w, "value": round(v, 2)}
    return out
