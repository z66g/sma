"""적정가치(Fair Value) 산출 엔진.

파이프라인:  재무 스냅샷(fundamentals) → ① 파생 지표 → ② 종목 성격 분류(archetype)
            → ③ 자본비용(WACC) → ④ 모델별 가치 산출(적용 가능성·검증 체크 포함)
            → ⑤ 성격별 가중 + 신뢰도 + 이상치 축소로 통합 → ⑥ 불확실성·판정
            + 진단: 역(逆) DCF(시장이 반영한 성장률) · 몬테카를로 분포 · 민감도 격자

모델 카탈로그 (Finbox 계열 + 확장):
  DCF 5Y/10Y × {영구성장(Gordon), EBITDA Exit, Revenue Exit} · DDM(안정/다단계) · EPV · 초과이익(RIM)
  · P/E · EV/EBITDA · EV/Revenue · P/S · P/B(ROE 보정) · P/FFO(REIT) · 피터 린치 · 그레이엄

설계 원칙:
  · 종목 성격에 맞지 않는 모델은 '미적용'으로 제외하고 이유를 남긴다 (예: 무배당 → DDM 제외, 은행 → FCFF DCF 제외).
  · 모든 가정은 데이터에서 유도하고(컨센서스·과거 실적·peer), 출력에 그대로 노출한다. 결측은 만들어 내지 않는다.
  · 결정적(deterministic): 몬테카를로 시드는 티커에서 유도한다 → 같은 입력이면 같은 출력.
"""
from __future__ import annotations

import json
import math
import os
import zlib
from datetime import date, timedelta
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

VERSION = 1
ERP_DEFAULT = 0.042                                        # 내재 ERP 를 못 읽었을 때의 값 (2025-11~2026-11 월별 내재 ERP 평균)
ERP = float(os.environ.get("SMA_ERP") or ERP_DEFAULT)      # 테스트·수집 실패 시 기본값. 실제 평가는 market_erp() 의 현재 내재 ERP
ERP_URL = "https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx"
ERP_TTL_DAYS = 10
EXIT_BLEND = float(os.environ.get("SMA_VAL_EXIT_BLEND", "0.0"))   # 리서치 시나리오 잔존가치에서 Exit 멀티플 방식의 비중
RF_FALLBACK = 0.0425
TAX_MARGINAL = 0.24                                        # 미국 연방 21% + 주세 근사
MC_SIMS = int(os.environ.get("SMA_VAL_MC", "4000"))
GROWTH_DECAY = 0.65          # 초과 성장률(g − g∞)의 연간 잔존 비율: 고성장은 빠르게 평균회귀한다 (반감기 ≈ 1.6년)
GROWTH_SOFT_CAP = 0.50
STABLE_BETA = (0.8, 1.2)     # 안정 성장기 베타 범위 (성숙 기업의 베타는 시장 평균 부근으로 모인다)
WACC_CONV_YEARS = 10         # 할인율이 안정기 수준에 도달하는 해
TAX_CONV_YEARS = 5           # 실효세율이 장기 세율에 도달하는 해 (일회성 세금 효과는 오래가지 않는다)
MAX_HORIZON = 20             # 리서치 시나리오의 최장 명시+감쇠 구간
MAX_EXPLICIT = 20            # 리서치가 직접 줄 수 있는 연도별 성장률 개수
AMORT_DECAY = 0.75           # 인수 무형자산 상각의 연간 잔존 비율

# 섹터 기본값 (peer 통계가 없을 때만 사용): 성숙기 영업이익률 / Sales-to-Capital
SECTOR_DEFAULT = {
    "technology": (0.22, 1.30), "communication-services": (0.18, 0.90), "healthcare": (0.18, 0.90),
    "industrials": (0.12, 1.50), "consumer-cyclical": (0.10, 1.80), "consumer-defensive": (0.10, 1.80),
    "energy": (0.12, 0.70), "basic-materials": (0.13, 0.80), "utilities": (0.18, 0.40),
    "financial-services": (0.25, 0.80), "real-estate": (0.30, 0.30),
}
CYCLICAL_SECTORS = {"basic-materials", "energy"}
CYCLICAL_INDUSTRIES = {
    "auto-manufacturers", "auto-parts", "steel", "aluminum", "copper", "gold", "silver", "uranium", "coking-coal",
    "thermal-coal", "other-industrial-metals-mining", "other-precious-metals-mining", "chemicals",
    "specialty-chemicals", "agricultural-inputs", "airlines", "marine-shipping", "residential-construction",
    "building-materials", "farm-heavy-construction-machinery", "semiconductor-equipment-materials",
    "oil-gas-e-p", "oil-gas-integrated", "oil-gas-drilling", "oil-gas-equipment-services",
    "oil-gas-refining-marketing", "paper-paper-products", "lumber-wood-production", "recreational-vehicles",
}

ARCH_KO = {
    "BANK": "은행·대출형 금융", "INSURANCE": "보험", "REIT": "리츠(REIT)",
    "PRE_REVENUE": "초기 단계·컨셉주 (매출 미미)", "HYPERGROWTH_UNPROFITABLE": "고성장 적자",
    "HIGH_GROWTH": "고성장 흑자", "CYCLICAL": "경기순환", "DIVIDEND_DEFENSIVE": "배당·방어주",
    "MATURE_COMPOUNDER": "성숙·현금창출", "TURNAROUND": "턴어라운드·저성장 적자",
}
MODEL_KO = {
    "dcf_growth_5y": "5년 DCF · 영구성장", "dcf_growth_10y": "10년 DCF · 영구성장",
    "dcf_ebitda_5y": "5년 DCF · EBITDA Exit", "dcf_ebitda_10y": "10년 DCF · EBITDA Exit",
    "dcf_revenue_5y": "5년 DCF · Revenue Exit", "dcf_revenue_10y": "10년 DCF · Revenue Exit",
    "ddm_stable": "배당할인(DDM) · 안정성장", "ddm_multi": "배당할인(DDM) · 다단계",
    "epv": "수익력 가치(EPV)", "residual_income": "초과이익 모델(RIM)",
    "pe": "P/E 멀티플", "ev_ebitda": "EV/EBITDA 멀티플", "ev_rev": "EV/Revenue 멀티플", "ps": "P/S 멀티플",
    "pb": "P/B 멀티플 (ROE 보정)", "p_ffo": "P/FFO (REIT)", "lynch": "피터 린치 공정가(PEG)",
    "graham": "그레이엄 공식",
}
FAMILY = {
    **{k: "DCF" for k in MODEL_KO if k.startswith("dcf_")}, "ddm_stable": "배당", "ddm_multi": "배당",
    "epv": "수익력", "residual_income": "초과이익", "pe": "상대가치", "ev_ebitda": "상대가치", "ev_rev": "상대가치",
    "ps": "상대가치", "pb": "상대가치", "p_ffo": "상대가치", "lynch": "공식", "graham": "공식",
}
# 성격별 기본 가중치 — 적용 불가 모델은 빠지고 나머지를 재정규화한다
WEIGHTS: Dict[str, Dict[str, float]] = {
    "MATURE_COMPOUNDER": {"dcf_growth_5y": 1.0, "dcf_ebitda_5y": 1.0, "dcf_growth_10y": 0.6, "dcf_ebitda_10y": 0.6,
                          "pe": 1.0, "ev_ebitda": 1.0, "epv": 0.5, "ddm_multi": 0.4, "ddm_stable": 0.3,
                          "graham": 0.3, "lynch": 0.3, "ev_rev": 0.2},
    "DIVIDEND_DEFENSIVE": {"ddm_multi": 1.0, "ddm_stable": 0.9, "dcf_growth_5y": 0.8, "dcf_ebitda_5y": 0.8,
                           "dcf_growth_10y": 0.4, "pe": 1.0, "ev_ebitda": 0.9, "epv": 0.5, "graham": 0.4},
    "HIGH_GROWTH": {"dcf_growth_10y": 1.0, "dcf_ebitda_10y": 1.0, "dcf_ebitda_5y": 0.6, "dcf_growth_5y": 0.4,
                    "dcf_revenue_10y": 0.4, "pe": 0.9, "ev_ebitda": 0.7, "lynch": 0.6, "ev_rev": 0.4,
                    "graham": 0.2, "epv": 0.15},
    "HYPERGROWTH_UNPROFITABLE": {"dcf_revenue_10y": 1.0, "dcf_growth_10y": 0.9, "dcf_ebitda_10y": 0.6,
                                 "dcf_revenue_5y": 0.5, "ev_rev": 1.0, "ps": 0.5, "pe": 0.4},
    "PRE_REVENUE": {"dcf_revenue_10y": 1.0, "dcf_growth_10y": 0.7, "dcf_ebitda_10y": 0.4, "ev_rev": 0.8, "pb": 0.3},
    "CYCLICAL": {"dcf_growth_5y": 0.9, "dcf_ebitda_5y": 0.9, "dcf_growth_10y": 0.5, "dcf_ebitda_10y": 0.4, "epv": 0.6,
                 "ev_ebitda": 1.0, "pe": 0.6, "pb": 0.4, "ddm_multi": 0.3, "graham": 0.2, "ev_rev": 0.3},
    "TURNAROUND": {"dcf_growth_10y": 0.8, "dcf_revenue_10y": 0.7, "dcf_revenue_5y": 0.6, "dcf_ebitda_10y": 0.5,
                   "ev_rev": 1.0, "ps": 0.6, "pb": 0.5, "ev_ebitda": 0.5},
    "BANK": {"residual_income": 1.0, "pb": 1.0, "pe": 1.0, "ddm_multi": 0.8, "ddm_stable": 0.5, "graham": 0.2},
    "INSURANCE": {"residual_income": 0.9, "pb": 1.0, "pe": 1.0, "ddm_multi": 0.7, "ddm_stable": 0.4, "graham": 0.2},
    "REIT": {"p_ffo": 1.0, "ddm_multi": 1.0, "ddm_stable": 0.7, "ev_ebitda": 0.7, "pb": 0.4},
}
ARCH_UNCERTAINTY = {"PRE_REVENUE": 30, "HYPERGROWTH_UNPROFITABLE": 20, "TURNAROUND": 20, "CYCLICAL": 12,
                    "HIGH_GROWTH": 15}
UNC_KO = {"LOW": "낮음", "MEDIUM": "보통", "HIGH": "높음", "VERY_HIGH": "매우 높음"}
MOS_BAND = {"LOW": 0.10, "MEDIUM": 0.15, "HIGH": 0.25, "VERY_HIGH": 0.35}
VERDICT_KO = {"DEEP_UNDERVALUED": "크게 저평가", "UNDERVALUED": "저평가", "FAIR": "적정 범위",
              "OVERVALUED": "고평가", "DEEP_OVERVALUED": "크게 고평가"}


