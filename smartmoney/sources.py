"""데이터 수집기 (전부 무료 소스, 실측 검증 2026-11).

| 데이터 | 1차 소스 | 폴백 |
|---|---|---|
| 일봉 OHLCV | Yahoo (yfinance) | — |
| 1분봉 (OBV) | Polygon aggs (무료 키) | Yahoo 1m |
| Off-exchange / Short volume | FINRA RegSHO CNMS 일일 파일 | — |
| ATS(다크풀) vs 비-ATS OTC 주간 | FINRA OTC Transparency API | — |
| 옵션 체인 + Greeks + IV30 | CBOE delayed quotes | Yahoo option_chain |
| 대차 수수료/가용잔고 | IBKR shortstock FTP | iborrowdesk |
| 공매도 잔고(SI) | Nasdaq API | Polygon short-interest |
| 공시 | SEC data.sec.gov submissions + Form 4 XML | — |
| 거시 | FRED fredgraph CSV + Yahoo(DXY/WTI/VIX) | — |
"""
from __future__ import annotations

import ftplib
import io
import math
import os
import re
import xml.etree.ElementTree as ET_XML
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from . import calendar as cal
from .net import FetchError, cached_json, cached_text, get, log, post_json

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "").strip()
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
              "Accept": "application/json, text/plain, */*"}


def _f(x, default=None) -> Optional[float]:
    try:
        v = float(x)
        return default if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return default


# ─── Yahoo ──────────────────────────────────────────────────────────────────
def yf_ticker(symbol: str):
    if yf is None:
        raise FetchError("yfinance not installed")
    return yf.Ticker(symbol)


def fetch_ohlcv(yft, session_date: date, period: str = "2y") -> List[Dict[str, Any]]:
    hist = yft.history(period=period, interval="1d", auto_adjust=False)
    if hist is None or hist.empty:
        raise FetchError("Yahoo: no daily OHLCV")
    hist = hist.reset_index()
    hist["Date"] = pd.to_datetime(hist["Date"]).dt.tz_localize(None)
    rows = []
    for _, r in hist.iterrows():
        d = r["Date"].date()
        if d > session_date:          # 장중 실행 시 미완성 봉 제거
            continue
        c = _f(r["Close"])
        if c is None:
            continue
        rows.append({"date": d.isoformat(), "open": _f(r["Open"], c), "high": _f(r["High"], c),
                     "low": _f(r["Low"], c), "close": c, "volume": _f(r["Volume"], 0.0) or 0.0})
    if not rows:
        raise FetchError("Yahoo: OHLCV empty after session trim")
    return rows


def fetch_session_bar(symbol: str, session_date: date, yft=None) -> Tuple[Optional[Dict[str, Any]], str]:
    """Yahoo 일봉에 세션 봉이 아직 없을 때 쓰는 폴백: Polygon 일봉 → Alpaca SIP 일봉 → Yahoo 1분봉.
    (전체 세션 거래량이 필요하므로 정규장 분봉 합산은 쓰지 않는다 — 다크풀 비중 분모 왜곡)"""
    if POLYGON_API_KEY:
        try:
            ds = session_date.isoformat()
            r = get(f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/{ds}/{ds}",
                    params={"adjusted": "false", "apiKey": POLYGON_API_KEY}, timeout=20, retries=1)
            for b in r.json().get("results") or []:
                if b.get("c"):
                    return ({"date": ds, "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"],
                             "volume": float(b.get("v") or 0)}, "Polygon 일봉")
        except Exception as e:  # noqa: BLE001
            log(f"{symbol}: polygon daily bar failed ({e})")
    from . import flow as F
    try:
        b = F.daily_bar(symbol, session_date)
        if b:
            return b, "Alpaca SIP 일봉"
    except Exception as e:  # noqa: BLE001
        log(f"{symbol}: alpaca daily bar failed ({e})")
    if yft is not None:
        # 최후 폴백: Yahoo 일봉 행이 종가 NaN 으로만 게시된 경우 → 일봉 거래량 + 정규장 1분봉 OHLC
        try:
            ds = session_date.isoformat()
            h = yft.history(period="5d", interval="1d", auto_adjust=False).reset_index()
            h["Date"] = pd.to_datetime(h["Date"]).dt.tz_localize(None)
            row = h[h["Date"].dt.date == session_date]
            vol = _f(row["Volume"].iloc[0], 0.0) if len(row) else 0.0
            m = yft.history(period="5d", interval="1m", prepost=False)
            if vol and m is not None and not m.empty:
                idx = pd.to_datetime(m.index)
                idx = idx.tz_convert("America/New_York") if idx.tz is not None else idx
                m = m[[x.date() == session_date for x in idx]].dropna(subset=["Close"])
                if len(m) >= 300:          # 정규장 거의 전 구간이 있어야 신뢰
                    return ({"date": ds, "open": float(m["Open"].iloc[0]), "high": float(m["High"].max()),
                             "low": float(m["Low"].min()), "close": float(m["Close"].iloc[-1]), "volume": vol},
                            "Yahoo 1분봉 OHLC + 일봉 거래량")
        except Exception as e:  # noqa: BLE001
            log(f"{symbol}: yahoo partial bar fallback failed ({e})")
    return None, ""


