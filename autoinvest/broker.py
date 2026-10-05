"""对 moomoo OpenAPI 的薄封装。默认模拟盘；实盘要在 main.py 里通过两道确认才会传进来。"""
from __future__ import annotations

import datetime as dt
import socket

# 美股常规交易时段在 moomoo 里显示为这些状态
OPEN_STATES = {"MORNING", "AFTERNOON"}
OPEN_ORDER_STATUSES = {"WAITING_SUBMIT", "SUBMITTING", "SUBMITTED", "FILLED_PART"}


class BrokerError(RuntimeError):
    pass


class MoomooBroker:
    def __init__(self, host: str = "127.0.0.1", port: int = 11111, acc_id: int = 0, env: str = "SIMULATE"):
        from moomoo import OpenQuoteContext, OpenSecTradeContext, SecurityFirm, TrdEnv, TrdMarket

        # SDK 连不上 OpenD 时会一直重试卡住，所以先自己试一下端口
        try:
            socket.create_connection((host, port), timeout=5).close()
        except OSError as e:
            raise BrokerError(f"连不上 OpenD（{host}:{port}）：{e}。请确认 OpenD 已打开并已登录") from e

        self.env = TrdEnv.REAL if env == "REAL" else TrdEnv.SIMULATE
        self.quote = OpenQuoteContext(host=host, port=port)
        self.trade = OpenSecTradeContext(filter_trdmarket=TrdMarket.US, host=host, port=port,
                                         security_firm=SecurityFirm.FUTUINC)
        self.acc_id = acc_id or self._find_account()

    def _check(self, ret, data, what: str):
        from moomoo import RET_OK

        if ret != RET_OK:
            raise BrokerError(f"{what} 失败：{data}")
        return data

    def _find_account(self) -> int:
        df = self._check(*self.trade.get_acc_list(), "查询账户列表")
        df = df[df["trd_env"] == self.env]
        us = df[df["trdmarket_auth"].apply(lambda m: "US" in list(m))]
        if self.env == "SIMULATE":
            stock = us[us["sim_acc_type"].isin(["STOCK", "STOCK_AND_OPTION"])]
            us = stock if len(stock) else us
        if not len(us):
            kind = "模拟盘" if self.env == "SIMULATE" else "实盘"
            raise BrokerError(f"没有找到美股{kind}账户，请在 App 里确认，或在 config.yaml 里填 acc_id")
        return int(us.iloc[0]["acc_id"])

    def market_state(self) -> str:
        state = self._check(*self.quote.get_global_state(), "查询市场状态")
        return str(state.get("market_us", "N/A"))

    def prices(self, codes: list[str]) -> dict[str, float]:
        df = self._check(*self.quote.get_market_snapshot(codes), "查询行情")
        out = {}
        for _, r in df.iterrows():
            out[r["code"]] = float(r["last_price"])
        missing = {c for c in codes if out.get(c, 0) <= 0}
        if missing:
            raise BrokerError(f"没有拿到这些标的的价格：{sorted(missing)}")
        return out

    def daily_closes(self, codes: list[str], days: int = 120) -> dict[str, list]:
        """每个代码最近 days 个交易日的收盘价（前复权），给 Claude 做分析用。"""
        out = {}
        start = (dt.date.today() - dt.timedelta(days=int(days * 1.6))).isoformat()
        for c in codes:
            ret, df, _ = self.quote.request_history_kline(c, start=start, end=dt.date.today().isoformat(),
                                                          max_count=1000)
            df = self._check(ret, df, f"查询 {c} 日 K 线")
            out[c] = [[str(t)[:10], round(float(p), 4)] for t, p in zip(df["time_key"], df["close"])][-days:]
        return out

    def account_cash(self) -> float:
        df = self._check(*self.trade.accinfo_query(trd_env=self.env, acc_id=self.acc_id, currency="USD"),
                         "查询资金")
        return float(df.iloc[0]["cash"])

    def positions(self) -> dict[str, float]:
        df = self._check(*self.trade.position_list_query(trd_env=self.env, acc_id=self.acc_id),
                         "查询持仓")
        return {r["code"]: float(r["qty"]) for _, r in df.iterrows()}

    def orders_since(self, start: dt.date) -> list[dict]:
        """今天的订单 + 历史订单（按 30 天一段查询），合并去重。"""
        rows: dict[str, dict] = {}
        today = dt.date.today()
        lo = start
        while lo < today:
            hi = min(lo + dt.timedelta(days=30), today)
            df = self._check(*self.trade.history_order_list_query(
                start=f"{lo} 00:00:00", end=f"{hi} 23:59:59", trd_env=self.env, acc_id=self.acc_id),
                "查询历史订单")
            for r in df.to_dict("records"):
                rows[r["order_id"]] = r
            lo = hi + dt.timedelta(days=1)
        df = self._check(*self.trade.order_list_query(trd_env=self.env, acc_id=self.acc_id, refresh_cache=True),
                         "查询今日订单")
        for r in df.to_dict("records"):
            rows[r["order_id"]] = r
        return list(rows.values())

    def order_status(self, order_ids: list[str]) -> dict[str, str]:
        """今天这些订单的当前状态，例如 SUBMITTED、FILLED_ALL。"""
        df = self._check(*self.trade.order_list_query(trd_env=self.env, acc_id=self.acc_id, refresh_cache=True),
                         "查询今日订单")
        wanted = set(order_ids)
        return {str(r["order_id"]): str(r["order_status"]) for r in df.to_dict("records")
                if str(r["order_id"]) in wanted}

    def place_limit(self, code: str, side: str, qty: int, price: float, remark: str) -> str:
        from moomoo import OrderType, TimeInForce, TrdSide

        df = self._check(*self.trade.place_order(
            price=price, qty=qty, code=code,
            trd_side=TrdSide.BUY if side == "BUY" else TrdSide.SELL,
            order_type=OrderType.NORMAL, trd_env=self.env, acc_id=self.acc_id,
            remark=remark, time_in_force=TimeInForce.DAY),
            f"下单 {side} {qty} {code}（实盘下单失败时，先确认 OpenD 里的交易已解锁）")
        return str(df.iloc[0]["order_id"])

    def close(self):
        self.quote.close()
        self.trade.close()
