"""SmartMoneyAnalyzer v3 — 수집 → 분석 → 품질평가 → 출력."""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import analytics as A
from . import calendar as cal
from . import events as EV
from . import flow as F
from . import history as H
from . import quality as Q
from . import sources as S
from .net import log

INDEX_ROOTS = {"SPX": {"SPX", "SPXW"}, "NDX": {"NDX", "NDXP"}, "RUT": {"RUT", "RUTW"}, "VIX": {"VIX", "VIXW"}}


class SmartMoneyAnalyzer:
    def __init__(self, ticker: str, session_date: Optional[str] = None):
        self.ticker = ticker.upper().strip()
        self.is_index = self.ticker.startswith("^")
        base = self.ticker.lstrip("^")
        self.display_ticker = base
        self.cboe_symbol = ("_" + base) if self.is_index else self.ticker
        self.option_roots = INDEX_ROOTS.get(base, {base}) if self.is_index else {base.replace(".", "").replace("-", "")}
        sd = session_date or os.environ.get("SMA_SESSION_DATE")
        self.session = cal.parse_date(sd) if sd else cal.last_completed_session()
        if not cal.is_trading_day(self.session):
            self.session = cal.prev_trading_day(self.session)
        self.date_str = self.session.isoformat()
        self.warnings: List[str] = []
        self.sources: Dict[str, List[str]] = {}
        self.timings: Dict[str, float] = {}

    # ── 수집 ──────────────────────────────────────────────────────────────
    def _try(self, layer: str, label: str, fn: Callable, *args, default=None):
        t0 = time.time()
        try:
            v = fn(*args)
            self.timings[label] = round(time.time() - t0, 2)
            return v
        except Exception as e:  # noqa: BLE001
            msg = f"{layer}/{label}: {str(e)[:220]}"
            self.warnings.append(msg)
            log(f"{self.ticker} WARN {msg}")
            return default

    def fetch(self) -> Dict[str, Any]:
        T, d = self.ticker, self.session
        yft = S.yf_ticker(T)
        data: Dict[str, Any] = {}
        data["ohlcv"] = self._try("l4", "yahoo_ohlcv", S.fetch_ohlcv, yft, d, default=[])
        if not data["ohlcv"]:
            return data
        self.sources["l4"] = ["Yahoo Finance (일봉)"]
        last_bar = data["ohlcv"][-1]["date"]
        if last_bar != self.date_str:
            self.warnings.append(f"l4/session_mismatch: 최신 일봉 {last_bar} ≠ 세션 {self.date_str}")

        chain = self._try("l3", "cboe_chain", S.cboe_chain, self.cboe_symbol, self.option_roots)
        if chain:
            self.sources["l3"] = ["CBOE delayed quotes (OI·IV·Greeks)"]
        else:
            chain = self._try("l3", "yahoo_chain", S.yahoo_chain, yft)
            if chain:
                self.sources["l3"] = ["Yahoo option_chain (폴백, Greeks=BS 근사)"]
        data["chain"] = chain or {}
        data["macro_raw"] = self._try("macro", "fred_yahoo", S.fetch_macro, default={})
        self.sources["macro"] = ["FRED (DFF·RRP·SOFR·IORB·WALCL·TGA·HY OAS·10Y)", "Yahoo (DXY·WTI·VIX)"]
        if self.is_index:
            return data

        data["finra"] = self._try("l1", "finra_regsho", S.finra_series, T, d, 20, default=[])
        if data["finra"] and data["finra"][-1]["date"] != self.date_str:
            self.warnings.append(f"l1/finra_lag: FINRA 최신 파일 {data['finra'][-1]['date']} (세션 {self.date_str} 미게시)")
        mb = self._try("l1", "minute_bars", S.fetch_minute_bars, T, yft, d, 5, default=([], ""))
        data["minute"], data["minute_src"] = mb
        data["ats"] = self._try("l1", "finra_ats_weekly", S.finra_ats_weekly, T, d, default=[])
        data["flow"] = self._fetch_flow(T, d)
        self.sources["l1"] = ["FINRA RegSHO CNMS (off-exchange)",
                              f"1분봉 ({data['minute_src'] or '없음'})", "FINRA OTC Transparency (ATS 주간)"] + \
            (["Alpaca SIP 체결 단위 (기관/프로/리테일 · 다크풀 VWAP)"] if data["flow"] else [])
        data["borrow"] = self._try("l2", "borrow", S.fetch_borrow, T)
        data["short_interest"] = self._try("l2", "short_interest", S.fetch_short_interest, T)
        data["float"] = self._try("l2", "float", S.fetch_float, yft, default={}) or {}
        self.sources["l2"] = ["FINRA RegSHO (short volume)",
                              f"대차: {(data['borrow'] or {}).get('source') or '수집 실패'}",
                              f"SI: {(data['short_interest'] or {}).get('source') or '수집 실패'}"]
        data["news"] = self._try("news", "yahoo_news", S.fetch_news, yft, d, default=[])
        data["sec"] = self._try("news", "sec_edgar", S.fetch_sec, T, d, default={"filings": []})
        data["calendar"] = self._try("news", "yahoo_calendar", S.fetch_calendar, yft, default={}) or {}
        self.sources["news"] = ["Yahoo news", "SEC EDGAR (submissions + Form 4 XML)", "Yahoo calendar"]
        return data

    def _fetch_flow(self, T: str, d: date, days: int = 5) -> List[Dict[str, Any]]:
        """체결 단위 수급(최근 5세션). 누락된 날만 Alpaca 에서 받아 state/flow 에 누적."""
        if not F.available():
            return []
        want = [d]
        while len(want) < days:
            want.insert(0, cal.prev_trading_day(want[0]))
        have = {r["date"]: r for r in H.read_state("flow", T)}
        for day in want:
            if day.isoformat() in have:
                continue
            row = self._try("l1", f"alpaca_trades_{day}", F.fetch_day, T, day)
            if row:
                have[day.isoformat()] = row
                H.upsert_state("flow", T, row, keep=60)
        rows = [have[x.isoformat()] for x in want if x.isoformat() in have]
        if rows and rows[-1]["date"] != d.isoformat():
            self.warnings.append(f"l1/flow: 세션 {d} 체결 데이터 미수집 — 체결 분해는 {rows[-1]['date']} 기준")
        return rows

    # ── 분석 ──────────────────────────────────────────────────────────────
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        T, d = self.ticker, self.session
        ohlcv = data["ohlcv"]
        l4 = A.analyze_l4(ohlcv)
        price = l4.get("current_price") or ohlcv[-1]["close"]
        calx = data.get("calendar") or {}
        opt_hist = H.read_state("options", self.display_ticker)
        l3 = A.analyze_l3(data.get("chain") or {}, price, d, opt_hist, calx.get("earnings_date")) \
            if data.get("chain") else {"signal": "NEUTRAL", "score": 0.0, "confidence": 0.0, "scenario": "NO_DATA"}
        macro = A.classify_macro(data.get("macro_raw") or {})
        if macro["failed"]:
            self.warnings.append("macro: (거시 유동성 검색 실패) — 미시 수급만으로 확률 산출")

        if self.is_index:
            l1 = {"scenario": "N/A", "signal": "NEUTRAL", "score": 0.0, "confidence": 0.0}
            l2 = dict(l1)
        else:
            l1 = A.analyze_l1(ohlcv, data.get("finra") or [], data.get("minute") or [],
                              data.get("minute_src") or "", data.get("ats") or [],
                              flow=data.get("flow") or [], flow_hist=H.read_state("flow", T))
            borrow = data.get("borrow")
            bhist = H.read_state("borrow", T)
            if borrow and borrow.get("fee") is not None:
                bhist = H.upsert_state("borrow", T, {"date": self.date_str, "fee": borrow["fee"],
                                                     "available": borrow.get("available"),
                                                     "capped": bool(borrow.get("available_capped")),
                                                     "source": borrow.get("source")})
            elif borrow and borrow.get("history"):
                bhist = borrow["history"]
            l2 = A.analyze_l2(data.get("finra") or [], ohlcv, borrow, bhist, data.get("short_interest"),
                              data.get("float") or {}, l1, l3.get("opt_vol_ratio"), l3.get("put_oi_trend_pct"))

        # 옵션 상태 누적 (OI 추세·핀닝 사후검증)
        if l3.get("oi_total"):
            H.upsert_state("options", self.display_ticker, {
                "date": self.date_str, "oi_total": l3["oi_total"], "call_oi": l3.get("call_oi"),
                "put_oi": l3.get("put_oi"), "total_vol": l3.get("total_volume"),
                "max_pain_by_exp": l3.get("max_pain_by_exp"), "flip": l3.get("flip_zone"),
                "atm_iv": l3.get("atm_iv"), "net_gex": l3.get("net_gex")})

        pat = A.detect_patterns(ohlcv, data.get("minute") or [], l1, l2, l3, l4,
                                flow=data.get("flow") or []) if not self.is_index \
            else {"patterns": [p for p in (["OPTION_PINNING"] if l3.get("scenario") == "PINNING" else [])], "detail": {}}
        layers = {"L1": l1, "L2": l2, "L3": l3, "L4": l4} if not self.is_index else {"L3": l3, "L4": l4}
        scen = A.scenario_probabilities(layers, macro, pat["patterns"])
        events = EV.upcoming(d, calx.get("earnings_date"), calx.get("ex_div"))
        phase = A.build_phases(d, ohlcv, l1, l3, l4, scen, events)
        levels = A.price_levels(price, l3, l4)
        quality = Q.assess({"l1": l1, "l2": l2, "l3": l3, "l4": l4}, macro,
                           bool(data.get("news")) or bool((data.get("sec") or {}).get("filings")),
                           self.sources, is_index=self.is_index)
        history = H.load_report_history(self.display_ticker, before=d)
        return {
            "meta": {"ticker": self.display_ticker, "symbol": self.ticker, "date": self.date_str,
                     "is_index": self.is_index, "price": price,
                     "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "warnings": self.warnings, "sources": self.sources, "timings": self.timings,
                     "name": (data.get("float") or {}).get("name"),
                     "early_close": cal.is_early_close(d)},
            "l1": l1, "l2": l2, "l3": l3, "l4": l4, "macro": macro, "patterns": pat["patterns"],
            "pattern_detail": pat["detail"], "scenarios": scen, "phase": phase, "levels": levels,
            "events": events, "news": data.get("news") or [], "filings": (data.get("sec") or {}).get("filings") or [],
            "quality": quality, "history": history, "ohlcv": ohlcv[-160:],
            "calendar": {k: (v.isoformat() if isinstance(v, date) else v) for k, v in calx.items()},
        }

    def run(self) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        t0 = time.time()
        data = self.fetch()
        if not data.get("ohlcv"):
            return None, data
        a = self.analyze(data)
        a["meta"]["elapsed_s"] = round(time.time() - t0, 1)
        return a, data
