#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Smart Money Flow Analyzer (sma.py)
----------------------------------
CLAUDE.md §13.3 SmartMoneyAnalyzer 구조 기반 구현.
입력:  티커 (argv[1])
출력:  ./output/{TICKER}_3Layer_Forensic_{YYYY-MM-DD}.html
       ./output/SmartMoney_{TICKER}_{YYYY-MM-DD}.md

데이터 소스 (전부 무료):
  - Yahoo Finance (yfinance)        : OHLCV, options chain, news, financials
  - FINRA RegSHO daily short volume : 공매도 % 실측
  - FINRA ATS (Dark Pool) weekly    : 다크풀 volume 대체치
  - FRED (API 키 없이도 fredgraph CSV): 매크로 지표
  - SEC EDGAR                       : 최근 filings
  - iborrowdesk.com                 : CTB fee / 가용 대차잔고 (best-effort scrape)

빠진 데이터(가격 티어 필요)는 모두 N/A + PARTIAL 플래그로 명시.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

try:
    import yfinance as yf
except Exception as e:
    print("[FATAL] yfinance 미설치: pip install -r requirements.txt", file=sys.stderr)
    raise

# ─────────────────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────────────────
UA = {
    "User-Agent": "SMA-Rebuild/2.0 (contact: local-analyst@example.com)"
}
OUT_DIR = Path(__file__).resolve().parent / "output"
OUT_DIR.mkdir(exist_ok=True)

# 색상(§11.1)
COLOR = {
    "bg_outer": "#FFFFFF", "bg_card": "#F6F8FA", "bg_panel": "#EAEEF2",
    "border": "#D0D7DE",
    "text": "#1F2328", "muted": "#656D76",
    "bull": "#1A7F5A", "bear": "#CF222B", "warn": "#9A6700", "info": "#0969DA",
    "alert_green": "#DAFBE1", "alert_red": "#FFEBE9",
    "alert_amber": "#FFF8C5", "alert_blue": "#DDF4FF",
    "chart_grid": "#EAEEF2",
}
FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'

# Polygon.io (massive.io 리브랜딩) — 환경변수로만 주입, 절대 커밋 X
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "").strip()
POLYGON_BASE    = "https://api.polygon.io"

# US 시장 휴장일 (대략 2025–2026, §9.1)
US_HOLIDAYS = {
    "2025-01-01","2025-01-20","2025-02-17","2025-04-18","2025-05-26",
    "2025-06-19","2025-07-04","2025-09-01","2025-11-27","2025-12-25",
    "2026-01-01","2026-01-19","2026-02-16","2026-04-03","2026-05-25",
    "2026-06-19","2026-07-03","2026-09-07","2026-11-26","2026-12-25",
}

# ─────────────────────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────────────────────
def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d.strftime("%Y-%m-%d") not in US_HOLIDAYS

def next_trading_day(d: date, k: int = 1) -> date:
    cur = d
    while k > 0:
        cur = cur + timedelta(days=1)
        if is_trading_day(cur):
            k -= 1
    return cur

def prev_trading_day(d: date, k: int = 1) -> date:
    cur = d
    while k > 0:
        cur = cur - timedelta(days=1)
        if is_trading_day(cur):
            k -= 1
    return cur

def safe_div(a, b, default=None):
    try:
        if b is None or b == 0 or (isinstance(b, float) and math.isnan(b)):
            return default
        r = a / b
        if isinstance(r, float) and math.isnan(r):
            return default
        return r
    except Exception:
        return default

def fmt_num(x, unit: str = "", decimals: int = 2) -> str:
    if x is None:
        return "N/A"
    try:
        if isinstance(x, float) and math.isnan(x):
            return "N/A"
    except Exception:
        pass
    if isinstance(x, (int, float)):
        if abs(x) >= 1e9:
            return f"{x/1e9:,.{decimals}f}B{unit}"
        if abs(x) >= 1e6:
            return f"{x/1e6:,.{decimals}f}M{unit}"
        if abs(x) >= 1e3 and decimals == 0:
            return f"{x:,.0f}{unit}"
        return f"{x:,.{decimals}f}{unit}"
    return str(x)

def fmt_pct(x, decimals: int = 1) -> str:
    return "N/A" if x is None else f"{x:+.{decimals}f}%"

def linregress_slope(ys: List[float]) -> float:
    if len(ys) < 2:
        return 0.0
    xs = np.arange(len(ys), dtype=float)
    ys_arr = np.array(ys, dtype=float)
    mask = ~np.isnan(ys_arr)
    if mask.sum() < 2:
        return 0.0
    xs, ys_arr = xs[mask], ys_arr[mask]
    m = ((xs - xs.mean()) * (ys_arr - ys_arr.mean())).sum() / ((xs - xs.mean()) ** 2).sum()
    return float(m)

def cluster_prices(prices: List[float], threshold_pct: float = 0.5) -> List[float]:
    if not prices:
        return []
    prices = sorted(prices)
    clusters = [[prices[0]]]
    for p in prices[1:]:
        if abs(p - clusters[-1][-1]) / clusters[-1][-1] * 100 <= threshold_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [float(np.mean(c)) for c in clusters]


