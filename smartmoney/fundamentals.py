"""재무 데이터 수집·정규화 — 적정가치(valuation) 엔진의 입력.

소스 (전부 무료):
  · Yahoo Finance  : 연간/분기 재무제표(손익·재무상태·현금흐름), 애널리스트 컨센서스(매출·EPS), 프로필·멀티플
  · Yahoo Industry : 동종업계 구성 종목 → 시가총액이 가까운 peer 선정 → peer 멀티플·베타·성장·마진
  · SEC EDGAR XBRL : companyfacts (10년+ 연간 매출·영업이익·순이익·EPS·DPS) → 경기순환 판별·미드사이클 마진

출력은 JSON 직렬화 가능한 단일 dict (`state/fundamentals/{T}.json` 스냅샷). 재무제표는 분기마다만 바뀌므로
TTL(기본 3일) 안에서는 스냅샷을 재사용하고, 저장해 둔 다음 실적일이 지나면 즉시 갱신한다.
모든 금액은 거래 통화(예: USD) 기준으로 환산한다. 결측은 None — 절대 값을 만들어 내지 않는다.
"""
from __future__ import annotations

import json
import math
import os
import time
import zlib
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .history import STATE_DIR
from .net import FetchError, cached_json, get, log

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

SCHEMA = 2
TTL_DAYS = float(os.environ.get("SMA_FUND_TTL_DAYS", "3"))
MAX_PEERS = int(os.environ.get("SMA_VAL_PEERS", "10"))
PEER_TTL_DAYS = float(os.environ.get("SMA_PEER_TTL_DAYS", "5"))
PEER_SLEEP_S = float(os.environ.get("SMA_PEER_SLEEP_S", "1.0"))
SEC_UA = {"User-Agent": os.environ.get("SEC_USER_AGENT", "SmartMoneyAnalyzer research admin@zbbg.kr")}


def _f(x) -> Optional[float]:
    try:
        v = float(x)
        return None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return None


# ─── 재무제표 → 기간별 레코드 ────────────────────────────────────────────────
# (출력 키, [Yahoo 행 이름 후보 …])  — 앞에 있는 후보가 우선
_INCOME = [
    ("revenue", ["Total Revenue", "Operating Revenue"]),
    ("gross_profit", ["Gross Profit"]),
    ("op_income", ["Operating Income", "Total Operating Income As Reported"]),
    ("ebit_yahoo", ["EBIT"]),
    ("pretax", ["Pretax Income"]),
    ("tax", ["Tax Provision"]),
    ("net_income", ["Net Income Common Stockholders", "Net Income"]),
    ("eps", ["Diluted EPS", "Basic EPS"]),
    ("shares_diluted", ["Diluted Average Shares", "Basic Average Shares"]),
    ("interest_expense", ["Interest Expense", "Interest Expense Non Operating"]),
    ("da_is", ["Reconciled Depreciation"]),
    ("rnd", ["Research And Development"]),
    ("unusual", ["Total Unusual Items"]),
]
_CASH = [
    ("cfo", ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"]),
    ("capex", ["Capital Expenditure"]),
    ("fcf", ["Free Cash Flow"]),
    ("da_cf", ["Depreciation And Amortization", "Depreciation Amortization Depletion"]),
    ("amort_intang", ["Amortization Of Intangibles", "Amortization Cash Flow"]),
    ("sbc", ["Stock Based Compensation"]),
    ("dividends_paid", ["Cash Dividends Paid", "Common Stock Dividend Paid"]),
    ("buybacks", ["Repurchase Of Capital Stock"]),
    ("issuance", ["Issuance Of Capital Stock"]),
    ("acquisitions", ["Purchase Of Business", "Net Business Purchase And Sale"]),
]
_BALANCE = [
    ("total_debt", ["Total Debt"]),
    ("lease_liabilities", ["Capital Lease Obligations"]),
    ("cash", ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents",
              "Cash Cash Equivalents And Federal Funds Sold"]),
    ("equity", ["Common Stock Equity", "Stockholders Equity"]),
    ("minority", ["Minority Interest"]),
    ("preferred", ["Preferred Stock Equity", "Preferred Stock"]),
    ("total_assets", ["Total Assets"]),
    ("intangibles", ["Goodwill And Other Intangible Assets"]),
    ("nwc", ["Working Capital"]),
    ("lt_investments", ["Investments And Advances", "Long Term Equity Investment"]),
    ("shares_out", ["Ordinary Shares Number", "Share Issued"]),
]
FLOW_KEYS = ["revenue", "gross_profit", "op_income", "ebit_yahoo", "pretax", "tax", "net_income", "eps",
             "interest_expense", "da", "amort_intang", "rnd", "unusual", "cfo", "capex", "fcf", "sbc", "dividends_paid",
             "buybacks", "issuance", "acquisitions"]
