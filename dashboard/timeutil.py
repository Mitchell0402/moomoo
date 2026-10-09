"""美东时间和交易时段。

Windows 上的 Python 没有自带时区数据库（要另装 tzdata），所以这里按美国夏令时规则自己算：
三月第二个星期日 2:00 到十一月第一个星期日 2:00 是夏令时（UTC-4），其余时间 UTC-5。
"""
from __future__ import annotations

import datetime as dt

OPEN = dt.time(9, 30)
CLOSE = dt.time(16, 0)


def _nth_sunday(year: int, month: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def et_offset(utc: dt.datetime) -> dt.timedelta:
    y = utc.year
    start = dt.datetime.combine(_nth_sunday(y, 3, 2), dt.time(7))   # 2:00 EST = 7:00 UTC
    end = dt.datetime.combine(_nth_sunday(y, 11, 1), dt.time(6))    # 2:00 EDT = 6:00 UTC
    naive = utc.replace(tzinfo=None)
    return dt.timedelta(hours=-4) if start <= naive < end else dt.timedelta(hours=-5)


def now_et() -> dt.datetime:
    """现在的美东时间（不带时区信息，和程序日志里的时间同一种写法）。"""
    utc = dt.datetime.now(dt.timezone.utc)
    return (utc + et_offset(utc)).replace(tzinfo=None)


def is_weekday(d: dt.date) -> bool:
    return d.weekday() < 5


def prev_weekday(d: dt.date) -> dt.date:
    d -= dt.timedelta(days=1)
    while not is_weekday(d):
        d -= dt.timedelta(days=1)
    return d


def session_phase(now: dt.datetime) -> str:
    """按时钟推断的时段（OpenD 没连上时用）：pre / open / post / closed。不知道节假日。"""
    if not is_weekday(now.date()):
        return "closed"
    t = now.time()
    if dt.time(4) <= t < OPEN:
        return "pre"
    if OPEN <= t < CLOSE:
        return "open"
    if CLOSE <= t < dt.time(20):
        return "post"
    return "closed"


# moomoo 的 market_us 状态 → 页面上的时段
PHASES = {
    "MORNING": "open", "AFTERNOON": "open", "REST": "open",
    "PRE_MARKET_BEGIN": "pre", "PRE_MARKET_END": "pre", "WAITING_OPEN": "pre",
    "AFTER_HOURS_BEGIN": "post", "AFTER_HOURS_END": "post",
    "CLOSED": "closed", "NIGHT_OPEN": "closed", "NIGHT": "closed", "NIGHT_END": "closed",
}
PHASE_LABELS = {"pre": "盘前", "open": "交易中", "post": "盘后", "closed": "休市"}


def next_run(now: dt.datetime, schedule: list[str]) -> dt.datetime | None:
    """下一次计划任务运行的时间（工作日，按 config 里的时间表）。"""
    times = sorted(dt.time.fromisoformat(s) for s in schedule)
    if not times:
        return None
    d = now.date()
    for _ in range(8):
        if is_weekday(d):
            for t in times:
                at = dt.datetime.combine(d, t)
                if at > now:
                    return at
        d += dt.timedelta(days=1)
    return None
