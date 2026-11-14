"""향후 30일 이벤트 캘린더 (실적·옵션 만기·FOMC·배당락·조기폐장)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from . import calendar as cal

# Fed 공식 FOMC 결정일 (발표된 일정만 기재. 다음 해 일정 공개 시 추가 필요)
FOMC = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29",
        "2026-09-16", "2026-10-28", "2026-12-09"]


def _third_friday(y: int, m: int) -> date:
    d = date(y, m, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def upcoming(session: date, earnings: Optional[date], ex_div: Optional[date], horizon: int = 30) -> List[Dict[str, Any]]:
    end = session + timedelta(days=horizon)
    ev: List[Dict[str, Any]] = []

    def add(d: Optional[date], name: str, sig: str, kind: str):
        if d and session < d <= end:
            ev.append({"date": d.isoformat(), "event": name, "significance": sig, "kind": kind,
                       "days_until": (d - session).days,
                       "trading_days_until": cal.trading_days_between(session, d)})
    add(earnings, "실적 발표 (Earnings)", "HIGH", "earnings")
    add(ex_div, "배당락 (Ex-Dividend)", "LOW", "dividend")
    for k in range(0, 3):
        y, m = session.year + (session.month - 1 + k) // 12, (session.month - 1 + k) % 12 + 1
        tf = _third_friday(y, m)
        if not cal.is_trading_day(tf):
            tf = cal.prev_trading_day(tf)
        quad = m in (3, 6, 9, 12)
        add(tf, "쿼드러플 위칭 (분기 만기)" if quad else "월간 옵션 만기 (OPEX)", "HIGH" if quad else "MED", "opex")
    for s in FOMC:
        add(cal.parse_date(s), "FOMC 금리 결정", "HIGH", "macro")
    d = session
    while d < end:
        d += timedelta(days=1)
        if cal.is_holiday(d) and d.weekday() < 5:
            add(d, "미국 증시 휴장", "MED", "holiday")
        elif cal.is_early_close(d):
            add(d, "조기 폐장 (13:00 ET)", "LOW", "holiday")
    return sorted(ev, key=lambda e: e["date"])
