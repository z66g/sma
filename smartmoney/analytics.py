"""L1–L4 분석, 패턴 탐지, 거시 분류, 시나리오 확률, 가격 레벨, Phase 구조.

설계 원칙
- 모든 레이어는 (score ∈ [-1,1], confidence ∈ [0,1]) 를 낸다. 데이터가 빠지면 confidence 가
  낮아지고 점수는 중립으로 수렴한다 — 결측을 0 으로 위장하지 않는다.
- 결측 필드는 None 으로 유지하고, quality 모듈이 이를 집계한다.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import calendar as cal

# ─── 수치 헬퍼 ──────────────────────────────────────────────────────────────


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def slope(ys: List[Optional[float]]) -> float:
    arr = np.array([np.nan if y is None else y for y in ys], dtype=float)
    xs = np.arange(len(arr), dtype=float)
    m = ~np.isnan(arr)
    if m.sum() < 2:
        return 0.0
    xs, arr = xs[m], arr[m]
    den = ((xs - xs.mean()) ** 2).sum()
    return float(((xs - xs.mean()) * (arr - arr.mean())).sum() / den) if den else 0.0


def mean(xs: List[Optional[float]]) -> Optional[float]:
    v = [x for x in xs if x is not None]
    return float(np.mean(v)) if v else None


def pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b in (None, 0):
        return None
    return (a - b) / b * 100.0


def ema(vals: List[float], n: int) -> List[float]:
    if not vals:
        return []
    k = 2 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi_wilder(closes: List[float], n: int = 14) -> Optional[float]:
    if len(closes) <= n:
        return None
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = np.mean(gains[:n]), np.mean(losses[:n])
    for g, l in zip(gains[n:], losses[n:]):
        ag = (ag * (n - 1) + g) / n
        al = (al * (n - 1) + l) / n
    if al == 0:
        return 100.0
    return float(100 - 100 / (1 + ag / al))


def atr(ohlcv: List[Dict], n: int = 14) -> Optional[float]:
    if len(ohlcv) <= n:
        return None
    trs = []
    for i in range(1, len(ohlcv)):
        h, l, pc = ohlcv[i]["high"], ohlcv[i]["low"], ohlcv[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = float(np.mean(trs[:n]))
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_greeks(S: float, K: float, T: float, iv: float, call: bool, r: float = 0.04) -> Tuple[float, float]:
    if S <= 0 or K <= 0 or T <= 0 or iv <= 0:
        return 0.0, 0.0
    sq = iv * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / sq
    delta = norm_cdf(d1) if call else norm_cdf(d1) - 1
    return delta, norm_pdf(d1) / (S * sq)


def dist_weight(strike: float, spot: float) -> float:
    d = abs(strike - spot) / spot * 100 if spot else 999
    if d <= 2.5:
        return 1.0
    if d <= 5:
        return 0.9
    if d <= 10:
        return 0.65
    if d <= 15:
        return 0.35
    if d <= 20:
        return 0.18
    return 0.07


def cluster_prices(prices: List[float], threshold_pct: float = 0.6) -> List[Tuple[float, int]]:
    """가격 군집 → [(평균가, 터치횟수)]."""
    if not prices:
        return []
    ps = sorted(prices)
    groups = [[ps[0]]]
    for p in ps[1:]:
        if abs(p - groups[-1][-1]) / groups[-1][-1] * 100 <= threshold_pct:
            groups[-1].append(p)
        else:
            groups.append([p])
    return [(float(np.mean(g)), len(g)) for g in groups]


# ─── L1 Dark Pool ───────────────────────────────────────────────────────────


def analyze_l1(ohlcv: List[Dict], finra: List[Dict], minute: List[Dict], minute_src: str,
               ats_weeks: List[Dict], flow: Optional[List[Dict]] = None,
               flow_hist: Optional[List[Dict]] = None) -> Dict[str, Any]:
    vol_by = {c["date"]: c["volume"] for c in ohlcv}
    ohlc_by = {c["date"]: c for c in ohlcv}
    series = []
    for r in finra:
        mv = vol_by.get(r["date"])
        dp = r["total_vol"] / mv * 100 if mv and r["total_vol"] <= mv * 1.02 else None
        series.append({"date": r["date"], "off_exchange_volume": r["total_vol"], "market_volume": mv,
                       "dp_pct": None if dp is None else min(dp, 100.0)})
    out: Dict[str, Any] = {"dp_series": series[-20:]}
    if not series:
        out.update(signal="NEUTRAL", score=0.0, confidence=0.0, scenario="NO_DATA")
        return out

    latest = series[-1]
    prior = series[:-1]
    dp_latest = latest["dp_pct"]
    dp_avg20 = mean([s["dp_pct"] for s in prior[-20:]])
    off_avg20 = mean([s["off_exchange_volume"] for s in prior[-20:]])
    for s in series:
        s["vol_ratio"] = (s["off_exchange_volume"] / off_avg20) if off_avg20 else None
        r = s["vol_ratio"] or 0
        s["anomaly"] = "EXTREME" if r >= 3 else "HIGH" if r >= 2 else "MODERATE" if r >= 1.5 else ""
    dp_class = ("N/A" if dp_latest is None else "INSTITUTIONAL_HEAVY" if dp_latest > 50 else
                "ELEVATED" if dp_latest > 40 else "NORMAL" if dp_latest > 30 else "RETAIL_DOMINANT")
    out.update(dp_pct=dp_latest, dp_avg20=dp_avg20,
               dp_ratio=(dp_latest / dp_avg20) if (dp_latest is not None and dp_avg20) else None,
               dp_class=dp_class, off_vol_ratio=latest.get("vol_ratio"),
               dp_days_over_40=sum(1 for s in series[-5:] if (s["dp_pct"] or 0) > 40))

    # ── Net signed volume (CLV-weighted) — 5 거래일 ──
    by_day: Dict[str, List[Dict]] = {}
    for b in minute:
        by_day.setdefault(b["date"], []).append(b)
    dates = [s["date"] for s in series[-5:]]
    cum, running, src_used = [], 0.0, set()
    vwap_by_day: Dict[str, float] = {}
    for d in dates:
        bars = by_day.get(d, [])
        R, src = None, "none"
        if len(bars) >= 60:
            sv = tv = pv = 0.0
            for b in bars:
                rng = b["h"] - b["l"]
                clv = ((b["c"] - b["l"]) - (b["h"] - b["c"])) / rng if rng > 0 else 0.0
                sv += clamp(clv) * b["v"]
                tv += b["v"]
                pv += (b.get("vw") or (b["h"] + b["l"] + b["c"]) / 3) * b["v"]
            if tv > 0:
                R, src = sv / tv, minute_src or "minute"
                vwap_by_day[d] = pv / tv
        if R is None and d in ohlc_by:
            c = ohlc_by[d]
            rng = c["high"] - c["low"]
            R, src = (((c["close"] - c["low"]) - (c["high"] - c["close"])) / rng if rng > 0 else 0.0), "daily_clv"
        src_used.add(src)
        day = (R or 0.0) * (vol_by.get(d) or 0.0)
        running += day
        cum.append({"date": d, "cum": running, "day": day, "R": R})
    avg_vol20 = mean([c["volume"] for c in ohlcv[-21:-1]]) or mean([c["volume"] for c in ohlcv[-20:]]) or 1.0
    delta_total = running
    strength = delta_total / avg_vol20 if avg_vol20 else 0.0

    # 20일 누적 (일봉 CLV) — 중기 추세 맥락
    acc20, acc_series = 0.0, []
    for c in ohlcv[-20:]:
        rng = c["high"] - c["low"]
        clv = ((c["close"] - c["low"]) - (c["high"] - c["close"])) / rng if rng > 0 else 0.0
        acc20 += clv * c["volume"]
        acc_series.append(acc20)

    closes5 = [ohlc_by[d]["close"] for d in dates if d in ohlc_by]
    p_slope = slope(closes5) / (closes5[-1] if closes5 else 1) * 100    # %/day
    o_slope = slope([c["cum"] for c in cum]) / avg_vol20 * 100           # %ADV/day
    if p_slope < -0.15 and o_slope > 2:
        divergence = "BULLISH_DIVERGENCE"
    elif p_slope > 0.15 and o_slope < -2:
        divergence = "BEARISH_DIVERGENCE"
    elif abs(p_slope) <= 0.15 and abs(o_slope) <= 2:
        divergence = "NEUTRAL"
    elif p_slope * o_slope > 0:
        divergence = "CONVERGENCE"
    else:
        divergence = "NEUTRAL"

    # VWAP 앵커 (전체 체결 기준 — 다크풀 전용 VWAP 은 체결 단위 데이터 필요)
    last_close = ohlcv[-1]["close"]
    vwap_session = vwap_by_day.get(dates[-1]) if dates else None
    vwap5 = None
    if vwap_by_day:
        num = sum(vwap_by_day[d] * (vol_by.get(d) or 0) for d in vwap_by_day)
        den = sum((vol_by.get(d) or 0) for d in vwap_by_day)
        vwap5 = num / den if den else None

    # ATS / 비-ATS OTC / Lit 코호트 (FINRA 주간, 실물)
    ats = None
    if ats_weeks:
        w = ats_weeks[0]
        ws = cal.parse_date(w["week_start"])
        wk_vol = sum(c["volume"] for c in ohlcv if ws <= cal.parse_date(c["date"]) <= ws + timedelta(days=4))
        if wk_vol > 0:
            ats_pct = w["ats_shares"] / wk_vol * 100
            otc_pct = (w["otc_shares"] or 0) / wk_vol * 100
            prev_pct = None
            if len(ats_weeks) > 1:
                p = ats_weeks[1]
                ps = cal.parse_date(p["week_start"])
                pv = sum(c["volume"] for c in ohlcv if ps <= cal.parse_date(c["date"]) <= ps + timedelta(days=4))
                prev_pct = p["ats_shares"] / pv * 100 if pv else None
            ats = {"week_start": w["week_start"], "ats_shares": w["ats_shares"],
                   "otc_non_ats_shares": w["otc_shares"], "week_volume": wk_vol,
                   "ats_share_pct": ats_pct, "otc_share_pct": otc_pct,
                   "lit_share_pct": max(0.0, 100 - ats_pct - otc_pct),
                   "ats_share_prev_pct": prev_pct,
                   "ats_trades_avg_size": (w["ats_shares"] / w["ats_trades"]) if w.get("ats_trades") else None,
                   "otc_trades_avg_size": ((w["otc_shares"] or 0) / w["otc_trades"]) if w.get("otc_trades") else None}

    # ── 체결 단위 4-Way (Alpaca SIP) — 있으면 1차 근거 ──
    fl = _flow_metrics(flow or [], flow_hist or [], ohlcv, avg_vol20, last_close)
    if fl:
        delta_total = fl["delta_total"]
        strength = delta_total / avg_vol20 if avg_vol20 else 0.0
        divergence = fl["divergence"]
        src_used.add("alpaca_trades")

    # ── 점수 ──
    if fl:
        s = math.tanh(fl["inst_strength"] / 0.10) * 0.45 + math.tanh(strength / 0.25) * 0.20
        if fl.get("dp_vwap_dist_pct") is not None:      # 다크풀 VWAP > 종가 = 기관이 종가 위에서 매수
            s += clamp(fl["dp_vwap_dist_pct"] / 1.0) * 0.10
        if (fl.get("iar") or 0) > 1.5:
            s *= 1.15
    else:
        s = math.tanh(strength / 0.25) * 0.65
    s += {"BULLISH_DIVERGENCE": 0.25, "BEARISH_DIVERGENCE": -0.25}.get(divergence, 0.0)
    if vwap5:
        s += 0.10 if last_close > vwap5 else -0.10
    s = clamp(s)
    conf = 0.45
    if fl:
        conf += 0.35
    elif any(x.endswith("_1m") or x == "minute" for x in src_used):
        conf += 0.25
    if dp_latest is not None and dp_latest > 40:
        conf += 0.15
    if (out.get("dp_ratio") or 0) > 1.1:
        conf += 0.15
    conf = min(conf, 1.0)
    if s > 0.2:
        scen = "ACCUMULATION"
    elif s < -0.2:
        scen = "DISTRIBUTION"
    elif (strength > 0) != (divergence == "BEARISH_DIVERGENCE") and divergence != "NEUTRAL" and abs(strength) > 0.05:
        scen = "AMBIGUOUS"
    else:
        scen = "NEUTRAL"
    out.update(delta_total=delta_total, obv_strength=strength, divergence=divergence,
               cumulative_daily=fl["cumulative_daily"] if fl else cum,
               obv_source=",".join(sorted(src_used)), window_days=len(fl["days"]) if fl else len(dates),
               acc20=acc20, acc20_strength=acc20 / avg_vol20 if avg_vol20 else None,
               avg_vol20=avg_vol20, vwap_session=vwap_session, vwap5=vwap5,
               vwap_dist_pct=pct(last_close, vwap5), ats=ats,
               delta_inst=fl["delta_inst"] if fl else None, delta_pro=fl["delta_pro"] if fl else None,
               delta_retail=fl["delta_retail"] if fl else None, iar=fl["iar"] if fl else None,
               dp_vwap=fl["dp_vwap"] if fl else None, dp_vwap_dist_pct=fl["dp_vwap_dist_pct"] if fl else None,
               flow=fl, clv_delta_total=running,
               score=s, confidence=conf, scenario=scen,
               signal="BULLISH" if s > 0.2 else "BEARISH" if s < -0.2 else "NEUTRAL",
               price_slope_pct=p_slope, obv_slope_pct=fl["inst_slope_pct"] if fl else o_slope)
    return out


def _flow_metrics(flow: List[Dict], hist: List[Dict], ohlcv: List[Dict], avg_vol20: float,
                  last_close: float) -> Optional[Dict[str, Any]]:
    """체결 단위 일별 집계(최근 N세션) → 4-Way OBV · IAR · 다크풀 VWAP · 세션별 이상 탐지."""
    if not flow:
        return None
    inst = sum(r["inst"] for r in flow); pro = sum(r["pro"] for r in flow); ret = sum(r["ret"] for r in flow)
    total = inst + pro + ret                                   # Total = 기관+프로+리테일 (계산값 우선)
    iar = abs(inst) / (abs(ret) + abs(pro)) if (abs(ret) + abs(pro)) else None
    latest = flow[-1]
    dvw = latest.get("dark_vwap")
    close_by = {c["date"]: c["close"] for c in ohlcv}
    ref_close = close_by.get(latest["date"], last_close)
    # 누적 시리즈 (기관 기준 다이버전스)
    cum, run_i, run_t = [], 0.0, 0.0
    for r in flow:
        run_i += r["inst"]; run_t += r["inst"] + r["pro"] + r["ret"]
        cum.append({"date": r["date"], "cum": run_t, "day": r["inst"] + r["pro"] + r["ret"],
                    "inst_cum": run_i, "inst": r["inst"], "pro": r["pro"], "ret": r["ret"]})
    closes = [close_by.get(r["date"]) for r in flow]
    p_sl = slope(closes) / (last_close or 1) * 100
    i_sl = slope([c["inst_cum"] for c in cum]) / (avg_vol20 or 1) * 100
    if p_sl < -0.15 and i_sl > 1:
        div = "BULLISH_DIVERGENCE"
    elif p_sl > 0.15 and i_sl < -1:
        div = "BEARISH_DIVERGENCE"
    elif abs(p_sl) <= 0.15 and abs(i_sl) <= 1:
        div = "NEUTRAL"
    elif p_sl * i_sl > 0:
        div = "CONVERGENCE"
    else:
        div = "NEUTRAL"
    # 세션별(프리/정규/애프터) 장외 거래량 이상 탐지 — 누적 이력 20일 평균 대비
    past = [h for h in hist if h.get("date") < latest["date"]][-20:]
    sessions = []
    for k, label in (("pre", "프리마켓"), ("reg", "정규장"), ("ah", "애프터마켓")):
        cur = (latest.get("sess") or {}).get(k) or {}
        avg = mean([((h.get("sess") or {}).get(k) or {}).get("dark") for h in past]) if past else None
        ratio = (cur.get("dark") / avg) if (avg and cur.get("dark") is not None) else None
        sessions.append({"session": label, "volume": cur.get("vol"), "dark": cur.get("dark"),
                         "dark_pct": cur.get("dark_pct"), "avg20_dark": avg, "ratio": ratio,
                         "flag": "" if ratio is None else ("EXTREME" if ratio >= 3 else "HIGH" if ratio >= 2
                                                           else "MODERATE" if ratio >= 1.5 else "")})
    return {
        "days": [r["date"] for r in flow], "delta_inst": inst, "delta_pro": pro, "delta_retail": ret,
        "delta_total": total, "iar": iar,
        "iar_class": None if iar is None else ("INSTITUTIONAL_LEAD" if iar > 1.5 else "MIXED" if iar >= 0.8 else "RETAIL_PRO_LEAD"),
        "inst_strength": inst / avg_vol20 if avg_vol20 else 0.0,
        "dp_vwap": dvw, "dp_vwap_dist_pct": ((dvw - ref_close) / ref_close * 100) if (dvw and ref_close) else None,
        "divergence": div, "inst_slope_pct": i_sl, "cumulative_daily": cum, "sessions": sessions,
        "blocks": latest.get("blocks"), "dark_blocks": latest.get("dark_blocks"),
        "last30_block_share": [r.get("last30_block_share") for r in flow],
        "last30_move_pct": [r.get("last30_move_pct") for r in flow],
        "bjzz_imbalance": latest.get("bjzz_imbalance"),
        "dark_pct_trades": latest.get("dark_pct"), "trades": latest.get("trades"),
        "cohort_vol_share": {k: (latest.get(f"vol_{k}") or 0) / ((latest.get("vol_inst") or 0) + (latest.get("vol_pro") or 0)
                                                               + (latest.get("vol_ret") or 0) or 1) * 100
                             for k in ("inst", "pro", "ret")},
    }


# ─── L2 Short / Borrow ──────────────────────────────────────────────────────


def analyze_l2(finra: List[Dict], ohlcv: List[Dict], borrow: Optional[Dict], borrow_hist: List[Dict],
               si: Optional[Dict], flt: Dict, l1: Dict, opt_vol_ratio: Optional[float],
               put_oi_trend: Optional[float]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    vol_by = {c["date"]: c["volume"] for c in ohlcv}
    ser = []
    for r in finra:
        sp = r["short_vol"] / r["total_vol"] * 100 if r["total_vol"] else None
        mv = vol_by.get(r["date"])
        ser.append({"date": r["date"], "short_pct": sp, "short_vol": r["short_vol"], "total_vol": r["total_vol"],
                    "est_short_total": (sp / 100 * mv) if (sp is not None and mv) else None})
    out["short_series"] = ser[-20:]
    if ser:
        sps = [x["short_pct"] for x in ser]
        latest = sps[-1]
        base = [x for x in sps[-15:-1] if x is not None]
        avg14 = float(np.mean(base)) if base else latest
        sd = float(np.std(base)) if len(base) >= 3 else 0.0
        z = (latest - avg14) / sd if sd > 0.5 and latest is not None else 0.0
        sl14 = slope(sps[-14:])
        sl5 = slope(sps[-5:])
        out.update(short_pct_latest=latest, short_avg_14d=avg14, short_slope=sl14, short_slope5=sl5,
                   anomaly_z=z, short_shares=ser[-1]["short_vol"], est_short_total=ser[-1]["est_short_total"],
                   slope_dir="RISING" if sl14 > 0.3 else "FALLING" if sl14 < -0.3 else "FLAT")
    else:
        out.update(short_pct_latest=None, slope_dir=None, anomaly_z=None, short_slope=None)

    # Borrow
    fee = borrow.get("fee") if borrow else None
    avail = borrow.get("available") if borrow else None
    capped = bool(borrow.get("available_capped")) if borrow else False
    prev = borrow_hist[-2] if len(borrow_hist) >= 2 else None
    fee_d = pct(fee, prev.get("fee")) if (prev and fee is not None) else None
    avail_d = None if capped or not prev or prev.get("capped") else pct(avail, prev.get("available"))
    out.update(ctb_fee=fee, ctb_delta_pct=fee_d, shares_available=avail, available_capped=capped,
               avail_delta_pct=avail_d, borrow_source=borrow.get("source") if borrow else None,
               borrow_series=borrow_hist[-20:], rebate=borrow.get("rebate") if borrow else None)

    # Short interest
    sio = None
    if si and si.get("rows"):
        rows = si["rows"]
        a = rows[0]
        b = rows[1] if len(rows) > 1 else None
        fs = flt.get("float_shares")
        sio = {"settlement_date": a["settlement_date"], "shares": a["shares"],
               "prev_shares": b["shares"] if b else None,
               "change_pct": pct(a["shares"], b["shares"]) if b else None,
               "days_to_cover": a.get("days_to_cover"),
               "pct_float": (a["shares"] / fs * 100) if (fs and a.get("shares")) else None,
               "source": si.get("source")}
    out["short_interest"] = sio

    # ── Case 분류 ──
    rising = out.get("slope_dir") == "RISING" or (out.get("anomaly_z") or 0) > 1.0
    falling = out.get("slope_dir") == "FALLING"
    ctb_rising = (fee_d or 0) > 5
    ctb_known = fee_d is not None
    ctb_flat = fee_d is None or abs(fee_d) <= 5
    ctb_note = "대차 수수료 변화 없음" if ctb_known else ("대차 수수료 변화 이력 부족" if fee is not None else "대차 수수료 미수집(잠정 판단)")
    supply_tight = (avail_d or 0) < -5
    l1s = l1.get("score") or 0
    case, case_basis = "MIXED", []
    if rising and (ctb_rising or supply_tight) and ((put_oi_trend or 0) > 3 or not opt_vol_ratio or opt_vol_ratio < 1.3):
        case = "CASE_2_DIRECTIONAL_SHORT"
        case_basis = ["공매도 비중 상승"] + ([f"대차 수수료 {fee_d:+.1f}%"] if ctb_rising else []) + \
                     ([f"대여 가능 물량 {avail_d:+.1f}%"] if supply_tight else [])
    elif rising and (opt_vol_ratio or 0) >= 1.3 and ctb_flat:
        case = "CASE_1_MM_DELTA_HEDGE"
        case_basis = [f"옵션 거래량 평소의 {opt_vol_ratio:.1f}배", ctb_note]
    elif (out.get("short_pct_latest") or 0) >= (out.get("short_avg_14d") or 999) and l1s > 0.2 \
            and (l1.get("dp_pct") or 0) > 40:
        case = "CASE_3_SYNTHETIC_HEDGE"
        case_basis = ["공매도 비중이 14일 평균 이상", "기관 순매수 흐름", "다크풀 비중 40% 이상"]
    elif rising and ctb_flat:
        case = "CASE_1_MM_DELTA_HEDGE"
        case_basis = ["공매도 비중 상승", ctb_note, "옵션 거래량 이력 부족" if opt_vol_ratio is None else f"옵션 거래량 평소의 {opt_vol_ratio:.1f}배"]
    elif falling:
        case = "SHORT_COVERING"
        case_basis = ["공매도 비중 추세 하락"]
    out.update(case=case, case_basis=case_basis)

    # ── 시나리오/점수 ──
    pct_float = (sio or {}).get("pct_float") or 0
    dtc = (sio or {}).get("days_to_cover") or 0
    s = 0.0
    if fee is not None and (fee > 15 or (fee > 5 and ctb_rising)) and (pct_float > 10 or dtc > 4):
        scen, s = "SHORT_SQUEEZE_RISK", 0.55
    elif case == "CASE_2_DIRECTIONAL_SHORT":
        scen, s = "DIRECTIONAL_SHORT", -0.55
    elif case == "CASE_3_SYNTHETIC_HEDGE":
        scen, s = "SYNTHETIC_HEDGE", 0.10
    elif case == "CASE_1_MM_DELTA_HEDGE":
        scen, s = "MM_HEDGE", -0.05
    elif case == "SHORT_COVERING":
        scen, s = "SHORT_COVERING", 0.30
    else:
        scen, s = "NEUTRAL", 0.0
    s -= clamp((out.get("anomaly_z") or 0) / 3, -0.2, 0.2) * 0.5
    sic = (sio or {}).get("change_pct")
    if sic is not None:
        s -= clamp(sic / 20, -0.15, 0.15)
    paradox = fee is not None and fee < 1.0 and avail_d is not None and avail_d < -5
    s = clamp(s)
    conf = 0.35 + (0.35 if fee is not None else 0) + (0.15 if sio else 0) + (0.15 if ser else 0)
    out.update(scenario=scen, score=s, confidence=min(conf, 1.0), low_ctb_paradox=paradox,
               signal="BULLISH" if s > 0.2 else "BEARISH" if s < -0.2 else "NEUTRAL",
               htb_class=None if fee is None else ("ETB" if fee < 1 else "MODERATE" if fee < 5 else
                                                  "HTB" if fee < 15 else "EXTREME" if fee < 50 else "CRITICAL"))
    return out


# ─── L3 Options ─────────────────────────────────────────────────────────────


def _third_friday(y: int, m: int) -> date:
    d = date(y, m, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def max_pain(calls: List[Dict], puts: List[Dict], spot: float, band: float = 0.30) -> Optional[float]:
    lo, hi = spot * (1 - band), spot * (1 + band)
    c_oi = {c["strike"]: c["oi"] for c in calls if c["oi"] > 0}
    p_oi = {p["strike"]: p["oi"] for p in puts if p["oi"] > 0}
    allk = set(c_oi) | set(p_oi)
    # ATM 근방 기준 + 저가주(행사가 간격이 넓은 종목) 대비 최소 8개 근접 행사가 보장
    strikes = sorted({k for k in allk if lo <= k <= hi} | set(sorted(allk, key=lambda k: abs(k - spot))[:8]))
    if len(strikes) < 3:
        return None
    all_c = sorted(c_oi.items())
    all_p = sorted(p_oi.items())
    best, best_k = None, None
    for K in strikes:
        pain = sum(oi * (K - s) for s, oi in all_c if s < K) + sum(oi * (s - K) for s, oi in all_p if s > K)
        if best is None or pain < best:
            best, best_k = pain, K
    return best_k


def _atm(opts: List[Dict], spot: float) -> Optional[Dict]:
    v = [o for o in opts if o.get("iv", 0) > 0 or o.get("bid", 0) > 0]
    return min(v, key=lambda o: abs(o["strike"] - spot)) if v else None


def analyze_l3(chain: Dict, spot: float, session: date, opt_hist: List[Dict],
               earnings: Optional[date]) -> Dict[str, Any]:
    by = chain.get("by_expiry") or {}
    quote = chain.get("quote") or {}

    def dte(e: str) -> int:
        return (cal.parse_date(e) - session).days

    def oi_tot(c):
        return sum(o["oi"] for o in c["calls"]) + sum(o["oi"] for o in c["puts"])

    live = {e: c for e, c in by.items() if dte(e) >= 1}
    out: Dict[str, Any] = {"source": chain.get("source")}
    if not live:
        out.update(signal="NEUTRAL", score=0.0, confidence=0.0, scenario="NO_DATA")
        return out
    exps = sorted(live)
    liquid = [e for e in exps if oi_tot(live[e]) >= 100]
    primary = liquid[0] if liquid else max(exps, key=lambda e: oi_tot(live[e]))
    pc = live[primary]
    def _std_monthly(e: str) -> bool:
        d = cal.parse_date(e)
        tf = _third_friday(d.year, d.month)
        return d == (tf if cal.is_trading_day(tf) else cal.prev_trading_day(tf))
    monthly_candidates = [e for e in exps if _std_monthly(e)]
    monthly = monthly_candidates[0] if monthly_candidates else None

    # Greeks 보강 (CBOE gamma 없으면 BS)
    for e, c in live.items():
        T = max(dte(e), 0.5) / 365.0
        for side, is_call in (("calls", True), ("puts", False)):
            for o in c[side]:
                if not o.get("gamma") and o.get("iv", 0) > 0:
                    o["delta"], o["gamma"] = bs_greeks(spot, o["strike"], T, o["iv"], is_call)

    window = [e for e in exps if dte(e) <= 60]
    gex: Dict[float, float] = {}
    gex_w: Dict[float, float] = {}
    call_oi_k: Dict[float, float] = {}
    put_oi_k: Dict[float, float] = {}
    tc_oi = tp_oi = tc_vol = tp_vol = 0.0
    for e in window:
        c = live[e]
        for side, sign in (("calls", 1), ("puts", -1)):
            for o in c[side]:
                k = o["strike"]
                g = sign * o["oi"] * (o.get("gamma") or 0) * 100 * spot * spot * 0.01
                gex[k] = gex.get(k, 0.0) + g
                gex_w[k] = gex_w.get(k, 0.0) + g * dist_weight(k, spot)
                if sign > 0:
                    call_oi_k[k] = call_oi_k.get(k, 0.0) + o["oi"]
                    tc_oi += o["oi"]
                    tc_vol += o["volume"]
                else:
                    put_oi_k[k] = put_oi_k.get(k, 0.0) + o["oi"]
                    tp_oi += o["oi"]
                    tp_vol += o["volume"]
    net_gex = sum(gex.values())

    # Gamma profile → flip (가상 spot 그리드에서 총 GEX 가 0 을 지나는 지점)
    opts = []
    for e in window:
        T = max(dte(e), 0.5) / 365.0
        for side, is_call in (("calls", True), ("puts", False)):
            for o in live[e][side]:
                if o["oi"] > 0 and o.get("iv", 0) > 0:
                    opts.append((o["strike"], T, o["iv"], o["oi"], 1 if is_call else -1))
    flip, regime, profile, flip_status = None, None, [], None
    if opts and spot > 0:
        K = np.array([x[0] for x in opts]); T = np.array([x[1] for x in opts]); IV = np.array([x[2] for x in opts])
        OI = np.array([x[3] for x in opts]); SG = np.array([x[4] for x in opts])
        grid = np.linspace(spot * 0.5, spot * 1.5, 401)       # ±50% (저가주·넓은 행사가 간격 대응)
        vals = []
        for S in grid:
            sq = IV * np.sqrt(T)
            d1 = (np.log(S / K) + 0.5 * IV * IV * T) / sq
            gam = np.exp(-0.5 * d1 * d1) / np.sqrt(2 * np.pi) / (S * sq)
            vals.append(float((SG * OI * gam * 100 * S * S * 0.01).sum()))
        near = [i for i, g in enumerate(grid) if spot * 0.8 <= g <= spot * 1.2]   # 차트는 ±20% 구간
        profile = [{"s": float(grid[i]), "g": vals[i]} for i in near[::4]]
        crosses = [float(grid[i] - vals[i] * (grid[i + 1] - grid[i]) / (vals[i + 1] - vals[i]))
                   for i in range(len(grid) - 1) if vals[i] * vals[i + 1] < 0]
        if crosses:
            flip = min(crosses, key=lambda x: abs(x - spot))
        mid = int(np.argmin(np.abs(grid - spot)))
        regime = "POSITIVE" if vals[mid] > 0 else "NEGATIVE"
        if flip is None:
            flip_status = "NO_FLIP_ALL_POSITIVE" if all(v > 0 for v in vals) else \
                          "NO_FLIP_ALL_NEGATIVE" if all(v < 0 for v in vals) else "NO_FLIP_IN_RANGE"
        else:
            flip_status = "FOUND" if abs(flip - spot) / spot <= 0.2 else "FOUND_FAR"

    # 콜월/풋월: 감마 노출 기준 (원거리 잔존 OI 배제), 현재가 ±20% 밴드
    call_gex_k: Dict[float, float] = {}
    put_gex_k: Dict[float, float] = {}
    for e in window:
        for o in live[e]["calls"]:
            call_gex_k[o["strike"]] = call_gex_k.get(o["strike"], 0.0) + o["oi"] * (o.get("gamma") or 0)
        for o in live[e]["puts"]:
            put_gex_k[o["strike"]] = put_gex_k.get(o["strike"], 0.0) + o["oi"] * (o.get("gamma") or 0)
    above = {k: v for k, v in call_gex_k.items() if spot <= k <= spot * 1.2 and v > 0}
    below = {k: v for k, v in put_gex_k.items() if spot * 0.8 <= k <= spot and v > 0}
    call_wall = max(above, key=above.get) if above else None
    put_wall = max(below, key=below.get) if below else None

    mp = max_pain(pc["calls"], pc["puts"], spot)
    mp_m = max_pain(live[monthly]["calls"], live[monthly]["puts"], spot) if monthly else None

    # IV / skew / term
    def exp_near(days: int) -> Optional[str]:
        cand = [e for e in exps if dte(e) >= 5 and oi_tot(live[e]) >= 50]
        return min(cand, key=lambda e: abs(dte(e) - days)) if cand else None
    e30, e60 = exp_near(30), exp_near(60)

    def atm_iv(e: Optional[str]) -> Optional[float]:
        if not e:
            return None
        ca, pa = _atm(live[e]["calls"], spot), _atm(live[e]["puts"], spot)
        ivs = [x["iv"] for x in (ca, pa) if x and x.get("iv", 0) > 0]
        return float(np.mean(ivs)) if ivs else None
    iv_front = atm_iv(exp_near(7))
    iv30 = atm_iv(e30)
    iv60 = atm_iv(e60) if e60 != e30 else None
    term = None
    if iv_front and iv60:
        term = "BACKWARDATION" if iv_front > iv60 * 1.05 else "CONTANGO" if iv_front < iv60 * 0.95 else "FLAT"

    skew25 = None
    if e30:
        cs = [o for o in live[e30]["calls"] if o.get("iv", 0) > 0 and o.get("delta")]
        ps = [o for o in live[e30]["puts"] if o.get("iv", 0) > 0 and o.get("delta")]
        if cs and ps:
            c25 = min(cs, key=lambda o: abs(o["delta"] - 0.25))
            p25 = min(ps, key=lambda o: abs(o["delta"] + 0.25))
            skew25 = (p25["iv"] - c25["iv"]) * 100
    skew10 = None
    if pc["calls"] and pc["puts"]:
        p10 = min(pc["puts"], key=lambda o: abs(o["strike"] - spot * 0.9))
        c10 = min(pc["calls"], key=lambda o: abs(o["strike"] - spot * 1.1))
        if p10.get("iv") and c10.get("iv"):
            skew10 = p10["iv"] - c10["iv"]

    # Expected move (ATM straddle mid)
    def straddle(e: Optional[str]) -> Optional[float]:
        if not e:
            return None
        ca, pa = _atm(live[e]["calls"], spot), _atm(live[e]["puts"], spot)
        if not ca or not pa:
            return None
        mid = lambda o: (o["bid"] + o["ask"]) / 2 if o["ask"] > 0 else o.get("last") or 0
        v = mid(ca) + mid(pa)
        return v if v > 0 else None
    em = straddle(primary)
    em_m = straddle(monthly)
    atm_spread = None
    ca, pa = _atm(pc["calls"], spot), _atm(pc["puts"], spot)
    if ca and pa and ca["ask"] > 0 and pa["ask"] > 0:
        atm_spread = ((ca["ask"] - ca["bid"]) + (pa["ask"] - pa["bid"])) / 2

    # Binary event (OTM IV 급등)
    # 바이너리 이벤트: (a) 30D 만기 OTM 10–25% 구간 IV 가 ATM×1.6 초과 (유동성 있는 행사가)
    #                 (b) 단기물 ATM IV 가 차월물 대비 25%+ 높은 기간구조 꺾임 (이벤트 프리미엄)
    binary, binary_basis = False, []
    if e30 and iv30:
        for o in live[e30]["calls"] + live[e30]["puts"]:
            mny = abs(o["strike"] - spot) / spot
            if 0.10 < mny <= 0.25 and o["oi"] >= 500 and o.get("bid", 0) > 0 and o.get("iv", 0) > iv30 * 1.6:
                binary = True
                binary_basis.append(f"외가격 {o['strike']:g} 행사가 IV {o['iv']*100:.0f}%로 ATM({iv30*100:.0f}%)보다 크게 높음")
                break
    if iv_front and iv60 and iv_front > iv60 * 1.25:
        binary = True
        binary_basis.append(f"단기 만기 IV {iv_front*100:.0f}%가 차월({iv60*100:.0f}%)보다 크게 높음")

    # P/C, 만기일 보정
    pc_oi = tp_oi / tc_oi if tc_oi else None
    pc_vol = tp_vol / tc_vol if tc_vol else None
    pc_vol_adj = pc_vol
    if dte(primary) <= 1 and len(window) > 1:
        cv = sum(o["volume"] for e in window[1:] for o in live[e]["calls"])
        pv = sum(o["volume"] for e in window[1:] for o in live[e]["puts"])
        pc_vol_adj = pv / cv if cv else pc_vol
    total_vol = tc_vol + tp_vol
    oi_total = tc_oi + tp_oi

    # 히스토리 기반: OI 추세, 옵션 거래량 배수, 핀닝 사후검증
    oi_trend = put_oi_trend = vol_ratio = None
    if opt_hist:
        past = [h for h in opt_hist if h.get("date") < session.isoformat()]
        if len(past) >= 1:
            ref = past[-5] if len(past) >= 5 else past[0]
            oi_trend = pct(oi_total, ref.get("oi_total"))
            put_oi_trend = pct(tp_oi, ref.get("put_oi"))
            vavg = mean([h.get("total_vol") for h in past[-20:]])
            vol_ratio = total_vol / vavg if vavg else None
    pin = None
    for h in reversed(opt_hist or []):
        mpx = (h.get("max_pain_by_exp") or {}).get(session.isoformat())
        if mpx:
            d = abs(spot - mpx) / mpx * 100
            pin = {"expiry": session.isoformat(), "max_pain": mpx, "dist_pct": d,
                   "status": "PINNING_SUCCESS" if d <= 0.1 else "PINNING_ATTEMPT" if d <= 0.5 else "BREAKAWAY"}
            break
    near = [k for k in set(call_oi_k) | set(put_oi_k) if abs(k - spot) / spot <= 0.10]
    top_strike = max(near, key=lambda k: call_oi_k.get(k, 0) + put_oi_k.get(k, 0)) if near else None

    # ── 점수 ──
    s = 0.0
    pv_ = pc_vol_adj if pc_vol_adj is not None else pc_vol
    if pv_ is not None:
        s += clamp((0.9 - pv_) / 0.6) * 0.30
    if skew25 is not None:
        s += clamp((4 - skew25) / 8) * 0.15
    if flip:
        s += clamp((spot - flip) / spot / 0.03) * 0.20
    elif regime:
        s += 0.10 if regime == "POSITIVE" else -0.10
    if mp and dte(primary) <= 7:
        s += clamp((mp - spot) / spot / 0.03) * 0.20
    if call_wall and put_wall:
        up, dn = (call_wall - spot) / spot, (spot - put_wall) / spot
        s += clamp((up - dn) / 0.10) * 0.15
    s = clamp(s)

    dte_p = dte(primary)
    if dte_p <= 5 and mp and abs(spot - mp) / mp < 0.005 and (net_gex > 0):
        scen = "PINNING"
    elif (pc_oi or 9) < 0.7 and (pv_ or 9) < 0.7 and (skew25 is None or skew25 < 3):
        scen = "GAMMA_SQUEEZE"
    elif (flip and spot < flip) or (regime == "NEGATIVE" and not flip):
        scen = "VOLATILITY_EXPANSION"
    elif (pc_oi or 0) > 1.2 or (skew25 or 0) > 6:
        scen = "HEDGING"
    else:
        scen = "NEUTRAL"
    conf = 0.4 + (0.3 if chain.get("source") == "cboe_delayed" else 0) + (0.15 if flip or regime else 0) \
        + (0.15 if skew25 is not None else 0)
    out.update(expiry=primary, dte=dte_p, monthly_expiry=monthly, all_expiries=exps[:12],
               max_pain=mp, max_pain_dist_pct=pct(spot, mp), monthly_max_pain=mp_m,
               pc_oi=pc_oi, pc_vol=pc_vol, pc_vol_adj=pc_vol_adj, skew=skew10, skew_25d=skew25,
               atm_iv=iv30 or iv_front, iv_front=iv_front, iv60=iv60,
               iv30=(quote.get("iv30") / 100) if quote.get("iv30") else None,
               term_structure=term, expected_move=em, expected_move_pct=(em / spot * 100) if em else None,
               monthly_expected_move=em_m, atm_spread=atm_spread, binary_event=binary,
               binary_basis=binary_basis,
               gex_by_strike=gex, gex_weighted=gex_w, net_gex=net_gex, net_gex_weighted=sum(gex_w.values()),
               flip_zone=flip, flip_status=flip_status, gamma_regime=regime, gamma_profile=profile,
               call_wall=call_wall, put_wall=put_wall, top_oi_strike=top_strike,
               call_oi=tc_oi, put_oi=tp_oi, oi_total=oi_total, total_volume=total_vol,
               oi_trend_pct=oi_trend, put_oi_trend_pct=put_oi_trend, opt_vol_ratio=vol_ratio, pinning=pin,
               earnings_in_window=bool(earnings and session < earnings <= cal.parse_date(primary)),
               score=s, confidence=min(conf, 1.0), scenario=scen,
               signal="BULLISH" if s > 0.2 else "BEARISH" if s < -0.2 else "NEUTRAL",
               max_pain_by_exp={e: max_pain(live[e]["calls"], live[e]["puts"], spot) for e in exps[:4]},
               chain_primary={"calls": pc["calls"], "puts": pc["puts"]}, spot=spot)
    return out


# ─── L4 Chart ───────────────────────────────────────────────────────────────


def analyze_l4(ohlcv: List[Dict]) -> Dict[str, Any]:
    closes = [c["close"] for c in ohlcv]
    vols = [c["volume"] for c in ohlcv]
    n = len(closes)
    if n < 25:
        return {"signal": "NEUTRAL", "score": 0.0, "confidence": 0.0, "scenario": "NO_DATA"}
    close = closes[-1]
    sma = lambda k: float(np.mean(closes[-k:])) if n >= k else None
    sma20, sma50, sma200 = sma(20), sma(50), sma(200)
    e9, e21 = ema(closes, 9)[-1], ema(closes, 21)[-1]
    rsi = rsi_wilder(closes[-120:])
    a14 = atr(ohlcv[-60:])

    def bb_at(i: int) -> Tuple[float, float, float]:
        w = closes[i - 19:i + 1]
        m, sd = float(np.mean(w)), float(np.std(w))
        return m + 2 * sd, m, m - 2 * sd
    up, mid, lo = bb_at(n - 1)
    width = (up - lo) / mid * 100
    widths = [(lambda u, m, l: (u - l) / m * 100)(*bb_at(i)) for i in range(max(19, n - 252), n)]
    pctile = float(sum(1 for w in widths if w <= width) / len(widths) * 100) if widths else None
    prev_w = float(np.mean(widths[-6:-1])) if len(widths) > 6 else width
    width_trend = "EXPANDING" if width > prev_w * 1.1 else "CONTRACTING" if width < prev_w * 0.9 else "NEUTRAL"
    pos = (close - lo) / (up - lo) if up > lo else 0.5
    band = ("UPPER_TOUCH" if close >= up else "LOWER_TOUCH" if close <= lo else "UPPER_ZONE" if pos > 0.8
            else "LOWER_ZONE" if pos < 0.2 else "MID_ZONE")

    if sma200 and sma50:
        if close > sma20 > sma50 > sma200:
            ma = "FULL_BULL"
        elif close < sma20 < sma50 < sma200:
            ma = "FULL_BEAR"
        elif close > sma20 > sma50 and close < sma200:
            ma = "RECOVERING"
        elif close < sma20 < sma50 and close > sma200:
            ma = "WEAKENING"
        else:
            ma = "MIXED"
    else:
        ma = "INSUFFICIENT_DATA"

    # S/R: 단계적 탐색 — 90일 스윙 피벗 → 1년 스윙 피벗 → POC·이동평균 → 52주 고/저
    def pivots(win):
        hs_, ls_ = [], []
        for i in range(2, len(win) - 2):
            if win[i]["high"] == max(w["high"] for w in win[i - 2:i + 3]):
                hs_.append(win[i]["high"])
            if win[i]["low"] == min(w["low"] for w in win[i - 2:i + 3]):
                ls_.append(win[i]["low"])
        return cluster_prices(hs_ + ls_, 0.8)
    vp = {}
    lo60, hi60 = min(c["low"] for c in ohlcv[-60:]), max(c["high"] for c in ohlcv[-60:])
    step = (hi60 - lo60) / 30 if hi60 > lo60 else 1
    for c in ohlcv[-60:]:
        b = int(((c["high"] + c["low"] + c["close"]) / 3 - lo60) / step)
        vp[b] = vp.get(b, 0) + c["volume"]
    poc = lo60 + (max(vp, key=vp.get) + 0.5) * step if vp else None
    hi52 = max(c["high"] for c in ohlcv[-252:])
    lo52 = min(c["low"] for c in ohlcv[-252:])
    up_b, dn_b = close * 1.002, close * 0.998
    res, sup, res_src, sup_src = [], [], [], []
    for label, lv in (("90d 스윙", pivots(ohlcv[-90:])), ("1y 스윙", pivots(ohlcv[-252:]))):
        r_ = sorted(p for p, _ in lv if p > up_b)
        s_ = sorted((p for p, _ in lv if p < dn_b), reverse=True)
        for x in r_:
            if len(res) < 2 and all(abs(x - y) / y > 0.008 for y in res):
                res.append(x); res_src.append(label)
        for x in s_:
            if len(sup) < 2 and all(abs(x - y) / y > 0.008 for y in sup):
                sup.append(x); sup_src.append(label)
    extra = [("거래량 POC(60d)", poc), ("SMA50", sma50), ("SMA200", sma200)]
    for label, v in extra + [("52주 저점", lo52)]:
        if v and v < dn_b and len(sup) < 2 and all(abs(v - y) / y > 0.008 for y in sup):
            sup.append(v); sup_src.append(label)
    for label, v in extra + [("52주 고점", hi52)]:
        if v and v > up_b and len(res) < 2 and all(abs(v - y) / y > 0.008 for y in res):
            res.append(v); res_src.append(label)
    pairs_s = sorted(zip(sup, sup_src), key=lambda t: -t[0])
    pairs_r = sorted(zip(res, res_src), key=lambda t: t[0])
    sup, sup_src = [a for a, _ in pairs_s], [b for _, b in pairs_s]
    res, res_src = [a for a, _ in pairs_r], [b for _, b in pairs_r]

    sl5 = slope(closes[-5:]) / close * 100
    sl20 = slope(closes[-20:]) / close * 100
    vol30 = float(np.mean(vols[-31:-1])) if n > 31 else float(np.mean(vols))
    vratio = vols[-1] / vol30 if vol30 else None

    s = {"FULL_BULL": 0.4, "RECOVERING": 0.15, "MIXED": 0.0, "WEAKENING": -0.15, "FULL_BEAR": -0.4}.get(ma, 0.0)
    s += clamp(sl20 / 0.5) * 0.25 + clamp(sl5 / 1.0) * 0.15
    if sma50:
        s += 0.1 if close > sma50 else -0.1
    if rsi is not None:
        s -= clamp((rsi - 50) / 30) * 0.10 if (rsi > 75 or rsi < 25) else 0
    s = clamp(s)
    if sl5 > 0 and sl20 > 0 and ma in ("FULL_BULL", "RECOVERING"):
        scen = "UPTREND"
    elif sl5 < 0 and sl20 < 0 and ma in ("FULL_BEAR", "WEAKENING"):
        scen = "DOWNTREND"
    elif (pctile or 100) <= 20 and abs(sl20) < 0.15:
        scen = "BREAKOUT_PENDING" if close >= mid else "BREAKDOWN_PENDING"
    else:
        scen = "RANGE_BOUND"
    return {
        "current_price": close, "prev_close": closes[-2], "change_pct": pct(close, closes[-2]),
        "sma20": sma20, "sma50": sma50, "sma200": sma200, "ema9": e9, "ema21": e21, "rsi14": rsi,
        "atr14": a14, "atr_pct": (a14 / close * 100) if a14 else None,
        "bb_upper": up, "bb_mid": mid, "bb_lower": lo, "bb_width_pct": width, "bb_width_pctile": pctile,
        "bb_width_trend": width_trend, "bb_position": pos, "band_status": band, "ma_alignment": ma,
        "immediate_resistance": res[0] if res else None, "key_resistance": res[1] if len(res) > 1 else None,
        "immediate_support": sup[0] if sup else None, "key_support": sup[1] if len(sup) > 1 else None,
        "resistance_source": res_src[:2], "support_source": sup_src[:2],
        "below_all_swing_lows": not any(x in ("90d 스윙", "1y 스윙") for x in sup_src[:1]),
        "poc60": poc, "slope5_pct": sl5, "slope20_pct": sl20, "vol_ratio30": vratio, "vol_avg30": vol30,
        "high_52w": max(c["high"] for c in ohlcv[-252:]), "low_52w": min(c["low"] for c in ohlcv[-252:]),
        "score": s, "confidence": 0.8 if sma200 else 0.5, "scenario": scen,
        "signal": "BULLISH" if s > 0.2 else "BEARISH" if s < -0.2 else "NEUTRAL",
    }


# ─── 패턴 ────────────────────────────────────────────────────────────────────


def detect_patterns(ohlcv: List[Dict], minute: List[Dict], l1: Dict, l2: Dict, l3: Dict, l4: Dict,
                    flow: Optional[List[Dict]] = None) -> Dict[str, Any]:
    found: List[str] = []
    detail: Dict[str, Any] = {}
    vols = [c["volume"] for c in ohlcv]

    # Theta Burn / L자 횡보: 거래량 ≤ 30일 평균 60% + 일중 변동폭 < (ATM 스트래들 일환산)×50% + 기관흐름 정체, 3일 연속
    if len(ohlcv) > 35:
        vol30 = float(np.mean(vols[-33:-3]))
        em, dte_ = l3.get("expected_move"), max(l3.get("dte") or 1, 1)
        daily_em = em / math.sqrt(dte_) if em else None
        cum = l1.get("cumulative_daily") or []
        day_abs = [abs(c["day"]) for c in cum]
        flat_flow = bool(day_abs) and max(day_abs[-3:]) < 0.25 * (l1.get("avg_vol20") or 1) * 0.4
        conds = []
        for c in ohlcv[-3:]:
            rng = c["high"] - c["low"]
            conds.append(c["volume"] < vol30 * 0.6 and (daily_em is None or rng < daily_em * 0.5))
        detail["theta_burn"] = {"low_vol_days": sum(conds), "flat_inst_flow": flat_flow, "daily_em": daily_em}
        if all(conds) and flat_flow:
            found.append("THETA_BURN")
        if (l4.get("vol_ratio30") or 0) > 1.5 and l4.get("bb_width_trend") == "EXPANDING" and \
                any(conds[:-1]):
            found.append("THETA_BURN_BREAKOUT")

    # Final Absorption (하락 추세 끝단에서 5개 중 ≥3)
    downtrend = (l4.get("slope20_pct") or 0) < 0 or (l4.get("slope5_pct") or 0) < 0
    fa = {}
    by_day: Dict[str, List[Dict]] = {}
    for b in minute:
        by_day.setdefault(b["date"], []).append(b)
    close_heavy_days, absorbed_days = 0, 0
    for d in sorted(by_day)[-3:]:
        bars = by_day[d]
        if len(bars) < 120:
            continue
        tot = sum(b["v"] for b in bars)
        last30 = bars[-30:]
        v30 = sum(b["v"] for b in last30)
        if tot and v30 / tot > 0.16:          # 정상 ~8-11% → 2배 집중
            close_heavy_days += 1
            mv = abs(last30[-1]["c"] - last30[0]["o"]) / last30[0]["o"] * 100 if last30[0]["o"] else 9
            if mv < 0.3:
                absorbed_days += 1
    if flow:
        # 실제 블록 체결 기준: 마지막 30분에 정규장 블록 거래량의 50% 이상이 몰린 날 (2일 연속)
        heavy = [(r.get("last30_block_share") or 0) > 0.5 for r in flow[-3:]]
        consec = any(heavy[i] and heavy[i + 1] for i in range(len(heavy) - 1))
        fa["c1_close_block_concentration"] = consec
        fa["c2_no_price_slip"] = any(h and abs(r.get("last30_move_pct") or 9) < 0.3
                                     for h, r in zip(heavy, flow[-3:]))
        fa["basis"] = "trade-level"
    else:
        fa["c1_close_block_concentration"] = close_heavy_days >= 2
        fa["c2_no_price_slip"] = absorbed_days >= 1 and close_heavy_days >= 1
        fa["basis"] = "minute-bar proxy"
    fa["c3_dp_over_40_2d"] = (l1.get("dp_days_over_40") or 0) >= 2
    cd = l2.get("ctb_delta_pct")
    fa["c4_ctb_stable_or_down"] = None if l2.get("ctb_fee") is None else (cd is None or cd <= 5)
    fa["c5_short_pct_falling"] = (l2.get("short_slope5") or 0) < 0
    met = sum(1 for k, v in fa.items() if k.startswith("c") and v is True)
    detail["final_absorption"] = {**fa, "met": met, "downtrend_context": downtrend}
    if downtrend and met >= 3:
        found.append("FINAL_ABSORPTION")

    if l2.get("low_ctb_paradox"):
        found.append("LOW_CTB_PARADOX")
    if l2.get("scenario") == "SHORT_SQUEEZE_RISK":
        found.append("SHORT_SQUEEZE_RISK")
    if l3.get("scenario") == "GAMMA_SQUEEZE":
        found.append("GAMMA_SQUEEZE_SETUP")
    if l3.get("scenario") == "PINNING":
        found.append("OPTION_PINNING")
    if l3.get("binary_event"):
        found.append("BINARY_EVENT_PRICED")
    if (l1.get("off_vol_ratio") or 0) >= 2:
        found.append("DARK_POOL_VOLUME_SPIKE")
    return {"patterns": found, "detail": detail}


# ─── 거시 유동성 ─────────────────────────────────────────────────────────────


def _last(series: List, back: int = 0) -> Optional[float]:
    return series[-1 - back][1] if series and len(series) > back else None


def classify_macro(m: Dict[str, Any]) -> Dict[str, Any]:
    rows, score = [], 0
    walcl, tga, rrp = m.get("walcl") or [], m.get("tga") or [], m.get("rrp") or []
    net_liq = net_liq_chg = None
    if walcl and tga and rrp:
        w, t, r = _last(walcl), _last(tga), _last(rrp)
        net_liq = (w - t) / 1000 - r                      # $B (WALCL/TGA 백만$, RRP 십억$)
        w4, t4 = _last(walcl, 4), _last(tga, 4)
        r4 = _last(rrp, 20)
        if None not in (w4, t4, r4):
            net_liq_chg = net_liq - ((w4 - t4) / 1000 - r4)
            score += 1 if net_liq_chg > 50 else -1 if net_liq_chg < -50 else 0
    rows.append({"name": "Net Liquidity (Fed BS−TGA−RRP)", "value": net_liq, "unit": "$B",
                 "change": net_liq_chg, "status": None if net_liq_chg is None else
                 ("FAVORABLE" if net_liq_chg > 50 else "RESTRICTED" if net_liq_chg < -50 else "NEUTRAL")})
    sofr, iorb = _last(m.get("sofr") or []), _last(m.get("iorb") or [])
    spread = (sofr - iorb) * 100 if (sofr is not None and iorb is not None) else None
    if spread is not None:
        score += -1 if spread > 3 else (1 if spread < -3 else 0)
    rows.append({"name": "SOFR − IORB", "value": spread, "unit": "bp", "change": None,
                 "status": None if spread is None else ("RESTRICTED" if spread > 3 else "NEUTRAL")})
    dff = m.get("dff") or []
    ff, ff60 = _last(dff), _last(dff, 60)
    ffc = (ff - ff60) if (ff is not None and ff60 is not None) else None
    if ffc is not None:
        score += 1 if ffc < -0.1 else -1 if ffc > 0.1 else 0
    rows.append({"name": "Fed Funds (eff.)", "value": ff, "unit": "%", "change": ffc,
                 "status": None if ffc is None else ("FAVORABLE" if ffc < -0.1 else "RESTRICTED" if ffc > 0.1 else "NEUTRAL")})
    rows.append({"name": "RRP", "value": _last(rrp), "unit": "$B", "change": (_last(rrp) - _last(rrp, 20))
                 if len(rrp) > 20 else None, "status": None})
    dxy = m.get("dxy") or []
    dxc = pct(_last(dxy), _last(dxy, 20)) if len(dxy) > 20 else None
    if dxc is not None:
        score += 1 if dxc < -1 else -1 if dxc > 1 else 0
    rows.append({"name": "DXY (20d)", "value": _last(dxy), "unit": "", "change": dxc,
                 "status": None if dxc is None else ("FAVORABLE" if dxc < -1 else "RESTRICTED" if dxc > 1 else "NEUTRAL")})
    vix = _last(m.get("vix") or [])
    if vix is not None:
        score += -1 if vix > 25 else (1 if vix < 15 else 0)
    rows.append({"name": "VIX", "value": vix, "unit": "", "change": None,
                 "status": None if vix is None else ("RESTRICTED" if vix > 25 else "FAVORABLE" if vix < 15 else "NEUTRAL")})
    hy = m.get("hy_oas") or []
    hyc = (_last(hy) - _last(hy, 20)) * 100 if len(hy) > 20 else None
    if hyc is not None:
        score += -1 if hyc > 30 else (1 if hyc < -30 else 0)
    rows.append({"name": "HY OAS (20d)", "value": _last(hy), "unit": "%", "change": hyc,
                 "status": None if hyc is None else ("RESTRICTED" if hyc > 30 else "NEUTRAL")})
    wti = _last(m.get("wti") or [])
    if wti is not None and wti > 90:
        score -= 1
    rows.append({"name": "WTI", "value": wti, "unit": "$", "change": pct(wti, _last(m.get("wti") or [], 20))
                 if len(m.get("wti") or []) > 20 else None,
                 "status": None if wti is None else ("RESTRICTED" if wti > 90 else "NEUTRAL")})
    rows.append({"name": "UST 10Y", "value": _last(m.get("dgs10") or []), "unit": "%",
                 "change": (_last(m["dgs10"]) - _last(m["dgs10"], 20)) * 100 if len(m.get("dgs10") or []) > 20 else None,
                 "status": None})
    available = sum(1 for r in rows if r["value"] is not None)
    env = "FAVORABLE" if score >= 2 else "RESTRICTED" if score <= -2 else "NEUTRAL"
    return {"env": env, "score": score, "rows": rows, "available": available,
            "failed": available == 0, "errors": m.get("errors") or []}


# ─── 시나리오 확률 ────────────────────────────────────────────────────────────

WEIGHTS = {"L1": 0.35, "L2": 0.20, "L3": 0.30, "L4": 0.15}


def scenario_probabilities(layers: Dict[str, Dict], macro: Dict, patterns: List[str]) -> Dict[str, Any]:
    contrib = {}
    raw = 0.0
    for k, w in WEIGHTS.items():
        L = layers.get(k) or {}
        c = (L.get("score") or 0.0) * (L.get("confidence") or 0.0) * w
        contrib[k] = c
        raw += c
    scores = [(layers.get(k) or {}).get("score") or 0 for k in WEIGHTS]
    dispersion = float(np.std(scores))
    conv = min(abs(raw) / 0.6, 1.0)
    p_dir = 0.30 + 0.45 * conv
    p_opp = 0.30 - 0.20 * conv
    p_neu = 1 - p_dir - p_opp + 0.15 * dispersion
    bull, bear = (p_dir, p_opp) if raw >= 0 else (p_opp, p_dir)
    adj = 0.0
    if macro.get("env") == "FAVORABLE":
        adj = 0.05 + 0.025 * min(max(macro.get("score", 0) - 2, 0), 2)
        bull += adj; bear -= adj * 0.7
    elif macro.get("env") == "RESTRICTED":
        adj = -(0.05 + 0.025 * min(max(-macro.get("score", 0) - 2, 0), 2))
        bear += -adj; bull += adj * 0.7
    contrib["macro"] = adj
    pb = 0.0
    if "FINAL_ABSORPTION" in patterns:
        pb += 0.05
    if "GAMMA_SQUEEZE_SETUP" in patterns:
        pb += 0.05
    if "SHORT_SQUEEZE_RISK" in patterns:
        pb += 0.05
    bull += pb
    bear -= pb * 0.6
    if "THETA_BURN" in patterns or "OPTION_PINNING" in patterns:
        p_neu += 0.05
    if "LOW_CTB_PARADOX" in patterns and "FINAL_ABSORPTION" not in patterns:
        bull -= 0.04; p_neu += 0.04
    contrib["patterns"] = pb
    bull, p_neu, bear = max(bull, 0.03), max(p_neu, 0.05), max(bear, 0.03)
    t = bull + p_neu + bear
    probs = [bull / t, p_neu / t, bear / t]
    for i in range(3):                       # 80% 상한 후 재분배
        if probs[i] > 0.80:
            ex = probs[i] - 0.80
            probs[i] = 0.80
            others = [j for j in range(3) if j != i]
            s = sum(probs[j] for j in others)
            for j in others:
                probs[j] += ex * probs[j] / s
    a, b, c = (round(p * 100, 1) for p in probs)
    b = round(100 - a - c, 1)
    return {"A_bullish": a, "B_neutral": b, "C_bearish": c, "raw_score": round(raw, 3),
            "macro": macro.get("env"), "dispersion": round(dispersion, 3),
            "contributions": {k: round(v, 3) for k, v in contrib.items()}, "patterns": patterns}


# ─── 가격 레벨 · Phase ───────────────────────────────────────────────────────


def price_levels(price: float, l3: Dict, l4: Dict) -> List[Dict[str, Any]]:
    em = l3.get("expected_move")
    cands = [
        ("Max Pain", l3.get("max_pain"), "HIGH" if (l3.get("dte") or 99) <= 5 else "MED"),
        ("Monthly Max Pain", l3.get("monthly_max_pain"), "MED"),
        ("GEX Flip", l3.get("flip_zone"), "HIGH"),
        ("Call Wall", l3.get("call_wall"), "HIGH"),
        ("Put Wall", l3.get("put_wall"), "HIGH"),
        ("Expected Move ↑", price + em if em else None, "MED"),
        ("Expected Move ↓", price - em if em else None, "MED"),
        ("Immediate Resistance", l4.get("immediate_resistance"), "HIGH"),
        ("Key Resistance", l4.get("key_resistance"), "MED"),
        ("Immediate Support", l4.get("immediate_support"), "HIGH"),
        ("Key Support", l4.get("key_support"), "MED"),
        ("SMA 50", l4.get("sma50"), "MED"), ("SMA 200", l4.get("sma200"), "MED"),
        ("Volume POC (60d)", l4.get("poc60"), "MED"),
        ("BB Upper", l4.get("bb_upper"), "LOW"), ("BB Lower", l4.get("bb_lower"), "LOW"),
        ("52W High", l4.get("high_52w"), "LOW"), ("52W Low", l4.get("low_52w"), "LOW"),
    ]
    out = [{"type": "Current Price", "price": price, "dist_pct": 0.0, "significance": "—"}]
    for name, p, sig in cands:
        if p:
            out.append({"type": name, "price": float(p), "dist_pct": (p - price) / price * 100, "significance": sig})
    return sorted(out, key=lambda x: -x["price"])


def build_phases(session: date, ohlcv: List[Dict], l1: Dict, l3: Dict, l4: Dict, scen: Dict,
                 events: List[Dict]) -> Dict[str, Any]:
    closes = [c["close"] for c in ohlcv]
    dates = [c["date"] for c in ohlcv]
    # Phase 1 시작: 최근 20거래일 내 SMA20 교차 시점 (레짐 시작)
    start_idx = max(0, len(closes) - 20)
    for i in range(len(closes) - 6, max(len(closes) - 21, 20), -1):   # 최소 5거래일
        s_now = np.mean(closes[i - 19:i + 1]); s_prev = np.mean(closes[i - 20:i])
        if (closes[i] - s_now) * (closes[i - 1] - s_prev) < 0:
            start_idx = i
            break
    p1_start = dates[start_idx]
    p1_end = dates[-2] if len(dates) > 1 else dates[-1]
    nxt = cal.next_trading_day(session)
    p2_end = cal.next_trading_day(session, 5)
    pe = l3.get("expiry")
    if pe:
        ped = cal.parse_date(pe)
        if nxt <= ped <= cal.next_trading_day(session, 7):
            p2_end = ped if cal.is_trading_day(ped) else cal.prev_trading_day(ped)
    p3_start = cal.next_trading_day(p2_end)
    p3_end = cal.next_trading_day(p2_end, 15)

    price = closes[-1]
    A, B, C = scen["A_bullish"], scen["B_neutral"], scen["C_bearish"]
    em_m = l3.get("monthly_expected_move") or (l4.get("atr14") or price * 0.02) * math.sqrt(15)
    ups = [v for v in (l3.get("call_wall"), l4.get("immediate_resistance"), l4.get("key_resistance"))
           if v and v > price * 1.003]
    dns = [v for v in (l3.get("put_wall"), l4.get("immediate_support"), l4.get("key_support"))
           if v and v < price * 0.997]
    t_up1 = min(ups) if ups else price + em_m * 0.5
    t_up2 = max(max(ups) if ups else 0, price + em_m)
    t_dn1 = max(dns) if dns else price - em_m * 0.5
    t_dn2 = min(min(dns) if dns else 1e18, price - em_m)
    if A >= max(B, C):
        direction = "UPWARD"
    elif C >= max(A, B):
        direction = "DOWNWARD"
    else:
        direction = "RANGE"
    catalysts = [e for e in events if p3_start.isoformat() <= e["date"] <= p3_end.isoformat()
                 or nxt.isoformat() <= e["date"] <= p2_end.isoformat()]
    return {
        "p1_date": f"{p1_start} ~ {p1_end}", "p1_start": p1_start, "p1_end": p1_end,
        "p2_date": f"{session.isoformat()} ~ {p2_end.isoformat()}", "p2_end": p2_end.isoformat(),
        "p3_date": f"{p3_start.isoformat()} ~ {p3_end.isoformat()}",
        "p3_direction": direction,
        "p3_target_hi": t_up1 if direction != "DOWNWARD" else t_up1,
        "p3_target_hi2": t_up2, "p3_target_lo": t_dn1, "p3_target_lo2": t_dn2,
        "p3_bb_width": l4.get("bb_width_pct"),
        "p1_return_pct": pct(price, closes[start_idx]),
        "catalysts": catalysts[:4],
    }