def fetch_minute_bars(symbol: str, yft, session_date: date, days: int = 5) -> Tuple[List[Dict[str, Any]], str]:
    """정규장 1분봉 [{date(ET), t, o,h,l,c,v,n}] 와 소스명."""
    start = cal.prev_trading_day(session_date, days - 1)
    bars: List[Dict[str, Any]] = []
    src = ""
    if POLYGON_API_KEY:
        try:
            url = (f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/minute/"
                   f"{start.isoformat()}/{session_date.isoformat()}")
            r = get(url, params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": POLYGON_API_KEY},
                    timeout=25, retries=1)
            for b in (r.json().get("results") or []):
                bars.append({"t": int(b["t"]), "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"],
                             "v": b.get("v", 0) or 0, "n": b.get("n", 0) or 0, "vw": b.get("vw")})
            src = "polygon_1m"
        except Exception as e:  # noqa: BLE001
            log(f"{symbol}: polygon minute bars failed ({e}) → yahoo 1m")
            bars = []
    if not bars:
        df = yft.history(period="8d", interval="1m", prepost=False)
        if df is not None and not df.empty:
            df = df.reset_index()
            col = "Datetime" if "Datetime" in df.columns else df.columns[0]
            ts = pd.to_datetime(df[col])
            if ts.dt.tz is None:
                ts = ts.dt.tz_localize("America/New_York")
            for t, (_, r) in zip(ts, df.iterrows()):
                c = _f(r["Close"])
                if c is None:
                    continue
                bars.append({"t": int(t.timestamp() * 1000), "o": _f(r["Open"], c), "h": _f(r["High"], c),
                             "l": _f(r["Low"], c), "c": c, "v": _f(r["Volume"], 0.0) or 0.0, "n": 0})
            src = "yahoo_1m"
    out = []
    for b in bars:
        dt = datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc)
        et = dt.astimezone(cal.ET) if cal.ET else dt - timedelta(hours=5)
        hm = et.hour * 60 + et.minute
        if not (570 <= hm < 960):     # 09:30–16:00 ET 정규장만
            continue
        d = et.date()
        if d < start or d > session_date:
            continue
        b["date"] = d.isoformat()
        out.append(b)
    return out, src


def fetch_news(yft, session_date: date, days: int = 7, limit: int = 5) -> List[Dict[str, Any]]:
    items = []
    cutoff = session_date - timedelta(days=days)
    for n in (yft.news or [])[:25]:
        c = n.get("content") if isinstance(n.get("content"), dict) else n
        title = (c.get("title") or "").strip()
        if not title:
            continue
        prov = c.get("provider")
        publisher = prov.get("displayName", "") if isinstance(prov, dict) else (c.get("publisher") or "")
        cu = c.get("canonicalUrl") or c.get("clickThroughUrl")
        link = cu.get("url", "") if isinstance(cu, dict) else (c.get("link") or "")
        ds = ""
        pub = c.get("pubDate") or c.get("displayTime")
        if isinstance(pub, str) and len(pub) >= 10:
            ds = pub[:10]
        elif c.get("providerPublishTime"):
            ds = datetime.fromtimestamp(c["providerPublishTime"], tz=timezone.utc).date().isoformat()
        try:
            if ds and cal.parse_date(ds) < cutoff:
                continue
        except ValueError:
            pass
        items.append({"date": ds, "title": title, "publisher": publisher, "link": link,
                      "summary": (c.get("summary") or "")[:400]})
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:limit]