# ─── 유틸 ────────────────────────────────────────────────────────────────────
def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _ok(*xs) -> bool:
    return all(x is not None and isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


def _div(a, b) -> Optional[float]:
    return a / b if _ok(a, b) and b != 0 else None


def _cagr(first: Optional[float], last: Optional[float], years: float) -> Optional[float]:
    if not _ok(first, last) or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def _pct(xs: Sequence[float], q: float) -> float:
    return float(np.percentile(np.asarray(xs, float), q))


def _wq(vals: Sequence[float], ws: Sequence[float], q: float) -> float:
    """가중 분위수 (q: 0~1)."""
    order = np.argsort(vals)
    v, w = np.asarray(vals, float)[order], np.asarray(ws, float)[order]
    cw = (np.cumsum(w) - 0.5 * w) / np.sum(w)
    return float(np.interp(q, cw, v))


# ─── ① 파생 지표 ────────────────────────────────────────────────────────────
def derive(F: Dict[str, Any], price: Optional[float], g_term: float) -> Dict[str, Any]:
    mk, ttm, annual, est = F.get("market") or {}, F.get("ttm") or {}, F.get("annual") or [], F.get("estimates") or {}
    notes: List[str] = []
    price = price if _ok(price) and price > 0 else mk.get("price")
    if not _ok(price) or price <= 0:
        raise ValueError("현재가 없음")
    shares = _div(mk.get("mcap"), mk.get("price")) or mk.get("shares_implied") or mk.get("shares") or ttm.get("shares_out")
    sd = ttm.get("shares_diluted")
    if _ok(shares, sd) and 1.0 < sd / shares < 1.15:
        shares = sd                                         # 희석 주식수 (스톡옵션·RSU·전환)
    if not _ok(shares) or shares <= 0:
        raise ValueError("주식수 없음")
    rev = ttm.get("revenue")
    if not _ok(rev) or rev <= 0:
        raise ValueError("TTM 매출 없음")
    ebit, da = ttm.get("ebit"), ttm.get("da") or 0.0
    ni = ttm.get("net_income")
    eps_gaap = ttm.get("eps") if _ok(ttm.get("eps")) else _div(ni, shares)

    # 유효세율: 최근 3개 연도 합산 (단년 왜곡 제거)
    tp = [(a.get("tax"), a.get("pretax")) for a in annual[-3:] if _ok(a.get("tax"), a.get("pretax"))]
    tcands = []
    if len(tp) >= 2 and all(p > 0 for _, p in tp):         # 적자 연도가 섞이면 합산 비율이 왜곡된다
        tcands.append(sum(t for t, _ in tp) / sum(p for _, p in tp))
    if _ok(ttm.get("tax"), ttm.get("pretax")) and ttm["pretax"] > 0:
        tcands.append(ttm["tax"] / ttm["pretax"])
    tcands = [t for t in tcands if 0.0 <= t <= 0.5]
    tax_eff = _clip(median(tcands), 0.05, 0.30) if tcands else 0.21
    interest = abs(ttm.get("interest_expense") or 0.0)
    eps_core = (ebit - interest) * (1 - tax_eff) / shares if _ok(ebit) else None
    eps_basis, eps_note = eps_gaap, "GAAP"
    if _ok(eps_gaap, eps_core) and abs(eps_gaap - eps_core) > 0.25 * abs(eps_core) and (eps_core > 0 or eps_gaap > 0):
        eps_basis, eps_note = eps_core, "핵심(영업)이익 기준 — GAAP EPS 에 일회성·영업외 손익 포함"
        notes.append(f"GAAP EPS {eps_gaap:.2f} vs 핵심(영업) EPS {eps_core:.2f}: 일회성·영업외 손익 → 핵심 EPS 를 이익 기준으로 사용")

    debt, cash = ttm.get("total_debt") or 0.0, ttm.get("cash") or 0.0
    minority, preferred = ttm.get("minority") or 0.0, ttm.get("preferred") or 0.0
    equity = ttm.get("equity")
    ic = (equity + debt - cash + minority) if _ok(equity) else None
    if ic is not None and ic <= 0.05 * rev:
        ic = None
    intang = ttm.get("intangibles") or 0.0
    ic_tan = (ic - intang) if ic is not None else None      # 유기적 성장에 필요한 자본: 인수 영업권·무형자산 제외
    asset_light = ic_tan is not None and ic_tan <= 0.05 * rev and _ok(ebit) and ebit > 0
    if ic_tan is not None and ic_tan <= 0.05 * rev:
        ic_tan = None
    capex = abs(ttm.get("capex") or 0.0)
    # 인수 무형자산 상각: 현금 유출이 없고 재투자도 필요 없는 비용 → FCFF 에 가산 (시간이 지나며 소멸)
    acq_heavy = _ok(ttm.get("total_assets")) and ttm["total_assets"] > 0 and intang / ttm["total_assets"] > 0.20
    amort_excess = 0.0
    if acq_heavy:
        ai = ttm.get("amort_intang")                        # 현금흐름표의 무형자산 상각(있으면) — 없으면 (D&A − CAPEX) 의 절반으로 보수 추정
        amort_excess = min(abs(ai), max(da - capex, 0.0)) if _ok(ai) else 0.5 * max(da - capex, 0.0)
    m_ttm = _div(ebit, rev)
    hist_m = [(a["end"], a["ebit"] / a["revenue"]) for a in annual if _ok(a.get("ebit"), a.get("revenue")) and a["revenue"] > 0]
    long = F.get("long") or {}
    lrev, lop = dict(map(tuple, long.get("revenue") or [])), dict(map(tuple, long.get("op_income") or []))
    long_m = [(e, lop[e] / lrev[e]) for e in sorted(lrev) if e in lop and lrev[e] > 0]
    merged = dict(long_m)
    merged.update(dict(hist_m))                             # Yahoo 표준화 값 우선
    all_m = [merged[k] for k in sorted(merged)]
    m_med5 = median([m for _, m in hist_m][-5:]) if hist_m else m_ttm
    m_long = median(all_m[-10:]) if len(all_m) >= 6 else None
    m_std = float(np.std(all_m[-10:])) if len(all_m) >= 4 else None
    pos_years = sum(1 for _, m in hist_m[-5:] if m > 0.02)

    # 성장: 과거
    ar = [(a["end"], a["revenue"]) for a in annual if _ok(a.get("revenue"))]
    g_hist = _cagr(ar[0][1], ar[-1][1], len(ar) - 1) if len(ar) >= 3 else None
    lr = sorted(lrev.items())
    g_long = _cagr(lr[-8][1] if len(lr) >= 8 else lr[0][1], lr[-1][1], min(len(lr) - 1, 7)) if len(lr) >= 5 else None
    g_yoy = mk.get("rev_growth")

    # 성장: 선행 (컨센서스 FY0/FY1 → NTM 으로 환산)
    last_fy = annual[-1]["end"] if annual else None
    qs = [q for q in (F.get("quarters") or []) if _ok(q.get("revenue"))]
    ytd_q = [q for q in qs if last_fy and q["end"] > last_fy][-3:] if F.get("ttm_source") == "quarters" else []
    frac = len(ytd_q) / 4.0
    ytd = sum(q["revenue"] for q in ytd_q)
    er = est.get("rev") or {}
    r0, r1 = (er.get("0y") or {}).get("avg"), (er.get("+1y") or {}).get("avg")
    ya = (er.get("0y") or {}).get("year_ago")
    ra = ar[-1][1] if ar else None
    stale_est = False
    if _ok(r0, ya, ra) and len(ar) >= 2 and abs(ya / ra - 1) > 0.05 and abs(ya / ar[-2][1] - 1) < 0.03:
        r0, r1, stale_est = r1, None, True                 # '0y' 가 이미 보고된 회계연도를 가리키는 경우
        notes.append("컨센서스 '당해연도'가 이미 보고된 회계연도 → 한 해 밀어 적용")
    fade = lambda g: g_term + (g - g_term) * 0.75          # noqa: E731
    g_src = "컨센서스"
    disp = None
    if _ok(r0) and r0 > 0:
        # 컨센서스 매출 정의가 보고 매출과 다를 수 있다(예: 거래소의 순매출) → 전년 실적 비율로 보고 기준에 맞춘다
        ya0 = None if stale_est else ya
        scale = ra / ya0 if _ok(ra, ya0) and ya0 > 0 and 0.4 < ra / ya0 < 2.5 else 1.0
        if abs(scale - 1) > 0.05:
            notes.append(f"컨센서스 매출 정의가 보고 매출과 다름(비율 {scale:.2f}) → 성장률만 사용해 보고 기준으로 환산")
        r0s, r1s = r0 * scale, (r1 * scale if _ok(r1) else None)
        g_fy0 = _div(r0s, ra) - 1 if _ok(ra) and ra > 0 else None
        g_fy1 = (r1s / r0s - 1) if _ok(r1s) else fade(g_fy0 if g_fy0 is not None else g_term)
        fy1 = r1s if _ok(r1s) else r0s * (1 + g_fy1)
        # 다음 회계연도 중 앞쪽 frac 구간의 매출 비중 (연중 성장을 기하급수로 가정 — 균등 분할은 고성장주를 과대추정)
        part = frac if abs(g_fy1) < 1e-6 else ((1 + g_fy1) ** frac - 1) / g_fy1
        rest = r0s - ytd
        last_q = qs[-1]["revenue"] if qs else None
        per_q = rest / max(4 - len(ytd_q), 1)
        sane = rest > 0 and (not _ok(last_q) or 0.5 < per_q / last_q < 2.5)
        if sane:
            rev_ntm = rest + part * fy1
            g1 = rev_ntm / rev - 1
        else:                                               # 분기 누계와 컨센서스가 어긋남 → 회계연도 성장률의 시간가중
            g1 = (1 - frac) * (g_fy0 if g_fy0 is not None else g_fy1) + frac * g_fy1
            rev_ntm = rev * (1 + g1)
            notes.append("분기 누계와 컨센서스 수준이 어긋나 회계연도 성장률의 시간가중으로 NTM 성장률 산출")
        g2 = (1 - frac) * g_fy1 + frac * fade(g_fy1)
        blk = er.get("+1y") if _ok(r1) and not stale_est else er.get("0y")
        if blk and _ok(blk.get("high"), blk.get("low"), blk.get("avg")) and blk["avg"] > 0:
            disp = (blk["high"] - blk["low"]) / (2 * blk["avg"])
    else:
        cands = [g for g in (g_hist, g_yoy) if _ok(g)]
        base = (0.6 * g_hist + 0.4 * g_yoy) if _ok(g_hist, g_yoy) else (cands[0] if cands else g_term)
        g1 = _clip(base, -0.15, 0.40)
        g2 = g_term + (g1 - g_term) * 0.8
        rev_ntm = rev * (1 + g1)
        g_src = "과거 성장률 (컨센서스 없음)"
    g1, g2 = _clip(g1, -0.50, 4.0), _clip(g2, -0.30, 2.0)

    ee = est.get("eps") or {}
    e0, e1 = (ee.get("0y") or {}).get("avg"), (ee.get("+1y") or {}).get("avg")
    if stale_est:
        e0, e1 = e1, None
    eps_ntm = ((1 - frac) * e0 + frac * e1) if _ok(e0, e1) else (e0 if _ok(e0) else mk.get("forward_eps"))
    eps_g = _div(e1, e0) - 1 if _ok(e0, e1) and e0 > 0 else None

    dps = mk.get("div_rate")
    if not _ok(dps) or dps <= 0:
        dp = abs(ttm.get("dividends_paid") or 0.0)
        dps = dp / shares if dp > 0 else 0.0
    ldps = [v for _, v in (long.get("dps") or []) if _ok(v) and v > 0]
    dps_g = _cagr(ldps[-6], ldps[-1], 5) if len(ldps) >= 6 else (_cagr(ldps[0], ldps[-1], len(ldps) - 1) if len(ldps) >= 3 else None)
    fcf = ttm.get("fcf")
    if not _ok(fcf) and _ok(ttm.get("cfo"), ttm.get("capex")):
        fcf = ttm["cfo"] + ttm["capex"]
    payout = _div(dps, eps_basis) if _ok(eps_basis) and eps_basis > 0 else None
    runway = cash / abs(fcf) if _ok(fcf) and fcf < 0 and cash > 0 else None
    mcap = price * shares
    return {
        "price": price, "shares": shares, "mcap": mcap, "rev": rev, "rev_ntm": rev_ntm, "ebit": ebit, "da": da,
        "ebitda": (ebit + da) if _ok(ebit) else None, "da_pct": da / rev, "ni": ni, "eps_gaap": eps_gaap,
        "eps_core": eps_core, "eps_basis": eps_basis, "eps_note": eps_note, "eps_ntm": eps_ntm, "eps_g": eps_g,
        "ltg": est.get("ltg"), "tax_eff": tax_eff, "interest": interest,
        "coverage": _div(ebit, interest) if interest > 0 else None,
        # 리스 부채: 리스료는 이미 영업이익에서 차감된다 → 주주가치 계산·자본구조에서는 리스를 뺀 금융부채를 쓴다 (EV 멀티플은 peer 와 같은 기준인 리스 포함)
        "leases": min(max(ttm.get("lease_liabilities") or 0.0, 0.0), debt), "debt_fin": debt - min(max(ttm.get("lease_liabilities") or 0.0, 0.0), debt),
        "debt": debt, "cash": cash, "net_debt": debt - cash, "minority": minority, "preferred": preferred,
        "equity": equity, "bvps": _div(equity, shares), "ic": ic,
        "roic": _div(ebit * (1 - tax_eff), ic) if _ok(ebit) and ic else None, "roe": _div(ni, equity) if _ok(equity) and equity > 0 else None,
        "s2c_own": _div(rev, ic_tan) if ic_tan else (6.0 if asset_light else None),
        "ic_tan": ic_tan, "ttm_end": ttm.get("end"),
        "roic_tan": (_div(ebit * (1 - tax_eff), ic_tan) if ic_tan else (9.9 if asset_light else None)) if _ok(ebit) else None, "capex": capex, "capex_pct": capex / rev,
        "amort_excess": amort_excess, "intangibles": intang, "m_ttm": m_ttm, "gross_m": _div(ttm.get("gross_profit"), rev), "m_med5": m_med5, "m_long": m_long, "m_std": m_std,
        "margin_hist": hist_m, "pos_years": pos_years, "g_hist": g_hist, "g_long": g_long, "g_yoy": g_yoy,
        "g1": g1, "g2": g2, "g_src": g_src, "g_disp": disp, "frac_fy": frac, "dps": dps, "dps_g": dps_g,
        "div_yield": dps / price, "payout": payout, "fcf": fcf, "fcf_yield": _div(fcf, mcap), "sbc": ttm.get("sbc"),
        "runway": runway, "ps": mcap / rev, "ev": mcap + debt - cash + minority + preferred,
        "ffo": (ni + da) if _ok(ni) else None, "notes": notes, "n_annual": len(ar), "has_est": g_src == "컨센서스",
    }


# ─── ② 성격 분류 ────────────────────────────────────────────────────────────
def classify(F: Dict[str, Any], D: Dict[str, Any]) -> Dict[str, Any]:
    pf = F.get("profile") or {}
    ind, sec = (pf.get("industry_key") or "").lower(), (pf.get("sector_key") or "").lower()
    name_ind = pf.get("industry") or "N/A"
    reasons: List[str] = []
    traits: List[str] = []
    m, g1, ps = D["m_ttm"], D["g1"], D["ps"]
    de = _div(D["debt"], D["equity"]) if _ok(D["equity"]) and D["equity"] > 0 else None

    def out(code: str) -> Dict[str, Any]:
        return {"code": code, "label": ARCH_KO[code], "reasons": reasons, "traits": traits,
                "sector": pf.get("sector"), "industry": name_ind}

    if "bank" in ind or (sec == "financial-services" and ind in ("credit-services", "mortgage-finance") and (de or 0) > 2.5):
        reasons.append(f"산업 '{name_ind}': 부채가 영업 원재료 → FCFF·EV 개념이 성립하지 않아 자기자본 기반 모델(RIM·P/B·P/E·DDM) 사용")
        return out("BANK")
    if D["ebit"] is None and "insurance" not in ind and not (sec == "real-estate"):
        reasons.append("손익계산서에 영업이익 항목이 없음(이자수익 중심 구조) → 금융업으로 판단, 자기자본 기반 모델 사용")
        return out("BANK")
    if "insurance" in ind:
        reasons.append(f"산업 '{name_ind}': 보험부채 구조 → 자기자본 기반 모델 사용")
        return out("INSURANCE")
    if sec == "real-estate" and ind.startswith("reit"):
        reasons.append(f"산업 '{name_ind}': 감가상각이 큰 배당 의무 구조 → FFO·배당 기반 모델 사용")
        return out("REIT")

    cyc_ind = sec in CYCLICAL_SECTORS or ind in CYCLICAL_INDUSTRIES
    vol = D["m_std"]
    cyc_vol = _ok(vol, D["m_long"]) and vol > 0.06 and vol > 0.5 * abs(D["m_long"]) and D["n_annual"] >= 3
    if cyc_ind:
        traits.append("경기민감 업종")
    if _ok(D["fcf"]) and D["fcf"] < 0:
        traits.append("현금 소진(FCF<0)")
    if _ok(D["sbc"]) and D["sbc"] / D["rev"] > 0.10:
        traits.append(f"주식보상 비중 높음(매출의 {D['sbc'] / D['rev'] * 100:.0f}%)")
    if _ok(D["eps_ntm"]) and D["eps_ntm"] > 0 and D["price"] / D["eps_ntm"] > 60 and g1 < 0.25:
        traits.append("서사 프리미엄(선행 P/E 60배 초과 · 성장률은 25% 미만)")

    profitable = _ok(m) and m > 0.03 and ((_ok(D["ni"]) and D["ni"] > 0) or (_ok(D["fcf"]) and D["fcf"] > 0))
    if (_ok(m) and m < -0.5 and ps > 15) or D["rev"] < 50e6:
        reasons.append(f"매출 대비 시가총액 {ps:.0f}배 · 영업이익률 {m * 100:.0f}%: 현재 실적이 아닌 미래 사업화에 가격이 매겨진 단계"
                       if _ok(m) else "매출 5천만 달러 미만: 사업화 이전 단계")
        reasons.append("→ 현재 이익 기반 모델(P/E·EV/EBITDA·EPV) 불가. 장기 매출 경로 + 목표 마진 DCF 와 선행 EV/Sales 만 사용")
        return out("PRE_REVENUE")
    if not profitable:
        if D["pos_years"] >= 2 and (cyc_ind or (cyc_vol and g1 < 0.15)):
            reasons.append(f"현재 저수익이나 최근 5년 중 {D['pos_years']}년 흑자 · " + ("경기민감 업종" if cyc_ind else "마진 변동성 큼") + ": 경기 저점 구간으로 판단")
            reasons.append("→ 당기 이익이 아닌 미드사이클(정상화) 마진으로 평가: EPV·정상화 EV/EBITDA·DCF")
            return out("CYCLICAL")
        if g1 >= 0.20:
            reasons.append(f"선행 매출 성장 {g1 * 100:.0f}% · 영업이익률 {(m or 0) * 100:.0f}%: 성장 우선·적자 구간")
            reasons.append("→ 이익 멀티플 불가. 매출 기반(Revenue Exit DCF·EV/Revenue) + 마진 수렴 DCF 중심")
            return out("HYPERGROWTH_UNPROFITABLE")
        reasons.append(f"영업이익률 {(m or 0) * 100:.0f}% · 선행 성장 {g1 * 100:.0f}%: 이익 미창출 + 저성장")
        reasons.append("→ 이익 멀티플 불가. 매출·자산 기반(EV/Revenue·P/S·P/B) + 마진 회복 가정 DCF")
        return out("TURNAROUND")
    if cyc_ind:
        reasons.append("경기민감 업종" + (f" (과거 마진 표준편차 {vol * 100:.1f}%p)" if _ok(vol) else "")
                       + f": 선행 성장 {g1 * 100:.0f}% 도 가격·사이클 요인이 큼 → 당기 이익을 그대로 연장하지 않음")
        reasons.append("→ 미드사이클(장기 중앙값) 마진으로 정상화: EPV·정상화 EV/EBITDA·5년 DCF 중심")
        return out("CYCLICAL")
    if g1 >= 0.15 or (_ok(D["g_hist"]) and D["g_hist"] >= 0.20 and g1 >= 0.12):
        reasons.append(f"선행 매출 성장 {g1 * 100:.0f}% · 영업이익률 {m * 100:.0f}%: 이익을 내며 고성장")
        reasons.append("→ 성장 지속 기간이 가치를 좌우: 10년 DCF(성장률 점진 수렴) + 선행 P/E·PEG 중심, 5년 모델·EPV 는 보조")
        return out("HIGH_GROWTH")
    if cyc_vol:
        reasons.append(f"영업이익률 변동성 큼 (과거 마진 표준편차 {vol * 100:.1f}%p): 당기 이익이 사이클 위치에 좌우")
        reasons.append("→ 미드사이클(장기 중앙값) 마진으로 정상화: EPV·정상화 EV/EBITDA·5년 DCF 중심")
        return out("CYCLICAL")
    if sec == "utilities" or (D["div_yield"] >= 0.025 and (D["payout"] or 0) >= 0.40 and g1 < 0.08):
        reasons.append(f"배당수익률 {D['div_yield'] * 100:.1f}% · 배당성향 {(D['payout'] or 0) * 100:.0f}% · 저성장: 배당이 주된 환원 경로")
        reasons.append("→ 배당할인(DDM) + 안정 현금흐름 DCF + 이익 멀티플")
        return out("DIVIDEND_DEFENSIVE")
    reasons.append(f"선행 성장 {g1 * 100:.0f}% · 영업이익률 {m * 100:.0f}% · 안정적 이익: 성숙 단계 현금창출 기업")
    reasons.append("→ 5년 DCF + 이익 멀티플(P/E·EV/EBITDA) 균형, EPV 로 하방 확인")
    return out("MATURE_COMPOUNDER")


# ─── Peer 통계 ───────────────────────────────────────────────────────────────
_RANGES = {"pe": (3, 120), "fpe": (3, 100), "ev_ebitda": (2, 80), "ev_rev": (0.1, 60), "pb": (0.2, 40),
           "ps": (0.1, 60), "beta": (0.2, 4), "rev_growth": (-0.6, 3), "op_margin": (-1, 0.8), "roe": (-1, 1.5)}


def peer_stats(peers: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for k, (lo, hi) in _RANGES.items():
        xs = [p[k] for p in peers if _ok(p.get(k)) and lo <= p[k] <= hi]
        if xs:
            out[k] = {"median": float(median(xs)), "p25": _pct(xs, 25), "p75": _pct(xs, 75), "n": len(xs)}
    prof = [p["op_margin"] for p in peers if _ok(p.get("op_margin")) and 0.02 < p["op_margin"] < 0.8]
    if len(prof) >= 3:
        out["op_margin_profitable"] = {"median": float(median(prof)), "p25": _pct(prof, 25), "p75": _pct(prof, 75), "n": len(prof)}
    s2c, bu = [], []
    for p in peers:
        if _ok(p.get("revenue"), p.get("book"), p.get("shares")):
            icp = p["book"] * p["shares"] + (p.get("debt") or 0) - (p.get("cash") or 0)
            if icp > 0.05 * p["revenue"] > 0:
                s2c.append(p["revenue"] / icp)
        if _ok(p.get("beta"), p.get("mcap")) and 0.2 <= p["beta"] <= 4 and p["mcap"] > 0:
            bu.append(p["beta"] / (1 + (1 - TAX_MARGINAL) * (p.get("debt") or 0) / p["mcap"]))
    if len(s2c) >= 3:
        out["s2c"] = {"median": float(median(s2c)), "n": len(s2c)}
    if len(bu) >= 3:
        out["beta_unlevered"] = {"median": float(median(bu)), "n": len(bu)}
    return out


# ─── ③ 자본비용 ─────────────────────────────────────────────────────────────
_SPREADS = [(8.5, .006), (6.5, .008), (5.5, .010), (4.25, .012), (3.0, .015), (2.5, .020), (2.25, .025), (2.0, .030),
            (1.75, .0375), (1.5, .045), (1.25, .055), (0.8, .075), (0.65, .10), (-1e9, .14)]


def capital(F: Dict[str, Any], D: Dict[str, Any], PS: Dict[str, Any], rf: float, erp: Optional[float] = None) -> Dict[str, Any]:
    ERP = erp if _ok(erp) and 0.02 <= erp <= 0.09 else globals()["ERP"]   # noqa: N806
    beta_raw = (F.get("market") or {}).get("beta")
    blume = 0.67 * beta_raw + 0.33 if _ok(beta_raw) else None
    de = D["debt_fin"] / D["mcap"]
    bu = (PS.get("beta_unlevered") or {}).get("median")
    relev = bu * (1 + (1 - TAX_MARGINAL) * de) if _ok(bu) else None
    if _ok(blume, relev):
        beta, src = 0.5 * blume + 0.5 * relev, "Blume 조정 회귀베타 50% + peer bottom-up 베타 50%"
    elif _ok(blume):
        beta, src = blume, "Blume 조정 회귀베타"
    elif _ok(relev):
        beta, src = relev, "peer bottom-up 베타"
    else:
        beta, src = 1.0, "시장 베타(1.0) 가정 — 베타 데이터 없음"
    beta = _clip(beta, 0.5, 2.5)
    mc = D["mcap"]
    # 규모 프리미엄: 대형·중형주에는 근거가 약하다(CRSP 상위 분위의 프리미엄 ≈ 0). 유동성이 얇은 소형주(시총 20억 달러 미만)에만,
    # 시총에 따라 연속적으로(계단 없이) 부여하고 안정 성장기에는 두지 않는다
    size = float(np.interp(math.log10(max(mc, 1.0)), [math.log10(5e7), math.log10(3e8), math.log10(2e9)], [0.03, 0.0175, 0.0]))
    ke = rf + beta * ERP + size
    cov = D["coverage"]
    loss = not (_ok(D["ebit"]) and D["ebit"] > 0)
    obs = D["interest"] / D["debt_fin"] if D["debt_fin"] > 0 and (D.get("interest") or 0) > 0 else None   # 실효 차입금리
    if D["debt_fin"] <= 0:
        spread = 0.012
    elif loss:
        # 영업적자 기업은 이자보상배율이 음수라 합성등급이 '부도'로 나오지만, 투자 단계의 적자는 부실과 다르다.
        # 실제로 돈을 빌려준 금리(실효금리)를 쓰되 B~CCC 등급 범위(rf+4.5%~7.5%)로 제한한다 — 전환사채 표면금리는
        # 전환권 가치만큼 낮게 찍히므로 하한을 두고, 자금조달 실패 위험은 할인율이 아니라 실패 확률·약세 시나리오가 담당한다(이중 반영 금지)
        spread = _clip(obs - rf, 0.045, 0.075) if obs is not None else 0.06      # 이자 자료가 없으면 범위의 중간(최악값 아님)
    elif cov is None:
        spread = 0.02
    else:
        spread = next(s for th, s in _SPREADS if cov > th)
    kd = min(rf + spread, ke)                               # 채권자는 주주보다 선순위: 타인자본비용 ≤ 자기자본비용
    tax = max(D["tax_eff"], 0.0) if _ok(D["ebit"]) and D["ebit"] > 0 else 0.0
    we = mc / (mc + D["debt_fin"])
    wacc = we * ke + (1 - we) * kd * (1 - tax)

    beta_T = _clip(beta + (1.0 - beta) * 0.5, STABLE_BETA[0], STABLE_BETA[1])
    ke_T = rf + beta_T * ERP

    def mature() -> float:
        """안정 성장기(잔존가치 구간) 자본비용. 잔존가치는 '성숙한 회사'의 가치이므로 그 구간의 베타는 성숙 기업 범위
        (0.8~1.2)에 있어야 한다 — 고베타가 영구히 유지된다고 두면 성장기의 위험을 영구 구간에 한 번 더 깎는 셈이 된다."""
        w = _clip(we + (0.85 - we) * 0.25, 0.5, 1.0)
        return w * ke_T + (1 - w) * (rf + min(spread, 0.02)) * (1 - TAX_MARGINAL)
    return {"rf": rf, "erp": ERP, "beta_raw": beta_raw, "beta_blume": blume, "beta_peer_unlevered": bu,
            "beta_peer_relevered": relev, "beta": beta, "beta_source": src, "size_premium": size, "ke": ke,
            "kd": kd, "spread": spread, "coverage": cov, "tax": tax, "we": we, "wd": 1 - we, "wacc": wacc,
            "kd_observed": obs, "beta_stable": beta_T, "wacc_t5": mature(), "wacc_t10": mature(), "ke_t": ke_T}


# ─── ④-a 전망 가정 ──────────────────────────────────────────────────────────
def assumptions(F: Dict[str, Any], D: Dict[str, Any], A: Dict[str, Any], K: Dict[str, Any],
                PS: Dict[str, Any], g_term: float) -> Dict[str, Any]:
    code = A["code"]
    sec = ((F.get("profile") or {}).get("sector_key") or "").lower()
    sec_m, sec_s2c = SECTOR_DEFAULT.get(sec, (0.14, 1.2))
    peer_m = (PS.get("op_margin_profitable") or {}).get("median")
    peer_m75 = (PS.get("op_margin_profitable") or {}).get("p75")
    m_ttm = D["m_ttm"] if _ok(D["m_ttm"]) else 0.0
    recent = [m for _, m in D["margin_hist"][-3:]]
    m_base = 0.5 * m_ttm + 0.5 * median(recent) if recent else m_ttm
    how = ""
    if code in ("PRE_REVENUE", "HYPERGROWTH_UNPROFITABLE"):
        m0, conv = m_ttm, None
        mT = _clip(0.8 * (peer_m if _ok(peer_m) else sec_m), 0.08, 0.28)
        if _ok(D["gross_m"]) and D["gross_m"] > 0:
            mT = min(mT, max(0.4 * D["gross_m"], 0.05))     # 매출총이익률이 허용하는 범위 안에서만
        how = f"목표 마진 {mT * 100:.0f}% = 흑자 peer 영업이익률 중앙값의 80%(실행 리스크 할인) — 예측 기간 말에 도달"
    elif code == "TURNAROUND":
        m0, conv = m_ttm, None
        mT = _clip(0.6 * (peer_m if _ok(peer_m) else sec_m), 0.04, 0.15)
        how = f"목표 마진 = 흑자 peer 중앙값의 60%({mT * 100:.0f}%) — 회복이 불완전할 가능성 반영"
    elif code == "CYCLICAL":
        m0, conv = m_ttm, 3
        mT = D["m_long"] if _ok(D["m_long"]) else (D["m_med5"] if _ok(D["m_med5"]) else m_base)
        mT = max(mT, 0.02)
        how = f"목표 마진 = 미드사이클(장기 중앙값) {mT * 100:.0f}% — 3년에 걸쳐 수렴"
    elif code == "HIGH_GROWTH":
        m0, conv = m_ttm, 5
        up = max(m_ttm, 0.5 * m_ttm + 0.5 * (peer_m75 if _ok(peer_m75) else m_ttm))
        mT = _clip(up, m_ttm, m_ttm + 0.08)
        how = f"목표 마진 {mT * 100:.0f}% = 현 수준({m_ttm * 100:.0f}%) + 영업 레버리지(peer 상위 25% 방향, +8%p 한도)"
    else:
        m0, conv = m_ttm, 3
        mT = 0.6 * m_base + 0.4 * (D["m_med5"] if _ok(D["m_med5"]) else m_base)
        how = f"목표 마진 {mT * 100:.0f}% = 최근 실적 60% + 5년 중앙값 40%"
    if code in ("MATURE_COMPOUNDER", "DIVIDEND_DEFENSIVE", "CYCLICAL") and D["amort_excess"] > 0:
        up_a = min(D["amort_excess"] / D["rev"], 0.05)
        mT += up_a                                          # 인수 무형자산 상각이 소멸하면 GAAP 마진이 그만큼 오른다 (EBITA 불변)
        how += f" + 인수 무형자산 상각 소멸분 {up_a * 100:.1f}%p"
    peer_s2c = (PS.get("s2c") or {}).get("median")
    ref = 0.5 * sec_s2c + 0.5 * peer_s2c if _ok(peer_s2c) else sec_s2c
    own = D["s2c_own"]
    if code == "PRE_REVENUE" or not _ok(own):
        s2c = _clip(ref, 0.8, 3.0)                          # 매출이 미미해 자기 비율이 무의미 → 업종 기준
    else:
        s2c = _clip(0.5 * _clip(own, 0.3, 6.0) + 0.5 * ref, 0.4, 5.0)
    da_T = D["da_pct"] if D["capex_pct"] >= D["da_pct"] else max(D["capex_pct"], 0.5 * D["da_pct"])
    roic = D["roic_tan"]                                    # 증분 투자수익률: 유형 투하자본 기준
    prem = _clip(0.5 * (roic - K["wacc"]), 0.0, 0.08) if _ok(roic) else 0.02
    p_fail = 0.0
    if _ok(D["fcf"]) and D["fcf"] < 0 and not (_ok(D["ebit"]) and D["ebit"] > 0):
        r = D["runway"] or 0.0
        p_fail = 0.25 if r < 1 else 0.15 if r < 2 else 0.08 if r < 3 else 0.04
        if D["debt"] > D["mcap"]:
            p_fail += 0.05
    tax0 = D["tax_eff"]
    return {"rev0": D["rev"], "g1": D["g1"], "g2": D["g2"], "gT": g_term, "m0": m0, "mT": mT, "conv": conv,
            "margin_how": how, "tax0": tax0, "taxT": _clip(max(tax0, TAX_MARGINAL), TAX_MARGINAL, 0.28), "s2c": s2c,
            # 매출이 아직 작은 회사는 D&A/매출 비율이 의미 없이 크다 → 30% 상한 (Exit 교차검증의 EBITDA 왜곡 방지)
            "roic_prem": prem, "da_pct": min(da_T, 0.30), "amort0": D["amort_excess"], "w0": K["wacc"], "wT5": K["wacc_t5"], "wT10": K["wacc_t10"],
            "p_fail": p_fail, "growth_source": D["g_src"],
            "scale_margin": m0 < 0 and code in ("PRE_REVENUE", "HYPERGROWTH_UNPROFITABLE", "TURNAROUND")}


# ─── ④-b DCF 코어 (numpy 브로드캐스트: 스칼라=기본 시나리오, 배열=몬테카를로) ──────
def dcf(p: Dict[str, Any], N: int, terminal: str = "growth", mult=None, gpath=None, detail: bool = False):
    f = lambda k: np.asarray(p[k], float)                  # noqa: E731
    g1, g2, gT, m0, mT, s2c = f("g1"), f("g2"), f("gT"), f("m0"), f("mT"), f("s2c")
    w0, wT = f("w0"), np.asarray(p["wT10" if N >= 8 else "wT5"], float)
    tax0, taxT = f("tax0"), f("taxT")
    conv = p.get("conv") or N
    # 컨센서스 구간(1~2년차) 이후: 50% 를 넘는 성장률은 초과분의 30% 만 인정한 수준에서 감쇠 시작 (초고성장의 복리 폭주 방지)
    g2c = np.where(g2 > GROWTH_SOFT_CAP, GROWTH_SOFT_CAP + 0.3 * (g2 - GROWTH_SOFT_CAP), g2)
    amort0 = float(p.get("amort0") or 0.0)
    rev0 = f("rev0")
    rev_prev = rev0 * np.ones(np.broadcast(g1, m0, mT, s2c, w0, gT).shape)
    scale_m = bool(p.get("scale_margin"))
    mpath = [float(x) for x in (p.get("margin_path") or [])]   # 리서치가 준 연도별 GAAP 영업이익률 (상용화 전 적자가 여러 해 이어지는 경우 등)
    explicit = p.get("capex_pct") is not None
    if explicit:
        cap_list = [float(x) for x in (p["capex_pct"] if isinstance(p["capex_pct"], (list, tuple)) else [p["capex_pct"]])]
        dp = p.get("dep_pct") if p.get("dep_pct") is not None else p["da_pct"]
        dep_list = [float(x) for x in (dp if isinstance(dp, (list, tuple)) else [dp])]   # 연도별 D&A 비율 (인수 상각 감소 경로 표현)
        dep, nwc = dep_list[-1], float(p.get("nwc_pct") or 0.0)                          # 마지막 값 = 영구 수준
    nol = np.zeros_like(rev_prev)
    pv = np.zeros_like(rev_prev)
    df = np.ones_like(rev_prev)
    rows = []
    ebit = fcff = m = rev_prev
    for t in range(1, N + 1):
        if gpath is not None:
            g = np.asarray(gpath[t - 1], float)
        else:
            g = g1 if t == 1 else g2 if t == 2 else gT + (g2c - gT) * GROWTH_DECAY ** (t - 2)
        rev = rev_prev * (1 + g)
        if mpath:        # 명시 경로: 준 연차까지는 그대로, 그 뒤는 마지막 값에서 목표 마진으로 직선 수렴(목표 연차가 이미 지났으면 바로 목표)
            L = len(mpath)
            if t <= L:
                m = np.asarray(mpath[t - 1], float)
            else:
                m = mpath[-1] + (mT - mpath[-1]) * (min((t - L) / (conv - L), 1.0) if conv > L else 1.0)
        elif scale_m:    # 적자 기업: 손실은 매출에 비례하지 않는다(고정비) → 규모가 커질수록 손실률이 줄고 N년차에 목표 도달
            m = mT - (mT - m0) * np.minimum(rev0 / rev, 1.5) * max(1 - t / conv, 0.0)   # 목표 연차까지 끊김 없이 수렴
        else:
            m = m0 + (mT - m0) * min(t / conv, 1.0)
        ebit = rev * m
        rate = tax0 + (taxT - tax0) * min(t / min(N, TAX_CONV_YEARS), 1.0)
        use = np.minimum(nol, 0.8 * np.maximum(ebit, 0))    # 이월결손금: 예측 기간 발생분, 과세소득의 80% 한도(미국 세법). 기존 누적분은 리서치가 nonop_assets 로
        taxes = (np.maximum(ebit, 0) - use) * rate
        nol = nol - use + np.maximum(-ebit, 0)
        if explicit:     # Finbox 식 명시적 현금흐름: NOPAT + D&A − CAPEX − 순운전자본 증가 (리서치가 CAPEX 계획을 직접 줄 때)
            cx = cap_list[min(t - 1, len(cap_list) - 1)]
            reinv = (cx - dep_list[min(t - 1, len(dep_list) - 1)]) * rev + nwc * (rev - rev_prev)
            fcff = ebit - taxes - reinv
        else:
            reinv = np.maximum(rev - rev_prev, 0) / s2c
            fcff = ebit - taxes - reinv + amort0 * AMORT_DECAY ** t
        w = w0 + (wT - w0) * min((t - 1) / max(min(N, WACC_CONV_YEARS) - 1, 1), 1.0)
        df = df / (1 + w)
        pv = pv + fcff * df * np.sqrt(1 + w)                # 기중(mid-year) 할인
        if detail:
            rows.append({"t": t, "g": float(g), "rev": float(rev), "margin": float(m), "ebit": float(ebit),
                         "tax": float(taxes), "reinv": float(reinv), "fcff": float(fcff), "wacc": float(w), "df": float(df)})
        rev_prev = rev
    roicT = wT + f("roic_prem")
    rr = np.clip(gT / roicT, 0, 0.9)
    nopat_T = rev_prev * m * (1 - taxT)
    if explicit:         # D&A 가 유지 CAPEX 를 넘는 부분(인수 무형자산 상각 등)은 현금이 나가지 않는 비용 → 세후로 되돌린다
        nopat_T = nopat_T + (1 - taxT) * max(dep - cap_list[-1], 0.0) * rev_prev
    # 영구 구간의 성장에는 두 방식 모두 재투자가 든다: 재투자율 = g ÷ 신규투자수익률. (명시적 빌드에서 마지막 해의 CAPEX≈D&A 를
    # 그대로 영구 적용하면 순투자 없이 영원히 성장하는 셈이 되어 신규투자수익률이 수백 % 로 계산된다)
    fcff_t1 = nopat_T * (1 + gT) * (1 - rr)
    # 예측 구간을 기중(mid-year) 할인했으므로 영구 구간의 현금흐름도 매년 중간에 들어온다 → 잔존가치에 (1+WACC)^0.5
    # (Exit 멀티플 방식은 N년 말 시점의 거래가치이므로 보정하지 않는다)
    mid = np.sqrt(1 + wT)
    tv_g = fcff_t1 / np.maximum(wT - gT, 0.025) * mid
    if terminal == "growth":
        tv = tv_g
    elif terminal == "ebitda":
        tv = np.maximum(ebit + rev_prev * (dep if explicit else f("da_pct")), 0) * np.asarray(mult, float)
    else:
        tv = rev_prev * np.asarray(mult, float)
    roll = (1 + w0) ** float(p.get("stub") or 0.0)         # 재무제표 기준일 → 평가일까지의 경과분만큼 가치를 앞으로 굴린다
    pv, tv_pv = pv * roll, tv * df * roll
    ev = pv + tv_pv
    if not detail:
        return ev
    ebitda_n = float(ebit + rev_prev * (dep if explicit else f("da_pct")))
    return {"ev": float(ev), "pv_fcff": float(pv), "tv": float(tv), "pv_tv": float(tv_pv),
            "tv_share": float(tv_pv / ev) if float(ev) > 0 else None, "rows": rows, "nopat_T": float(nopat_T),
            "rev_n": float(rev_prev), "ebitda_n": ebitda_n, "fcff_n": float(fcff), "wT": float(wT),
            "tv_growth": float(tv_g), "fund_ev_ebitda": float(tv_g / ebitda_n) if ebitda_n > 0 else None,
            "fund_ev_rev": float(tv_g / rev_prev),
            "implied_g": float((tv * wT - fcff_t1 / (1 + gT) * mid) / (tv + fcff_t1 / (1 + gT) * mid)) if float(tv) > 0 else None,
            "N": N, "w0": float(w0), "nopat_n": float(ebit - taxes), "reinv_n": float(reinv)}


def per_share(ev, D: Dict[str, Any], p_fail=0.0):
    eq = ev - D["debt_fin"] - D["preferred"] - D["minority"] + D["cash"]
    return eq * (1 - p_fail) / D["shares"]


def _scen(p: Dict[str, Any], sign: int) -> Dict[str, Any]:
    """sign=-1 보수 / +1 낙관: 성장·마진·할인율을 동시에 한 단계씩 이동."""
    q = dict(p)
    sg = lambda g: max(0.15 * abs(g), 0.015)               # noqa: E731
    q["g1"], q["g2"] = p["g1"] + sign * sg(p["g1"]), p["g2"] + sign * sg(p["g2"])
    q["mT"] = p["mT"] + sign * max(0.10 * abs(p["mT"]), 0.01)
    for k in ("w0", "wT5", "wT10"):
        q[k] = p[k] - sign * 0.0075
    q["gT"] = p["gT"] + sign * 0.005
    return q


def _res(mid: str, **kw) -> Dict[str, Any]:
    r = {"id": mid, "name": MODEL_KO[mid], "family": FAMILY[mid], "applicable": True, "mid": None, "low": None,
         "high": None, "rel": 1.0, "notes": [], "checks": [], "reason": ""}
    r.update(kw)
    return r


def _na(mid: str, reason: str) -> Dict[str, Any]:
    return _res(mid, applicable=False, reason=reason)


def _check(r: Dict[str, Any], name: str, ok: bool, text: str, penalty: float = 1.0) -> None:
    r["checks"].append({"name": name, "ok": bool(ok), "text": text})
    if not ok:
        r["rel"] *= penalty


def _finish(r: Dict[str, Any]) -> Dict[str, Any]:
    if not r["applicable"]:
        return r
    if not _ok(r["mid"]) or r["mid"] <= 0:
        r["applicable"] = False
        r["reason"] = "산출 가치가 0 이하 (부채·현금 소진이 사업가치를 초과)" if _ok(r["mid"]) else "산출 불가"
        return r
    lo, hi = (r["low"] if _ok(r["low"]) else r["mid"]), (r["high"] if _ok(r["high"]) else r["mid"])
    r["low"], r["high"] = max(min(lo, r["mid"]), 0.0), max(hi, r["mid"])
    return r


# ─── ④-c 모델들 ─────────────────────────────────────────────────────────────
def model_dcf(D, P, PS, K, N: int, kind: str, base: Dict[str, Any]) -> Dict[str, Any]:
    mid = f"dcf_{kind}_{N}y"
    r = _res(mid)
    if P["mT"] <= 0:
        return _na(mid, "목표 영업이익률이 0 이하")
    own_cap = None
    mult = lo_m = hi_m = None
    if kind == "ebitda":
        if base["ebitda_n"] <= 0:
            return _na(mid, "예측 말 EBITDA 가 음수")
        peer = (PS.get("ev_ebitda") or {}).get("median")
        own = _div(D["ev"], D["ebitda"]) if _ok(D["ebitda"]) and D["ebitda"] > 0 else None
        fund = base["fund_ev_ebitda"]
        if _ok(own) and _ok(peer):
            own_cap = min(own, 1.5 * peer)
        elif _ok(own):
            own_cap = min(own, 30.0)
        parts = [(peer, 0.45 if N < 8 else 0.35), (own_cap, 0.20 if N < 8 else 0.10), (fund, 0.35 if N < 8 else 0.55)]
        parts = [(v, w) for v, w in parts if _ok(v) and v > 0]
        if not parts or not _ok(peer if _ok(peer) else own_cap):
            return _na(mid, "Exit 멀티플 근거(peer·자기 EV/EBITDA) 없음")
        mult = _clip(sum(v * w for v, w in parts) / sum(w for _, w in parts), 4.0, 30.0 if N < 8 else 25.0)
        r["notes"].append("Exit EV/EBITDA %.1f배 = peer 중앙값 %s · 현재 %s · 성숙기 이론값 %s 가중평균" % (
            mult, f"{peer:.1f}" if _ok(peer) else "N/A", f"{own:.1f}" if _ok(own) else "N/A", f"{fund:.1f}" if _ok(fund) else "N/A"))
    elif kind == "revenue":
        peer = (PS.get("ev_rev") or {}).get("median")
        pm = (PS.get("op_margin_profitable") or {}).get("median")
        adj = _clip(P["mT"] / pm, 0.5, 2.0) if _ok(pm) and pm > 0.03 else 1.0
        fund = base["fund_ev_rev"]
        parts = [(peer * adj if _ok(peer) else None, 0.5 if N < 8 else 0.3), (fund, 0.5 if N < 8 else 0.7)]
        parts = [(v, w) for v, w in parts if _ok(v) and v > 0]
        if not parts:
            return _na(mid, "Exit EV/Revenue 근거 없음")
        mult = _clip(sum(v * w for v, w in parts) / sum(w for _, w in parts), 0.3, 15.0)
        r["notes"].append("Exit EV/Revenue %.1f배 = peer 중앙값 %s(마진 보정 ×%.2f) · 목표 마진 기반 이론값 %.1f 가중평균" % (
            mult, f"{peer:.1f}" if _ok(peer) else "N/A", adj, fund))
    if mult is not None:
        lo_m, hi_m = mult * 0.85, mult * 1.15
    term = "growth" if kind == "growth" else kind
    d = dcf(P, N, term, mult, detail=True)
    r["mid"] = float(per_share(d["ev"], D, P["p_fail"]))
    r["low"] = float(per_share(dcf(_scen(P, -1), N, term, lo_m), D, P["p_fail"]))
    r["high"] = float(per_share(dcf(_scen(P, +1), N, term, hi_m), D, P["p_fail"]))
    r["detail"] = {"ev": d["ev"], "tv_share": d["tv_share"], "exit_multiple": mult, "implied_g": d["implied_g"], "wacc": P["w0"], "wacc_T": d["wT"]}
    if kind == "ebitda":
        r["detail"]["exit_market"] = {"peer": peer if _ok(peer) else None, "own": own_cap}
    if _ok(d["tv_share"]):
        _check(r, "잔존가치 비중", d["tv_share"] <= 0.85, f"기업가치의 {d['tv_share'] * 100:.0f}%가 잔존가치"
               + ("" if d["tv_share"] <= 0.85 else " — 먼 미래 가정 의존도 높음"), 0.75)
    if kind != "growth" and _ok(d["implied_g"]):
        lim = K["rf"] + 0.01
        _check(r, "Exit 내재 성장률", d["implied_g"] <= lim, f"Exit 멀티플이 내포한 영구성장률 {d['implied_g'] * 100:.1f}%"
               + ("" if d["implied_g"] <= lim else f" > 무위험금리+1%({lim * 100:.1f}%) — 멀티플이 낙관적"), 0.7)
    if P["p_fail"] > 0:
        r["notes"].append(f"현금 소진 구조: 자금조달 실패 확률 {P['p_fail'] * 100:.0f}% 를 주주가치에 반영")
    return r


def _fair_multiple(key: str, D, P, PS, slope_g: float, slope_m: float) -> Optional[Dict[str, float]]:
    st = PS.get(key)
    if not st or st["n"] < 3:
        return None
    pg, pm = (PS.get("rev_growth") or {}).get("median"), (PS.get("op_margin") or {}).get("median")
    dg = _clip(D["g1"] - pg, -0.3, 0.5) if _ok(pg) else 0.0
    m_co = D["m_ttm"] if _ok(D["m_ttm"]) else 0.0
    dm = _clip(m_co - pm, -0.3, 0.3) if _ok(pm) else 0.0
    adj = math.exp(_clip(slope_g * dg + slope_m * dm, math.log(0.6), math.log(1.8)))
    return {"mid": st["median"] * adj, "low": st["p25"] * adj, "high": st["p75"] * adj, "adj": adj, "n": st["n"],
            "median": st["median"]}


def _peer_rel(r: Dict[str, Any], fm: Dict[str, float], scope: str) -> None:
    _check(r, "peer 수", fm["n"] >= 5, f"유효 peer {fm['n']}개" + ("" if fm["n"] >= 5 else " — 표본 부족"), 0.7)
    if "sector" in scope:
        _check(r, "peer 범위", False, "동일 산업 peer 부족 → 섹터 상위 종목으로 보충", 0.8)
    r["notes"].append(f"peer 중앙값 {fm['median']:.1f}배 × 성장·마진 보정 {fm['adj']:.2f} = 적용 {fm['mid']:.1f}배")


def model_pe(D, P, PS, A, scope) -> Dict[str, Any]:
    r = _res("pe")
    vals, lows, highs, ws = [], [], [], []
    eps_t = D["eps_basis"]
    if A["code"] == "CYCLICAL" and _ok(D["m_ttm"]) and D["m_ttm"] > 0 and _ok(eps_t) and eps_t > 0:
        eps_t = eps_t * _clip(P["mT"] / D["m_ttm"], 0.4, 2.5)
        r["notes"].append(f"경기순환: 후행 EPS 를 미드사이클 마진으로 정상화 → {eps_t:.2f}")
    fm_f = _fair_multiple("fpe", D, P, PS, 1.0, 0.3)
    fm_t = _fair_multiple("pe", D, P, PS, 1.0, 0.3)
    if fm_f and _ok(D["eps_ntm"]) and D["eps_ntm"] > 0:
        vals.append(D["eps_ntm"] * fm_f["mid"]); lows.append(D["eps_ntm"] * fm_f["low"]); highs.append(D["eps_ntm"] * fm_f["high"]); ws.append(0.6)
        r["notes"].append(f"선행: NTM EPS {D['eps_ntm']:.2f} × {fm_f['mid']:.1f}배 (peer 선행 P/E 중앙값 {fm_f['median']:.1f}, 보정 {fm_f['adj']:.2f})")
    if fm_t and _ok(eps_t) and eps_t > 0:
        vals.append(eps_t * fm_t["mid"]); lows.append(eps_t * fm_t["low"]); highs.append(eps_t * fm_t["high"]); ws.append(0.4)
        r["notes"].append(f"후행: EPS {eps_t:.2f}({D['eps_note']}) × {fm_t['mid']:.1f}배 (peer 후행 P/E 중앙값 {fm_t['median']:.1f})")
    if not vals:
        return _na("pe", "EPS 가 음수이거나 peer P/E 표본 부족")
    w = sum(ws)
    r["mid"] = sum(v * x for v, x in zip(vals, ws)) / w
    r["low"] = sum(v * x for v, x in zip(lows, ws)) / w
    r["high"] = sum(v * x for v, x in zip(highs, ws)) / w
    fm = fm_f or fm_t
    _check(r, "peer 수", fm["n"] >= 5, f"유효 peer {fm['n']}개" + ("" if fm["n"] >= 5 else " — 표본 부족"), 0.7)
    if "sector" in scope:
        _check(r, "peer 범위", False, "동일 산업 peer 부족 → 섹터 상위 종목으로 보충", 0.8)
    return r


def model_ev_multiple(mid: str, D, P, PS, A, K, scope) -> Dict[str, Any]:
    r = _res(mid)
    if mid == "ev_ebitda":
        basis = D["ebitda"]
        if A["code"] == "CYCLICAL" and _ok(basis):
            basis = D["rev"] * (P["mT"] + D["da_pct"])
            r["notes"].append(f"경기순환: 미드사이클 마진 기준 정상화 EBITDA {basis / 1e9:.2f}B")
        if not _ok(basis) or basis <= 0:
            return _na(mid, "EBITDA 가 음수")
        fm = _fair_multiple("ev_ebitda", D, P, PS, 1.0, 0.3)
        if not fm:
            return _na(mid, "peer EV/EBITDA 표본 부족")
        evs = [basis * fm[k] for k in ("mid", "low", "high")]
    else:
        st = PS.get("ev_rev")
        if not st or st["n"] < 3:
            return _na(mid, "peer EV/Revenue 표본 부족")
        pg, pm = (PS.get("rev_growth") or {}).get("median"), (PS.get("op_margin_profitable") or {}).get("median")
        m_eff = max(D["m_ttm"] if _ok(D["m_ttm"]) else 0.0, 0.5 * P["mT"])
        adj_m = _clip(m_eff / pm, 0.5, 2.0) if _ok(pm) and pm > 0.03 else 1.0
        adj_g = math.exp(_clip(1.2 * (_clip(D["g1"] - pg, -0.3, 0.5) if _ok(pg) else 0.0), math.log(0.7), math.log(1.6)))
        adj = _clip(adj_m * adj_g, 0.5, 2.2)
        fm = {"mid": st["median"] * adj, "low": st["p25"] * adj, "high": st["p75"] * adj, "adj": adj, "n": st["n"], "median": st["median"]}
        if A["code"] == "PRE_REVENUE":
            # 매출이 미미한 단계: 2년 뒤 매출 × peer 멀티플을 자기자본비용으로 현재가치 환산
            rev2 = D["rev"] * (1 + P["g1"]) * (1 + P["g2"])
            disc = (1 + K["ke"]) ** 2
            evs = [rev2 * fm[k] / disc for k in ("mid", "low", "high")]
            r["notes"].append(f"선행 방식: 2년 뒤 매출 {rev2 / 1e9:.2f}B × 멀티플 ÷ (1+자기자본비용)² — 현재 매출은 사업 규모를 대표하지 못함")
            r["rel"] *= 0.8
        else:
            evs = [D["rev"] * fm[k] for k in ("mid", "low", "high")]
    r["mid"], r["low"], r["high"] = (float(per_share(e, D)) for e in evs)
    _peer_rel(r, fm, scope)
    return r


def model_ps(D, P, PS, scope) -> Dict[str, Any]:
    r = _res("ps")
    st = PS.get("ps")
    if not st or st["n"] < 3:
        return _na("ps", "peer P/S 표본 부족")
    pm = (PS.get("op_margin_profitable") or {}).get("median")
    m_eff = max(D["m_ttm"] if _ok(D["m_ttm"]) else 0.0, 0.5 * P["mT"])
    adj = _clip(m_eff / pm, 0.5, 2.0) if _ok(pm) and pm > 0.03 else 1.0
    sps = D["rev"] / D["shares"]
    r["mid"], r["low"], r["high"] = sps * st["median"] * adj, sps * st["p25"] * adj, sps * st["p75"] * adj
    _peer_rel(r, {"mid": st["median"] * adj, "adj": adj, "n": st["n"], "median": st["median"]}, scope)
    return r


def model_pb(D, PS, K, A, g_term, scope) -> Dict[str, Any]:
    r = _res("pb")
    b = D["bvps"]
    if not _ok(b) or b <= 0:
        return _na("pb", "주당순자산이 0 이하")
    vals = []
    st, roe = PS.get("pb"), D["roe"]
    if st and st["n"] >= 3:
        proe = (PS.get("roe") or {}).get("median")
        adj = math.exp(_clip(2.0 * _clip((roe or 0) - proe, -0.15, 0.20), math.log(0.6), math.log(1.8))) if _ok(roe, proe) else 1.0
        vals.append((b * st["median"] * adj, b * st["p25"] * adj, b * st["p75"] * adj))
        r["notes"].append(f"peer P/B 중앙값 {st['median']:.2f}배 × ROE 보정 {adj:.2f}")
        _check(r, "peer 수", st["n"] >= 5, f"유효 peer {st['n']}개", 0.7)
    if A["code"] in ("BANK", "INSURANCE") and _ok(roe) and roe > 0:
        ke, g = K["ke"], min(g_term, K["ke"] - 0.03)
        j = lambda x: _clip((x - g) / (ke - g), 0.3, 4.0)   # noqa: E731
        vals.append((b * j(roe), b * j(roe - 0.02), b * j(roe + 0.02)))
        r["notes"].append(f"이론 P/B = (ROE {roe * 100:.1f}% − g) ÷ (자기자본비용 {ke * 100:.1f}% − g) = {j(roe):.2f}배")
    if not vals:
        return _na("pb", "peer P/B 표본 부족")
    r["mid"], r["low"], r["high"] = (sum(v[i] for v in vals) / len(vals) for i in range(3))
    if A["code"] == "PRE_REVENUE":
        r["notes"].append("초기 단계: 순자산(주로 현금)이 하방 기준선 역할")
    return r


def model_ddm(mid: str, D, K, A, g_term) -> Dict[str, Any]:
    r = _res(mid)
    dps, ke = D["dps"], K["ke"]
    if not _ok(dps) or dps <= 0:
        return _na(mid, "무배당")
    payout = D["payout"]
    if A["code"] == "REIT" and _ok(D["ffo"]) and D["ffo"] > 0:
        payout = dps * D["shares"] / D["ffo"]
    if payout is None or payout <= 0:
        return _na(mid, "이익이 음수 — 배당 지속 가능성 판단 불가")
    if payout < 0.30 and A["code"] != "REIT":
        return _na(mid, f"배당성향 {payout * 100:.0f}% · 수익률 {D['div_yield'] * 100:.1f}%: 배당이 주된 환원 경로가 아님 (DDM 은 가치를 과소평가)")
    if payout > 1.15:
        return _na(mid, f"배당성향 {payout * 100:.0f}%: 이익으로 배당을 감당하지 못함")
    roe = D["roe"]
    sust = roe * (1 - min(payout, 1.0)) if _ok(roe) and roe > 0 else None
    cands = [g for g in (D["eps_g"], D["dps_g"], sust) if _ok(g)]
    g_s = _clip(median(cands), 0.0, 0.12) if cands else g_term
    if mid == "ddm_stable":
        if payout < 0.40 and A["code"] != "REIT":
            return _na(mid, f"배당성향 {payout * 100:.0f}%: 이익의 대부분을 유보 → 현재 배당만 영구 연장하는 안정성장 모형은 과소평가 (다단계 모형 사용)")
        g = _clip(min(g_s, g_term + 0.01), 0.0, ke - 0.03)
        v = lambda k, gg: dps * (1 + gg) / max(k - gg, 0.02)  # noqa: E731
        r["mid"], r["low"], r["high"] = v(ke, g), v(ke + 0.0075, max(g - 0.005, 0)), v(ke - 0.0075, min(g + 0.005, ke - 0.0325))
        r["notes"].append(f"DPS {dps:.2f} × (1+{g * 100:.1f}%) ÷ (자기자본비용 {ke * 100:.1f}% − {g * 100:.1f}%)")
    else:
        # 성장이 둔화되면 재투자 필요가 줄어 배당성향이 오른다: 성숙기 배당성향 = 1 − g∞ ÷ ROE∞ (현 배당성향보다 낮아지지는 않음)
        roe_T = _clip(roe, ke, ke + 0.04) if _ok(roe) and roe > 0 else ke
        pay_T = payout if A["code"] == "REIT" else _clip(max(payout, 1 - g_term / roe_T), payout, 1.0)

        def v(k, gs):
            pv, e_ = 0.0, dps / payout
            for t in range(1, 6):
                e_ *= 1 + gs + (g_term - gs) * (t - 1) / 5
                pv += e_ * payout / (1 + k) ** t
            return pv + e_ * (1 + g_term) * pay_T / max(k - g_term, 0.02) / (1 + k) ** 5
        r["mid"], r["low"], r["high"] = v(ke, g_s), v(ke + 0.0075, max(g_s - 0.02, 0)), v(ke - 0.0075, g_s + 0.02)
        r["notes"].append(f"1단계 5년: 이익성장 {g_s * 100:.1f}%→{g_term * 100:.1f}% · 배당성향 {payout * 100:.0f}% / "
                          f"이후 영구성장 · 성숙기 배당성향 {pay_T * 100:.0f}% (자기자본비용 {ke * 100:.1f}%)")
    fit = 0.8 <= payout <= 1.1 or A["code"] == "REIT"
    _check(r, "배당 대표성", fit or payout >= 0.5, f"배당성향 {payout * 100:.0f}%"
           + ("" if fit else " — 배당이 잉여현금의 80~110% 일 때 DDM 이 가장 잘 맞는다" + (" (그 미만은 과소평가 경향)" if payout < 0.8 else " (여력 부족)")),
           0.6)
    if not fit and payout >= 0.5:
        r["rel"] *= 0.8
    return r


def model_epv(D, P, K, A) -> Dict[str, Any]:
    r = _res("epv")
    ms = [m for _, m in D["margin_hist"][-5:]] + ([D["m_ttm"]] if _ok(D["m_ttm"]) else [])
    m_norm = D["m_long"] if (A["code"] == "CYCLICAL" and _ok(D["m_long"])) else (median(ms) if ms else None)
    if not _ok(m_norm) or m_norm <= 0:
        return _na("epv", "정상화 영업이익이 0 이하")
    lo_m = min(m_norm, _pct(ms, 25)) if len(ms) >= 3 else m_norm * 0.85
    hi_m = max(m_norm, _pct(ms, 75)) if len(ms) >= 3 else m_norm * 1.15
    v = lambda m, w: float(per_share(D["rev"] * m * (1 - P["taxT"]) / w, D))  # noqa: E731
    r["mid"], r["low"], r["high"] = v(m_norm, K["wacc"]), v(lo_m, K["wacc"] + 0.0075), v(hi_m, K["wacc"] - 0.0075)
    r["notes"].append(f"정상화 영업이익률 {m_norm * 100:.1f}% × 매출 × (1−세율) ÷ WACC {K['wacc'] * 100:.1f}% — 성장 0 가정(하방 기준선)")
    return r


def model_rim(D, K, g_term) -> Dict[str, Any]:
    r = _res("residual_income")
    b0, ke = D["bvps"], K["ke"]
    if not _ok(b0) or b0 <= 0:
        return _na("residual_income", "주당순자산이 0 이하")
    roe0 = _div(D["eps_ntm"], b0) if _ok(D["eps_ntm"]) and D["eps_ntm"] > 0 else D["roe"]
    if not _ok(roe0):
        return _na("residual_income", "ROE 산출 불가")
    payout = _clip(D["payout"] if _ok(D["payout"]) else 0.4, 0.2, 0.8)

    def v(k, roe_start, omega):
        roe_t = k + _clip(0.3 * (roe_start - k), -0.02, 0.04)
        b, pv, ri = b0, 0.0, 0.0
        for t in range(1, 11):
            roe = roe_start + (roe_t - roe_start) * t / 10
            ri = (roe - k) * b
            pv += ri / (1 + k) ** t
            b += roe * b * (1 - payout)
        return b0 + pv + ri * omega / (1 + k - omega) / (1 + k) ** 10
    r["mid"], r["low"], r["high"] = v(ke, roe0, 0.6), v(ke + 0.0075, roe0 - 0.015, 0.5), v(ke - 0.0075, roe0 + 0.015, 0.7)
    r["notes"].append(f"순자산 {b0:.2f} + 초과이익(ROE {roe0 * 100:.1f}% − 자기자본비용 {ke * 100:.1f}%)의 현재가치, 초과수익은 10년간 점진 소멸")
    return r


def _eps_growth_5y(D, base10: Dict[str, Any], P) -> float:
    if _ok(D["ltg"]) and -0.1 < D["ltg"] < 0.6:
        return _clip(D["ltg"], 0.0, 0.35)
    rows = base10["rows"]
    n0 = D["rev"] * max(P["m0"], 0.01)
    n5 = rows[4]["ebit"]
    g = _cagr(n0, n5, 5) if n5 > 0 else None
    return _clip(g if _ok(g) else P["g2"], 0.0, 0.35)


def model_lynch(D, base10, P) -> Dict[str, Any]:
    r = _res("lynch")
    eps = D["eps_ntm"] if _ok(D["eps_ntm"]) and D["eps_ntm"] > 0 else D["eps_basis"]
    if not _ok(eps) or eps <= 0:
        return _na("lynch", "EPS 가 음수")
    g = _eps_growth_5y(D, base10, P)
    if g < 0.08:
        return _na("lynch", f"이익 성장률 {g * 100:.0f}%: PEG 는 성장주용 — 저성장 기업에는 과도하게 낮은 배수를 준다")
    pe = lambda x: _clip(100 * x + 100 * D["div_yield"], 6.0, 35.0)  # noqa: E731
    r["mid"], r["low"], r["high"] = eps * pe(g), eps * pe(g * 0.75), eps * pe(g * 1.25)
    r["notes"].append(f"공정 P/E = 5년 이익성장률 {g * 100:.0f}% + 배당수익률 {D['div_yield'] * 100:.1f}% = {pe(g):.1f}배 (PEG 1, 상한 35배)")
    return r


def model_graham(D, base10, P, aaa: float) -> Dict[str, Any]:
    r = _res("graham")
    eps = D["eps_basis"]
    if not _ok(eps) or eps <= 0:
        return _na("graham", "EPS 가 음수")
    g = min(_eps_growth_5y(D, base10, P), 0.15)
    v = lambda gg: eps * (8.5 + 2 * 100 * gg) * 4.4 / (aaa * 100)  # noqa: E731
    r["mid"], r["low"], r["high"] = v(g), v(g * 0.7), v(min(g * 1.3, 0.18))
    r["notes"].append(f"EPS {eps:.2f} × (8.5 + 2×{g * 100:.0f}) × 4.4 ÷ AAA 회사채 수익률 {aaa * 100:.2f}%")
    return r


def model_ffo(D, K, g_term) -> Dict[str, Any]:
    r = _res("p_ffo")
    if not _ok(D["ffo"]) or D["ffo"] <= 0:
        return _na("p_ffo", "FFO 가 음수")
    affo = 0.85 * D["ffo"] / D["shares"]                    # 유지보수 자본지출 15% 차감 근사
    g = _clip(D["g1"], 0.0, 0.04)
    mult = lambda k, gg: _clip((1 + gg) / max(k - gg, 0.03), 8.0, 28.0)  # noqa: E731
    r["mid"], r["low"], r["high"] = affo * mult(K["ke"], g), affo * mult(K["ke"] + 0.0075, max(g - 0.005, 0)), affo * mult(K["ke"] - 0.0075, g + 0.005)
    r["notes"].append(f"주당 AFFO(≈FFO×0.85) {affo:.2f} × 이론 배수 {mult(K['ke'], g):.1f}배 (자기자본비용 {K['ke'] * 100:.1f}%, 성장 {g * 100:.1f}%)")
    return r


# ─── 진단: 역 DCF · 몬테카를로 · 민감도 ──────────────────────────────────────
def reverse_dcf(D, P, base10) -> Dict[str, Any]:
    """현재 주가가 내포한 가정을 역산한다.

    ① 향후 5년 매출 CAGR (이후 5년은 영구성장률로 선형 수렴; 마진·재투자·할인율은 기본 가정)
    ② ①로 설명되지 않으면 목표 영업이익률 (성장은 기본 경로) — '성장이 아니라 수익성 도약'에 가격이 매겨진 경우."""
    gT, price = P["gT"], D["price"]

    def val_g(c: float) -> float:
        path = [c] * 5 + [c + (gT - c) * k / 5 for k in range(1, 6)]
        return float(per_share(dcf(P, 10, "growth", gpath=path), D, P["p_fail"]))

    def val_m(m: float) -> float:
        return float(per_share(dcf({**P, "mT": m}, 10, "growth"), D, P["p_fail"]))

    def solve(fn, lo: float, hi: float) -> float:
        for _ in range(50):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if fn(mid) < price else (lo, mid)
        return (lo + hi) / 2
    base_cagr = _cagr(D["rev"], base10["rows"][4]["rev"], 5)
    out = {"base_cagr_5y": base_cagr, "target_margin": P["mT"], "hist_cagr": D["g_hist"]}
    lo, hi = -0.30, 1.50
    v_lo, v_hi = val_g(lo), val_g(hi)
    if v_lo < price < v_hi:
        c = solve(val_g, lo, hi)
        gap = c - base_cagr if _ok(base_cagr) else None
        return {**out, "status": "OK", "implied_cagr_5y": c, "gap_vs_base": gap,
                "text": f"현재가는 향후 5년 연 {c * 100:.1f}% 매출 성장(목표 영업이익률 {P['mT'] * 100:.0f}%)을 반영"
                        + (f" — 기본 가정 {base_cagr * 100:.1f}% 대비 {gap * 100:+.1f}%p" if gap is not None else "")}
    if price <= min(v_lo, v_hi):
        return {**out, "status": "BELOW", "text": "현재가는 5년간 연 −30% 역성장 시나리오의 가치보다도 낮음 — 시장이 존속 자체를 의심하거나 모델이 포착하지 못한 부채·희석이 있음"}
    m_hi = 0.60
    if val_m(P["mT"]) < price < val_m(m_hi):
        m = solve(val_m, P["mT"], m_hi)
        return {**out, "status": "MARGIN", "implied_margin": m,
                "text": f"성장률만으로는 현재가가 설명되지 않음 — 기본 성장 경로에서 목표 영업이익률 {m * 100:.0f}% 가 필요 "
                        f"(기본 가정 {P['mT'] * 100:.0f}%). 시장은 수익 구조의 도약 또는 아직 매출이 없는 신사업에 가격을 매기고 있음"}
    return {**out, "status": "ABOVE", "text": "현재가는 5년 연 150% 성장 또는 영업이익률 60% 가정으로도 설명되지 않음 — "
            "현 재무·컨센서스 밖의 요인(신사업 옵션가치·서사·수급)이 가격을 지배"}


def monte_carlo(ticker: str, D, P, N: int, exit_mult: Optional[float]) -> Dict[str, Any]:
    rng = np.random.default_rng(zlib.crc32(ticker.encode()))
    S = MC_SIMS
    sg = min(max(0.25 * abs(P["g1"]), 0.02) + min((D["g_disp"] or 0.0) / 1.5, 0.15), 0.30)
    eg = rng.normal(0, 1, S)
    q = dict(P)
    q["g1"] = P["g1"] + sg * eg
    q["g2"] = P["g2"] + sg * 0.8 * (0.8 * eg + 0.6 * rng.normal(0, 1, S))
    sm = max(0.15 * abs(P["mT"]), 0.5 * (D["m_std"] or 0.0), 0.01)
    q["mT"] = np.maximum(P["mT"] + rng.normal(0, sm, S), 0.005)
    dw = rng.normal(0, 0.0075, S)
    for k in ("w0", "wT5", "wT10"):
        q[k] = np.maximum(P[k] + dw, 0.04)
    q["gT"] = np.clip(P["gT"] + rng.normal(0, 0.004, S), 0.005, 0.04)
    q["s2c"] = P["s2c"] * np.exp(rng.normal(0, 0.2, S))
    ev = dcf(q, N, "growth")
    if exit_mult:
        ev = 0.5 * ev + 0.5 * dcf(q, N, "ebitda", exit_mult * np.exp(rng.normal(0, 0.2, S)))
    v = np.maximum(np.asarray(per_share(ev, D, P["p_fail"]), float), 0.0)
    pc = {f"p{k}": float(np.percentile(v, k)) for k in (5, 10, 25, 50, 75, 90, 95)}
    lo, hi = np.percentile(v, 1), np.percentile(v, 99)
    hist, edges = np.histogram(v[(v >= lo) & (v <= hi)], bins=28) if hi > lo else (np.array([S]), np.array([lo, lo + 1]))
    return {**pc, "mean": float(v.mean()), "prob_above_price": float((v > D["price"]).mean()), "sims": S, "horizon": N,
            "hist": [int(x) for x in hist], "edges": [float(x) for x in edges],
            "inputs": {"growth_sd": sg, "margin_sd": sm, "wacc_sd": 0.0075, "g_term_sd": 0.004, "s2c_logsd": 0.2}}


def sensitivity(D, P, N: int) -> Dict[str, Any]:
    dws, dgs = [-0.01, -0.005, 0.0, 0.005, 0.01], [-0.01, -0.005, 0.0, 0.005, 0.01]
    grid = []
    for dw in dws:
        q = dict(P)
        for k in ("w0", "wT5", "wT10"):
            q[k] = P[k] + dw
        grid.append([float(per_share(dcf({**q, "gT": P["gT"] + dg}, N, "growth"), D, P["p_fail"])) for dg in dgs])
    return {"wacc": [P["w0"] + d for d in dws], "g": [P["gT"] + d for d in dgs], "grid": grid, "horizon": N}


# ─── ⑤ 통합 ────────────────────────────────────────────────────────────────
def aggregate(models: List[Dict[str, Any]], code: str, price: float) -> Dict[str, Any]:
    table = WEIGHTS[code]
    used = [m for m in models if m["applicable"] and table.get(m["id"], 0) > 0]
    for m in models:
        m["included"], m["weight"] = False, 0.0
        if m["applicable"] and table.get(m["id"], 0) <= 0:
            m["reason"] = "이 유형에서는 참고용 (통합 가중치 0)"
    if not used:
        return {"fair": None}
    raw = [table[m["id"]] * m["rel"] for m in used]
    med = _wq([m["mid"] for m in used], raw, 0.5)
    for i, m in enumerate(used):                            # 통합 중앙값에서 3배 이상 벗어난 모델은 가중치 축소
        if not med / 3 <= m["mid"] <= med * 3:
            raw[i] *= 0.25
            m["checks"].append({"name": "이상치", "ok": False, "text": "모델 간 중앙값에서 3배 이상 이탈 — 가중치 1/4"})
    tot = sum(raw)
    ws = [x / tot for x in raw]
    mids = [m["mid"] for m in used]
    mean = sum(v * w for v, w in zip(mids, ws))
    wmed = _wq(mids, ws, 0.5)
    cv = math.sqrt(sum(w * (v - mean) ** 2 for v, w in zip(mids, ws))) / mean if mean > 0 else 1.0
    fair = 0.5 * mean + 0.5 * wmed if cv <= 0.35 else 0.25 * mean + 0.75 * wmed   # 편차가 크면 중앙값 쪽으로 (꼬리 모델 영향 축소)
    low = min(sum(m["low"] * w for m, w in zip(used, ws)), _wq(mids, ws, 0.25), fair)
    high = max(sum(m["high"] * w for m, w in zip(used, ws)), _wq(mids, ws, 0.75), fair)
    for m, w in zip(used, ws):
        m["included"], m["weight"] = True, w
    fam: Dict[str, List[Tuple[float, float]]] = {}
    for m, w in zip(used, ws):
        fam.setdefault("내재가치" if m["family"] in ("DCF", "배당", "수익력", "초과이익") else "상대가치·공식", []).append((m["mid"], w))
    fam_val = {k: sum(v * w for v, w in xs) / sum(w for _, w in xs) for k, xs in fam.items()}
    return {"fair": fair, "low": low, "high": high, "cv": cv, "n": len(used), "family": fam_val}


def _uncertainty(agg, code: str, D, F, dcfs: List[Dict[str, Any]]) -> Dict[str, Any]:
    score, why = 100.0, []
    n = agg["n"]
    pen = 0 if n >= 9 else 10 if n >= 5 else 25 if n >= 3 else 45
    if pen:
        score -= pen
        why.append(f"통합 모델 {n}개" + (" (3개 미만)" if n < 3 else ""))
    dp = min(35.0, agg["cv"] * 70)
    score -= dp
    if agg["cv"] > 0.25:
        why.append(f"모델 간 편차 큼 (변동계수 {agg['cv'] * 100:.0f}%)")
    if code in ARCH_UNCERTAINTY:
        score -= ARCH_UNCERTAINTY[code]
        why.append(f"'{ARCH_KO[code]}' 유형: 가치의 대부분이 먼 미래 가정에 의존")
    if _ok(D["m_ttm"]) and D["m_ttm"] < 0 and code not in ARCH_UNCERTAINTY.keys() - {"CYCLICAL", "HIGH_GROWTH"}:
        score -= 12
        why.append("현재 영업적자 — 정상화 이익 가정에 의존")
    widths = [(m["high"] - m["low"]) / m["mid"] for m in dcfs if m.get("included") and m["mid"] > 0]
    if widths and median(widths) > 0.8:
        score -= 6
        why.append(f"DCF 보수~낙관 폭이 기준값의 {median(widths) * 100:.0f}%")
    if not D["has_est"]:
        score -= 8
        why.append("애널리스트 컨센서스 없음")
    if len(F.get("peers") or []) < 5:
        score -= 8
        why.append("peer 5개 미만")
    if D["n_annual"] < 3:
        score -= 8
        why.append("연간 실적 3년 미만")
    tvs = [m["detail"]["tv_share"] for m in dcfs if m.get("included") and _ok((m.get("detail") or {}).get("tv_share"))]
    if tvs and median(tvs) > 0.8:
        score -= 5
        why.append(f"DCF 잔존가치 비중 {median(tvs) * 100:.0f}%")
    fv = agg["family"]
    if len(fv) == 2:
        a, b = fv["내재가치"], fv["상대가치·공식"]
        if max(a, b) / max(min(a, b), 1e-9) > 1.6:
            score -= 6
            why.append(f"내재가치({a:,.0f}) vs 상대가치({b:,.0f}) 괴리")
    score = max(score, 0.0)
    level = "LOW" if score >= 78 else "MEDIUM" if score >= 58 else "HIGH" if score >= 38 else "VERY_HIGH"
    return {"level": level, "label": UNC_KO[level], "score": round(score), "reasons": why}


# ─── 메인 ───────────────────────────────────────────────────────────────────
def evaluate(F: Dict[str, Any], price: Optional[float] = None, rf: Optional[float] = None,
             aaa: Optional[float] = None, as_of: Optional[str] = None,
             research: Optional[Dict[str, Any]] = None, erp: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    warnings = list(F.get("warnings") or [])
    if not _ok(rf) or not 0.005 <= rf <= 0.12:
        rf = RF_FALLBACK
        warnings.append(f"10년 국채금리 수집 실패 → {RF_FALLBACK * 100:.2f}% 가정")
    aaa = aaa if _ok(aaa) and 0.01 <= aaa <= 0.15 else rf + 0.009
    # 영구성장률: 장기 명목 성장률은 무위험금리를 넘지 못한다. 내재 ERP 는 '영구성장률 = 무위험금리' 전제로 추정된 값이므로
    # 영구성장률만 따로 낮게 잡으면(예: 금리와 무관하게 3%) 같은 시장을 두 번 보수적으로 보게 된다 → rf − 1%p, 2~4% 범위
    g_term = _clip(rf - 0.01, 0.02, 0.04)
    D = derive(F, price, g_term)
    A = classify(F, D)
    PS = peer_stats(F.get("peers") or [])
    K = capital(F, D, PS, rf, (erp or {}).get("erp"))
    K["erp_source"] = (erp or {}).get("label") or ("환경변수 SMA_ERP" if os.environ.get("SMA_ERP") else f"기본값 {ERP * 100:.1f}% (내재 ERP 수집 안 됨)")
    code, scope = A["code"], F.get("peer_scope") or ""
    fin = code in ("BANK", "INSURANCE")
    P = assumptions(F, D, A, K, PS, g_term)
    try:                                                    # 재무제표 기준일(TTM 종료) 이후 경과 기간
        P["stub"] = _clip((date.fromisoformat(str(as_of)) - date.fromisoformat(str(D["ttm_end"]))).days / 365.0, 0.0, 0.75)
    except (ValueError, TypeError):
        P["stub"] = 0.0
    for key, lab in (("capex", "CAPEX"), ("interest_expense", "이자비용"), ("da", "감가상각")):
        if (F.get("ttm") or {}).get(key) is None:
            warnings.append(f"TTM {lab} 자료 없음 — 0 으로 처리됨. 관련 가정(자본집약도·타인자본비용)은 리서치에서 직접 확인할 것")
    models: List[Dict[str, Any]] = []
    base5 = base10 = None
    exit10 = exitp = None
    if fin or code == "REIT":
        why = "금융업: 부채가 영업자산이라 FCFF·기업가치(EV) 개념 부적합" if fin else "REIT: 감가상각·자본구조 특성상 FFO·배당 모델 사용"
        models += [_na(f"dcf_{k}_{n}y", why) for n in (5, 10) for k in ("growth", "ebitda", "revenue")]
        models += [_na("epv", why), _na("ev_rev", why), _na("ps", why)]
        if fin:
            models.append(_na("ev_ebitda", why))
        base10 = {"rows": [{"ebit": 0.0, "rev": D["rev"] * (1 + D["g1"]) ** (i + 1)} for i in range(10)]}
    else:
        base5, base10 = dcf(P, 5, "growth", detail=True), dcf(P, 10, "growth", detail=True)
        short_ok = code != "PRE_REVENUE"
        for n, base in ((5, base5), (10, base10)):
            for kind in ("growth", "ebitda", "revenue"):
                if n == 5 and not short_ok:
                    models.append(_na(f"dcf_{kind}_5y", "초기 단계: 5년 안에 정상 수익성 도달 가정이 비현실적 → 10년 모델만 사용"))
                    continue
                m = model_dcf(D, P, PS, K, n, kind, base)
                if kind == "ebitda" and n == 10 and m["applicable"]:
                    exit10 = m["detail"]["exit_multiple"]
                    exitp = m["detail"]["exit_market"]
                models.append(m)
        models.append(model_epv(D, P, K, A) if code not in ("PRE_REVENUE", "HYPERGROWTH_UNPROFITABLE", "TURNAROUND")
                      else _na("epv", "현재 이익이 없어 '성장 0 수익력' 정의 불가"))
        models.append(model_ev_multiple("ev_rev", D, P, PS, A, K, scope))
        models.append(model_ps(D, P, PS, scope))
    if not fin:
        models.append(model_ev_multiple("ev_ebitda", D, P, PS, A, K, scope) if code != "PRE_REVENUE"
                      else _na("ev_ebitda", "EBITDA 가 음수"))
    models.append(model_pe(D, P, PS, A, scope))
    models.append(model_pb(D, PS, K, A, g_term, scope))
    models += [model_ddm("ddm_stable", D, K, A, g_term), model_ddm("ddm_multi", D, K, A, g_term)]
    models.append(model_rim(D, K, g_term) if fin else _na("residual_income", "금융업 전용 모델 (비금융은 FCFF DCF 가 동일 정보를 포함)"))
    models.append(model_ffo(D, K, g_term) if code == "REIT" else _na("p_ffo", "REIT 전용"))
    models += [model_lynch(D, base10, P), model_graham(D, base10, P, aaa)]
    if fin:
        P.update(w0=K["ke"])
    models = [_finish(m) for m in models]
    agg = aggregate(models, code, D["price"])
    order = {k: i for i, k in enumerate(MODEL_KO)}
    models.sort(key=lambda m: (not m.get("included"), not m["applicable"], -m.get("weight", 0), order[m["id"]]))
    mk = F.get("market") or {}
    out: Dict[str, Any] = {
        "version": VERSION, "ticker": F.get("ticker"), "as_of": as_of, "fundamentals_asof": F.get("fetched"),
        "currency": (F.get("profile") or {}).get("currency") or "USD", "price": D["price"],
        "archetype": A, "wacc": K, "assumptions": {k: v for k, v in P.items()}, "models": models,
        "peers": {"scope": scope, "stats": PS, "table": F.get("peers") or []},
        "analyst": {"target_mean": mk.get("target_mean"), "target_low": mk.get("target_low"),
                    "target_high": mk.get("target_high"), "n": mk.get("n_analysts"),
                    "upside_pct": (mk["target_mean"] / D["price"] - 1) * 100 if _ok(mk.get("target_mean")) else None},
        "metrics": {k: D[k] for k in ("mcap", "ev", "rev", "rev_ntm", "ebit", "ebitda", "ni", "fcf", "eps_gaap", "eps_core",
                                      "eps_ntm", "m_ttm", "m_med5", "m_long", "m_std", "g_hist", "g_long", "g1", "g2", "roic",
                                      "roe", "net_debt", "cash", "debt", "bvps", "dps", "div_yield", "payout", "fcf_yield",
                                      "runway", "ps", "tax_eff", "shares", "coverage")},
        "sources": F.get("sources") or [], "warnings": warnings + D["notes"],
    }
    out["method"] = "MECHANICAL"
    if agg.get("fair") is None:
        out.update({"fair_value": None, "status": "NO_MODEL",
                    "summary": "적용 가능한 모델이 없어 적정가치를 산출하지 못했습니다."})
        return apply_research(out, D, P, exitp, research, as_of) if research else out
    unc = _uncertainty(agg, code, D, F, [m for m in models if m["id"].startswith("dcf_")])
    fair, band = agg["fair"], MOS_BAND[unc["level"]]
    up = fair / D["price"] - 1
    over = D["price"] / fair - 1                            # 안전마진 밴드는 불확실성이 클수록 넓다 (양방향 대칭)
    verdict = ("DEEP_UNDERVALUED" if up > 2.5 * band else "UNDERVALUED" if up > band else
               "DEEP_OVERVALUED" if over > 2.5 * band else "OVERVALUED" if over > band else "FAIR")
    out.update({"status": "OK", "fair_value": fair, "fair_low": agg["low"], "fair_high": agg["high"],
                "upside_pct": up * 100, "verdict": verdict, "verdict_ko": VERDICT_KO[verdict], "mos_band_pct": band * 100,
                "uncertainty": unc, "n_models": agg["n"], "dispersion_cv": agg["cv"], "family_values": agg["family"]})
    if base10 is not None and not fin and code != "REIT":
        primary = 10 if code in ("HIGH_GROWTH", "HYPERGROWTH_UNPROFITABLE", "PRE_REVENUE", "TURNAROUND") else 5
        out["projection"] = {"rows": base10["rows"], "tv_share_10y": base10["tv_share"], "tv_share_5y": base5["tv_share"]}
        out["reverse_dcf"] = reverse_dcf(D, P, base10)
        try:
            out["monte_carlo"] = monte_carlo(F.get("ticker") or "", D, P, primary, exit10 if primary == 10 else None)
        except Exception as e:  # noqa: BLE001
            out["warnings"].append(f"몬테카를로 실패: {str(e)[:80]}")
        out["sensitivity"] = sensitivity(D, P, primary)
    if not 1 / 3 <= fair / D["price"] <= 3:
        out["model_gap"] = True                             # 모델과 시장의 괴리가 3배 이상: 적정가보다 '시장이 내포한 가정'이 핵심 정보
        out["warnings"].append("[기계적 기준선] 적정가와 현재가의 괴리가 3배 이상 — 현 재무·컨센서스 기반 모델로 설명되지 않는 가격. "
                               "적정가 수치보다 역DCF(시장 내재 가정)를 우선 참고")
    inc = [m for m in models if m.get("included")]
    top = ", ".join(f"{m['name']}({m['weight'] * 100:.0f}%)" for m in inc[:3])
    out["summary"] = (f"'{A['label']}' 유형 → {agg['n']}개 모델 통합 적정가 {fair:,.2f} (범위 {agg['low']:,.2f}~{agg['high']:,.2f}), "
                      f"현재가 {D['price']:,.2f} 대비 {up * 100:+.1f}% · {VERDICT_KO[verdict]} (불확실성 {unc['label']}). 주요 모델: {top}")
    return apply_research(out, D, P, exitp, research, as_of) if research else out


# ─── 리서치 기반 적정가 ──────────────────────────────────────────────────────
# 기계적 모델은 재무제표 숫자만 본다. 사업모델·계획·뉴스·실행력은 애널리스트 리서치(docs/VALUATION_RESEARCH.md)가
# 시나리오 가정(성장 경로·목표 마진·위험·확률)과 신사업 옵션가치로 번역하고, 이 함수는 그 가정을 같은 DCF 코어로 계산한다.
RESEARCH_STALE_DAYS = 100
CONF_PENALTY = {"HIGH": 0, "MEDIUM": 12, "LOW": 25}


def _verdict(fair: float, price: float, band: float) -> str:
    up, over = fair / price - 1, price / fair - 1
    return ("DEEP_UNDERVALUED" if up > 2.5 * band else "UNDERVALUED" if up > band else
            "DEEP_OVERVALUED" if over > 2.5 * band else "OVERVALUED" if over > band else "FAIR")


def _growth_path(explicit: Sequence[float], gT: float, min_len: int = 0) -> List[float]:
    """리서치가 준 연도별 성장률 → 영구성장률에 수렴할 때까지 감쇠시킨 전체 경로.

    잔존가치는 '안정 성장'을 전제한다. 그래서 성장률이 영구성장률 ±1%p 안에 들어올 때까지 예측 구간을 늘린다
    (최소 10년, 최장 MAX_HORIZON 년). 10년에서 무조건 끊으면 아직 성장 중인 회사는 11년차에 성장률이 절벽처럼 떨어진다.
    min_len = 이익률 경로·목표 마진 도달 연차가 요구하는 최소 길이 (잔존가치는 목표 마진에 도달한 뒤에 계산해야 한다)."""
    gp = [float(g) for g in explicit][:MAX_EXPLICIT]
    last = gp[-1]
    while len(gp) < MAX_HORIZON and (len(gp) < max(10, min_len) or abs(last - gT) > 0.01):
        last = gT + (min(last, GROWTH_SOFT_CAP) - gT) * GROWTH_DECAY
        gp.append(last)
    return gp


def scenario_value(D, P, exitp, s: Dict[str, Any], shift: float = 0.0) -> Dict[str, Any]:
    """시나리오 1개 → 주당 가치. `value_per_share` 가 직접 주어지면(금융·REIT 등) 그대로 쓴다.

    exitp = Exit 멀티플의 시장 근거 {"peer": peer EV/EBITDA 중앙값, "own": 현재 자기 배수(상한 적용)}.
    shift = 할인율 민감도 계산용 가감(모든 연도에 동일)."""
    if _ok(s.get("value_per_share")):
        return {"value": float(s["value_per_share"]), "how": "애널리스트 직접 산출", "rev5": None, "rev10": None,
                "cagr5": None, "direct": True}
    q = dict(P)
    q["mT"], q["conv"] = float(s["target_margin"]), int(s.get("margin_year") or 5)
    q["scale_margin"] = q["m0"] < 0
    adj = float(s.get("wacc_adj") or 0.0) + shift
    for k in ("w0", "wT5", "wT10"):
        q[k] = max(P[k] + adj, 0.04)
    if _ok(s.get("s2c")):
        q["s2c"] = float(s["s2c"])
    if _ok(s.get("terminal_growth")):
        q["gT"] = _clip(float(s["terminal_growth"]), 0.01, 0.04)
    else:
        # 영구 구간이 명시 구간의 마지막 해보다 빨리 성장할 수는 없다: 리서치가 말년 성장률을 엔진의 영구성장률보다 낮게 봤다면
        # 그 성장률이 그 시나리오의 안정 성장률이다 (하한 2% — 물가 수준의 명목 성장)
        q["gT"] = _clip(min(float(P["gT"]), float(s["rev_growth"][-1])), 0.02, float(P["gT"]))
    mp = [float(x) for x in (s.get("margin_path") or [])][:MAX_HORIZON]
    if mp:
        q["margin_path"] = mp
    gp = _growth_path(s["rev_growth"], q["gT"], max(len(mp), min(q["conv"], MAX_HORIZON)))
    N = len(gp)
    if s.get("capex_pct") is not None:                      # 명시적 현금흐름 빌드 (CAPEX·D&A·순운전자본을 매출 대비 비율로)
        q["capex_pct"] = s["capex_pct"]
        q["dep_pct"] = s["da_pct"] if s.get("da_pct") is not None else P["da_pct"]
        q["nwc_pct"] = float(s.get("nwc_pct") or 0.0)
    if _ok(s.get("tax_rate")):                              # 구조적으로 낮거나 높은 장기 세율의 근거가 있을 때
        q["taxT"] = _clip(float(s["tax_rate"]), 0.05, 0.35)
    # 실패 위험은 리서치가 정한다: 약세 시나리오가 이미 실패 경로를 그리고 있으면 엔진의 기계적 실패확률을 또 곱해서는 안 된다
    pf = float(s["fail_prob"]) if _ok(s.get("fail_prob")) else 0.0
    dg = dcf(q, N, "growth", gpath=gp, detail=True)
    xi = [float(x) for x in (s.get("extra_investment") or [])][:N]
    # 잔존가치의 재투자율(g ÷ 신규투자수익률)은 '이 시나리오가 그리는 성숙기'의 수익성에서 나와야 한다. 과거 실적의 ROIC 를 쓰면
    # 지금 적자인 회사는 시나리오가 높은 수익성을 가정해도 영구 구간에서는 초과수익 0 으로 계산된다(적자·전환기 기업에 불리한 쏠림).
    # 시나리오 말년 ROIC = 말년 세후영업이익 ÷ (현재 유형 투하자본 + 예측 기간 누적 순투자). 초과수익의 절반만 영구히 인정(상한 8%p)
    roic_N = None
    # 신사업(옵션)용 선투자는 본업의 투하자본이 아니다 — 그 수익은 옵션가치에 있으므로 본업 ROIC 의 분모에서 뺀다
    xi_core = [] if s.get("extra_investment_noncore") else xi
    ic_N = max(D.get("ic_tan") or 0.0, 0.0) + sum(r["reinv"] for r in dg["rows"]) + sum(xi_core)
    if ic_N > 0:
        roic_N = dg["nopat_T"] / ic_N
        q["roic_prem"] = _clip(0.5 * (roic_N - dg["wT"]), 0.0, 0.08)
        dg = dcf(q, N, "growth", gpath=gp, detail=True)
    ev, mult, ev_x = dg["ev"], None, None
    if exitp and q["mT"] > 0 and dg["ebitda_n"] > 0:
        # Exit 멀티플 = 시장 근거(peer·자기 배수) 45% + '이 시나리오'의 성숙기 이론 배수 55%. 이론 배수를 기계적 기준선에서
        # 빌려오면 마진·자본집약도가 다른 시나리오에 맞지 않는 배수가 적용된다(자본집약적 사업에서 두 방식이 크게 어긋나던 원인)
        mkt = [(v, w) for v, w in ((exitp.get("peer"), 0.35), (exitp.get("own"), 0.10)) if _ok(v) and v > 0]
        if mkt:
            parts = mkt + ([(dg["fund_ev_ebitda"], 0.55)] if _ok(dg["fund_ev_ebitda"]) and dg["fund_ev_ebitda"] > 0 else [])
            mult = _clip(sum(v * w for v, w in parts) / sum(w for _, w in parts), 4.0, 25.0)
            ev_x = float(dcf(q, N, "ebitda", mult, gpath=gp))
            ev = (1 - EXIT_BLEND) * dg["ev"] + EXIT_BLEND * ev_x
    # 매출보다 앞서 집행되는 투자(위성망·공장·광산 등): Sales-to-Capital 방식은 재투자를 매출 증가 시점에 잡으므로
    # 리서치가 연도별 추가 유출을 명시하면 기중 할인해 차감한다
    pv_x, dfx = 0.0, 1.0
    for t, x in enumerate(xi):
        w = dg["rows"][t]["wacc"]
        dfx /= (1 + w)
        pv_x += x * dfx * math.sqrt(1 + w)
    ev -= pv_x
    rows = dg["rows"]
    ebits = [r["ebit"] for r in rows]
    last_loss = max((r["t"] for r in rows if r["ebit"] <= 0), default=0)
    be = last_loss + 1 if 0 < last_loss < len(rows) else None           # 흑자 전환 = 마지막 적자 연도의 다음 해 (흑자→적자→흑자 경로 포함)
    loss_cum = -sum(x for x in ebits if x < 0)                          # 예측 기간 누적 영업손실
    inv = sum(r["reinv"] for r in rows[1:]) + sum(xi[1:])
    d_nopat = (rows[-1]["ebit"] - rows[-1]["tax"]) - (rows[0]["ebit"] - rows[0]["tax"])
    rev = [float(q["rev0"])] + [r["rev"] for r in rows]
    cap_T = dep_T = None
    if s.get("capex_pct") is not None:
        cx, dp = s["capex_pct"], q["dep_pct"]
        cap_T = float(cx[-1] if isinstance(cx, (list, tuple)) else cx)
        dep_T = float(dp[-1] if isinstance(dp, (list, tuple)) else dp)
    return {"extra_investment_pv": pv_x, "value": float(per_share(ev, D, pf)),
            "how": f"{N}년 DCF (영구성장" + (" · EBITDA Exit 평균" if ev_x is not None and EXIT_BLEND > 0 else "") + ")",
            "rev5": rev[5], "rev10": rev[10], "cagr5": _cagr(rev[0], rev[5], 5), "wacc": q["w0"], "wacc_T": dg["wT"],
            "fail_prob": pf, "N": N, "g_T": float(q["gT"]), "tv_share": dg["tv_share"], "ev_growth": dg["ev"] - pv_x,
            "ev_exit": (ev_x - pv_x) if ev_x is not None else None, "exit_multiple": mult,
            "fund_multiple": dg["fund_ev_ebitda"], "roic_incr": (d_nopat / inv) if inv > 0 else None,
            "roic_T": roic_N, "roic_prem": float(q["roic_prem"]),
            "margin_T": rows[-1]["margin"], "capex_T": cap_T, "dep_T": dep_T,
            "ebit_path": ebits, "breakeven_year": be, "loss_cum": loss_cum, "margin_path_given": bool(mp)}


SCEN_KO = {"bear": "약세", "base": "기본", "bull": "강세"}


def _klass(verdict: str) -> str:
    return "UNDER" if "UNDER" in verdict else "OVER" if "OVER" in verdict else "FAIR"


def apply_research(out: Dict[str, Any], D, P, exitp, R: Dict[str, Any], as_of: Optional[str]) -> Dict[str, Any]:
    price = D["price"]
    adjm = R.get("adjustments") or {}
    p_fail_engine = P.get("p_fail") or 0.0
    if _ok(adjm.get("shares")) and 0.5 <= adjm["shares"] / D["shares"] <= 2.0:
        # 엔진 주식수(시가총액÷주가)가 미획득 성과주 등을 포함/누락하는 경우: 리서치가 공시 근거로 준 희석 주식수를 쓴다
        D = {**D, "shares": float(adjm["shares"])}
    P = dict(P)
    if _ok(adjm.get("start_margin")):                       # 일회성 항목을 걷어낸 출발 GAAP 영업이익률 (근거: start_margin_basis)
        P["m0"] = float(adjm["start_margin"])
    if _ok(adjm.get("revenue_base")) and 0.3 <= adjm["revenue_base"] / D["rev"] <= 3.0:
        P["rev0"] = float(adjm["revenue_base"])             # 분할·매각·종결된 인수를 반영한 pro forma 매출 기준
    shares = D["shares"]
    # 엔진 부채에는 운용리스 부채가 들어 있다. 리스료는 이미 영업이익에서 차감되므로 리서치가 금액을 주면 이중 차감을 되돌린다
    lease_back = float(adjm.get("operating_leases_in_debt") or 0.0) if D.get("leases", 0.0) <= 0 else 0.0   # 엔진이 이미 뺐으면 중복 가산 금지
    add = (float(adjm.get("nonop_assets") or 0.0) + lease_back
           - float(adjm.get("debt_like_liabilities") or 0.0)) / shares     # 연금 부족분 등 부채성 항목은 차감
    if adjm.get("minority_in_share_count"):                 # Up-C 구조: 비지배지분 보유자가 이미 주식수에 포함 → 차감분 복원
        add += D["minority"] / shares
    dil = 1 + float(adjm.get("extra_dilution_pct") or 0.0)
    ms = float(adjm["minority_economic_share"]) if _ok(adjm.get("minority_economic_share")) else None
    opts = []
    for o in R.get("options") or []:
        ev_ = float(o["prob"]) * float(o["value_per_share"])
        opts.append({"name": o["name"], "prob": float(o["prob"]), "value_per_share": float(o["value_per_share"]),
                     "expected": ev_, "basis": o.get("basis")})
    opt_total = sum(o["expected"] for o in opts)

    def run(shift: float = 0.0) -> Tuple[Dict[str, Any], float]:
        sc, fv = {}, 0.0
        for name in ("bear", "base", "bull"):
            s = R["scenarios"][name]
            r = scenario_value(D, P, exitp, s, shift)
            raw = r["value"]
            if ms is not None:      # 소수주주의 '경제적 몫': 장부 비지배지분 차감을 되돌리고, 전체 주주가치에서 그 비율을 뗀다
                raw = (r["value"] + D["minority"] / shares) * (1 - ms)
            core = max(raw + add, 0.0)                      # 영업 외 자산은 음(-)의 주주가치를 먼저 메운 뒤에 남는 만큼만 가치
            # 옵션가치도 주주의 몫이다: 회사가 자금조달에 실패하면 함께 사라지고, 추가 희석도 똑같이 받는다
            val = (core + opt_total * (1 - (r.get("fail_prob") or 0.0))) / dil
            sc[name] = {**r, "prob": float(s["prob"]), "core": core, "value": val, "story": s.get("story"),
                        "target_margin": s.get("target_margin"), "margin_year": s.get("margin_year"),
                        "rev_growth": s.get("rev_growth"), "margin_path": s.get("margin_path"), "wacc_adj": s.get("wacc_adj") or 0.0,
                        "fail_prob_given": _ok(s.get("fail_prob"))}
            fv += float(s["prob"]) * val
        return sc, fv

    scen, fair = run()
    lo, hi = min(v["value"] for v in scen.values()), max(v["value"] for v in scen.values())
    modeled = [k for k, v in scen.items() if not v.get("direct")]
    sens = {"minus_1pt": run(-0.01)[1], "plus_1pt": run(+0.01)[1]} if modeled else None
    fair_sym = 0.25 * scen["bear"]["value"] + 0.5 * scen["base"]["value"] + 0.25 * scen["bull"]["value"]
    # 시나리오 분산: 확률가중 변동계수 (단순 범위는 '서로 다른 세계'를 제대로 쓴 리서치일수록 불리해진다)
    spread = math.sqrt(sum(v["prob"] * (v["value"] - fair) ** 2 for v in scen.values())) / fair if fair > 0 else 9.9
    conf = str(R.get("confidence") or "MEDIUM").upper()
    age = None
    try:
        age = (date.fromisoformat(as_of) - date.fromisoformat(R["as_of"])).days if as_of else None
    except (ValueError, KeyError, TypeError):
        pass
    stale = age is not None and age > RESEARCH_STALE_DAYS
    new_fin = R.get("_new_financials")                      # 리서치 이후 새 분기 재무제표가 나옴 (리서치는 작성 시점의 재무 기준으로 계산)
    code = out["archetype"]["code"]

    # ── 자체 점검: 발행 전에 계산 결과의 내적 일관성을 기계적으로 확인한다 (사람이 증상을 보고 지적하기 전에 드러나도록) ──
    checks: List[Dict[str, str]] = []
    chk = lambda level, text: checks.append({"level": level, "text": text})  # noqa: E731
    if p_fail_engine > 0 and any(not scen[k]["fail_prob_given"] for k in modeled):
        chk("ERROR", f"현금 소진 구조(엔진 추정 자금조달 실패확률 {p_fail_engine * 100:.0f}%)인데 fail_prob 를 명시하지 않은 시나리오가 있음 — "
                     "각 시나리오에 직접 정할 것 (약세 시나리오가 이미 실패 경로면 0)")
    pb, pu = scen["bear"]["prob"], scen["bull"]["prob"]
    if abs(pb - pu) > 0.011 and len(str(R.get("prob_basis") or "").strip()) < 40:
        chk("WARN", f"확률이 비대칭(약세 {pb * 100:.0f}% · 강세 {pu * 100:.0f}%)인데 근거(prob_basis)가 없음 — "
                    f"대칭(25/50/25)이면 적정가 {fair_sym:,.2f}")
    if not scen["bear"]["value"] <= scen["base"]["value"] <= scen["bull"]["value"]:
        chk("WARN", "시나리오 가치가 약세 ≤ 기본 ≤ 강세 순서가 아님")
    tv_gap = False
    for k in modeled:
        v, ko = scen[k], SCEN_KO[k]
        eg, ex = v["ev_growth"], v["ev_exit"]
        if ex is not None and (eg <= 0 < ex or (eg > 0 and not 0.6 <= ex / eg <= 1 / 0.6)):
            tv_gap = tv_gap or k == "base"
            chk("WARN" if k == "base" else "INFO",
                f"{ko}: Exit 멀티플 교차검증과 크게 다름 (적용한 영구성장 방식 기업가치 {eg / 1e9:,.1f}B · 시장 배수 {v['exit_multiple']:.1f}배 적용 시 {ex / 1e9:,.1f}B"
                + (f"; 이 시나리오의 수익성·자본집약도가 설명하는 배수는 {v['fund_multiple']:.1f}배" if _ok(v.get("fund_multiple")) else "") + ")")
        if v.get("loss_cum") and not v.get("margin_path_given"):
            chk("INFO", f"{ko}: 손실 경로는 엔진의 기본 가정(목표 연차까지 직선으로 축소) — "
                        + (f"{v['breakeven_year']}년차 흑자 전환" if v.get("breakeven_year") else f"{v['N']}년 안에 흑자 전환 없음")
                        + f", 그때까지 누적 영업손실 {v['loss_cum'] / 1e9:,.2f}B. 상용화 일정·비용 구조가 이와 다르면 margin_path 로 연도별 이익률을 지정할 것 "
                          "(영업손실을 extra_investment 에 넣으면 말년 ROIC 가 낮아지고 결손금 절세가 빠진다)")
        if _ok(v["tv_share"]) and v["tv_share"] > 0.9:
            chk("INFO", f"{ko}: 기업가치의 {v['tv_share'] * 100:.0f}%가 {v['N']}년 뒤 잔존가치")
        if _ok(v["roic_incr"]) and _ok(v["cagr5"]) and v["cagr5"] > 0.05 and v["roic_incr"] < v["wacc_T"] - 0.01:
            chk("INFO", f"{ko}: 증분 투하자본수익률 {v['roic_incr'] * 100:.1f}% < 자본비용 {v['wacc_T'] * 100:.1f}% — "
                        "이 가정에서는 성장할수록 주주가치가 줄어든다 (마진·자본집약도 가정 재확인)")
        if v["capex_T"] is not None and v["capex_T"] < v["dep_T"] - 0.02:
            chk("INFO", f"{ko}: 마지막 해 D&A({v['dep_T'] * 100:.1f}%)가 CAPEX({v['capex_T'] * 100:.1f}%)보다 2%p 이상 큼 — "
                        "초과분을 비현금 상각으로 보고 영구 구간 이익에 세후 가산했다. 유지 CAPEX 를 낮게 잡은 것이 아닌지 확인")
        if v["wacc"] > 0.15:
            chk("INFO", f"{ko}: 시작 할인율 {v['wacc'] * 100:.1f}% (15% 초과) — 베타·자본구조 확인")
    score, why = 100.0, []
    score -= min(45.0, spread * 45)
    if spread > 0.4:
        why.append(f"시나리오 간 편차 큼 (확률가중 변동계수 {spread * 100:.0f}% · 약세 {lo:,.0f} ~ 강세 {hi:,.0f})")
    if CONF_PENALTY.get(conf, 12):
        score -= CONF_PENALTY.get(conf, 12)
        why.append(f"리서치 확신도 {conf}")
    if code in ARCH_UNCERTAINTY:
        score -= ARCH_UNCERTAINTY[code] / 2
        why.append(f"'{ARCH_KO[code]}' 유형")
    if opt_total > 0.25 * fair:
        score -= 10
        why.append(f"적정가의 {opt_total / fair * 100:.0f}%가 아직 매출이 없는 신사업 옵션가치")
    if stale:
        score -= 15
        why.append(f"리서치 {age}일 경과 — 갱신 필요")
    if new_fin:
        stale = True
        score -= 10
        why.append(f"리서치 이후 새 분기 실적({new_fin}) 발표 — 리서치 갱신 필요 (계산은 리서치 작성 시점의 재무 기준)")
        chk("WARN", f"리서치 작성 이후 새 재무제표({new_fin} 기준)가 나왔다 — 성장 경로·재무 조정은 작성 시점 기준으로 유지했다. 리서치를 갱신할 것")
    if tv_gap:
        score -= 6
        why.append("기본 시나리오의 잔존가치가 시장 배수 교차검증과 크게 어긋남")
    mech = out.get("fair_value")
    if _ok(mech) and mech > 0 and not 0.5 <= fair / mech <= 2.0:
        score -= 6
        why.append(f"기계적 기준선({mech:,.0f})과 {fair / mech:.1f}배 차이 — 리서치 가정이 과거 실적과 크게 다름")
    rate_el = None
    if sens and fair > 0:
        rate_el = (sens["minus_1pt"] - sens["plus_1pt"]) / 2 / fair      # 할인율 1%p 당 적정가 변화율
        if rate_el > 0.15:
            score -= 5
            why.append(f"할인율 1%p 에 적정가가 ±{rate_el * 100:.0f}% 움직임 (먼 미래 현금흐름 의존)")
    score = max(score, 0.0)
    level = "LOW" if score >= 78 else "MEDIUM" if score >= 58 else "HIGH" if score >= 38 else "VERY_HIGH"
    band = MOS_BAND[level]
    verdict = _verdict(fair, price, band)
    robust = True
    if sens:
        robust = all(_klass(_verdict(f, price, band)) == _klass(verdict) for f in sens.values() if f > 0)
        if not robust:
            chk("WARN", f"판정이 할인율 ±1%p 에 따라 바뀜 (적정가 {sens['plus_1pt']:,.2f} ~ {sens['minus_1pt']:,.2f}) — "
                        "'저평가/고평가' 결론보다 범위를 볼 것")
    out["mechanical"] = {k: out.get(k) for k in ("fair_value", "fair_low", "fair_high", "upside_pct", "verdict", "verdict_ko",
                                                 "n_models", "uncertainty", "summary", "status")}
    out["research"] = {**{k: R.get(k) for k in ("as_of", "analyst", "thesis", "business", "performance", "plans", "news", "risks",
                                                "catalysts", "confidence", "sources", "adjustments", "valuation_notes", "second_opinion",
                                                "prob_basis")},
                       "scenarios": scen, "options": opts, "option_value": opt_total, "age_days": age, "stale": stale,
                       "anchor": {"ttm_end": D.get("ttm_end")}, "checks": checks, "rate_sensitivity": sens, "rate_elasticity": rate_el, "fair_symmetric": fair_sym,
                       "verdict_robust": robust}
    out.update({"method": "RESEARCH", "status": "OK", "fair_value": fair, "fair_low": lo, "fair_high": hi,
                "upside_pct": (fair / price - 1) * 100, "verdict": verdict, "verdict_ko": VERDICT_KO[verdict],
                "mos_band_pct": band * 100, "model_gap": not 1 / 3 <= fair / price <= 3,
                "uncertainty": {"level": level, "label": UNC_KO[level], "score": round(score), "reasons": why}})
    out["summary"] = (f"리서치 기반 적정가 {fair:,.2f} = 약세 {scen['bear']['value']:,.2f}({scen['bear']['prob'] * 100:.0f}%) · "
                      f"기본 {scen['base']['value']:,.2f}({scen['base']['prob'] * 100:.0f}%) · 강세 {scen['bull']['value']:,.2f}({scen['bull']['prob'] * 100:.0f}%)의 확률가중"
                      + (f" (신사업 옵션 {opt_total:,.2f} 포함)" if opt_total > 0 else "")
                      + f". 현재가 {price:,.2f} 대비 {(fair / price - 1) * 100:+.1f}% · {VERDICT_KO[verdict]} (불확실성 {UNC_KO[level]})."
                      + (f" 할인율 ±1%p 범위 {sens['plus_1pt']:,.2f}~{sens['minus_1pt']:,.2f}"
                         + ("" if robust else " — 판정이 할인율에 민감") + "." if sens else "")
                      + (f" 기계적 기준선 {mech:,.2f}." if _ok(mech) else ""))
    return out


def compact(v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """리포트 JSON·대시보드용 요약 (무거운 배열 제외)."""
    if not v:
        return None
    r = lambda x, d=2: round(x, d) if isinstance(x, float) else x  # noqa: E731
    keep = ("version", "status", "as_of", "fundamentals_asof", "currency", "price", "fair_value", "fair_low", "fair_high",
            "upside_pct", "verdict", "verdict_ko", "mos_band_pct", "n_models", "summary")
    out = {k: r(v.get(k)) for k in keep}
    a, u, k = v.get("archetype") or {}, v.get("uncertainty") or {}, v.get("wacc") or {}
    out["archetype"] = {"code": a.get("code"), "label": a.get("label"), "traits": a.get("traits") or []}
    out["uncertainty"] = {"level": u.get("level"), "label": u.get("label"), "score": u.get("score")}
    out["wacc"], out["ke"] = r(k.get("wacc"), 4), r(k.get("ke"), 4)
    out["wacc_stable"], out["erp"], out["rf"], out["g_term"] = r(k.get("wacc_t10"), 4), r(k.get("erp"), 4), r(k.get("rf"), 4), r((v.get("assumptions") or {}).get("gT"), 4)
    out["models"] = [{"id": m["id"], "name": m["name"], "mid": r(m["mid"]), "low": r(m["low"]), "high": r(m["high"]),
                      "weight": r(m["weight"], 3)} for m in v.get("models") or [] if m.get("included")]
    rd, mc, an = v.get("reverse_dcf") or {}, v.get("monte_carlo") or {}, v.get("analyst") or {}
    out["implied_cagr_5y"] = r(rd.get("implied_cagr_5y"), 4) if rd.get("status") == "OK" else None
    out["base_cagr_5y"] = r(rd.get("base_cagr_5y"), 4)
    out["mc"] = {k2: r(mc.get(k2)) for k2 in ("p10", "p50", "p90", "prob_above_price")} if mc else None
    out["analyst_target"] = r(an.get("target_mean"))
    out["method"] = v.get("method") or "MECHANICAL"
    out["model_gap"] = bool(v.get("model_gap"))
    R = v.get("research")
    if R:
        out["research"] = {"as_of": R.get("as_of"), "analyst": R.get("analyst"), "thesis": R.get("thesis"),
                           "confidence": R.get("confidence"), "stale": R.get("stale"), "option_value": r(R.get("option_value")),
                           "scenarios": {k: {"prob": r(s["prob"], 3), "value": r(s["value"]), "cagr5": r(s.get("cagr5"), 4),
                                             "target_margin": s.get("target_margin")} for k, s in R["scenarios"].items()},
                           "n_sources": len(R.get("sources") or []),
                           "rate_sensitivity": ({k2: r(x) for k2, x in R["rate_sensitivity"].items()} if R.get("rate_sensitivity") else None),
                           "verdict_robust": R.get("verdict_robust", True), "fair_symmetric": r(R.get("fair_symmetric")),
                           "n_warn": sum(1 for c in R.get("checks") or [] if c.get("level") in ("WARN", "ERROR")),
                           "second_opinion": ({k: (R["second_opinion"] or {}).get(k) for k in ("analyst", "fair_value")}
                                              if R.get("second_opinion") else None)}
        out["mechanical_fair"] = r((v.get("mechanical") or {}).get("fair_value"))
    return out


def market_rates() -> Tuple[Optional[float], Optional[float]]:
    """(10년 국채금리, Moody's AAA 회사채 수익률) — FRED. 실패 시 None."""
    from . import sources as S
    out: List[Optional[float]] = []
    for sid in ("DGS10", "DAAA"):
        try:
            out.append(S.fred_series(sid, n=5)[-1][1] / 100)
        except Exception:  # noqa: BLE001
            out.append(None)
    return out[0], out[1]


def _xlsx_numbers(blob: bytes, sheet: str) -> List[Dict[str, float]]:
    """xlsx 시트의 숫자 셀만 읽는다 (zip + XML — 별도 라이브러리 불필요). 반환: 행별 {열문자: 값}."""
    import io
    import re
    import zipfile
    import xml.etree.ElementTree as ET
    m = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    z = zipfile.ZipFile(io.BytesIO(blob))
    wbx = z.read("xl/workbook.xml")
    _xlsx_numbers.date1904 = b'date1904="1"' in wbx or b"date1904='1'" in wbx or b'date1904="true"' in wbx
    rid = next(sh.get(rns + "id") for sh in ET.fromstring(z.read("xl/workbook.xml")).iter(m + "sheet")
               if (sh.get("name") or "").strip().lower() == sheet.lower())
    target = next(r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")) if r.get("Id") == rid)
    rows = []
    for row in ET.fromstring(z.read(target.lstrip("/") if target.startswith("/") else "xl/" + target)).iter(m + "row"):
        vals: Dict[str, float] = {}
        for c in row:
            v = c.find(m + "v")
            if v is not None and c.get("t") in (None, "n"):
                try:
                    vals[re.match(r"[A-Z]+", c.get("r")).group(0)] = float(v.text)
                except (TypeError, ValueError, AttributeError):
                    pass
        rows.append(vals)
    return rows


def market_erp(force: bool = False) -> Dict[str, Any]:
    """현재 시장의 내재 주식위험프리미엄 — Damodaran(NYU Stern)이 매월 초 S&P 500 가격·현금흐름으로 역산해 공개하는 값.

    고정 상수(예: 4.5%)를 쓰면 '지금 시장 전체가 비싼가'에 대한 견해가 모든 종목의 적정가에 섞여 들어간다. 개별 종목 평가는
    시장이 현재 위험에 매기는 가격을 그대로 받아들이고(시장 중립) 그 위에서 종목의 괴리를 봐야 한다.
    월별 잡음으로 적정가가 출렁이지 않도록 최근 3개월 평균을 쓴다. 수집 실패 시 마지막 저장값 → 기본값."""
    env = os.environ.get("SMA_ERP")
    if env:
        return {"erp": float(env), "label": f"환경변수 SMA_ERP {float(env) * 100:.2f}%", "source": "env"}
    from .history import STATE_DIR
    path = STATE_DIR / "valuation" / "erp.json"
    saved = None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    today = date.today()
    if saved and not force:
        try:
            if (today - date.fromisoformat(saved["fetched"])).days < ERP_TTL_DAYS and \
                    0 <= (today - date.fromisoformat(saved["as_of"])).days < 75:
                return saved
        except (KeyError, ValueError, TypeError):
            pass
    try:
        from . import net
        r = net.get(ERP_URL, ok_404=False, timeout=40)
        rows = _xlsx_numbers(r.content, "Last 12 months data")
        epoch = date(1904, 1, 1) if getattr(_xlsx_numbers, "date1904", False) else date(1899, 12, 30)
        rd = [((epoch + timedelta(days=int(x["A"]))).isoformat(), x["D"], x.get("C"))
              for x in rows if x.get("A", 0) > 30000 and 0.015 <= x.get("D", 0) <= 0.10]
        rd = [x for x in rd if abs((today - date.fromisoformat(x[0])).days) < 500]
        rd.sort()
        if len(rd) < 3 or (today - date.fromisoformat(rd[-1][0])).days > 120:
            raise ValueError("최근 월별 ERP 행을 찾지 못함")
        erp = sum(x[1] for x in rd[-3:]) / 3
        out = {"erp": round(erp, 5), "latest": rd[-1][1], "as_of": rd[-1][0], "fetched": today.isoformat(), "source": ERP_URL,
               "readings": rd[-12:],
               "label": f"Damodaran 내재 ERP 최근 3개월 평균 {erp * 100:.2f}% ({rd[-1][0][:7]} 현재 {rd[-1][1] * 100:.2f}%)"}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        return out
    except Exception as e:  # noqa: BLE001
        if saved:
            return {**saved, "label": saved.get("label", "") + " · 갱신 실패(저장값 사용)"}
        return {"erp": ERP_DEFAULT, "label": f"기본값 {ERP_DEFAULT * 100:.1f}% (내재 ERP 수집 실패: {str(e)[:60]})", "source": "default"}


def run(symbol: str, price: Optional[float] = None, yft=None, rf: Optional[float] = None,
        as_of: Optional[date] = None, force: bool = False, research: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """수집 + 평가 (엔진·CLI 공용 진입점)."""
    from . import fundamentals as FN
    F = FN.load(symbol, yft, force=force)
    if price is None and not _ok((F.get("market") or {}).get("price")):
        price = FN.last_price(symbol, yft)                  # 프로필 응답에 현재가가 빠진 경우 (호출 제한 등)
    r10, aaa = market_rates()
    return evaluate(F, price, rf if _ok(rf) else r10, aaa, as_of.isoformat() if as_of else None, research, market_erp())
