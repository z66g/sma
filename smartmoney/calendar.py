"""NYSE 거래일 캘린더 (규칙 기반 — 연도 제한 없음).

- 휴장일: New Year's, MLK, Presidents, Good Friday, Memorial, Juneteenth(2022~),
  Independence, Labor, Thanksgiving, Christmas (+ 토/일 대체휴일 규칙).
- 조기폐장(13:00 ET): 독립기념일 전날, 추수감사절 다음날, 크리스마스 이브.
- 세션 날짜: ET 기준 마감(16:00) + 데이터 안정화 버퍼 이후에만 "완료된 세션"으로 인정.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Optional, Set

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    ET = None

# 정규장 마감 후 이 시각(ET)이 지나야 당일 세션을 "완료"로 본다.
# FINRA RegSHO 일일 파일은 보통 18:00 ET 전후 게시된다.
SESSION_READY_ET = time(18, 30)


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def nyse_holidays(year: int) -> Set[date]:
    hs: Set[date] = set()
    # New Year's Day — 토요일이면 대체휴일 없음 (NYSE 규칙)
    ny = date(year, 1, 1)
    if ny.weekday() == 6:
        hs.add(ny + timedelta(days=1))
    elif ny.weekday() != 5:
        hs.add(ny)
    hs.add(_nth_weekday(year, 1, 0, 3))      # MLK
    hs.add(_nth_weekday(year, 2, 0, 3))      # Presidents
    hs.add(_easter(year) - timedelta(days=2))  # Good Friday
    hs.add(_last_weekday(year, 5, 0))        # Memorial
    if year >= 2022:
        hs.add(_observed(date(year, 6, 19)))  # Juneteenth
    hs.add(_observed(date(year, 7, 4)))      # Independence
    hs.add(_nth_weekday(year, 9, 0, 1))      # Labor
    hs.add(_nth_weekday(year, 11, 3, 4))     # Thanksgiving
    hs.add(_observed(date(year, 12, 25)))    # Christmas
    hs |= {d for d in SPECIAL_CLOSURES if d.year == year}
    return hs


# 규칙 밖 임시 휴장 (국가 애도일·재해)
SPECIAL_CLOSURES = {date(2012, 10, 29), date(2012, 10, 30), date(2018, 12, 5), date(2025, 1, 9)}


def is_holiday(d: date) -> bool:
    return d in nyse_holidays(d.year)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and not is_holiday(d)


def is_early_close(d: date) -> bool:
    if not is_trading_day(d):
        return False
    jul3 = date(d.year, 7, 3)
    tg = _nth_weekday(d.year, 11, 3, 4)
    return d in {jul3, tg + timedelta(days=1), date(d.year, 12, 24)}


def next_trading_day(d: date, k: int = 1) -> date:
    cur = d
    while k > 0:
        cur += timedelta(days=1)
        if is_trading_day(cur):
            k -= 1
    return cur


def prev_trading_day(d: date, k: int = 1) -> date:
    cur = d
    while k > 0:
        cur -= timedelta(days=1)
        if is_trading_day(cur):
            k -= 1
    return cur


def trading_days_between(a: date, b: date) -> int:
    """a 이후 b 까지(포함)의 거래일 수. a>=b 이면 0."""
    n, cur = 0, a
    while cur < b:
        cur += timedelta(days=1)
        if is_trading_day(cur):
            n += 1
    return n


def now_et() -> datetime:
    now = datetime.now(timezone.utc)
    return now.astimezone(ET) if ET else now - timedelta(hours=5)


def last_completed_session(at: Optional[datetime] = None) -> date:
    """`at`(기본: 지금) 시점에 데이터가 완비된 가장 최근 정규장 세션."""
    t = (at.astimezone(ET) if (at and ET and at.tzinfo) else at) or now_et()
    d = t.date()
    if is_trading_day(d) and t.time() >= SESSION_READY_ET:
        return d
    return prev_trading_day(d)


def legacy_session_for(run_date: date) -> date:
    """v2 리포트(파일명=KST 실행일, 미국 장 마감 후 아침 실행) → 실제 세션 날짜.

    v2 는 KST 오전에 돌았으므로 데이터는 항상 '실행일 직전 거래일' 세션이다.
    """
    return prev_trading_day(run_date)


def parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()