def fetch_calendar(yft) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        c = yft.calendar
    except Exception:  # noqa: BLE001
        return out
    if not isinstance(c, dict):
        return out

    def _d(v):
        if isinstance(v, list):
            v = v[0] if v else None
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        try:
            return cal.parse_date(str(v)) if v else None
        except ValueError:
            return None
    out["earnings_date"] = _d(c.get("Earnings Date"))
    out["eps_avg"] = _f(c.get("Earnings Average"))
    out["ex_div"] = _d(c.get("Ex-Dividend Date"))
    return out


def fetch_float(yft) -> Dict[str, Optional[float]]:
    try:
        info = yft.get_info() or {}
    except Exception:  # noqa: BLE001
        info = {}
    return {"float_shares": _f(info.get("floatShares")),
            "shares_outstanding": _f(info.get("sharesOutstanding")),
            "short_pct_float_yahoo": _f(info.get("shortPercentOfFloat")),
            "sector": info.get("sector"), "name": info.get("shortName") or info.get("longName")}


# ─── FINRA RegSHO daily (off-exchange volume & short volume) ────────────────
_REGSHO_MEMO: Dict[str, Optional[Dict[str, Tuple[float, float, float]]]] = {}


def finra_regsho(d: date) -> Optional[Dict[str, Tuple[float, float, float]]]:
    """{SYMBOL: (short_vol, short_exempt, total_vol)} — FINRA TRF/ADF 보고분 (off-exchange)."""
    key = d.isoformat()
    if key in _REGSHO_MEMO:
        return _REGSHO_MEMO[key]
    url = f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d.strftime('%Y%m%d')}.txt"
    try:
        txt = cached_text(url, max_age_s=72 * 3600)
    except FetchError as e:              # 파일 미게시·임시 휴장일 등 — 그 날만 건너뛴다
        log(f"finra regsho {key}: {e}")
        txt = None
    parsed: Optional[Dict[str, Tuple[float, float, float]]] = None
    if txt and len(txt) > 200:
        parsed = {}
        for line in txt.splitlines()[1:]:
            p = line.split("|")
            if len(p) >= 5:
                try:
                    parsed[p[1].strip().upper()] = (float(p[2] or 0), float(p[3] or 0), float(p[4] or 0))
                except ValueError:
                    continue
    _REGSHO_MEMO[key] = parsed
    return parsed


def finra_series(symbol: str, session_date: date, n: int = 20) -> List[Dict[str, Any]]:
    """최근 n 거래일 RegSHO 행 (오래된→최신)."""
    rows = []
    d = session_date if cal.is_trading_day(session_date) else cal.prev_trading_day(session_date)
    tries = 0
    while len(rows) < n and tries < n + 12:
        tbl = finra_regsho(d)
        if tbl and symbol in tbl:
            s, ex, tot = tbl[symbol]
            rows.append({"date": d.isoformat(), "short_vol": s, "short_exempt": ex, "total_vol": tot})
        d = cal.prev_trading_day(d)
        tries += 1
    return list(reversed(rows))


def finra_backfill(symbol: str, session_date: date, have: set, days: int = 250, max_fetch: int = 400) -> List[Dict[str, Any]]:
    """과거 RegSHO 원천 행 (have 에 없는 날만). 일별 파일은 디스크 캐시돼 여러 종목이 공유한다."""
    out, d, n_fetch = [], session_date, 0
    for _ in range(days):
        d = cal.prev_trading_day(d)
        if d.isoformat() in have:
            continue
        if n_fetch >= max_fetch:
            break
        n_fetch += 1
        tbl = finra_regsho(d)
        if tbl and symbol in tbl:
            s_, ex, tot = tbl[symbol]
            out.append({"date": d.isoformat(), "short_vol": s_, "short_exempt": ex, "total_vol": tot})
    return out


