"""实时行情：后台线程定时从本机 OpenD 取报价，页面只读这里的缓存。

只用行情接口（OpenQuoteContext），不创建交易连接、不解锁交易。
- 报价快照：交易时段每 15 秒一次，其余时间每 5 分钟一次（不需要订阅）。
- 当天分钟线：订阅 1 分钟 K 线后每分钟取一次；订阅失败时用看板自己记下的报价点画图。
- 标普 500 日线：启动时和每天收盘后各取一次，用来画历史走势里的大盘线。
OpenD 没开时每分钟重试一次，期间页面显示最近一次程序运行的数据。
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import random
import socket
import threading
import time

from . import timeutil

log = logging.getLogger("dashboard")


def num(x) -> float | None:
    """行情里的数字：缺失、0、NaN 都当作没有。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


class Feed:
    """所有行情来源的共同接口：snapshot() 返回一份不会再被改动的字典。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {"status": "starting", "message": "正在连接 OpenD", "quotes": {}, "bars": {},
                      "samples": [], "spy_daily": [], "market_state": None, "quote_date": None,
                      "updated_at": None, "source": None}

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._data)

    def _set(self, **kw):
        with self._lock:
            self._data = {**self._data, **kw}

    def now(self) -> dt.datetime:
        return timeutil.now_et()

    def start(self):
        pass

    def stop(self):
        pass


class OpenDFeed(Feed):
    def __init__(self, host: str, port: int, codes: list[str], bench: str = "US.SPY", start_date: str = ""):
        super().__init__()
        self.host, self.port = host, port
        self.codes = list(dict.fromkeys(codes))
        self.bench = bench
        self.start_date = start_date
        self.ctx = None
        self.subscribed = False
        self._stop = threading.Event()
        self._last_kline = 0.0
        self._day_bars: dict = {}
        self._thread = threading.Thread(target=self._run, name="quotes", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._close()

    def _close(self):
        if self.ctx is not None:
            try:
                self.ctx.close()
            except Exception:  # noqa: BLE001
                pass
        self.ctx = None
        self.subscribed = False

    def _connect(self):
        # SDK 连不上 OpenD 时会一直重试卡住，所以先自己试一下端口
        socket.create_connection((self.host, self.port), timeout=5).close()
        from moomoo import OpenQuoteContext

        self.ctx = OpenQuoteContext(host=self.host, port=self.port)

    def _run(self):
        while not self._stop.is_set():
            wait = 60
            try:
                if self.ctx is None:
                    self._connect()
                wait = self._tick()
            except Exception as e:  # noqa: BLE001 行情出错只影响看板自己
                log.warning("行情更新失败：%s", e)
                self._close()
                self._set(status="offline", message=f"没有连上 OpenD：{e}")
                wait = 60
            self._stop.wait(wait)

    def _tick(self) -> int:
        from moomoo import RET_OK

        ret, state = self.ctx.get_global_state()
        market_state = str(state.get("market_us", "")) if ret == RET_OK else None
        ret, df = self.ctx.get_market_snapshot(self.codes)
        if ret != RET_OK and len(self.codes) > 1:
            # 有一个代码查不到时整批都会失败：逐个试一次，去掉查不到的
            bad = [c for c in self.codes if self.ctx.get_market_snapshot([c])[0] != RET_OK]
            if bad and len(bad) < len(self.codes):
                log.warning("这些代码查不到报价，看板不再显示：%s", bad)
                self.codes = [c for c in self.codes if c not in bad]
                self.subscribed = False
                ret, df = self.ctx.get_market_snapshot(self.codes)
        if ret != RET_OK:
            raise RuntimeError(f"查询报价失败：{df}")
        quotes, qdate = {}, None
        for r in df.to_dict("records"):
            last = num(r.get("last_price"))
            if not last:
                continue
            quotes[r["code"]] = {
                "last": last, "prev_close": num(r.get("prev_close_price")), "open": num(r.get("open_price")),
                "high": num(r.get("high_price")), "low": num(r.get("low_price")),
                "high52": num(r.get("highest52weeks_price")), "low52": num(r.get("lowest52weeks_price")),
                "name": r.get("name"), "time": str(r.get("update_time") or ""),
            }
            if r["code"] == self.bench and r.get("update_time"):
                qdate = str(r["update_time"])[:10]
        now = timeutil.now_et()
        phase = timeutil.PHASES.get(market_state or "", timeutil.session_phase(now))
        qdate = qdate or now.date().isoformat()

        snap = self.snapshot()
        samples = [s for s in snap["samples"] if s[0][:10] == qdate]
        if phase == "open":
            samples.append((now.strftime("%Y-%m-%d %H:%M:%S"), {c: q["last"] for c, q in quotes.items()}))
        bars = snap["bars"]
        if time.monotonic() - self._last_kline > 55 or not bars:
            bars = self._klines(qdate) or bars
            self._last_kline = time.monotonic()
        spy_daily = self._refresh_spy(snap["spy_daily"], now, phase)
        self._set(status="live", message="", quotes=quotes, bars=bars, samples=samples[-800:],
                  spy_daily=spy_daily, market_state=market_state, quote_date=qdate,
                  updated_at=now.isoformat(timespec="seconds"), source="opend")
        return 15 if phase == "open" else 300

    # 标普日线：启动后取一次，收盘后再取一次拿到今天的收盘价；取不到时 5 分钟后再试，不是每 15 秒试一次
    _spy_loaded = None
    _spy_closed = None
    _spy_retry = 0.0

    def _refresh_spy(self, spy_daily: list, now, phase: str) -> list:
        today = now.date()
        closed = phase in ("post", "closed")
        need = not spy_daily or self._spy_loaded != today or (closed and self._spy_closed != today)
        if not need or time.monotonic() < self._spy_retry:
            return spy_daily
        got = self._spy_history()
        if not got:
            self._spy_retry = time.monotonic() + 300
            return spy_daily
        self._spy_loaded = today
        if closed:
            self._spy_closed = today
        return got

    def _klines(self, qdate: str) -> dict:
        from moomoo import RET_OK, KLType, SubType

        if not self.subscribed:
            ret, err = self.ctx.subscribe(self.codes, [SubType.K_1M], subscribe_push=False)
            if ret != RET_OK:
                log.info("订阅分钟线失败，改用报价点画图：%s", err)
                return {}
            self.subscribed = True
        out = {}
        late = timeutil.now_et().strftime("%Y-%m-%d %H:%M:%S") > f"{qdate} 09:46:00"
        for c in self.codes:
            ret, df = self.ctx.get_cur_kline(c, 1000, KLType.K_1M)
            got = self._regular(df, qdate) if ret == RET_OK else {}
            # 最近 1000 根里可能大半是盘后和夜盘的（晚上打开看板时 SPY 就是这样），白天开头没取到的话，
            # 按日期补取这一天的分钟线（取到了就每只每天只取一次）
            if late and (not got or min(got) > f"{qdate} 09:45:00"):
                if not self._day_bars.get((c, qdate)):
                    self._day_bars[(c, qdate)] = self._history_day(c, qdate)
                got = {**self._day_bars[(c, qdate)], **got}
            if got:
                out[c] = sorted(got.items())
        return out

    @staticmethod
    def _regular(df, qdate: str) -> dict:
        return {str(t): num(p) for t, p in zip(df["time_key"], df["close"])
                if timeutil.in_session(str(t), qdate) and num(p)}

    def _history_day(self, code: str, qdate: str) -> dict:
        from moomoo import RET_OK, KLType

        try:
            ret, df, _ = self.ctx.request_history_kline(code, start=qdate, end=qdate, ktype=KLType.K_1M,
                                                        max_count=1000)
        except Exception as e:  # noqa: BLE001
            log.info("取 %s 当天分钟线失败：%s", code, e)
            return {}
        if ret != RET_OK:
            log.info("取 %s 当天分钟线失败：%s", code, df)
            return {}
        return self._regular(df, qdate)

    def _spy_history(self) -> list:
        from moomoo import RET_OK

        start = self.start_date or (dt.date.today() - dt.timedelta(days=120)).isoformat()
        start = (dt.date.fromisoformat(start[:10]) - dt.timedelta(days=10)).isoformat()
        try:
            ret, df, _ = self.ctx.request_history_kline(self.bench, start=start, end=dt.date.today().isoformat(),
                                                        max_count=1000)
        except Exception as e:  # noqa: BLE001
            log.info("取标普 500 日线失败：%s", e)
            return []
        if ret != RET_OK:
            log.info("取标普 500 日线失败：%s", df)
            return []
        return [(str(t)[:10], num(p)) for t, p in zip(df["time_key"], df["close"]) if num(p)]


class OfflineFeed(Feed):
    def __init__(self, message: str):
        super().__init__()
        self._set(status="offline", message=message)


class DemoFeed(Feed):
    """演示模式：不连 OpenD，在最近一次运行的那天编一段行情，时钟停在当天 15:20。
    用来预览页面和截图，数据是假的，页面上会标明。"""

    def __init__(self, prev_prices: dict, codes: list[str], day: str, spy_daily: list | None = None,
                 seed: int = 7):
        super().__init__()
        rnd = random.Random(seed)
        day = dt.date.fromisoformat(day)
        self.clock = dt.datetime.combine(day, dt.time(15, 20))
        defaults = {"US.SPY": 668.4, "US.QQQ": 602.1, "US.DIA": 461.3, "US.IWM": 241.7, "US.TLT": 84.9,
                    "US.GLD": 248.2, "US.USO": 91.4, "US.UUP": 27.3}
        betas = {"US.SPY": 1.0, "US.QQQ": 1.35, "US.DIA": 0.85, "US.IWM": 1.4, "US.TLT": -0.45,
                 "US.GLD": 0.2, "US.USO": 0.6, "US.UUP": -0.15, "US.SCHB": 1.02, "US.SCHF": 0.8,
                 "US.SCHZ": -0.15, "US.SCHO": 0.0, "US.GLDM": 0.2}
        start = dt.datetime.combine(day, timeutil.OPEN)
        minutes = int((self.clock - start).total_seconds() // 60)
        path = [0.0]
        for i in range(minutes):
            path.append(path[-1] + rnd.gauss(0, 0.0005) + (0.00003 if i < 150 else -0.00002))
        quotes, bars = {}, {}
        for c in dict.fromkeys(codes):
            prev = prev_prices.get(c) or defaults.get(c, 50.0)
            beta = betas.get(c, 0.5)
            own, series = 0.0, []
            for i in range(minutes):
                own += rnd.gauss(0, 0.00022)
                p = prev * math.exp(beta * path[i + 1] + own + (0.0012 if c == "US.USO" else 0))
                t = start + dt.timedelta(minutes=i + 1)
                series.append((t.strftime("%Y-%m-%d %H:%M:%S"), round(p, 4)))
            quotes[c] = {"last": series[-1][1], "prev_close": prev, "open": series[0][1],
                         "high": max(p for _, p in series), "low": min(p for _, p in series),
                         "high52": round(prev * (1.04 + 0.05 * rnd.random()), 2),
                         "low52": round(prev * (0.76 + 0.1 * rnd.random()), 2), "name": c, "time": series[-1][0]}
            bars[c] = series
        self._set(status="demo", message="演示模式：行情是模拟的，不是真实数据", quotes=quotes, bars=bars,
                  spy_daily=spy_daily or [], market_state="AFTERNOON", quote_date=day.isoformat(),
                  updated_at=self.clock.isoformat(timespec="seconds"), source="demo")

    def now(self) -> dt.datetime:
        return self.clock