STOCK_KEYS = ["total_debt", "lease_liabilities", "cash", "equity", "minority", "preferred", "total_assets", "intangibles", "nwc",
              "lt_investments", "shares_out"]
PER_SHARE = {"eps"}
NOT_MONEY = {"shares_diluted", "shares_out"}


def _frame(df) -> Dict[str, Dict[str, float]]:
    """DataFrame(행=계정, 열=기간말) → {계정: {YYYY-MM-DD: 값}}"""
    out: Dict[str, Dict[str, float]] = {}
    if df is None or getattr(df, "empty", True):
        return out
    for name, row in df.iterrows():
        d = {}
        for col, v in row.items():
            fv = _f(v)
            if fv is not None:
                try:
                    d[col.strftime("%Y-%m-%d")] = fv
                except AttributeError:
                    d[str(col)[:10]] = fv
        if d:
            out[str(name)] = d
    return out


def _periods(inc: Dict, cf: Dict, bs: Dict, fx: float) -> List[Dict[str, Any]]:
    ends = sorted(set().union(*[set(v) for v in inc.values()] or [set()]))
    rows: List[Dict[str, Any]] = []
    for end in ends:
        r: Dict[str, Any] = {"end": end}
        for table, spec in ((inc, _INCOME), (cf, _CASH), (bs, _BALANCE)):
            for key, names in spec:
                for nm in names:
                    v = (table.get(nm) or {}).get(end)
                    if v is not None:
                        r[key] = v if key in NOT_MONEY else v * fx
                        break
        if r.get("revenue") is None:
            continue
        r["da"] = r.get("da_cf") if r.get("da_cf") is not None else r.get("da_is")
        r.pop("da_cf", None), r.pop("da_is", None)
        # 영업이익: 은행·보험은 'Operating Income' 행이 없다 → 세전이익을 대용 (표시용; 금융 모델은 EBIT 미사용)
        r["ebit"] = r.get("op_income") if r.get("op_income") is not None else None
        rows.append(r)
    return rows


def _ttm(quarters: List[Dict[str, Any]], annual: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], str]:
    """최근 4개 분기 합(유량) + 최신 분기 잔액(저량). 분기가 부족하면 최근 연간으로 대체."""
    q = [x for x in quarters if x.get("revenue") is not None][-4:]
    ok = len(q) == 4
    if ok:
        span = (date.fromisoformat(q[-1]["end"]) - date.fromisoformat(q[0]["end"])).days
        ok = 250 <= span <= 300
    if not ok:
        if not annual:
            return {}, "none"
        return dict(annual[-1]), "annual"
    out: Dict[str, Any] = {"end": q[-1]["end"]}
    for k in FLOW_KEYS + ["ebit"]:
        vals = [x.get(k) for x in q]
        if all(v is not None for v in vals):
            out[k] = sum(vals)
        elif k in ("sbc", "dividends_paid", "buybacks", "issuance", "acquisitions", "unusual", "rnd",
                   "interest_expense") and any(v is not None for v in vals):
            out[k] = sum(v or 0.0 for v in vals)
    for k in STOCK_KEYS:
        for x in reversed(quarters):
            if x.get(k) is not None:
                out[k] = x[k]
                break
    out["shares_diluted"] = q[-1].get("shares_diluted")
    return out, "quarters"