# ─── FINRA OTC Transparency (weekly ATS vs non-ATS OTC) ─────────────────────
FINRA_OTC = "https://api.finra.org/data/group/otcMarket/name/weeklySummary"


def _finra_week(symbol: str, week: date, tier: str, code: str) -> Optional[Dict[str, float]]:
    body = {"limit": 5, "compareFilters": [
        {"compareType": "EQUAL", "fieldName": "weekStartDate", "fieldValue": week.isoformat()},
        {"compareType": "EQUAL", "fieldName": "tierIdentifier", "fieldValue": tier},
        {"compareType": "EQUAL", "fieldName": "issueSymbolIdentifier", "fieldValue": symbol},
        {"compareType": "EQUAL", "fieldName": "summaryTypeCode", "fieldValue": code}]}
    j = cached_json(f"finra_otc:{symbol}:{week}:{tier}:{code}", lambda: post_json(FINRA_OTC, body),
                    max_age_s=24 * 3600)
    if isinstance(j, list) and j:
        x = j[0]
        return {"shares": _f(x.get("totalWeeklyShareQuantity"), 0.0),
                "trades": _f(x.get("totalWeeklyTradeCount"), 0.0)}
    return None


def finra_ats_weekly(symbol: str, session_date: date, weeks_back: int = 8) -> List[Dict[str, Any]]:
    """가장 최근 공개된 2개 주의 ATS / 비-ATS OTC 주간 합계 (최신→과거)."""
    monday = session_date - timedelta(days=session_date.weekday())
    found: List[Dict[str, Any]] = []
    tier_hit: Optional[str] = None
    for k in range(1, weeks_back + 1):
        wk = monday - timedelta(weeks=k)
        tiers = [tier_hit] if tier_hit else ["T1", "T2"]
        for tier in tiers:
            ats = _finra_week(symbol, wk, tier, "ATS_W_SMBL")
            if not ats:
                continue
            otc = _finra_week(symbol, wk, tier, "OTC_W_SMBL") or {"shares": None, "trades": None}
            tier_hit = tier
            found.append({"week_start": wk.isoformat(), "tier": tier,
                          "ats_shares": ats["shares"], "ats_trades": ats["trades"],
                          "otc_shares": otc["shares"], "otc_trades": otc["trades"]})
            break
        if len(found) >= 2:
            break
    return found


# ─── CBOE delayed options ───────────────────────────────────────────────────
_OCC = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def cboe_chain(cboe_symbol: str, roots: set) -> Dict[str, Any]:
    url = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{cboe_symbol}.json"
    r = get(url, timeout=30, headers=BROWSER_UA)
    j = r.json()
    data = j.get("data") or {}
    raw = data.get("options") or []
    if not raw:
        raise FetchError("CBOE: empty options array")
    by_exp: Dict[str, Dict[str, list]] = {}
    for o in raw:
        m = _OCC.match(o.get("option", ""))
        if not m:
            continue
        root, ymd, cp, k8 = m.groups()
        if root not in roots:
            continue
        exp = f"20{ymd[0:2]}-{ymd[2:4]}-{ymd[4:6]}"
        row = {"strike": int(k8) / 1000.0, "oi": _f(o.get("open_interest"), 0.0),
               "volume": _f(o.get("volume"), 0.0), "iv": _f(o.get("iv"), 0.0),
               "delta": _f(o.get("delta"), 0.0), "gamma": _f(o.get("gamma"), 0.0),
               "bid": _f(o.get("bid"), 0.0), "ask": _f(o.get("ask"), 0.0),
               "last": _f(o.get("last_trade_price"), 0.0)}
        b = by_exp.setdefault(exp, {"calls": [], "puts": []})
        (b["calls"] if cp == "C" else b["puts"]).append(row)
    quote = {k: data.get(k) for k in ("current_price", "close", "prev_day_close", "volume", "iv30",
                                      "iv30_change", "last_trade_time")}
    return {"by_expiry": by_exp, "quote": quote, "timestamp": j.get("timestamp"), "source": "cboe_delayed"}


