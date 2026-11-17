"""체결(Trade-level) 기반 수급 분해 — Alpaca Market Data (무료 Basic 플랜, SIP 전 거래소 + FINRA TRF).

무료 플랜은 '최근 15분'만 제한되므로 장 마감 후 실행하는 일일 분석에는 제약이 없다
(docs.alpaca.markets/docs/about-market-data-api: Historical data limitation = latest 15 minutes, 200 calls/min).

산출 (CLAUDE.md §2.1 / §3 분류 규칙):
  - 체결 규모 분류: 기관 = 10,000주↑ 또는 $200k↑ 블록 · 리테일 = 100주 미만 또는 $5,000 미만 · 프로 = 그 외
  - 방향: tick rule (직전 정규 체결가 대비 상승=매수 / 하락=매도 / 동일=직전 부호 승계)
  - OBV Δ (기관/프로/리테일/전체), IAR = |기관| / (|리테일| + |프로|)
  - 다크풀(장외) = 거래소 코드 'D' (FINRA ADF/TRF 보고분): 세션별(프리/정규/애프터) 거래량, 다크풀 전용 VWAP, 장외 블록 수
  - Final Absorption ①②: 정규장 마지막 30분 블록 집중도와 그 구간 가격 변화
  - 리테일 교차검증: BJZZ (장외 서브페니 가격 개선 체결로 리테일 매수/매도 식별)
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import calendar as cal
from .net import FetchError, log, session

KEY_ID = os.environ.get("ALPACA_API_KEY_ID", "").strip()
SECRET = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
BASE = "https://data.alpaca.markets/v2/stocks/trades"
MIN_INTERVAL = 0.32          # ≤ ~190 calls/min (무료 한도 200/min)
MAX_PAGES = 400              # 안전 상한 (10k × 400 = 4M 체결/일)

# 방향 판정·규모 분류에서 제외할 비정규 체결 조건 (평균가·지연 보고·파생가 등)
EXCLUDE_COND = {"B", "W", "4", "P", "Z", "U", "C", "N", "R", "H", "7", "V", "M", "9", "Q"}
INST_SHARES, INST_NOTIONAL = 10_000, 200_000.0
RETAIL_SHARES, RETAIL_NOTIONAL = 100, 5_000.0

_last_call = 0.0


def available() -> bool:
    return bool(KEY_ID and SECRET)


def _get(params: Dict[str, Any], url: str = BASE) -> Dict[str, Any]:
    global _last_call
    headers = {"APCA-API-KEY-ID": KEY_ID, "APCA-API-SECRET-KEY": SECRET, "Accept": "application/json"}
    for attempt in range(6):
        wait = MIN_INTERVAL - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        try:
            r = session().get(url, params=params, headers=headers, timeout=40)
        except Exception as e:  # noqa: BLE001
            if attempt == 5:
                raise FetchError(f"alpaca: {e}")
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 429:
            time.sleep(min(60, 3 * (attempt + 1)))
            continue
        if r.status_code in (401, 403):
            raise FetchError(f"alpaca HTTP {r.status_code}: {r.text[:160]}")
        if r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise FetchError(f"alpaca HTTP {r.status_code}: {r.text[:160]}")
        return r.json()
    raise FetchError("alpaca: retries exhausted")


def daily_bar(symbol: str, d: date) -> Optional[Dict[str, Any]]:
    """SIP 일봉 1개 (Yahoo 일봉 미게시 시 폴백). 무료 플랜 SIP 는 15분 이전 데이터 제공."""
    if not available():
        return None
    j = _get({"timeframe": "1Day", "start": d.isoformat(), "end": d.isoformat(), "feed": "sip",
              "adjustment": "raw", "limit": 5}, url=f"https://data.alpaca.markets/v2/stocks/{symbol}/bars")
    for b in j.get("bars") or []:
        t = _ts(b["t"])
        et = t.astimezone(cal.ET) if cal.ET else t
        if et.date() == d and b.get("c"):
            return {"date": d.isoformat(), "open": float(b["o"]), "high": float(b["h"]), "low": float(b["l"]),
                    "close": float(b["c"]), "volume": float(b.get("v") or 0)}
    return None


def _et_bounds(d: date):
    def at(h: int, m: int) -> datetime:
        if cal.ET:
            return datetime(d.year, d.month, d.day, h, m, tzinfo=cal.ET).astimezone(timezone.utc)
        return datetime(d.year, d.month, d.day, h + 5, m, tzinfo=timezone.utc)
    close_h = 13 if cal.is_early_close(d) else 16
    return at(4, 0), at(9, 30), at(close_h, 0), at(20, 0), at(close_h - 1, 30) if close_h == 16 else at(12, 30)


def _ts(s: str) -> datetime:
    # RFC3339 나노초 → 마이크로초로 절단
    s = s.rstrip("Z")
    if "." in s:
        a, b = s.split(".", 1)
        s = f"{a}.{b[:6]}"
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class _Agg:
    def __init__(self, bounds):
        self.pre_start, self.reg_start, self.reg_end, self.ah_end, self.last30 = bounds
        self.last_px: Optional[float] = None
        self.last_sign = 0
        self.obv = {"inst": 0.0, "pro": 0.0, "ret": 0.0}
        self.vol = {"inst": 0.0, "pro": 0.0, "ret": 0.0}
        self.sess = {k: {"vol": 0.0, "dark": 0.0, "trades": 0} for k in ("pre", "reg", "ah")}
        self.dark_pv = self.dark_v = 0.0
        self.blocks = self.dark_blocks = 0
        self.block_vol_reg = self.block_vol_last30 = 0.0
        self.px_last30_open: Optional[float] = None
        self.px_close: Optional[float] = None
        self.bjzz_buy = self.bjzz_sell = 0.0
        self.trades = 0
        self.excluded = 0

    def add(self, tr: Dict[str, Any]) -> None:
        p, s = tr.get("p"), tr.get("s")
        if not p or not s:
            return
        t = _ts(tr["t"])
        x = tr.get("x", "")
        conds = set(tr.get("c") or [])
        sess = "pre" if t < self.reg_start else "reg" if t < self.reg_end else "ah"
        S = self.sess[sess]
        S["vol"] += s
        S["trades"] += 1
        dark = x == "D"
        if dark:
            S["dark"] += s
        self.trades += 1
        if conds & EXCLUDE_COND:
            self.excluded += 1
            return
        # 방향 (tick rule)
        if self.last_px is None or p == self.last_px:
            sign = self.last_sign
        else:
            sign = 1 if p > self.last_px else -1
        self.last_px, self.last_sign = p, sign
        notional = p * s
        cohort = ("inst" if (s >= INST_SHARES or notional >= INST_NOTIONAL) else
                  "ret" if (s < RETAIL_SHARES or notional < RETAIL_NOTIONAL) else "pro")
        self.obv[cohort] += sign * s
        self.vol[cohort] += s
        if cohort == "inst":
            self.blocks += 1
            if dark:
                self.dark_blocks += 1
        if sess == "reg":
            if dark:
                self.dark_pv += p * s
                self.dark_v += s
            if cohort == "inst":
                self.block_vol_reg += s
                if t >= self.last30:
                    self.block_vol_last30 += s
            if t >= self.last30 and self.px_last30_open is None:
                self.px_last30_open = p
            self.px_close = p
            # BJZZ: 장외 체결의 서브페니 가격 개선 → 리테일 (센트 소수부 0.6~1 매수 / 0~0.4 매도)
            if dark:
                frac = round((p * 100) % 1, 4)
                if 0.6 < frac < 1:
                    self.bjzz_buy += s
                elif 0 < frac < 0.4:
                    self.bjzz_sell += s

    def result(self, d: date, pages: int) -> Dict[str, Any]:
        tot_vol = sum(v["vol"] for v in self.sess.values())
        dark_vol = sum(v["dark"] for v in self.sess.values())
        inst, pro, ret = self.obv["inst"], self.obv["pro"], self.obv["ret"]
        bj = self.bjzz_buy + self.bjzz_sell
        return {
            "date": d.isoformat(), "trades": self.trades, "pages": pages, "excluded": self.excluded,
            "inst": inst, "pro": pro, "ret": ret, "total": inst + pro + ret,
            "vol_inst": self.vol["inst"], "vol_pro": self.vol["pro"], "vol_ret": self.vol["ret"],
            "total_vol": tot_vol, "dark_vol": dark_vol,
            "dark_pct": dark_vol / tot_vol * 100 if tot_vol else None,
            "dark_vwap": self.dark_pv / self.dark_v if self.dark_v else None,
            "sess": {k: {"vol": v["vol"], "dark": v["dark"],
                         "dark_pct": v["dark"] / v["vol"] * 100 if v["vol"] else None} for k, v in self.sess.items()},
            "blocks": self.blocks, "dark_blocks": self.dark_blocks,
            "last30_block_share": self.block_vol_last30 / self.block_vol_reg if self.block_vol_reg else None,
            "last30_move_pct": ((self.px_close - self.px_last30_open) / self.px_last30_open * 100)
            if (self.px_close and self.px_last30_open) else None,
            "bjzz_imbalance": (self.bjzz_buy - self.bjzz_sell) / bj if bj else None,
            "bjzz_vol": bj,
        }


def fetch_day(symbol: str, d: date) -> Dict[str, Any]:
    """하루치 전 체결을 페이지 단위로 스트리밍 집계 (메모리에 전부 올리지 않음)."""
    if not available():
        raise FetchError("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY 미설정")
    bounds = _et_bounds(d)
    agg = _Agg(bounds)
    params = {"symbols": symbol, "start": bounds[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
              "end": bounds[3].strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 10000, "feed": "sip", "sort": "asc"}
    pages, token = 0, None
    t0 = time.time()
    while True:
        if token:
            params["page_token"] = token
        j = _get(params)
        for tr in (j.get("trades") or {}).get(symbol, []) or []:
            agg.add(tr)
        pages += 1
        token = j.get("next_page_token")
        if not token or pages >= MAX_PAGES:
            break
    if agg.trades == 0:
        raise FetchError(f"alpaca: no trades for {symbol} on {d}")
    res = agg.result(d, pages)
    log(f"{symbol} flow {d}: {agg.trades:,} trades · {pages} pages · {time.time() - t0:.1f}s")
    return res