# ─────────────────────────────────────────────────────────────────────────────
# SmartMoneyAnalyzer (§13.3)
# ─────────────────────────────────────────────────────────────────────────────
class SmartMoneyAnalyzer:
    def __init__(self, ticker: str, analysis_date: Optional[str] = None, config: Optional[dict] = None):
        self.ticker = ticker.upper().strip()
        self.date_str = analysis_date or datetime.utcnow().strftime("%Y-%m-%d")
        self.config = config or {}
        self.warnings: List[str] = []
        self.yf = yf.Ticker(self.ticker)

    # ─── 2. Data Acquisition ──────────────────────────────────────────────
    def fetch_all_data(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"partial": {}}
        print(f"[fetch] L4 chart (yfinance OHLCV)...", file=sys.stderr)
        data["l4"]   = self._safe(self.fetch_l4_chart,      "l4")
        print(f"[fetch] L3 options (yfinance chain)...", file=sys.stderr)
        data["l3"]   = self._safe(self.fetch_l3_options,    "l3", data.get("l4", {}))
        print(f"[fetch] L2 short vol (FINRA)...", file=sys.stderr)
        data["l2"]   = self._safe(self.fetch_l2_short,      "l2")
        print(f"[fetch] L1 dark pool (FINRA ATS)...", file=sys.stderr)
        data["l1"]   = self._safe(self.fetch_l1_darkpool,   "l1", data.get("l4", {}))
        print(f"[fetch] Macro (FRED)...", file=sys.stderr)
        data["macro"]= self._safe(self.fetch_macro,         "macro")
        print(f"[fetch] News & SEC...", file=sys.stderr)
        data["news"] = self._safe(self.fetch_news,          "news")
        return data

    def _safe(self, fn, key, *args):
        try:
            return fn(*args)
        except Exception as e:
            self.warnings.append(f"{key}: {e}")
            return {"_error": str(e), "_partial": True}

    # ── L4: OHLCV ─────────────────────────────────────────────────────────
    def fetch_l4_chart(self) -> Dict[str, Any]:
        hist = self.yf.history(period="1y", interval="1d", auto_adjust=False)
        if hist.empty:
            raise RuntimeError(f"no OHLCV for {self.ticker}")
        hist = hist.reset_index()
        hist["Date"] = pd.to_datetime(hist["Date"]).dt.tz_localize(None)
        ohlcv = [
            {
                "date": row["Date"].strftime("%Y-%m-%d"),
                "open": float(row["Open"]), "high": float(row["High"]),
                "low":  float(row["Low"]),  "close":float(row["Close"]),
                "volume": float(row["Volume"]) if not pd.isna(row["Volume"]) else 0.0,
            } for _, row in hist.iterrows()
        ]
        return {
            "ohlcv": ohlcv,
            "current_price": ohlcv[-1]["close"],
            "prev_close":    ohlcv[-2]["close"] if len(ohlcv) > 1 else ohlcv[-1]["close"],
            "52w_high": float(hist["High"].max()),
            "52w_low":  float(hist["Low"].min()),
        }

    # ── L3: Options ───────────────────────────────────────────────────────
    def fetch_l3_options(self, l4: Dict[str, Any]) -> Dict[str, Any]:
        try:
            expiries = list(self.yf.options)
        except Exception as e:
            raise RuntimeError(f"options unavailable: {e}")
        if not expiries:
            raise RuntimeError("no option expiries")
        today = datetime.strptime(self.date_str, "%Y-%m-%d").date()
        # 현재 주차 ATM 체인 — DTE 최소 양수
        def dte_of(e):
            return (datetime.strptime(e, "%Y-%m-%d").date() - today).days
        primary = min(expiries, key=lambda e: (dte_of(e) < 0, abs(dte_of(e))))
        spot = l4.get("current_price", 0) or 0
        chain = self.yf.option_chain(primary)
        calls = chain.calls.copy(); puts = chain.puts.copy()
        calls["type"] = "call"; puts["type"] = "put"
        def pack(df, spot):
            out = []
            for _, r in df.iterrows():
                k   = float(r.get("strike", 0))
                iv  = float(r.get("impliedVolatility", 0) or 0)
                oi  = float(r.get("openInterest", 0) or 0)
                vol = float(r.get("volume", 0) or 0)
                # Greeks 근사 (BS delta/gamma — yfinance에는 greeks 없음)
                delta, gamma = bs_delta_gamma(spot, k, max(dte_of(primary),1)/365.0, iv, r["type"])
                out.append({
                    "strike": k, "oi": oi, "volume": vol,
                    "iv": iv, "delta": delta, "gamma": gamma,
                    "bid": float(r.get("bid",0) or 0), "ask": float(r.get("ask",0) or 0),
                })
            return out
        return {
            "expiry": primary,
            "dte": dte_of(primary),
            "all_expiries": expiries[:10],
            "spot": spot,
            "calls": pack(calls, spot),
            "puts":  pack(puts, spot),
        }

    # ── L2: FINRA short volume ────────────────────────────────────────────
    def fetch_l2_short(self) -> Dict[str, Any]:
        # 최근 20 거래일 FINRA consolidated short volume
        today = datetime.strptime(self.date_str, "%Y-%m-%d").date()
        rows = []
        d = prev_trading_day(today)
        tried = 0
        while len(rows) < 20 and tried < 45:
            url = f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d.strftime('%Y%m%d')}.txt"
            try:
                r = requests.get(url, headers=UA, timeout=15)
                if r.status_code == 200 and len(r.text) > 100:
                    for line in r.text.splitlines()[1:]:
                        parts = line.split("|")
                        if len(parts) >= 5 and parts[1].strip().upper() == self.ticker:
                            rows.append({
                                "date": d.strftime("%Y-%m-%d"),
                                "short_vol": float(parts[2] or 0),
                                "short_exempt": float(parts[3] or 0),
                                "total_vol": float(parts[4] or 0),
                            })
                            break
            except Exception:
                pass
            d = prev_trading_day(d); tried += 1

        for r in rows:
            r["short_pct"] = safe_div(r["short_vol"], r["total_vol"], 0) * 100

        # CTB (iborrowdesk.com — JSON endpoint)
        ctb = self._fetch_ctb()
        return {
            "history": list(reversed(rows)),  # oldest → newest
            "ctb": ctb,
            "_partial": len(rows) < 5,
        }

    def _fetch_ctb(self) -> Dict[str, Any]:
        try:
            url = f"https://iborrowdesk.com/api/ticker/{self.ticker}"
            r = requests.get(url, headers=UA, timeout=12)
            if r.status_code == 200:
                j = r.json()
                daily = j.get("daily", [])[-14:]
                latest = j.get("latest") or (daily[-1] if daily else {})
                return {
                    "latest_fee":   float(latest.get("fee", 0) or 0),
                    "latest_avail": float(latest.get("available", 0) or 0),
                    "history":      daily,
                    "_partial": not bool(daily),
                }
        except Exception:
            pass
        return {"_partial": True, "_error": "iborrowdesk unavailable"}

    # ── L1: Dark Pool (FINRA ATS 대체) ────────────────────────────────────
    def fetch_l1_darkpool(self, l4: Dict[str, Any]) -> Dict[str, Any]:
        """
        정확한 Cboe/ChartExchange 세션별 데이터는 유료이므로
        FINRA 'Short-Sale Volume' 파일에 포함된 Market 필드(N=NASDAQ, Q=NYSE, ...)와
        consolidated vs FINRA_ADF 차이로 다크풀 비중을 근사한다.
        또한 거래 규모 기반 OBV 4-way는 tick 데이터가 없어 근사만 가능하다.
        """
        today = datetime.strptime(self.date_str, "%Y-%m-%d").date()
        ohlcv = l4.get("ohlcv", []) if l4 else []
        dp_rows = []
        d = prev_trading_day(today)
        tried = 0
        while len(dp_rows) < 10 and tried < 30:
            ds = d.strftime("%Y%m%d")
            # FINRA ADF (off-exchange ≈ dark) file
            adf_url = f"https://cdn.finra.org/equity/regsho/daily/FNSQshvol{ds}.txt"
            try:
                r = requests.get(adf_url, headers=UA, timeout=15)
                if r.status_code == 200:
                    adf_short = None; adf_total = None
                    for line in r.text.splitlines()[1:]:
                        parts = line.split("|")
                        if len(parts) >= 5 and parts[1].strip().upper() == self.ticker:
                            adf_short = float(parts[2] or 0)
                            adf_total = float(parts[4] or 0)
                            break
                    if adf_total is not None:
                        # consolidated total
                        c_url = f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{ds}.txt"
                        try:
                            rc = requests.get(c_url, headers=UA, timeout=15)
                            c_total = None
                            for line in rc.text.splitlines()[1:]:
                                parts = line.split("|")
                                if len(parts) >= 5 and parts[1].strip().upper() == self.ticker:
                                    c_total = float(parts[4] or 0); break
                            dp_pct = safe_div(adf_total, c_total, None)
                            dp_rows.append({
                                "date": d.strftime("%Y-%m-%d"),
                                "dp_volume": adf_total,
                                "consolidated_volume": c_total,
                                "dp_pct": (dp_pct * 100) if dp_pct else None,
                            })
                        except Exception:
                            pass
            except Exception:
                pass
            d = prev_trading_day(d); tried += 1

        # OBV 4-way: Polygon 분봉 있으면 진짜 계산, 없으면 일봉 근사
        obv_data, obv_note = None, "OBV 4-way: 일봉 근사(분봉 부재)."
        if POLYGON_API_KEY:
            try:
                minute_bars = self._fetch_polygon_minutes(today, days=5)
                if minute_bars:
                    obv_data = self._compute_obv_4way_from_minutes(minute_bars)
                    obv_note = (f"OBV 4-way: Polygon 1분봉 {len(minute_bars)}개 기준 "
                                f"(top 20% 분봉=institutional, mid 60%=professional, bot 20%=retail).")
            except Exception as e:
                obv_note = f"OBV 4-way: Polygon 호출 실패 ({e}) — 일봉 근사로 fallback."
        if obv_data is None:
            obv_data = self._approx_obv_4way(ohlcv)

        return {
            "sessions": list(reversed(dp_rows)),
            "obv_4way": obv_data,
            "_partial": len(dp_rows) < 3,
            "_note": obv_note,
        }

    # ── Polygon.io 분봉 수집 ──────────────────────────────────────────────
    def _fetch_polygon_minutes(self, today: date, days: int = 5) -> List[Dict[str, Any]]:
        """
        Polygon aggs endpoint: /v2/aggs/ticker/{T}/range/1/minute/{from}/{to}
        반환: [{t, o, h, l, c, v, vw, n} ...]  t = ms epoch
        """
        if not POLYGON_API_KEY:
            return []
        start = prev_trading_day(today, days).strftime("%Y-%m-%d")
        end   = today.strftime("%Y-%m-%d")
        url   = (f"{POLYGON_BASE}/v2/aggs/ticker/{self.ticker}/range/1/minute/"
                 f"{start}/{end}?adjusted=true&sort=asc&limit=50000&apiKey={POLYGON_API_KEY}")
        r = requests.get(url, timeout=20)
        if r.status_code == 429:
            raise RuntimeError("Polygon rate limit (무료 티어 5 req/min)")
        if r.status_code != 200:
            raise RuntimeError(f"Polygon HTTP {r.status_code}")
        j = r.json()
        if j.get("status") not in ("OK","DELAYED"):
            raise RuntimeError(f"Polygon status={j.get('status')}")
        return j.get("results", []) or []

    def _compute_obv_4way_from_minutes(self, bars: List[Dict]) -> Dict[str, Any]:
        """
        1분봉 거래규모 기반 4-way OBV 계산.
        분봉 volume 분포를 percentile로 bucket 분류:
          - top 20%  : institutional (블록 주문이 들어간 바 근사)
          - mid 60%  : professional
          - bot 20%  : retail
        각 버킷별로 signed volume(=sign(close-prev_close) × volume) 누적.
        """
        if len(bars) < 30:
            return {"_partial": True}
        df = pd.DataFrame(bars)
        df = df.sort_values("t").reset_index(drop=True)
        df["prev_c"] = df["c"].shift(1)
        df["delta"]  = df["c"] - df["prev_c"]
        df["sign"]   = np.sign(df["delta"]).fillna(0)
        df["signed_vol"] = df["sign"] * df["v"]
        df["notional"] = df["v"] * df["c"]

        # percentile 컷오프
        v80 = df["v"].quantile(0.80)
        v20 = df["v"].quantile(0.20)

        inst_mask   = df["v"] >= v80
        retail_mask = df["v"] <= v20
        prof_mask   = ~(inst_mask | retail_mask)

        delta_inst = float(df.loc[inst_mask,   "signed_vol"].sum())
        delta_pro  = float(df.loc[prof_mask,   "signed_vol"].sum())
        delta_ret  = float(df.loc[retail_mask, "signed_vol"].sum())
        delta_tot  = delta_inst + delta_pro + delta_ret

        iar = safe_div(abs(delta_inst), (abs(delta_ret) + abs(delta_pro)), None)

        # 마지막 1시간 vs 그 이전 1시간 비교로 divergence 근사
        df["bucket"] = pd.cut(df.index, bins=min(60, len(df)//10), labels=False)
        price_path = df.groupby("bucket")["c"].last().dropna().tolist()
        inst_path  = df.loc[inst_mask].groupby("bucket")["signed_vol"].sum().cumsum().dropna().tolist() or [0,0]
        price_slope = linregress_slope(price_path[-20:])
        obv_slope   = linregress_slope(inst_path[-20:])
        if price_slope < 0 and obv_slope > 0:
            divergence = "BULLISH_DIVERGENCE"
        elif price_slope > 0 and obv_slope < 0:
            divergence = "BEARISH_DIVERGENCE"
        elif abs(price_slope) < 1e-4 and abs(obv_slope) < 1e-4:
            divergence = "NEUTRAL"
        else:
            divergence = "CONVERGENCE"

        # 세션 분해 (Pre / Regular / AH) — ET 기준 ms epoch
        def session_of(ts_ms):
            # UTC to ET = -5h or -4h (DST). Polygon 's'/'e' 필드 없으면 간단화: 시각(hour) 판정
            hr = datetime.utcfromtimestamp(ts_ms/1000).hour
            # UTC 9:30–16:00 ET = 13:30–20:00 UTC (표준시) / 13:30–19:59 DST
            if 13 <= hr < 20: return "Regular"
            if hr < 13: return "Pre"
            return "AH"
        df["sess"] = df["t"].map(session_of)
        sess_vol = df.groupby("sess")["v"].sum().to_dict()

        return {
            "delta_institutional": delta_inst,
            "delta_professional":  delta_pro,
            "delta_retail":        delta_ret,
            "delta_total":         delta_tot,
            "iar":                 iar,
            "divergence":          divergence,
            "minute_bar_count":    int(len(df)),
            "v80_threshold":       float(v80),
            "v20_threshold":       float(v20),
            "session_volume":      {k: float(v) for k, v in sess_vol.items()},
            "_source":             "polygon_1min",
        }

    def _approx_obv_4way(self, ohlcv: List[Dict]) -> Dict[str, Any]:
        if len(ohlcv) < 6:
            return {"_partial": True}
        df = pd.DataFrame(ohlcv)
        df["delta"] = df["close"].diff().fillna(0)
        df["sign"]  = np.sign(df["delta"])
        df["signed_vol"] = df["sign"] * df["volume"]

        # 최근 5영업일 델타
        recent = df.tail(5)
        # 일평균 거래량 기준 상위 20% → institutional 블록 근사
        v_med   = df["volume"].rolling(30).median().iloc[-1]
        block   = recent[recent["volume"] > v_med * 1.8]
        prof    = recent[(recent["volume"] > v_med * 0.8) & (recent["volume"] <= v_med * 1.8)]
        retail  = recent[recent["volume"] <= v_med * 0.8]

        delta_inst = float(block["signed_vol"].sum())
        delta_pro  = float(prof["signed_vol"].sum())
        delta_ret  = float(retail["signed_vol"].sum())
        delta_tot  = delta_inst + delta_pro + delta_ret

        iar = safe_div(abs(delta_inst), (abs(delta_ret) + abs(delta_pro)), None)

        # 5-day divergence
        closes = df["close"].tail(5).tolist()
        obv_inst_series = block["signed_vol"].cumsum().tolist() or [0,0]
        price_slope = linregress_slope(closes)
        obv_slope   = linregress_slope(obv_inst_series)
        if price_slope < 0 and obv_slope > 0:
            divergence = "BULLISH_DIVERGENCE"
        elif price_slope > 0 and obv_slope < 0:
            divergence = "BEARISH_DIVERGENCE"
        elif abs(price_slope) < 1e-4 and abs(obv_slope) < 1e-4:
            divergence = "NEUTRAL"
        else:
            divergence = "CONVERGENCE"

        return {
            "delta_institutional": delta_inst,
            "delta_professional":  delta_pro,
            "delta_retail":        delta_ret,
            "delta_total":         delta_tot,
            "iar":                 iar,
            "divergence":          divergence,
        }

    # ── Macro (FRED CSV) ──────────────────────────────────────────────────
    def fetch_macro(self) -> Dict[str, Any]:
        def fred_series(sid: str) -> Optional[List[Tuple[str, float]]]:
            url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
            try:
                r = requests.get(url, headers=UA, timeout=15)
                if r.status_code != 200:
                    return None
                out = []
                for line in r.text.splitlines()[-60:]:
                    if "," in line and not line.startswith("DATE"):
                        d, v = line.split(",")[:2]
                        try:
                            out.append((d, float(v)))
                        except Exception:
                            continue
                return out
            except Exception:
                return None

        ff  = fred_series("FEDFUNDS")
        rrp = fred_series("RRPONTSYD")
        sofr= fred_series("SOFR")
        dxy = None
        try:
            d = yf.Ticker("DX-Y.NYB").history(period="1mo")
            if not d.empty:
                dxy = [(i.strftime("%Y-%m-%d"), float(v)) for i, v in d["Close"].items()]
        except Exception:
            pass
        oil = None
        try:
            d = yf.Ticker("CL=F").history(period="1mo")
            if not d.empty:
                oil = [(i.strftime("%Y-%m-%d"), float(v)) for i, v in d["Close"].items()]
        except Exception:
            pass

        return {
            "fedfunds": ff[-1] if ff else None,
            "fedfunds_trend": (ff[-1][1] - ff[-4][1]) if ff and len(ff) >= 4 else None,
            "rrp":       rrp[-1] if rrp else None,
            "rrp_trend": (rrp[-1][1] - rrp[-5][1]) if rrp and len(rrp) >= 5 else None,
            "sofr":      sofr[-1] if sofr else None,
            "dxy":       dxy[-1] if dxy else None,
            "dxy_change_pct": (safe_div(dxy[-1][1] - dxy[0][1], dxy[0][1], 0) * 100) if dxy else None,
            "oil":       oil[-1] if oil else None,
        }

    # ── News + SEC ───────────────────────────────────────────────────────
    def fetch_news(self) -> Dict[str, Any]:
        news_items = []
        try:
            for n in (self.yf.news or [])[:8]:
                ts = n.get("providerPublishTime") or 0
                news_items.append({
                    "title": n.get("title", ""),
                    "publisher": n.get("publisher", ""),
                    "link": n.get("link", ""),
                    "date": datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts else "",
                })
        except Exception:
            pass

        # SEC EDGAR recent filings
        filings = []
        try:
            url = ("https://www.sec.gov/cgi-bin/browse-edgar"
                   f"?action=getcompany&CIK={self.ticker}&type=&dateb=&owner=include&count=10&output=atom")
            r = requests.get(url, headers=UA, timeout=15)
            if r.status_code == 200:
                for entry in re.findall(r"<entry>(.*?)</entry>", r.text, re.S)[:6]:
                    t = re.search(r"<title>(.*?)</title>", entry, re.S)
                    u = re.search(r'<link[^/]*href="([^"]+)"', entry)
                    d = re.search(r"<updated>(.*?)</updated>", entry)
                    if t:
                        filings.append({
                            "title": html.unescape(t.group(1).strip()),
                            "link":  u.group(1) if u else "",
                            "date":  (d.group(1)[:10] if d else ""),
                        })
        except Exception:
            pass

        # 다가오는 이벤트: earnings
        upcoming = []
        try:
            cal = self.yf.calendar
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed:
                    if isinstance(ed, list): ed = ed[0]
                    upcoming.append({
                        "date": str(ed)[:10],
                        "event": "Earnings Call",
                        "significance": "HIGH",
                    })
        except Exception:
            pass

        return {"news": news_items, "filings": filings, "events": upcoming}

    # ─── 3–9. Analysis ────────────────────────────────────────────────────
    def analyze_darkpool(self, l1: Dict) -> Dict[str, Any]:
        if not l1 or l1.get("_error"):
            return {"signal": "NEUTRAL", "confidence": "LOW", "note": "L1 데이터 수집 실패", "_partial": True}
        sessions = l1.get("sessions", [])
        obv = l1.get("obv_4way", {}) or {}

        dp_pct_latest = sessions[-1].get("dp_pct") if sessions else None
        if dp_pct_latest is None:
            dp_class = "N/A"
        elif dp_pct_latest > 50:
            dp_class = "INSTITUTIONAL_HEAVY"
        elif dp_pct_latest > 40:
            dp_class = "ELEVATED"
        elif dp_pct_latest > 30:
            dp_class = "NORMAL"
        else:
            dp_class = "RETAIL_DOMINANT"

        inst_delta = obv.get("delta_institutional", 0) or 0
        divergence = obv.get("divergence", "NEUTRAL")

        # Scenario
        if divergence == "BULLISH_DIVERGENCE" and (dp_pct_latest or 0) > 35:
            scenario, signal = "ACCUMULATION", "BULLISH"
        elif divergence == "BEARISH_DIVERGENCE" and (dp_pct_latest or 0) > 35:
            scenario, signal = "DISTRIBUTION", "BEARISH"
        elif inst_delta > 0 and divergence != "BEARISH_DIVERGENCE":
            scenario, signal = "ACCUMULATION", "BULLISH"
        elif inst_delta < 0 and divergence != "BULLISH_DIVERGENCE":
            scenario, signal = "DISTRIBUTION", "BEARISH"
        else:
            scenario, signal = "NEUTRAL", "NEUTRAL"

        conf = "HIGH" if (dp_pct_latest or 0) > 40 and divergence != "NEUTRAL" else \
               "MEDIUM" if sessions else "LOW"
        return {
            "dp_pct": dp_pct_latest, "dp_class": dp_class,
            "obv": obv, "divergence": divergence,
            "scenario": scenario, "signal": signal, "confidence": conf,
            "sessions": sessions,
        }

    def analyze_short(self, l2: Dict) -> Dict[str, Any]:
        if not l2 or l2.get("_error"):
            return {"signal": "NEUTRAL", "confidence": "LOW", "_partial": True}
        hist = l2.get("history", [])
        ctb  = l2.get("ctb", {}) or {}

        if not hist:
            return {"signal": "NEUTRAL", "confidence": "LOW", "_partial": True}

        latest = hist[-1]
        short_pcts = [h["short_pct"] for h in hist if h.get("short_pct") is not None]
        short_avg_14 = float(np.mean(short_pcts)) if short_pcts else 0
        short_slope  = linregress_slope(short_pcts[-14:])
        ctb_fee = ctb.get("latest_fee")
        ctb_hist= ctb.get("history", [])
        ctb_delta_pct = None
        if len(ctb_hist) >= 2:
            a, b = ctb_hist[-2].get("fee", 0), ctb_hist[-1].get("fee", 0)
            ctb_delta_pct = safe_div((b - a), a, 0) * 100 if a else None

        # Case 분류 (간소화)
        case = "CASE_2_DIRECTIONAL_SHORT"
        if (ctb_delta_pct is not None and abs(ctb_delta_pct) < 3) and latest["short_pct"] > short_avg_14 * 1.2:
            case = "CASE_1_MM_DELTA_HEDGE"  # 숏 급증 + CTB 변동 없음
        elif ctb_fee and ctb_fee > 5 and short_slope > 0:
            case = "CASE_2_DIRECTIONAL_SHORT"
        # Case 3 (synthetic hedge)는 L1 교차검증에서 판정

        # Scenario
        if ctb_fee and ctb_fee > 15 and short_slope > 0:
            scenario, signal = "SHORT_SQUEEZE_RISK", "BULLISH"
        elif case == "CASE_2_DIRECTIONAL_SHORT" and latest["short_pct"] > 50:
            scenario, signal = "DIRECTIONAL_SHORT", "BEARISH"
        elif case == "CASE_1_MM_DELTA_HEDGE":
            scenario, signal = "MM_HEDGE", "NEUTRAL"
        elif short_slope < 0:
            scenario, signal = "NEUTRAL", "NEUTRAL"
        else:
            scenario, signal = "NEUTRAL", "NEUTRAL"

        return {
            "latest_short_pct": latest["short_pct"],
            "avg_14d": short_avg_14, "slope": short_slope,
            "ctb_fee": ctb_fee, "ctb_delta_pct": ctb_delta_pct,
            "shares_available": ctb.get("latest_avail"),
            "case": case, "scenario": scenario, "signal": signal,
            "history": hist,
            "confidence": "MEDIUM" if ctb_fee is not None else "LOW",
        }

    def analyze_options(self, l3: Dict) -> Dict[str, Any]:
        if not l3 or l3.get("_error"):
            return {"signal": "NEUTRAL", "confidence": "LOW", "_partial": True}
        calls, puts = l3.get("calls", []), l3.get("puts", [])
        spot = l3.get("spot", 0) or 0
        # Max Pain
        max_pain = calculate_max_pain({"calls": calls, "puts": puts})

        total_call_oi = sum(c["oi"] for c in calls)
        total_put_oi  = sum(p["oi"] for p in puts)
        total_call_vol= sum(c["volume"] for c in calls)
        total_put_vol = sum(p["volume"] for p in puts)
        pc_oi = safe_div(total_put_oi, total_call_oi, None)
        pc_vol= safe_div(total_put_vol, total_call_vol, None)

        # IV Skew (10% OTM)
        def find_near(opts, target):
            if not opts: return None
            return min(opts, key=lambda o: abs(o["strike"] - target))
        otm_put  = find_near(puts,  spot * 0.90)
        otm_call = find_near(calls, spot * 1.10)
        skew = None
        if otm_put and otm_call and otm_put["iv"] and otm_call["iv"]:
            skew = otm_put["iv"] - otm_call["iv"]

        # GEX
        gex = calculate_gex({"calls": calls, "puts": puts}, spot)
        net_gex = sum(gex["gex_by_strike"].values()) if gex["gex_by_strike"] else 0
        flip = gex["flip_zone"]

        # Scenario
        if l3.get("dte", 99) <= 5 and max_pain and abs(spot - max_pain) / max_pain < 0.005:
            scenario = "PINNING"
        elif pc_oi is not None and pc_oi < 0.7 and pc_vol and pc_vol < 0.7:
            scenario = "GAMMA_SQUEEZE"
        elif skew and skew < -0.03:
            scenario = "GAMMA_SQUEEZE"
        elif net_gex < 0:
            scenario = "VOLATILITY_EXPANSION"
        else:
            scenario = "HEDGING"

        if scenario in ("GAMMA_SQUEEZE",):
            signal = "BULLISH"
        elif scenario == "VOLATILITY_EXPANSION" and pc_oi and pc_oi > 1.3:
            signal = "BEARISH"
        elif scenario == "PINNING":
            signal = "NEUTRAL"
        else:
            signal = "NEUTRAL"

        return {
            "expiry": l3.get("expiry"), "dte": l3.get("dte"),
            "max_pain": max_pain, "max_pain_dist_pct": safe_div(spot - max_pain, max_pain, 0) * 100 if max_pain else None,
            "pc_oi": pc_oi, "pc_vol": pc_vol,
            "skew": skew,
            "gex_by_strike": gex["gex_by_strike"], "net_gex": net_gex, "flip_zone": flip,
            "scenario": scenario, "signal": signal,
            "confidence": "HIGH" if net_gex and len(calls)+len(puts) > 40 else "MEDIUM",
            "calls": calls, "puts": puts, "spot": spot,
        }

    def analyze_chart(self, l4: Dict) -> Dict[str, Any]:
        if not l4 or l4.get("_error"):
            return {"signal": "NEUTRAL", "confidence": "LOW", "_partial": True}
        oh = l4["ohlcv"]
        closes = [c["close"] for c in oh]
        vols   = [c["volume"] for c in oh]
        if len(closes) < 20:
            return {"signal": "NEUTRAL", "confidence": "LOW", "_partial": True}

        sma20  = float(np.mean(closes[-20:]))
        sma50  = float(np.mean(closes[-50:])) if len(closes) >= 50 else None
        sma200 = float(np.mean(closes[-200:])) if len(closes) >= 200 else None
        close  = closes[-1]

        if sma200 and sma50:
            if close > sma20 > sma50 > sma200:  ma = "FULL_BULL"
            elif close < sma20 < sma50 < sma200: ma = "FULL_BEAR"
            elif close > sma20 > sma50 and close < sma200: ma = "RECOVERING"
            else: ma = "MIXED"
        else:
            ma = "INSUFFICIENT_DATA"

        # BB
        bb_mid = sma20; bb_std = float(np.std(closes[-20:], ddof=0))
        bb_up  = bb_mid + 2 * bb_std; bb_lo = bb_mid - 2 * bb_std
        bb_width = safe_div(bb_up - bb_lo, bb_mid, 0) * 100
        bb_pos   = safe_div(close - bb_lo, bb_up - bb_lo, 0)

        # S/R
        highs = [c["high"] for c in oh[-60:]]
        lows  = [c["low"]  for c in oh[-60:]]
        pivots_h, pivots_l = [], []
        for i in range(2, len(highs)-2):
            if highs[i] == max(highs[i-2:i+3]): pivots_h.append(highs[i])
            if lows[i]  == min(lows[i-2:i+3]):  pivots_l.append(lows[i])
        res_levels = [r for r in cluster_prices(pivots_h, 0.5) if r > close]
        sup_levels = [s for s in cluster_prices(pivots_l, 0.5) if s < close]
        imm_res = min(res_levels) if res_levels else None
        key_res = sorted(res_levels)[1] if len(res_levels) > 1 else None
        imm_sup = max(sup_levels) if sup_levels else None
        key_sup = sorted(sup_levels, reverse=True)[1] if len(sup_levels) > 1 else None

        short_slope = linregress_slope(closes[-5:])
        med_slope   = linregress_slope(closes[-20:])
        if short_slope > 0 and med_slope > 0 and ma in ("FULL_BULL","RECOVERING"):
            scenario, signal = "UPTREND", "BULLISH"
        elif short_slope < 0 and med_slope < 0 and ma == "FULL_BEAR":
            scenario, signal = "DOWNTREND", "BEARISH"
        elif bb_width < 5 and abs(med_slope) < (close * 0.001):
            scenario, signal = "BREAKOUT_PENDING" if short_slope > 0 else "RANGE_BOUND", "NEUTRAL"
        else:
            scenario, signal = "RANGE_BOUND", "NEUTRAL"

        return {
            "ma_alignment": ma, "sma20": sma20, "sma50": sma50, "sma200": sma200,
            "bb_upper": bb_up, "bb_lower": bb_lo, "bb_width_pct": bb_width, "bb_position": bb_pos,
            "immediate_resistance": imm_res, "key_resistance": key_res,
            "immediate_support":    imm_sup, "key_support":    key_sup,
            "scenario": scenario, "signal": signal,
            "confidence": "HIGH" if ma in ("FULL_BULL","FULL_BEAR") else "MEDIUM",
            "current_price": close,
        }

    # ─── 8. Patterns ──────────────────────────────────────────────────────
    def detect_patterns(self, l1, l2, l3, l4) -> List[str]:
        patterns = []
        # Theta burn
        try:
            oh = l4.get("ohlcv", []) if l4 else []
            if len(oh) > 33:
                vol30 = np.mean([c["volume"] for c in oh[-33:-3]])
                recent = oh[-3:]
                low_vol = all(c["volume"] < vol30 * 0.6 for c in recent)
                tight_range = all((c["high"] - c["low"]) / c["low"] * 100 < 1.5 for c in recent)
                if low_vol and tight_range:
                    patterns.append("THETA_BURN")
        except Exception: pass
        # Short squeeze setup
        try:
            if l2 and (l2.get("ctb_fee") or 0) > 15 and (l2.get("slope") or 0) > 0:
                patterns.append("SHORT_SQUEEZE_RISK")
        except Exception: pass
        # Gamma squeeze setup
        try:
            if l3 and l3.get("scenario") == "GAMMA_SQUEEZE":
                patterns.append("GAMMA_SQUEEZE_SETUP")
        except Exception: pass
        # Final absorption (5개 조건 중 ≥3)
        try:
            score = 0
            if l1 and (l1.get("dp_pct") or 0) > 40: score += 1
            if l2 and (l2.get("slope") or 0) < 0: score += 1
            if l2 and l2.get("ctb_delta_pct") is not None and l2["ctb_delta_pct"] >= -5 and l2["ctb_delta_pct"] < 5: score += 1
            if l1 and (l1.get("obv", {}).get("delta_institutional", 0) or 0) > 0: score += 1
            if score >= 3:
                patterns.append("FINAL_ABSORPTION")
        except Exception: pass
        # Low-CTB paradox
        try:
            ctb = l2.get("ctb_fee") if l2 else None
            avail_hist = [h.get("available", 0) for h in (l2 or {}).get("history", [])]
            if ctb is not None and ctb < 1.0 and len(avail_hist) >= 2:
                delta = safe_div(avail_hist[-1] - avail_hist[-2], avail_hist[-2], 0) * 100
                if delta < -5:
                    patterns.append("LOW_CTB_PARADOX")
        except Exception: pass
        return patterns

    def classify_macro(self, macro: Dict) -> str:
        if not macro or macro.get("_error"):
            return "NEUTRAL"
        ff = macro.get("fedfunds_trend")
        rrp = macro.get("rrp_trend")
        dxy_chg = macro.get("dxy_change_pct")

        score = 0
        if ff is not None:
            score += 1 if ff < 0 else (-1 if ff > 0 else 0)
        if dxy_chg is not None:
            score += 1 if dxy_chg < -1 else (-1 if dxy_chg > 1 else 0)
        if rrp is not None and rrp < 0:
            score += 1  # RRP 감소 = 유동성 배치 → 위험자산 우호 해석
        if score >= 2:   return "FAVORABLE"
        if score <= -2:  return "RESTRICTED"
        return "NEUTRAL"

    def calculate_scenarios(self, l1, l2, l3, l4, macro_env, patterns) -> Dict[str, Any]:
        l1s = l1.get("signal", "NEUTRAL")
        l2s = l2.get("signal", "NEUTRAL")
        l3s = l3.get("signal", "NEUTRAL")
        l4s = l4.get("signal", "NEUTRAL")
        weights = {"L1":0.35, "L2":0.20, "L3":0.30, "L4":0.15}
        sm = {"BULLISH":1, "NEUTRAL":0, "BEARISH":-1}
        raw = (sm.get(l1s,0)*weights["L1"] + sm.get(l2s,0)*weights["L2"] +
               sm.get(l3s,0)*weights["L3"] + sm.get(l4s,0)*weights["L4"])

        if raw >  0.5: pb, pn, pr = 0.65, 0.20, 0.15
        elif raw >  0.2: pb, pn, pr = 0.50, 0.30, 0.20
        elif raw > -0.2: pb, pn, pr = 0.30, 0.40, 0.30
        elif raw > -0.5: pb, pn, pr = 0.20, 0.30, 0.50
        else:            pb, pn, pr = 0.15, 0.20, 0.65

        if   macro_env == "FAVORABLE":  pb = min(pb+0.07, 0.80); pr = max(pr-0.07, 0.05)
        elif macro_env == "RESTRICTED": pr = min(pr+0.07, 0.80); pb = max(pb-0.07, 0.05)

        if "FINAL_ABSORPTION" in patterns:     pb += 0.05; pr -= 0.05
        if "THETA_BURN"       in patterns:     pn += 0.05
        if "GAMMA_SQUEEZE_SETUP" in patterns:  pb += 0.08
        if "SHORT_SQUEEZE_RISK"  in patterns:  pb += 0.06
        pb = max(pb, 0.01); pn = max(pn, 0.01); pr = max(pr, 0.01)
        tot = pb+pn+pr; pb/=tot; pn/=tot; pr/=tot
        pb = min(pb, 0.80); pn = min(pn, 0.80); pr = min(pr, 0.80)
        return {
            "A_bullish": round(pb*100, 1),
            "B_neutral": round(pn*100, 1),
            "C_bearish": round(pr*100, 1),
            "raw_score": round(raw, 3),
            "macro":     macro_env,
            "patterns":  patterns,
        }

    # ─── 10. Section builders ─────────────────────────────────────────────
    def run_analysis(self, data: Dict) -> Dict[str, Any]:
        l1 = self.analyze_darkpool(data.get("l1", {}))
        l2 = self.analyze_short(data.get("l2", {}))
        l3 = self.analyze_options(data.get("l3", {}))
        l4 = self.analyze_chart(data.get("l4", {}))
        patterns = self.detect_patterns(l1, l2, l3, l4)
        macro_env = self.classify_macro(data.get("macro", {}))
        scenarios = self.calculate_scenarios(l1, l2, l3, l4, macro_env, patterns)
        today = datetime.strptime(self.date_str, "%Y-%m-%d").date()
        phase = {
            "p1_date": prev_trading_day(today, 10).strftime("%Y-%m-%d") + " ~ " + prev_trading_day(today, 1).strftime("%Y-%m-%d"),
            "p2_date": today.strftime("%Y-%m-%d"),
            "p3_date": next_trading_day(today, 5).strftime("%Y-%m-%d") + " ~ " + next_trading_day(today, 20).strftime("%Y-%m-%d"),
        }
        return {
            "l1": l1, "l2": l2, "l3": l3, "l4": l4,
            "patterns": patterns, "macro_env": macro_env,
            "scenarios": scenarios, "phase": phase,
            "raw_data": data,
            "meta": {
                "ticker": self.ticker, "date": self.date_str,
                "price": l4.get("current_price"),
                "warnings": self.warnings,
            },
        }

    # ─── 12. Output ───────────────────────────────────────────────────────
    def generate_outputs(self, analysis: Dict) -> Tuple[str, str]:
        html_str = render_html(analysis, self)
        md_str   = render_markdown(analysis, self)
        html_path = OUT_DIR / f"{self.ticker}_3Layer_Forensic_{self.date_str}.html"
        md_path   = OUT_DIR / f"SmartMoney_{self.ticker}_{self.date_str}.md"
        html_path.write_text(html_str, encoding="utf-8")
        md_path.write_text(md_str, encoding="utf-8")
        return str(html_path), str(md_path)


# ─────────────────────────────────────────────────────────────────────────────
# Option math (§5)
# ─────────────────────────────────────────────────────────────────────────────
def norm_pdf(x): return math.exp(-0.5*x*x)/math.sqrt(2*math.pi)
def norm_cdf(x):
    # Abramowitz & Stegun approximation
    a1, a2, a3 =  0.254829592, -0.284496736, 1.421413741
    a4, a5     = -1.453152027,  1.061405429
    p, sign = 0.3275911, 1
    if x < 0: sign = -1; x = -x
    t = 1.0/(1.0 + p*x)
    y = 1.0 - (((((a5*t + a4)*t) + a3)*t + a2)*t + a1)*t * math.exp(-x*x)
    return 0.5*(1.0 + sign*y)

def bs_delta_gamma(S, K, T, sigma, opt_type):
    try:
        if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
            return (0.0, 0.0)
        d1 = (math.log(S/K) + (0.5*sigma*sigma)*T) / (sigma*math.sqrt(T))
        delta = norm_cdf(d1) if opt_type == "call" else norm_cdf(d1) - 1
        gamma = norm_pdf(d1) / (S * sigma * math.sqrt(T))
        return (delta, gamma)
    except Exception:
        return (0.0, 0.0)

def calculate_max_pain(chain: Dict) -> Optional[float]:
    calls, puts = chain["calls"], chain["puts"]
    strikes = sorted(set([c["strike"] for c in calls] + [p["strike"] for p in puts]))
    if not strikes: return None
    call_oi = {c["strike"]: c["oi"] for c in calls}
    put_oi  = {p["strike"]: p["oi"] for p in puts}
    total_pain = {}
    for K in strikes:
        pain = 0
        for S in strikes:
            if S < K and S in call_oi:
                pain += call_oi[S] * (K - S)
            if S > K and S in put_oi:
                pain += put_oi[S] * (S - K)
        total_pain[K] = pain
    return min(total_pain, key=total_pain.get) if total_pain else None

def calculate_gex(chain: Dict, spot: float) -> Dict:
    results = {}
    for opt_type in ("calls", "puts"):
        for opt in chain[opt_type]:
            strike = opt["strike"]; oi = opt["oi"]; gamma = opt["gamma"]
            if spot <= 0: continue
            dist_pct = abs(strike - spot) / spot * 100
            if   dist_pct <= 2.5:  w = 1.00
            elif dist_pct <= 5.0:  w = 0.90
            elif dist_pct <= 10:   w = 0.65
            elif dist_pct <= 15:   w = 0.35
            elif dist_pct <= 20:   w = 0.18
            else:                  w = 0.07
            raw = oi * gamma * 100 * spot * spot
            g = +raw * w if opt_type == "calls" else -raw * w
            results[strike] = results.get(strike, 0) + g
    strikes = sorted(results.keys())
    flip = None
    for i in range(len(strikes)-1):
        g1 = results[strikes[i]]; g2 = results[strikes[i+1]]
        if g1 * g2 < 0:
            flip = (strikes[i]*abs(g2) + strikes[i+1]*abs(g1)) / (abs(g1)+abs(g2))
            break
    return {"gex_by_strike": results, "flip_zone": flip}


# ─────────────────────────────────────────────────────────────────────────────
# 렌더링
# ─────────────────────────────────────────────────────────────────────────────
def _badge(text, kind="info"):
    bg = {"pos":COLOR["alert_green"], "neg":COLOR["alert_red"], "warn":COLOR["alert_amber"], "info":COLOR["alert_blue"]}[kind]
    fg = {"pos":COLOR["bull"],        "neg":COLOR["bear"],       "warn":COLOR["warn"],         "info":COLOR["info"]}[kind]
    return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;">{html.escape(str(text))}</span>'

def _section_header(n, name, badges: List[Tuple[str, str]] = None):
    b = "".join(_badge(t, k) for t, k in (badges or []))
    return f"""
<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:0.5px solid {COLOR['warn']};margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:{COLOR['warn']};font-weight:700;font-size:14px;">▶ SECTION {n} · {html.escape(name)}</span>
  <div style="display:flex;gap:6px;flex-wrap:wrap;">{b}</div>
</div>
"""

def _sig_color(sig):
    return {"BULLISH":COLOR["bull"], "BEARISH":COLOR["bear"], "NEUTRAL":COLOR["warn"]}.get(sig, COLOR["muted"])

def _table(headers: List[str], rows: List[List[str]], highlight_rows: Dict[int,str] = None) -> str:
    highlight_rows = highlight_rows or {}
    th = "".join(f'<th style="background:{COLOR["bg_panel"]};color:{COLOR["muted"]};padding:4px 6px;border:0.5px solid {COLOR["border"]};text-align:left;">{html.escape(h)}</th>' for h in headers)
    body = []
    for i, row in enumerate(rows):
        bg = highlight_rows.get(i) or (COLOR["bg_outer"] if i % 2 == 0 else COLOR["bg_card"])
        tds = "".join(f'<td style="padding:4px 6px;border:0.5px solid {COLOR["border"]};color:{COLOR["text"]};">{c}</td>' for c in row)
        body.append(f'<tr style="background:{bg};">{tds}</tr>')
    return f'<table style="border-collapse:collapse;width:100%;font-family:{FONT};font-size:12px;"><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def render_html(a: Dict, analyzer: SmartMoneyAnalyzer) -> str:
    meta = a["meta"]; l1, l2, l3, l4 = a["l1"], a["l2"], a["l3"], a["l4"]
    scenarios = a["scenarios"]; phase = a["phase"]; patterns = a["patterns"]
    price = meta.get("price") or 0
    raw   = a["raw_data"]

    # Section 0
    s0 = _section_header(0, "News · Events · Filings · Macro")
    macro = raw.get("macro", {}) or {}
    events = (raw.get("news", {}) or {}).get("events", [])
    newsitems = (raw.get("news", {}) or {}).get("news", [])
    filings = (raw.get("news", {}) or {}).get("filings", [])

    ev_rows = [[e.get("date","-"), html.escape(str(e.get("event","-"))), e.get("significance","MED")] for e in events] or [["-","(이벤트 없음)","-"]]
    news_rows = [[n.get("date","-"), html.escape(str(n.get("title","-"))[:80]), html.escape(str(n.get("publisher","-")))] for n in newsitems[:5]] or [["-","(최근 뉴스 없음)","-"]]
    fil_rows  = [[f.get("date","-")[:10], html.escape(str(f.get("title","-"))[:80])] for f in filings[:3]] or [["-","(최근 filings 없음)"]]

    ff = macro.get("fedfunds"); ffd = macro.get("fedfunds_trend")
    dxy_chg = macro.get("dxy_change_pct")
    liq = analyzer.classify_macro(macro)
    macro_rows = [
        ["Fed Funds", f"{ff[1]:.2f}%" if ff else "N/A", f"Δ {ffd:+.2f}pp" if ffd is not None else "-"],
        ["DXY 20d",   (f"{macro['dxy'][1]:.2f}" if macro.get("dxy") else "N/A"), fmt_pct(dxy_chg) if dxy_chg is not None else "-"],
        ["RRP",       f"{macro['rrp'][1]:,.0f}B" if macro.get("rrp") else "N/A", "-"],
        ["Oil (WTI)", (f"{macro['oil'][1]:.2f}" if macro.get("oil") else "N/A"), "-"],
        ["Liquidity", liq, "-"],
    ]
    s0_body = f"""
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;">
  <div style="background:{COLOR['bg_card']};border:0.5px solid {COLOR['border']};border-radius:6px;padding:12px;">
    <div style="font-weight:600;font-size:12px;color:{COLOR['muted']};margin-bottom:6px;">Upcoming Events (30d)</div>
    {_table(["Date","Event","Sig"], ev_rows)}
  </div>
  <div style="background:{COLOR['bg_card']};border:0.5px solid {COLOR['border']};border-radius:6px;padding:12px;">
    <div style="font-weight:600;font-size:12px;color:{COLOR['muted']};margin-bottom:6px;">Recent News (7d)</div>
    {_table(["Date","Headline","Publisher"], news_rows)}
  </div>
  <div style="background:{COLOR['bg_card']};border:0.5px solid {COLOR['border']};border-radius:6px;padding:12px;">
    <div style="font-weight:600;font-size:12px;color:{COLOR['muted']};margin-bottom:6px;">SEC Filings</div>
    {_table(["Date","Form / Title"], fil_rows)}
  </div>
  <div style="background:{COLOR['bg_card']};border:0.5px solid {COLOR['border']};border-radius:6px;padding:12px;">
    <div style="font-weight:600;font-size:12px;color:{COLOR['muted']};margin-bottom:6px;">Macro Environment</div>
    {_table(["Indicator","Value","Δ"], macro_rows)}
  </div>
</div>
"""

    # Section 1 (L1)
    l1_badges = [(l1.get("scenario","NEUTRAL"), "pos" if l1.get("signal")=="BULLISH" else "neg" if l1.get("signal")=="BEARISH" else "warn"),
                 (f"Conf {l1.get('confidence','LOW')}", "info")]
    obv = l1.get("obv", {}) or {}
    sessions = l1.get("sessions", [])
    session_rows = [[s["date"],
                     fmt_num(s.get("dp_volume"), "", 0),
                     fmt_num(s.get("consolidated_volume"), "", 0),
                     f"{s.get('dp_pct'):.1f}%" if s.get("dp_pct") is not None else "N/A"]
                    for s in sessions[-10:]] or [["-","-","-","-"]]
    obv_chart_data = {
        "labels": ["Institutional","Professional","Retail","Total"],
        "values": [obv.get("delta_institutional",0) or 0,
                   obv.get("delta_professional",0) or 0,
                   obv.get("delta_retail",0) or 0,
                   obv.get("delta_total",0) or 0],
    }
    s1 = _section_header(1, "Dark Pool Layer (L1)", l1_badges) + f"""
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
  <div>
    <div style="font-weight:600;font-size:12px;color:{COLOR['muted']};margin-bottom:6px;">Session Volume (10d, FINRA ADF proxy)</div>
    {_table(["Date","DP Vol","Total Vol","DP %"], session_rows)}
    <p style="font-size:11px;color:{COLOR['muted']};margin-top:6px;">
      {html.escape(raw.get('l1',{}).get('_note','')) if raw.get('l1') else ''}
    </p>
  </div>
  <div>
    <div style="font-weight:600;font-size:12px;color:{COLOR['muted']};margin-bottom:6px;">OBV 4-Way Decomposition (Δ)</div>
    <canvas id="obv4way" height="220"></canvas>
    <div style="font-size:11px;color:{COLOR['text']};margin-top:6px;">
      IAR (Institutional Absorption Ratio): <b>{fmt_num(obv.get('iar'),'',2)}</b> · Divergence: <b>{obv.get('divergence','N/A')}</b>
      {("<br>Sessions · " + " · ".join(f"{k}: {fmt_num(v,'',0)}" for k,v in (obv.get('session_volume') or {}).items())) if obv.get('session_volume') else ""}
      {"<br><span style='color:"+COLOR['info']+";'>Source: Polygon 1-min bars ("+str(obv.get('minute_bar_count','?'))+")</span>" if obv.get('_source')=='polygon_1min' else ""}
    </div>
  </div>
</div>
"""

    # Section 2 (L2)
    l2_badges = [(l2.get("scenario","NEUTRAL"), "pos" if l2.get("signal")=="BULLISH" else "neg" if l2.get("signal")=="BEARISH" else "warn"),
                 (f"Case {l2.get('case','N/A').replace('CASE_','')}" , "info")]
    hist = l2.get("history", [])
    short_rows = [[h["date"], fmt_num(h.get("short_vol"),"",0), fmt_num(h.get("total_vol"),"",0),
                   f"{h.get('short_pct'):.1f}%" if h.get("short_pct") is not None else "N/A"]
                  for h in hist[-14:]] or [["-","-","-","-"]]
    ctb_fee = l2.get("ctb_fee")
    ctb_delta = l2.get("ctb_delta_pct")
    ctb_label = "ETB" if (ctb_fee or 0) < 1 else ("HTB" if (ctb_fee or 0) > 15 else "Moderate")
    s2 = _section_header(2, "Short Volume Layer (L2)", l2_badges) + f"""
<div style="display:grid;grid-template-columns:2fr 1fr;gap:12px;">
  <div>
    <div style="font-weight:600;font-size:12px;color:{COLOR['muted']};margin-bottom:6px;">Short % (FINRA consolidated, 14d)</div>
    {_table(["Date","Short Vol","Total Vol","Short %"], short_rows)}
    <div style="font-size:11px;color:{COLOR['text']};margin-top:6px;">
      14d Avg Short%: <b>{l2.get('avg_14d',0):.1f}%</b> · Slope: <b>{l2.get('slope',0):+.3f}</b>
    </div>
  </div>
  <div>
    <div style="font-weight:600;font-size:12px;color:{COLOR['muted']};margin-bottom:6px;">CTB / Borrow (iborrowdesk)</div>
    {_table(["Metric","Value","Δ %"], [
        ["CTB Fee", f"{ctb_fee:.2f}%" if ctb_fee is not None else "N/A", fmt_pct(ctb_delta) if ctb_delta is not None else "-"],
        ["Class", ctb_label, "-"],
        ["Shares Available", fmt_num(l2.get('shares_available'),'',0), "-"],
    ])}
  </div>
</div>
"""

    # Section 3 (L3)
    l3_badges = [(l3.get("scenario","NEUTRAL"), "pos" if l3.get("signal")=="BULLISH" else "neg" if l3.get("signal")=="BEARISH" else "warn"),
                 (f"DTE {l3.get('dte','-')}", "info")]
    gex_by_strike = l3.get("gex_by_strike", {}) or {}
    gex_labels = sorted(gex_by_strike.keys())
    gex_vals   = [gex_by_strike[k] for k in gex_labels]
    net_gex = l3.get("net_gex", 0)
    flip = l3.get("flip_zone")
    max_pain = l3.get("max_pain")

    l3_rows = [
        ["Expiry", l3.get("expiry","-")],
        ["Spot", f"${l3.get('spot',0):.2f}"],
        ["Max Pain", f"${max_pain:.2f}" if max_pain else "N/A"],
        ["Max Pain Dist", fmt_pct(l3.get("max_pain_dist_pct")) if l3.get("max_pain_dist_pct") is not None else "N/A"],
        ["P/C OI", f"{l3.get('pc_oi'):.2f}" if l3.get("pc_oi") is not None else "N/A"],
        ["P/C Vol", f"{l3.get('pc_vol'):.2f}" if l3.get("pc_vol") is not None else "N/A"],
        ["IV Skew (put−call)", f"{l3.get('skew')*100:+.2f}pp" if l3.get("skew") is not None else "N/A"],
        ["Net GEX", fmt_num(net_gex, "", 0)],
        ["GEX Flip Zone", f"${flip:.2f}" if flip else "N/A"],
    ]
    s3 = _section_header(3, "Options Layer (L3)", l3_badges) + f"""
<div style="display:grid;grid-template-columns:1fr 1.5fr;gap:12px;">
  <div>{_table(["Metric","Value"], l3_rows)}</div>
  <div>
    <div style="font-weight:600;font-size:12px;color:{COLOR['muted']};margin-bottom:6px;">GEX by Strike</div>
    <canvas id="gexChart" height="260"></canvas>
  </div>
</div>
"""

    # Section 4 (L4)
    l4_badges = [(l4.get("scenario","NEUTRAL"), "pos" if l4.get("signal")=="BULLISH" else "neg" if l4.get("signal")=="BEARISH" else "warn"),
                 (l4.get("ma_alignment","N/A"), "info")]
    l4_rows = [
        ["Current",   f"${l4.get('current_price',0):.2f}"],
        ["SMA 20 / 50 / 200", f"${l4.get('sma20',0):.2f} / " + (f"${l4.get('sma50'):.2f}" if l4.get('sma50') else "-") + " / " + (f"${l4.get('sma200'):.2f}" if l4.get('sma200') else "-")],
        ["BB Upper / Lower", f"${l4.get('bb_upper',0):.2f} / ${l4.get('bb_lower',0):.2f}"],
        ["BB Width %", f"{l4.get('bb_width_pct',0):.2f}%"],
        ["BB Position", f"{l4.get('bb_position',0):.2f}"],
        ["Immediate R / S", f"{('$'+format(l4.get('immediate_resistance'), '.2f')) if l4.get('immediate_resistance') else '-'}  /  {('$'+format(l4.get('immediate_support'), '.2f')) if l4.get('immediate_support') else '-'}"],
        ["Key R / S", f"{('$'+format(l4.get('key_resistance'), '.2f')) if l4.get('key_resistance') else '-'}  /  {('$'+format(l4.get('key_support'), '.2f')) if l4.get('key_support') else '-'}"],
    ]
    s4 = _section_header(4, "Chart / Technical Layer (L4)", l4_badges) + _table(["Metric","Value"], l4_rows)

    # Section 5 (Integrated)
    s5_badges = [(f"Score {scenarios['raw_score']:+.2f}", "info"),
                 (f"Macro {scenarios['macro']}",
                  "pos" if scenarios['macro']=="FAVORABLE" else "neg" if scenarios['macro']=="RESTRICTED" else "warn")]
    phase_rows = [
        ["Phase 1 — Setup (완료)",     phase["p1_date"], "축적/분배 완료 구간", "COMPLETE"],
        ["Phase 2 — Transition (현재)", phase["p2_date"], f"{l1.get('scenario','N/A')} × {l3.get('scenario','N/A')}", "IN PROGRESS"],
        ["Phase 3 — Resolution (미래)", phase["p3_date"], "촉발 신호 시 추세 전개", "PENDING"],
    ]

    imm_res = l4.get("immediate_resistance"); key_res = l4.get("key_resistance")
    imm_sup = l4.get("immediate_support"); key_sup = l4.get("key_support")
    def dist(p):
        if p is None or not price: return "-"
        return f"{(p-price)/price*100:+.1f}%"
    price_rows = [
        ["Current Price", f"${price:.2f}", "0%", "—"],
        ["Max Pain",      f"${max_pain:.2f}" if max_pain else "N/A", dist(max_pain), "HIGH" if l3.get("dte",99) <= 5 else "MED"],
        ["GEX Flip",      f"${flip:.2f}" if flip else "N/A",         dist(flip),     "HIGH" if flip else "LOW"],
        ["Imm Resistance",f"${imm_res:.2f}" if imm_res else "-",     dist(imm_res),  "HIGH"],
        ["Key Resistance",f"${key_res:.2f}" if key_res else "-",     dist(key_res),  "MED"],
        ["Imm Support",   f"${imm_sup:.2f}" if imm_sup else "-",     dist(imm_sup),  "HIGH"],
        ["Key Support",   f"${key_sup:.2f}" if key_sup else "-",     dist(key_sup),  "MED"],
    ]
    s5 = _section_header(5, "3-Layer Integrated Scenario", s5_badges) + f"""
<div style="font-weight:600;font-size:12px;color:{COLOR['muted']};margin:8px 0 4px;">Architect Phase Table</div>
{_table(["Phase","Dates","Description","Status"], phase_rows)}
<div style="font-weight:600;font-size:12px;color:{COLOR['muted']};margin:12px 0 4px;">Probability Bar</div>
<canvas id="probChart" height="120"></canvas>
<div style="font-size:11px;color:{COLOR['muted']};margin-top:6px;">
  Raw Score: <b>{scenarios['raw_score']:+.2f}</b> | Macro: <b>{scenarios['macro']}</b> | Patterns: <b>{', '.join(patterns) if patterns else 'none'}</b>
</div>
<div style="font-weight:600;font-size:12px;color:{COLOR['muted']};margin:12px 0 4px;">Key Price Level Map</div>
{_table(["Level Type","Price","Distance","Significance"], price_rows)}
"""

    # Section 6 — Summary
    s6_badges = [("3-Line Summary","info")]
    l1_summary = _one_liner_l1(l1, raw.get("l1", {}) or {})
    l2_summary = _one_liner_l2(l2)
    l3_summary = _one_liner_l3(l3)
    triggers = _triggers(l1, l2, l3, l4, price, max_pain, flip)
    risks = _risks(l1, l2, l3, scenarios, patterns)
    s6 = _section_header(6, "Summary & Action Points", s6_badges) + f"""
<div class="summary-row" style="font-size:11px;line-height:1.7;color:{COLOR['text']};margin-bottom:10px;">
  <div><b style="color:{COLOR['bear']};">①</b> [DARK POOL] {l1_summary}</div>
  <div><b style="color:{COLOR['bear']};">②</b> [SHORT/CTB] {l2_summary}</div>
  <div><b style="color:{COLOR['bear']};">③</b> [OPTIONS] {l3_summary}</div>
</div>
<div style="font-weight:600;font-size:12px;color:{COLOR['muted']};margin:8px 0 4px;">Trigger Checklist</div>
{_table(["Trigger","Type","Status","Implication"], triggers)}
<div style="font-weight:600;font-size:12px;color:{COLOR['muted']};margin:12px 0 4px;">Risk Factors</div>
<ul style="font-size:12px;color:{COLOR['text']};margin-left:18px;">
  {''.join(f'<li>{html.escape(r)}</li>' for r in risks)}
</ul>
"""

    # Section 7 — Core conclusions (bold, bottom)
    s7 = _section_header(7, "Core Conclusions") + _core_conclusions(a)

    warn_html = ""
    if meta.get("warnings"):
        lis = "".join(f"<li>{html.escape(w)}</li>" for w in meta["warnings"])
        warn_html = f"""
<div style="background:{COLOR['alert_amber']};border:0.5px solid {COLOR['warn']};padding:8px 12px;border-radius:6px;margin-bottom:16px;font-size:11px;color:{COLOR['warn']};">
  <b>⚠ 수집 경고 (PARTIAL 섹션 포함):</b><ul style="margin:4px 0 0 16px;">{lis}</ul>
</div>"""

    # MD 아카이브 (숨김)
    md_archive = render_markdown(a, analyzer)
    md_block = f'<div id="md-archive" style="display:none;"><pre id="md-archive-text">{html.escape(md_archive)}</pre></div>'

    # Chart.js init
    chart_js = _chart_js(obv_chart_data, gex_labels, gex_vals, flip, price, max_pain, scenarios)

    title = f"{meta['ticker']} Smart Money Forensic · {meta['date']}"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body {{ background:{COLOR['bg_outer']}; color:{COLOR['text']}; font-family:{FONT}; font-size:13px; margin:0; padding:24px; }}
  .wrap {{ max-width:1200px; margin:0 auto; }}
  .hdr  {{ border-bottom:1px solid {COLOR['border']}; padding-bottom:12px; margin-bottom:16px; }}
  .hdr h1 {{ font-size:20px; margin:0 0 4px 0; }}
  .hdr .sub {{ font-size:12px; color:{COLOR['muted']}; }}
  @media print {{ body{{padding:12px;font-size:11px}} canvas{{max-height:220px}} }}
</style>
</head>
<body>
<div class="wrap">
<div class="hdr">
  <h1>Smart Money Analyzer — {html.escape(meta['ticker'])}</h1>
  <div class="sub">Analysis Date: {meta['date']} · Current Price: ${price:.2f} · Expiry: {l3.get('expiry','N/A')} · Patterns: {', '.join(patterns) if patterns else 'none'}</div>
</div>
{warn_html}
{s0}{s0_body}
{s1}
{s2}
{s3}
{s4}
{s5}
{s6}
{s7}
<div style="margin-top:24px;font-size:10px;color:{COLOR['muted']};text-align:center;">
  Generated by sma.py · Data: Yahoo Finance, FINRA, FRED, SEC EDGAR, iborrowdesk (free tier only)
</div>
</div>
{md_block}
<script>{chart_js}</script>
</body>
</html>
"""


def _one_liner_l1(l1, raw_l1):
    dp = l1.get("dp_pct"); obv = l1.get("obv", {})
    iar = obv.get("iar"); div = obv.get("divergence","N/A")
    if l1.get("_partial"):
        return "FINRA ATS 파싱 불완전. 기관 흐름 판정 보류."
    return (f"다크풀 비중 {dp:.1f}% · IAR {iar:.2f} · {div} — " if dp is not None and iar is not None
            else f"다크풀/OBV 부분 데이터. {div}. ") + f"시나리오: {l1.get('scenario','NEUTRAL')}."

def _one_liner_l2(l2):
    if l2.get("_partial"):
        return "FINRA 공매도 파일에서 종목 매치 실패 — 부분 데이터."
    fee = l2.get("ctb_fee")
    return (f"Short% {l2.get('latest_short_pct',0):.1f} (14d avg {l2.get('avg_14d',0):.1f}), "
            f"CTB {fee:.2f}%  " if fee is not None else f"Short% {l2.get('latest_short_pct',0):.1f} (14d avg {l2.get('avg_14d',0):.1f}). ") + \
           f"{l2.get('case','N/A')} → {l2.get('scenario','NEUTRAL')}."

def _one_liner_l3(l3):
    if l3.get("_partial"):
        return "옵션 체인 부재."
    mp = l3.get("max_pain"); ng = l3.get("net_gex", 0); flip = l3.get("flip_zone")
    return (f"Max Pain ${mp:.2f} ({l3.get('max_pain_dist_pct',0):+.1f}%), "
            f"Net GEX {fmt_num(ng,'',0)}, Flip "
            + (f"${flip:.2f}. " if flip else "N/A. ")
            + f"시나리오: {l3.get('scenario','NEUTRAL')}.")

def _triggers(l1, l2, l3, l4, price, max_pain, flip):
    rows = []
    def met(cond): return "MET" if cond else "NOT MET"
    rows.append(["Volume spike > 150%", "BULLISH", "WATCHING", "Breakout start"])
    if l2.get("ctb_fee") is not None:
        rows.append([f"CTB > 5%", "BULLISH", met(l2["ctb_fee"] > 5), "Squeeze setup"])
    if flip and price:
        rows.append(["Price > GEX Flip", "BULLISH", met(price > flip), "Vol dampened"])
    if max_pain and price:
        rows.append(["Price < Max Pain", "BEARISH", met(price < max_pain), "Downward gravity"])
    if l4.get("sma50"):
        rows.append(["Close > SMA50", "BULLISH", met(l4["current_price"] > l4["sma50"]), "Trend support"])
    if (l1.get("obv") or {}).get("delta_institutional") is not None:
        rows.append(["Inst OBV > 0", "BULLISH",
                     met(l1["obv"]["delta_institutional"] > 0),
                     "Institutional accumulation"])
    return rows

def _risks(l1, l2, l3, scenarios, patterns):
    out = []
    if "LOW_CTB_PARADOX" in patterns:
        out.append("[LOW_CTB_PARADOX]: CTB 저렴한데 가용잔고 축소 — 조용한 공매도 축적 가능성. 기술적 진입 금지.")
    if "THETA_BURN" in patterns:
        out.append("[THETA_BURN]: 3+일 저변동성 L자 눌림. 돌파 시 방향이 비대칭적으로 증폭.")
    if l1.get("_partial"):
        out.append("[DATA_GAP]: L1 (다크풀) 데이터 부분 수집 — 결론의 신뢰도 1단계 하향.")
    if l2.get("_partial"):
        out.append("[DATA_GAP]: L2 (CTB) 데이터 부분 수집.")
    if scenarios["macro"] == "RESTRICTED":
        out.append("[MACRO_HEADWIND]: 매크로 긴축 — 상승 시나리오 확률을 모델 대비 5-10%p 할인.")
    if not out:
        out = ["[CATALYST_RISK]: 미공개 공시 발생 시 가정 붕괴 가능. Section 0 확인 필수.",
               "[LIQUIDITY_SHOCK]: 시장 전체 유동성 급변 시 구조적 포지션 재조정."]
    return out

def _core_conclusions(a) -> str:
    l1, l2, l3, l4 = a["l1"], a["l2"], a["l3"], a["l4"]
    scen = a["scenarios"]; patterns = a["patterns"]; meta = a["meta"]
    price = meta.get("price") or 0

    # Paragraph 1
    top = max([("A", scen["A_bullish"]), ("B", scen["B_neutral"]), ("C", scen["C_bearish"])], key=lambda x: x[1])
    p1 = (
        f"<b>핵심 단일 발견.</b> {meta['ticker']}의 4개 레이어 가중합 점수는 {scen['raw_score']:+.2f} · "
        f"시나리오 확률은 [A] {scen['A_bullish']}% / [B] {scen['B_neutral']}% / [C] {scen['C_bearish']}% 로 수렴하며, "
        f"최우선은 <b>시나리오 [{top[0]}] ({top[1]}%)</b>이다. "
        f"L1은 {l1.get('scenario','N/A')} ({l1.get('signal','NEUTRAL')}), L2는 {l2.get('scenario','N/A')} "
        f"({l2.get('case','N/A')}), L3은 {l3.get('scenario','N/A')} (Max Pain ${l3.get('max_pain') or 0:.2f}, "
        f"Net GEX {fmt_num(l3.get('net_gex') or 0, '', 0)})의 구조적 배치를 보여준다. "
        f"패턴 탐지 결과: {', '.join(patterns) if patterns else '정형 패턴 없음'}. "
        f"이 조합에서 Architect의 의도는 {'축적(accumulation)' if top[0]=='A' else '분배(distribution)' if top[0]=='C' else '소강/타이밍 조정(pinning/theta)'}의 "
        f"단계에 가깝다고 해석된다."
    )

    # Paragraph 2
    p2 = (
        f"<b>구조적 메커니즘.</b> 다크풀 기관 Δ는 {fmt_num((l1.get('obv') or {}).get('delta_institutional') or 0, '', 0)}, "
        f"IAR {fmt_num((l1.get('obv') or {}).get('iar'), '', 2)}, Divergence {(l1.get('obv') or {}).get('divergence','N/A')}로 기록되며, "
        f"L2에서 Short% 기울기 {l2.get('slope',0):+.3f} · CTB "
        + (f"{l2['ctb_fee']:.2f}%" if l2.get('ctb_fee') is not None else "N/A")
        + f" 조합은 L3 GEX 플립 {('$'+format(l3.get('flip_zone'),'.2f')) if l3.get('flip_zone') else 'N/A'} 및 "
        f"Max Pain 거리 {l3.get('max_pain_dist_pct') or 0:+.1f}%와 " +
        ("정합적으로" if (l1.get("signal")==l3.get("signal")) else "부분 상충적으로") +
        " 맞물린다. 매크로는 <b>" + scen['macro'] + "</b>로 분류되어 확률을 " +
        ("+" if scen['macro']=="FAVORABLE" else ("−" if scen['macro']=="RESTRICTED" else "0")) +
        "5~10%p 조정했다. 해석이 틀릴 위험은 CTB와 기관 OBV의 동시 역전, 또는 다가오는 이벤트(어닝/FOMC)의 가이던스 변질이다."
    )

    # Paragraph 3
    p3_triggers = []
    if l3.get("flip_zone"): p3_triggers.append(f"가격이 GEX 플립(${l3['flip_zone']:.2f})을 돌파")
    if l2.get("ctb_fee") is not None: p3_triggers.append(f"CTB가 {l2['ctb_fee']:.1f}%에서 +3pp 이상 급등")
    if l1.get("dp_pct") is not None: p3_triggers.append(f"다크풀 비중이 {l1['dp_pct']:.0f}%에서 2거래일 연속 45% 이상 유지")
    p3 = (
        f"<b>결정적 트리거.</b> 본 가설을 확정하는 단일 신호는 다음 중 어느 것이든 먼저 발동될 때다: "
        + ("; ".join(p3_triggers) if p3_triggers else "거래량 +150% 스파이크 + BB 확장")
        + f". 반대로 <b>" + ("기관 OBV가 음전환(누적 5영업일 합산 < 0)" if top[0]=="A" else
                            "기관 OBV가 양전환하면서 CTB가 급락" if top[0]=="C" else
                            "L1·L2·L3 중 2개 레이어 신호가 동시에 반대 방향으로 고착") +
        "</b>되면 본 분석의 방향성 가정은 재설정되어야 한다."
    )

    return f"""
<div style="font-size:13px;line-height:1.8;font-weight:500;color:{COLOR['text']};">
  <p style="margin-bottom:14px;">{p1}</p>
  <p style="margin-bottom:14px;">{p2}</p>
  <p>{p3}</p>
</div>"""


def _chart_js(obv_data, gex_labels, gex_vals, flip, spot, max_pain, scenarios) -> str:
    obv_colors = [COLOR["bull"], COLOR["warn"], COLOR["bear"], "#A32D2D"]
    gex_bar_colors = [COLOR["bull"] if v >= 0 else COLOR["bear"] for v in gex_vals]
    annotations = []
    if flip:
        annotations.append(f"{{type:'line',scaleID:'x',value:{flip},borderColor:'{COLOR['warn']}',borderWidth:1.5,label:{{display:true,content:'Flip ${flip:.2f}',position:'start'}}}}")
    if spot:
        annotations.append(f"{{type:'line',scaleID:'x',value:{spot},borderColor:'{COLOR['info']}',borderWidth:1.5,borderDash:[6,4],label:{{display:true,content:'Spot ${spot:.2f}'}}}}")
    if max_pain:
        annotations.append(f"{{type:'line',scaleID:'x',value:{max_pain},borderColor:'{COLOR['warn']}',borderWidth:1.5,borderDash:[2,3],label:{{display:true,content:'Max Pain ${max_pain:.2f}'}}}}")

    return f"""
Chart.defaults.font.family = {json.dumps(FONT)};
Chart.defaults.color = "{COLOR['text']}";

new Chart(document.getElementById('obv4way'), {{
  type:'bar',
  data:{{
    labels:{json.dumps(obv_data['labels'])},
    datasets:[{{
      label:'Δ OBV',
      data:{json.dumps(obv_data['values'])},
      backgroundColor:{json.dumps(obv_colors)},
      borderWidth:0
    }}]
  }},
  options:{{
    indexAxis:'y',
    plugins:{{legend:{{display:false}},title:{{display:true,text:'OBV 4-Way Decomposition (Δ from prior session)'}}}},
    scales:{{x:{{grid:{{color:'{COLOR['chart_grid']}'}}}},y:{{grid:{{display:false}}}}}}
  }}
}});

new Chart(document.getElementById('gexChart'), {{
  type:'bar',
  data:{{
    labels:{json.dumps([f'{s:.0f}' for s in gex_labels])},
    datasets:[{{
      label:'GEX',
      data:{json.dumps(gex_vals)},
      backgroundColor:{json.dumps(gex_bar_colors)},
      borderWidth:0
    }}]
  }},
  options:{{
    plugins:{{legend:{{display:false}},title:{{display:true,text:'Gamma Exposure (GEX) by Strike'}}}},
    scales:{{y:{{grid:{{color:'{COLOR['chart_grid']}'}}}},x:{{grid:{{display:false}}}}}}
  }}
}});

new Chart(document.getElementById('probChart'), {{
  type:'bar',
  data:{{
    labels:['[A] Bullish','[B] Neutral','[C] Bearish'],
    datasets:[{{
      label:'Probability %',
      data:[{scenarios['A_bullish']},{scenarios['B_neutral']},{scenarios['C_bearish']}],
      backgroundColor:['{COLOR['bull']}','{COLOR['warn']}','{COLOR['bear']}'],
      borderWidth:0
    }}]
  }},
  options:{{
    indexAxis:'y',
    plugins:{{legend:{{display:false}},title:{{display:false}}}},
    scales:{{x:{{max:80,grid:{{color:'{COLOR['chart_grid']}'}}}},y:{{grid:{{display:false}}}}}}
  }}
}});
"""


# ─── MD 렌더 ─────────────────────────────────────────────────────────────────
def render_markdown(a: Dict, analyzer: SmartMoneyAnalyzer) -> str:
    m = a["meta"]; l1,l2,l3,l4 = a["l1"],a["l2"],a["l3"],a["l4"]
    scen = a["scenarios"]; phase = a["phase"]
    obv = l1.get("obv",{}) or {}
    price = m.get("price") or 0

    def opt_pct(v, dec=1):
        return f"{v:.{dec}f}%" if v is not None else "N/A"
    def opt_dol(v, dec=2):
        return f"${v:.{dec}f}" if v is not None else "N/A"
    dp_pct_s = opt_pct(l1.get("dp_pct"))
    short_pct_s = opt_pct(l2.get("latest_short_pct"))
    ctb_s = opt_pct(l2.get("ctb_fee"), 2)
    flip_s = opt_dol(l3.get("flip_zone"))
    pain_s = opt_dol(l3.get("max_pain"))
    imm_r = l4.get("immediate_resistance") or 0
    imm_s = l4.get("immediate_support") or 0
    patterns_s = ", ".join(a["patterns"]) if a["patterns"] else "none"

    lines = [
        "---",
        f"ticker: {m['ticker']}",
        f"date: {m['date']}",
        f"generated_by: Smart Money Analyzer (sma.py)",
        "---",
        "",
        f"# Smart Money Analysis: {m['ticker']}",
        f"**Analysis Date**: {m['date']}  ",
        f"**Current Price**: ${price:.2f}  ",
        f"**Options Expiry**: {l3.get('expiry','N/A')}  ",
        f"**Analyst**: Smart Money Analyzer v2.0",
        "",
        "---",
        "",
        "## Key Metrics Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Institutional OBV Δ | {fmt_num(obv.get('delta_institutional'),'',0)} |",
        f"| Professional OBV Δ | {fmt_num(obv.get('delta_professional'),'',0)} |",
        f"| Retail OBV Δ       | {fmt_num(obv.get('delta_retail'),'',0)} |",
        f"| Total OBV Δ        | {fmt_num(obv.get('delta_total'),'',0)} |",
        f"| Dark Pool %        | {dp_pct_s} |",
        f"| IAR                | {fmt_num(obv.get('iar'),'',2)} |",
        f"| Short %            | {short_pct_s} |",
        f"| CTB Fee            | {ctb_s} |",
        f"| Available Shares   | {fmt_num(l2.get('shares_available'),'',0)} |",
        f"| Max Pain           | {pain_s} |",
        f"| GEX Flip Zone      | {flip_s} |",
        f"| Net GEX            | {fmt_num(l3.get('net_gex'),'',0)} |",
        f"| Scenario [A] Bull  | {scen['A_bullish']}% |",
        f"| Scenario [B] Neut  | {scen['B_neutral']}% |",
        f"| Scenario [C] Bear  | {scen['C_bearish']}% |",
        "",
        "---",
        "",
        "## Architect Phase Structure",
        f"- **Phase 1 (Completed)**: {phase['p1_date']} — 주기/분배 이미 전개",
        f"- **Phase 2 (Current)**:   {phase['p2_date']} — {l1.get('scenario')} × {l3.get('scenario')}",
        f"- **Phase 3 (Target)**:    {phase['p3_date']} — 상단 ${imm_r:.2f} / 하단 ${imm_s:.2f}",
        "",
        "---",
        "",
        "## Core Conclusion",
        f"- L1 (Dark Pool): **{l1.get('scenario','N/A')}** · signal={l1.get('signal','NEUTRAL')} · conf={l1.get('confidence','LOW')}",
        f"- L2 (Short/CTB): **{l2.get('scenario','N/A')}** · {l2.get('case','N/A')} · conf={l2.get('confidence','LOW')}",
        f"- L3 (Options):   **{l3.get('scenario','N/A')}** · DTE={l3.get('dte','-')} · net_gex={fmt_num(l3.get('net_gex'),'',0)}",
        f"- L4 (Chart):     **{l4.get('scenario','N/A')}** · MA={l4.get('ma_alignment','N/A')}",
        f"- Macro: **{scen['macro']}** · Raw Score: **{scen['raw_score']:+.2f}** · Patterns: **{patterns_s}**",
        "",
        "---",
        "",
        "## Cumulative History",
        "",
        "| 날짜 | 종가 | 기관OBV | 프로OBV | 리테일OBV | 전체OBV | Short% | CTB | 가용잔고 | GEX플립 | MaxPain |",
        "|------|------|---------|---------|-----------|---------|--------|-----|---------|---------|---------|",
        (f"| {m['date']} | ${price:.2f} | {fmt_num(obv.get('delta_institutional'),'',0)} | "
         f"{fmt_num(obv.get('delta_professional'),'',0)} | {fmt_num(obv.get('delta_retail'),'',0)} | "
         f"{fmt_num(obv.get('delta_total'),'',0)} | {short_pct_s} | {ctb_s} | "
         f"{fmt_num(l2.get('shares_available'),'',0)} | {flip_s} | {pain_s} |"),
        "",
    ]
    if m.get("warnings"):
        lines.append("## Warnings (Partial Data)")
        for w in m["warnings"]:
            lines.append(f"- {w}")
        lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Smart Money Flow Analyzer (sma.py)")
    ap.add_argument("ticker", help="Stock ticker, e.g. NVDA")
    ap.add_argument("--date", default=None, help="Analysis date YYYY-MM-DD (default: today UTC)")
    args = ap.parse_args()

    analyzer = SmartMoneyAnalyzer(args.ticker, analysis_date=args.date)
    t0 = time.time()
    print(f"[sma] Analyzing {analyzer.ticker} @ {analyzer.date_str}...", file=sys.stderr)
    data = analyzer.fetch_all_data()
    analysis = analyzer.run_analysis(data)
    html_path, md_path = analyzer.generate_outputs(analysis)
    dt = time.time() - t0
    print(f"[sma] done ({dt:.1f}s)", file=sys.stderr)
    print(f"HTML: {html_path}")
    print(f"MD:   {md_path}")

if __name__ == "__main__":
    main()