def yahoo_chain(yft, max_exp: int = 8) -> Dict[str, Any]:
    by_exp: Dict[str, Dict[str, list]] = {}
    for e in list(yft.options or [])[:max_exp]:
        try:
            ch = yft.option_chain(e)
        except Exception:  # noqa: BLE001
            continue

        def pack(df):
            return [{"strike": _f(r.get("strike"), 0.0), "oi": _f(r.get("openInterest"), 0.0) or 0.0,
                     "volume": _f(r.get("volume"), 0.0) or 0.0, "iv": _f(r.get("impliedVolatility"), 0.0) or 0.0,
                     "delta": 0.0, "gamma": 0.0, "bid": _f(r.get("bid"), 0.0) or 0.0,
                     "ask": _f(r.get("ask"), 0.0) or 0.0, "last": _f(r.get("lastPrice"), 0.0) or 0.0}
                    for _, r in df.iterrows()]
        by_exp[e] = {"calls": pack(ch.calls), "puts": pack(ch.puts)}
    if not by_exp:
        raise FetchError("Yahoo: no option chain")
    return {"by_expiry": by_exp, "quote": {}, "timestamp": None, "source": "yahoo_fallback"}


# ─── Borrow (CTB fee / availability) ────────────────────────────────────────
def _ibkr_usa_table() -> Dict[str, Dict[str, Any]]:
    def download() -> str:
        # 2026-11 실측: ftp3 은 접속 타임아웃, ftp2 는 정상 (GitHub Actions 러너 기준)
        errs = []
        for host in ("ftp2.interactivebrokers.com", "ftp3.interactivebrokers.com"):
            try:
                ftp = ftplib.FTP(timeout=30)
                ftp.connect(host, 21)
                ftp.login("shortstock", "")
                ftp.set_pasv(True)
                buf = io.BytesIO()
                ftp.retrbinary("RETR usa.txt", buf.write)
                try:
                    ftp.quit()
                except Exception:  # noqa: BLE001
                    pass
                return buf.getvalue().decode("utf-8", "replace")
            except Exception as e:  # noqa: BLE001
                errs.append(f"{host}: {e}")
        raise FetchError("IBKR FTP: " + "; ".join(errs))

    def produce():
        text = download()
        table: Dict[str, Dict[str, Any]] = {}
        asof = None
        for line in text.splitlines():
            if line.startswith("#BOF"):
                parts = line.split("|")
                asof = "|".join(parts[1:3]) if len(parts) > 2 else None
                continue
            if line.startswith("#") or "|" not in line:
                continue
            p = line.split("|")
            if len(p) < 8:
                continue
            raw_avail = p[7].strip()
            table[p[0].strip().upper()] = {"fee": _f(p[6]), "rebate": _f(p[5]),
                                           "available": _f(raw_avail.lstrip(">").replace(",", "")),
                                           "available_capped": raw_avail.startswith(">"), "asof": asof}
        if not table:
            raise FetchError("IBKR FTP: empty table")
        return table
    return cached_json("ibkr_usa_txt", produce, max_age_s=4 * 3600)


def fetch_borrow(symbol: str) -> Dict[str, Any]:
    errors = []
    try:
        row = _ibkr_usa_table().get(symbol)
        if row and row.get("fee") is not None:
            return {**row, "source": "ibkr_ftp"}
        errors.append("ibkr: symbol not listed")
    except Exception as e:  # noqa: BLE001
        errors.append(f"ibkr_ftp: {e}")
    try:
        r = get(f"https://iborrowdesk.com/api/ticker/{symbol}", timeout=15, retries=0)
        j = r.json()
        daily = j.get("daily") or []
        latest = j.get("latest") or (daily[-1] if daily else None)
        if latest:
            return {"fee": _f(latest.get("fee")), "rebate": _f(latest.get("rebate")),
                    "available": _f(latest.get("available")), "asof": latest.get("date") or latest.get("time"),
                    "source": "iborrowdesk",
                    "history": [{"date": str(x.get("date") or x.get("time", ""))[:10], "fee": _f(x.get("fee")),
                                 "available": _f(x.get("available"))} for x in daily[-20:]]}
        errors.append("iborrowdesk: empty")
    except Exception as e:  # noqa: BLE001
        errors.append(f"iborrowdesk: {e}")
    raise FetchError("; ".join(errors))