# ─── 애널리스트 컨센서스 ──────────────────────────────────────────────────────
def _estimates(yft, fx_fin: float, fin_ccy: str, ccy: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, attr in (("rev", "revenue_estimate"), ("eps", "earnings_estimate")):
        try:
            df = getattr(yft, attr)
        except Exception:  # noqa: BLE001
            df = None
        if df is None or getattr(df, "empty", True):
            continue
        block = {}
        for period in ("0q", "+1q", "0y", "+1y"):
            if period not in df.index:
                continue
            row = df.loc[period]
            cur = str(row.get("currency") or "").upper()
            k = fx_fin if (cur and cur == fin_ccy.upper() and cur != ccy.upper()) else 1.0
            avg = _f(row.get("avg"))
            if avg is None:
                continue
            block[period] = {"avg": avg * k, "low": (_f(row.get("low")) or avg) * k,
                             "high": (_f(row.get("high")) or avg) * k,
                             "n": _f(row.get("numberOfAnalysts")), "growth": _f(row.get("growth")),
                             "year_ago": (lambda v: None if v is None else v * k)(
                                 _f(row.get("yearAgoRevenue" if key == "rev" else "yearAgoEps")))}
        if block:
            out[key] = block
    try:
        ge = yft.growth_estimates
        if ge is not None and not ge.empty and "LTG" in ge.index:
            out["ltg"] = _f(ge.loc["LTG"].get("stockTrend"))
    except Exception:  # noqa: BLE001
        pass
    try:
        tr = yft.eps_trend
        if tr is not None and not tr.empty and "+1y" in tr.index:
            cur, old = _f(tr.loc["+1y"].get("current")), _f(tr.loc["+1y"].get("90daysAgo"))
            if cur is not None and old not in (None, 0):
                out["eps_revision_90d"] = cur / old - 1
    except Exception:  # noqa: BLE001
        pass
    return out


# ─── Peer (동종업계) ─────────────────────────────────────────────────────────
_INFO_KEYS = {
    "mcap": "marketCap", "ev": "enterpriseValue", "pe": "trailingPE", "fpe": "forwardPE",
    "ev_ebitda": "enterpriseToEbitda", "ev_rev": "enterpriseToRevenue", "pb": "priceToBook",
    "ps": "priceToSalesTrailing12Months", "beta": "beta", "rev_growth": "revenueGrowth",
    "eps_growth": "earningsGrowth", "op_margin": "operatingMargins", "ebitda_margin": "ebitdaMargins",
    "net_margin": "profitMargins", "roe": "returnOnEquity", "debt": "totalDebt", "cash": "totalCash",
    "revenue": "totalRevenue", "book": "bookValue", "shares": "sharesOutstanding", "div_yield": "dividendYield",
}


def _info(yft, tries: int = 3) -> Dict[str, Any]:
    """Yahoo quoteSummary — 호출 제한(429/crumb) 시 빈 dict 가 오므로 간격을 두고 재시도."""
    info: Dict[str, Any] = {}
    for i in range(tries):
        try:
            info = yft.get_info() or {}
        except Exception:  # noqa: BLE001
            info = {}
        if info.get("marketCap") or info.get("totalRevenue"):
            return info
        if i < tries - 1:
            time.sleep(4.0 * (i + 1))
    return info


def _peer_cache_path():
    return STATE_DIR / "fundamentals" / "_peers.json"


def _peer_cache() -> Dict[str, Dict[str, Any]]:
    try:
        v = json.loads(_peer_cache_path().read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _peer_info(sym: str, cache: Dict[str, Dict[str, Any]], today: date) -> Optional[Dict[str, Any]]:
    """peer 프로필은 여러 종목이 공유한다 → state/fundamentals/_peers.json 에 PEER_TTL 일 동안 재사용 (Yahoo 호출 절감)."""
    rec = cache.get(sym)
    try:
        if rec and (today - date.fromisoformat(rec.get("fetched", ""))).days < PEER_TTL_DAYS:
            return rec
    except ValueError:
        pass
    time.sleep(PEER_SLEEP_S)
    info = _info(yf.Ticker(sym), tries=2)
    if not info.get("marketCap"):
        return rec                                           # 실패 시 오래된 값이라도 사용
    new = {k: _f(info.get(src)) for k, src in _INFO_KEYS.items()}
    new.update(symbol=sym, name=info.get("shortName") or info.get("longName"), industry=info.get("industry"),
               fetched=today.isoformat())
    cache[sym] = new
    return new


def _peer_candidates(industry_key: Optional[str], sector_key: Optional[str], self_sym: str,
                     mcap: Optional[float]) -> Tuple[List[str], str]:
    """동일 industry 구성 종목 중 시가총액(로그 거리)이 가까운 순. 5개 미만이면 sector 상위 종목으로 보충."""
    def table(kind: str, key: str) -> List[Tuple[str, float]]:
        def produce():
            obj = yf.Industry(key) if kind == "industry" else yf.Sector(key)
            tc = obj.top_companies
            total = _f((obj.overview or {}).get("market_cap")) or 0.0
            if tc is None or tc.empty:
                return []
            return [[str(s), (_f(r.get("market weight")) or 0.0) * total] for s, r in tc.iterrows()]
        return [(s, w) for s, w in cached_json(f"valind:{kind}:{key}", produce, max_age_s=3 * 86400)]

    scope = "industry"
    cands: List[Tuple[str, float]] = []
    if industry_key:
        try:
            cands = table("industry", industry_key)
        except Exception as e:  # noqa: BLE001
            log(f"{self_sym} peers: industry {industry_key}: {str(e)[:100]}")
    cands = [(s, w) for s, w in cands if s.upper() != self_sym.upper()]
    if len(cands) < 5 and sector_key:
        try:
            extra = [(s, w) for s, w in table("sector", sector_key) if s.upper() != self_sym.upper()]
            have = {s for s, _ in cands}
            cands += [(s, w) for s, w in extra if s not in have]
            scope = "industry+sector" if have else "sector"
        except Exception as e:  # noqa: BLE001
            log(f"{self_sym} peers: sector {sector_key}: {str(e)[:100]}")
    if mcap and mcap > 0:
        def dist(w: float) -> float:
            return abs(math.log(w / mcap)) if w and w > 0 else 9.0
        cands.sort(key=lambda sw: dist(sw[1]))
    return [s for s, _ in cands[:MAX_PEERS]], scope


def fetch_peers(industry_key, sector_key, self_sym: str, mcap) -> Tuple[List[Dict[str, Any]], str]:
    syms, scope = _peer_candidates(industry_key, sector_key, self_sym, mcap)
    cache, today = _peer_cache(), datetime.now(timezone.utc).date()
    n0 = json.dumps(cache, sort_keys=True)
    out = []
    for s in syms:
        try:
            rec = _peer_info(s, cache, today)
            if rec and rec.get("mcap"):
                out.append(rec)
        except Exception as e:  # noqa: BLE001
            log(f"{self_sym} peer {s}: {str(e)[:100]}")
    if json.dumps(cache, sort_keys=True) != n0:
        try:
            p = _peer_cache_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        except OSError:
            pass
    return out, scope


# ─── SEC XBRL 장기 이력 ─────────────────────────────────────────────────────
_SEC_TAGS = {
    "revenue": ("max", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                        "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet", "Revenue"]),
    "op_income": ("first", ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"]),
    "net_income": ("first", ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss"]),
    "eps": ("first", ["EarningsPerShareDiluted", "DilutedEarningsLossPerShare", "EarningsPerShareBasic"]),
    "dps": ("first", ["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"]),
    "cfo": ("first", ["NetCashProvidedByUsedInOperatingActivities"]),
    "equity": ("first", ["StockholdersEquity", "Equity"]),
}
_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A", "10-KT"}


def _sec_cik(symbol: str) -> Optional[str]:
    def produce():
        r = get("https://www.sec.gov/files/company_tickers.json", headers=SEC_UA, timeout=25)
        return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in r.json().values()}
    return cached_json("sec_company_tickers", produce, max_age_s=7 * 86400).get(symbol.upper().replace(".", "-"))


def sec_history(symbol: str) -> Dict[str, List[List[Any]]]:
    """{계정: [[회계연도말, 값], …]} 오름차순. 연간(≈12개월) 값만, 같은 기간은 가장 늦게 제출된 값."""
    cik = _sec_cik(symbol)
    if not cik:
        return {}

    def produce():
        r = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", headers=SEC_UA, timeout=45)
        facts = (r.json() or {}).get("facts") or {}
        pool: Dict[str, Any] = {}
        for tax in ("us-gaap", "ifrs-full"):
            pool.update(facts.get(tax) or {})
        out: Dict[str, List[List[Any]]] = {}
        for key, (mode, tags) in _SEC_TAGS.items():
            series: Dict[str, Tuple[str, float]] = {}
            for tag in tags:
                units = (pool.get(tag) or {}).get("units") or {}
                rows = next((v for u, v in units.items() if u.upper().startswith("USD")), None)
                if not rows:
                    continue
                for x in rows:
                    if x.get("form") not in _ANNUAL_FORMS or x.get("val") is None or not x.get("end"):
                        continue
                    if key != "equity":
                        if not x.get("start"):
                            continue
                        days = (date.fromisoformat(x["end"]) - date.fromisoformat(x["start"])).days
                        if not 340 <= days <= 380:
                            continue
                    elif x.get("fp") != "FY":
                        continue
                    end, filed, val = x["end"], x.get("filed") or "", float(x["val"])
                    prev = series.get(end)
                    if prev is None:
                        series[end] = (filed, val)
                    elif mode == "max" and tag != tags[0] and filed <= prev[0]:
                        if val > prev[1]:
                            series[end] = (prev[0], val)
                    elif filed > prev[0]:
                        series[end] = (filed, val)
                if series and mode == "first":
                    break
            if series:
                # 회계연도말이 30일 이내로 겹치는 중복(53주 회계 등)은 뒤의 것만
                ends = sorted(series)
                keep = [e for i, e in enumerate(ends)
                        if i == len(ends) - 1 or (date.fromisoformat(ends[i + 1]) - date.fromisoformat(e)).days > 200]
                out[key] = [[e, series[e][1]] for e in keep][-16:]
        return out
    return cached_json(f"secfacts:{cik}", produce, max_age_s=7 * 86400)


def last_price(symbol: str, yft=None) -> Optional[float]:
    try:
        yft = yft or yf.Ticker(symbol)
        return _f(yft.fast_info["lastPrice"])
    except Exception:  # noqa: BLE001
        return None


# ─── 메인 수집 ───────────────────────────────────────────────────────────────
def fetch(symbol: str, yft=None) -> Dict[str, Any]:
    if yf is None:
        raise FetchError("yfinance not installed")
    yft = yft or yf.Ticker(symbol)
    warnings: List[str] = []
    info = _info(yft)
    if not info.get("marketCap") and not info.get("totalRevenue"):
        raise FetchError("Yahoo info: 재무 프로필 없음 (ETF·지수·상장폐지?)")
    if not (info.get("sectorKey") or info.get("sector") or info.get("industryKey")):
        raise FetchError("Yahoo info: 섹터·산업 프로필 누락 (호출 제한으로 인한 부분 응답?) — 스냅샷 저장 안 함")
    ccy = str(info.get("currency") or "USD").upper()
    fin = str(info.get("financialCurrency") or ccy).upper()
    fx = 1.0
    if fin != ccy:
        try:
            fx = _f(yf.Ticker(f"{fin}{ccy}=X").fast_info["lastPrice"]) or 1.0
        except Exception as e:  # noqa: BLE001
            warnings.append(f"FX {fin}->{ccy} 실패: {str(e)[:80]} (환산 없이 사용)")

    def stmt(name: str) -> Dict[str, Dict[str, float]]:
        try:
            return _frame(getattr(yft, name))
        except Exception as e:  # noqa: BLE001
            warnings.append(f"{name}: {str(e)[:100]}")
            return {}

    annual = _periods(stmt("income_stmt"), stmt("cashflow"), stmt("balance_sheet"), fx)
    quarters = _periods(stmt("quarterly_income_stmt"), stmt("quarterly_cashflow"), stmt("quarterly_balance_sheet"), fx)
    if not annual and not quarters:
        raise FetchError("Yahoo: 재무제표 없음")
    ttm, ttm_src = _ttm(quarters, annual)
    if ttm_src != "quarters":
        warnings.append("TTM: 분기 4개 미확보 → 최근 연간 실적으로 대체")

    est = _estimates(yft, fx, fin, ccy)
    if "rev" not in est:
        warnings.append("애널리스트 매출 컨센서스 없음 → 과거 성장률 기반 추정")

    def ts(key: str) -> Optional[str]:
        v = _f(info.get(key))
        return datetime.fromtimestamp(v, tz=timezone.utc).date().isoformat() if v else None

    profile = {
        "name": info.get("longName") or info.get("shortName"), "sector": info.get("sector"),
        "industry": info.get("industry"), "sector_key": info.get("sectorKey"),
        "industry_key": info.get("industryKey"), "country": info.get("country"),
        "quote_type": info.get("quoteType"), "currency": ccy, "fin_currency": fin, "fx": fx,
        "last_fy_end": ts("lastFiscalYearEnd"), "mrq": ts("mostRecentQuarter"),
        "summary": (info.get("longBusinessSummary") or "")[:400],
    }
    market = {k: _f(info.get(src)) for k, src in {
        **_INFO_KEYS, "price": "currentPrice", "price_alt": "regularMarketPrice", "shares_implied": "impliedSharesOutstanding",
        "trailing_eps": "trailingEps", "forward_eps": "forwardEps", "div_rate": "dividendRate",
        "payout": "payoutRatio", "div_yield_5y": "fiveYearAvgDividendYield", "peg": "trailingPegRatio",
        "target_mean": "targetMeanPrice", "target_median": "targetMedianPrice", "target_low": "targetLowPrice",
        "target_high": "targetHighPrice", "n_analysts": "numberOfAnalystOpinions",
        "rec_mean": "recommendationMean", "roa": "returnOnAssets", "float_shares": "floatShares",
    }.items()}

    if market.get("price") is None:
        market["price"] = market.pop("price_alt", None) or last_price(symbol, yft)
    market.pop("price_alt", None)
    try:
        peers, peer_scope = fetch_peers(profile["industry_key"], profile["sector_key"], symbol, market.get("mcap"))
    except Exception as e:  # noqa: BLE001
        peers, peer_scope = [], "none"
        warnings.append(f"peers: {str(e)[:100]}")
    if len(peers) < 4:
        warnings.append(f"peer {len(peers)}개 — 상대가치 모델 신뢰도 낮음")

    try:
        long = sec_history(symbol)
    except Exception as e:  # noqa: BLE001
        long = {}
        warnings.append(f"SEC XBRL 장기 이력: {str(e)[:100]}")

    next_earn = None
    try:
        c = yft.calendar
        ed = (c or {}).get("Earnings Date") if isinstance(c, dict) else None
        ed = ed[0] if isinstance(ed, list) and ed else ed
        next_earn = ed.isoformat()[:10] if hasattr(ed, "isoformat") else None
    except Exception:  # noqa: BLE001
        pass

    return {
        "schema": SCHEMA, "ticker": symbol.upper(), "fetched": datetime.now(timezone.utc).date().isoformat(),
        "incomplete": len(peers) < 3 or "rev" not in est,   # 다음 실행에서 다시 시도 (호출 제한·일시 누락 대비)
        "next_earnings": next_earn, "profile": profile, "market": market,
        "annual": annual[-6:], "quarters": quarters[-8:], "ttm": ttm, "ttm_source": ttm_src,
        "estimates": est, "peers": peers, "peer_scope": peer_scope, "long": long,
        "sources": ["Yahoo Finance (재무제표·컨센서스·프로필)", f"Yahoo Industry peers ({peer_scope}, {len(peers)}개)"]
                   + (["SEC EDGAR XBRL companyfacts (장기 이력)"] if long else []),
        "warnings": warnings,
    }


# ─── 스냅샷 (state/fundamentals/{T}.json) ───────────────────────────────────
def _snap_path(symbol: str):
    return STATE_DIR / "fundamentals" / f"{symbol.upper()}.json"


def read_snapshot(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        v = json.loads(_snap_path(symbol).read_text(encoding="utf-8"))
        return v if isinstance(v, dict) and v.get("schema") == SCHEMA else None
    except (OSError, ValueError):
        return None


def _stale(snap: Dict[str, Any], today: date) -> bool:
    try:
        fetched = date.fromisoformat(snap["fetched"])
    except (KeyError, ValueError):
        return True
    if snap.get("incomplete") and (today - fetched).days >= 1:
        return True
    # 종목마다 0~2일 지터 → 워치리스트 전체가 같은 날 한꺼번에 갱신되지 않게 분산 (Yahoo 호출 제한 회피)
    if (today - fetched).days >= TTL_DAYS + zlib.crc32(str(snap.get("ticker", "")).encode()) % 3:
        return True
    ne = snap.get("next_earnings")
    try:                                  # 저장 당시 '다음 실적일'이 지났으면 새 분기 실적 반영
        return bool(ne) and fetched <= date.fromisoformat(ne) < today
    except ValueError:
        return False


def load(symbol: str, yft=None, force: bool = False, today: Optional[date] = None) -> Dict[str, Any]:
    """스냅샷이 신선하면 재사용, 아니면 수집 후 저장. 수집 실패 + 과거 스냅샷 존재 → 스냅샷(경고 포함) 반환."""
    today = today or datetime.now(timezone.utc).date()
    snap = read_snapshot(symbol)
    if snap and not force and not _stale(snap, today):
        snap["from_cache"] = True
        return snap
    try:
        fresh = fetch(symbol, yft)
    except Exception as e:  # noqa: BLE001
        if snap:
            snap["from_cache"] = True
            snap.setdefault("warnings", []).append(f"재무 데이터 갱신 실패({str(e)[:100]}) — {snap.get('fetched')} 스냅샷 사용")
            return snap
        raise
    try:
        p = _snap_path(symbol)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(fresh, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass
    fresh["from_cache"] = False
    return fresh