# ─── Short interest (bi-monthly) ────────────────────────────────────────────
def fetch_short_interest(symbol: str) -> Dict[str, Any]:
    errors = []
    try:
        r = get(f"https://api.nasdaq.com/api/quote/{symbol}/short-interest",
                params={"assetClass": "stocks"}, headers=BROWSER_UA, timeout=20, retries=1)
        rows = (((r.json() or {}).get("data") or {}).get("shortInterestTable") or {}).get("rows") or []
        parsed = []
        for x in rows[:6]:
            try:
                sd = datetime.strptime(x["settlementDate"], "%m/%d/%Y").date().isoformat()
            except (KeyError, ValueError):
                continue
            parsed.append({"settlement_date": sd, "shares": _f(str(x.get("interest", "")).replace(",", "")),
                           "avg_daily_volume": _f(str(x.get("avgDailyShareVolume", "")).replace(",", "")),
                           "days_to_cover": _f(x.get("daysToCover"))})
        if parsed:
            return {"rows": parsed, "source": "nasdaq"}
        errors.append("nasdaq: no rows")
    except Exception as e:  # noqa: BLE001
        errors.append(f"nasdaq: {e}")
    if POLYGON_API_KEY:
        try:
            r = get("https://api.polygon.io/stocks/v1/short-interest",
                    params={"ticker": symbol, "limit": 6, "sort": "settlement_date.desc", "apiKey": POLYGON_API_KEY},
                    timeout=20, retries=1)
            res = r.json().get("results") or []
            parsed = [{"settlement_date": str(x.get("settlement_date"))[:10], "shares": _f(x.get("short_interest")),
                       "avg_daily_volume": _f(x.get("avg_daily_volume")), "days_to_cover": _f(x.get("days_to_cover"))}
                      for x in res]
            parsed = sorted([p for p in parsed if p["shares"]], key=lambda p: p["settlement_date"], reverse=True)
            if parsed:
                return {"rows": parsed, "source": "polygon"}
            errors.append("polygon: no rows")
        except Exception as e:  # noqa: BLE001
            errors.append(f"polygon: {e}")
    raise FetchError("; ".join(errors))


# ─── SEC EDGAR ──────────────────────────────────────────────────────────────
SEC_UA = {"User-Agent": os.environ.get("SEC_USER_AGENT", "SmartMoneyAnalyzer research admin@zbbg.kr")}
FLAG_FORMS = {
    "4": ("INSIDER", "내부자 거래 (Form 4)"), "8-K": ("MATERIAL", "중요 사건 (8-K)"),
    "S-3": ("DILUTION", "증권신고 (S-3, 희석 가능)"), "S-3ASR": ("DILUTION", "자동 선반등록 (S-3ASR)"),
    "424B5": ("DILUTION", "투자설명서 (424B5, 발행)"), "424B3": ("DILUTION", "투자설명서 (424B3)"),
    "SC 13D": ("OWNERSHIP", "5%+ 능동지분 (13D)"), "SCHEDULE 13D": ("OWNERSHIP", "5%+ 능동지분 (13D)"),
    "SC 13G": ("OWNERSHIP", "5%+ 수동지분 (13G)"), "SCHEDULE 13G": ("OWNERSHIP", "5%+ 수동지분 (13G)"),
    "SCHEDULE 13G/A": ("OWNERSHIP", "13G 정정"), "10-Q": ("PERIODIC", "분기보고서"),
    "10-K": ("PERIODIC", "연차보고서"), "DEF 14A": ("PROXY", "주총 위임장"),
}


def _sec_cik(symbol: str) -> Optional[str]:
    def produce():
        r = get("https://www.sec.gov/files/company_tickers.json", headers=SEC_UA, timeout=25)
        return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in r.json().values()}
    return cached_json("sec_company_tickers", produce, max_age_s=7 * 86400).get(symbol.upper())


def _parse_form4(cik: str, accession: str, doc: str) -> Optional[Dict[str, Any]]:
    raw_doc = doc.split("/")[-1]
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{raw_doc}"
    txt = cached_text(url, max_age_s=30 * 86400, headers=SEC_UA, timeout=15)
    if not txt or "<ownershipDocument" not in txt:
        return None
    try:
        root = ET_XML.fromstring(txt.encode("utf-8"))
    except ET_XML.ParseError:
        return None
    owner = (root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or "").strip()
    title = (root.findtext(".//reportingOwnerRelationship/officerTitle") or "").strip()
    buys = sells = 0.0
    codes = set()
    for t in root.findall(".//nonDerivativeTransaction"):
        code = (t.findtext(".//transactionCoding/transactionCode") or "").strip()
        sh = _f(t.findtext(".//transactionAmounts/transactionShares/value"), 0.0) or 0.0
        px = _f(t.findtext(".//transactionAmounts/transactionPricePerShare/value"), 0.0) or 0.0
        codes.add(code)
        if code == "P":
            buys += sh * px
        elif code == "S":
            sells += sh * px
    return {"owner": owner, "title": title, "buy_usd": buys, "sell_usd": sells, "codes": sorted(codes)}


def fetch_sec(symbol: str, session_date: date, days: int = 30) -> Dict[str, Any]:
    cik = _sec_cik(symbol)
    if not cik:
        raise FetchError(f"SEC: CIK not found for {symbol}")
    r = get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=SEC_UA, timeout=25)
    rec = (r.json().get("filings") or {}).get("recent") or {}
    cutoff = session_date - timedelta(days=days)
    out = []
    f4_parsed = 0
    for i, form in enumerate(rec.get("form", [])[:80]):
        try:
            fd = cal.parse_date(rec["filingDate"][i])
        except (KeyError, ValueError, IndexError):
            continue
        if fd < cutoff:
            break
        flag, label = FLAG_FORMS.get(form, ("OTHER", form))
        item = {"date": fd.isoformat(), "form": form, "flag": flag, "label": label,
                "desc": (rec.get("primaryDocDescription") or [""] * (i + 1))[i] or "",
                "items": (rec.get("items") or [""] * (i + 1))[i] or "",
                "link": (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                         f"{rec['accessionNumber'][i].replace('-', '')}/{rec['primaryDocument'][i]}")}
        if form == "4" and f4_parsed < 6:
            f4_parsed += 1
            try:
                item["form4"] = _parse_form4(cik, rec["accessionNumber"][i], rec["primaryDocument"][i])
            except Exception:  # noqa: BLE001
                item["form4"] = None
        out.append(item)
    return {"cik": cik, "filings": out}


# ─── FRED / macro ───────────────────────────────────────────────────────────
def fred_series(sid: str, n: int = 400) -> List[Tuple[str, float]]:
    txt = cached_text(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", max_age_s=12 * 3600,
                      ok_404=False, timeout=25)
    out: List[Tuple[str, float]] = []
    for line in (txt or "").splitlines()[1:]:
        p = line.split(",")
        if len(p) >= 2:
            v = _f(p[1])
            if v is not None:
                out.append((p[0], v))
    if not out:
        raise FetchError(f"FRED {sid}: empty")
    return out[-n:]


def yahoo_close_series(symbol: str, period: str = "3mo") -> List[Tuple[str, float]]:
    def produce():
        h = yf.Ticker(symbol).history(period=period)
        return [(i.strftime("%Y-%m-%d"), float(v)) for i, v in h["Close"].items() if not math.isnan(v)]
    return cached_json(f"yclose:{symbol}:{period}", produce, max_age_s=3 * 3600)


def fetch_macro() -> Dict[str, Any]:
    out: Dict[str, Any] = {"errors": []}
    for key, sid in (("dff", "DFF"), ("rrp", "RRPONTSYD"), ("sofr", "SOFR"), ("iorb", "IORB"),
                     ("walcl", "WALCL"), ("tga", "WTREGEN"), ("dgs10", "DGS10"), ("dgs2", "DGS2"),
                     ("hy_oas", "BAMLH0A0HYM2")):
        try:
            out[key] = fred_series(sid, n=260)
        except Exception as e:  # noqa: BLE001
            out[key] = []
            out["errors"].append(f"FRED {sid}: {e}")
    for key, sym in (("dxy", "DX-Y.NYB"), ("wti", "CL=F"), ("vix", "^VIX"), ("spx", "^GSPC")):
        try:
            out[key] = yahoo_close_series(sym)
        except Exception as e:  # noqa: BLE001
            out[key] = []
            out["errors"].append(f"Yahoo {sym}: {e}")
    return out
