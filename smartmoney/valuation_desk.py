"""Valuation Desk — 적정가치를 SMA 수급 분석과 분리해 운영하는 별도 기능.

흐름 (주간 AI 내러티브와 같은 '준비 → 작성 → 게시' 구조, API 과금 없음):
  1) prepare : 재무 다이제스트 + 기계적 기준선 + SEC 공시 링크 + 리서치 지침·JSON 템플릿 → WORK_DIR/{T}_package.md
  2) research: Claude 세션이 패키지를 읽고 10-K/10-Q·실적발표·가이던스·사업계획·뉴스를 조사해
               시나리오 가정(성장 경로·목표 마진·위험·확률)과 신사업 옵션가치를 {T}_research.json 으로 작성
  3) publish : 리서치를 검증(validate) → 같은 DCF 코어로 계산 → 발행
                 state/valuation/research/{T}.json   리서치 원본 (이력·재계산용)
                 reports/valuation/{T}.json          발행 수치 (SMA 가 읽는 유일한 접점)
                 reports/tickers/{T}/valuation.html  상세 페이지

SMA(엔진·대시보드)는 `read_published()` 로 **수치만** 읽는다. 재무 수집·모델 계산·리서치는 일일 수급 분석과 무관하게 돈다.
리서치가 없는 종목은 기계적 기준선만 발행되며(method=MECHANICAL), SMA 는 기본적으로 이를 적정가로 표시하지 않는다.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import fundamentals as FN
from . import valuation as V
from .history import REPORTS_DIR, STATE_DIR
from .net import get, log

PUB_DIR = REPORTS_DIR / "valuation"
RESEARCH_DIR = STATE_DIR / "valuation" / "research"
ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "docs" / "VALUATION_RESEARCH.md"
SCEN = ("bear", "base", "bull")
MIN_SOURCES = 6


# ─── 리서치 검증 ─────────────────────────────────────────────────────────────
def validate(R: Any, ticker: str, direct_value_only: bool = False) -> Tuple[List[str], List[str]]:
    """(errors, warnings). errors 가 있으면 발행하지 않는다 — 근거 없는 숫자가 사이트에 올라가는 것을 막는 관문."""
    err: List[str] = []
    warn: List[str] = []
    if not isinstance(R, dict):
        return ["JSON 객체가 아님"], warn
    if str(R.get("ticker", "")).upper() != ticker.upper():
        err.append(f"ticker 불일치: {R.get('ticker')} ≠ {ticker}")
    try:
        d = date.fromisoformat(str(R.get("as_of")))
        if d > datetime.now(timezone.utc).date():
            err.append("as_of 가 미래 날짜")
    except ValueError:
        err.append("as_of 는 YYYY-MM-DD")
    if not isinstance(R.get("thesis"), str) or len(R["thesis"].strip()) < 40:
        err.append("thesis: 40자 이상의 투자 논지 필요")
    for key, need in (("business", ("summary", "moat")), ("performance", ("summary",))):
        blk = R.get(key)
        if not isinstance(blk, dict) or any(not str(blk.get(k) or "").strip() for k in need):
            err.append(f"{key}: {', '.join(need)} 필수")
    for key, n in (("plans", 1), ("news", 1), ("risks", 2), ("catalysts", 1)):
        if not isinstance(R.get(key), list) or len(R[key]) < n:
            err.append(f"{key}: 최소 {n}개 항목")
    src = R.get("sources")
    if not isinstance(src, list) or len(src) < MIN_SOURCES:
        err.append(f"sources: 최소 {MIN_SOURCES}개 (공시·실적자료·뉴스)")
    else:
        bad = [s for s in src if not isinstance(s, dict) or not str(s.get("url", "")).startswith("http") or not s.get("title")]
        if bad:
            err.append(f"sources: title·http(s) url 이 없는 항목 {len(bad)}개")
        if not any("sec.gov" in str(s.get("url", "")) for s in src if isinstance(s, dict)):
            warn.append("sources 에 SEC 공시(sec.gov)가 없음 — 1차 자료 확인 권장")
    if str(R.get("confidence", "")).upper() not in V.CONF_PENALTY:
        err.append("confidence: LOW / MEDIUM / HIGH")

    sc = R.get("scenarios")
    if not isinstance(sc, dict) or any(k not in sc for k in SCEN):
        return err + ["scenarios: bear / base / bull 모두 필요"], warn
    probs = []
    for k in SCEN:
        s = sc[k]
        if not isinstance(s, dict):
            err.append(f"scenarios.{k}: 객체가 아님")
            continue
        p = s.get("prob")
        if not V._ok(p) or not 0.05 <= p <= 0.9:
            err.append(f"scenarios.{k}.prob: 0.05~0.90")
        else:
            probs.append(p)
        if len(str(s.get("story") or "").strip()) < 30:
            err.append(f"scenarios.{k}.story: 이 시나리오가 실현되는 조건을 30자 이상으로")
        if V._ok(s.get("value_per_share")):
            if s["value_per_share"] <= 0:
                err.append(f"scenarios.{k}.value_per_share > 0")
            if not str(s.get("value_basis") or "").strip():
                err.append(f"scenarios.{k}.value_basis: 직접 산출 근거(방법·입력값) 필수")
            continue
        if direct_value_only:
            err.append(f"scenarios.{k}: 금융·REIT 유형은 value_per_share + value_basis 로 직접 산출")
            continue
        g = s.get("rev_growth")
        if not isinstance(g, list) or not 3 <= len(g) <= V.MAX_EXPLICIT or any(not V._ok(x) or not -0.6 <= x <= 4.0 for x in g):
            err.append(f"scenarios.{k}.rev_growth: 연도별 성장률 3~{V.MAX_EXPLICIT}개 (소수, −0.6~4.0)")
        if not V._ok(s.get("target_margin")) or not -0.2 <= s["target_margin"] <= 0.7:
            err.append(f"scenarios.{k}.target_margin: −0.2~0.7")
        my = s.get("margin_year")
        if my is not None and (not isinstance(my, int) or not 1 <= my <= V.MAX_HORIZON):
            err.append(f"scenarios.{k}.margin_year: 1~{V.MAX_HORIZON} 정수")
        mp = s.get("margin_path")
        if mp is not None:
            if not isinstance(mp, list) or not 1 <= len(mp) <= V.MAX_HORIZON or any(not V._ok(x) or not -500.0 <= x <= 0.7 for x in mp):
                err.append(f"scenarios.{k}.margin_path: 연도별 GAAP 영업이익률 1~{V.MAX_HORIZON}개 (소수, −500~0.7)")
            if len(str(s.get("margin_path_basis") or "").strip()) < 40:
                err.append(f"scenarios.{k}.margin_path 를 쓰려면 margin_path_basis(40자 이상): 연도별 영업손익을 어떤 비용·매출 가정에서 계산했는지")
        if s.get("wacc_adj") is not None and (not V._ok(s["wacc_adj"]) or abs(s["wacc_adj"]) > 0.03):
            err.append(f"scenarios.{k}.wacc_adj: ±0.03 이내")
        if s.get("wacc_adj") and len(str(s.get("wacc_adj_basis") or "").strip()) < 40:
            err.append(f"scenarios.{k}.wacc_adj 를 쓰려면 wacc_adj_basis(40자 이상): 이 시나리오에서 '시장 전체와 함께 움직이는 위험'이 "
                       "왜 달라지는지. 사업이 잘 안 될 위험은 이미 시나리오의 현금흐름과 확률에 들어 있다(이중 반영 금지)")
        if s.get("fail_prob") is not None and (not V._ok(s["fail_prob"]) or not 0 <= s["fail_prob"] <= 0.9):
            err.append(f"scenarios.{k}.fail_prob: 0~0.9")
        cx = s.get("capex_pct")
        if cx is not None:
            xs = cx if isinstance(cx, list) else [cx]
            if not xs or len(xs) > V.MAX_HORIZON or any(not V._ok(x) or not -2.0 <= x <= 6.0 for x in xs) or xs[-1] < 0:
                err.append(f"scenarios.{k}.capex_pct: 매출 대비 CAPEX 비율(−2.0~6.0; 음수는 투자세액공제·보조금 수령 등 순유입 연도, 마지막 값은 0 이상), "
                           f"숫자 또는 연도별 목록(최대 {V.MAX_HORIZON})")
            dl = s.get("da_pct")
            if dl is not None:
                ds = dl if isinstance(dl, list) else [dl]
                if not ds or len(ds) > V.MAX_HORIZON or any(not V._ok(x) or not 0 <= x <= 0.6 for x in ds):
                    err.append(f"scenarios.{k}.da_pct: 0~0.6, 숫자 또는 연도별 목록(최대 {V.MAX_HORIZON})")
            cu, du = s.get("capex_usd"), s.get("da_usd")
            if cu is not None or du is not None:
                if (not isinstance(cu, list) or not isinstance(du, list) or not 1 <= len(cu) <= V.MAX_HORIZON or len(cu) != len(du)
                        or any(not V._ok(x) or abs(x) > 1e12 for x in cu) or any(not V._ok(x) or not 0 <= x <= 1e12 for x in du)):
                    err.append(f"scenarios.{k}.capex_usd / da_usd: 연도별 CAPEX·D&A 금액(달러) — 같은 길이의 목록(1~{V.MAX_HORIZON}개), "
                               "CAPEX 는 순유입 연도에 음수 가능, D&A 는 0 이상")
            if s.get("nwc_pct") is not None and (not V._ok(s["nwc_pct"]) or not -0.5 <= s["nwc_pct"] <= 0.8):
                err.append(f"scenarios.{k}.nwc_pct: −0.5~0.8")
            if s.get("extra_investment"):
                err.append(f"scenarios.{k}: capex_pct(명시적 현금흐름)와 extra_investment 는 함께 쓰지 않는다 — CAPEX 계획은 capex_pct 에 모두 담을 것")
        if cx is None and (s.get("capex_usd") is not None or s.get("da_usd") is not None):
            err.append(f"scenarios.{k}: capex_usd / da_usd 를 쓰려면 그 이후 연도에 적용할 비율 capex_pct · da_pct 도 함께 줄 것(유지 수준)")
        au = s.get("amort_usd")
        if au is not None and (not isinstance(au, list) or len(au) > V.MAX_HORIZON or any(not V._ok(x) or not 0 <= x <= 1e12 for x in au)
                               or len(str(s.get("amort_usd_basis") or "").strip()) < 40):
            err.append(f"scenarios.{k}.amort_usd: 연도별 비현금 상각 금액(달러, 0 이상, 최대 {V.MAX_HORIZON}개) + amort_usd_basis(40자 이상: "
                       "어떤 인수의 무형자산·매수가격 배분 상각이고 공시된 상각 일정이 무엇인지). 교체가 필요한 설비의 감가상각은 넣지 않는다")
        if s.get("dilution_pct") is not None and (not V._ok(s["dilution_pct"]) or not -0.3 <= s["dilution_pct"] <= 1.0
                                                 or len(str(s.get("dilution_basis") or "").strip()) < 40):
            err.append(f"scenarios.{k}.dilution_pct: 이 시나리오의 주당 가치에서 생기는 순희석률(−0.3~1.0; 자사주 선도매입 등 주식수 감소는 음수) + "
                       "dilution_basis(40자 이상: 어떤 증권이 어떤 전환가·상한에서 몇 주로 바뀌는지)")
        if s.get("terminal_growth") is not None and (not V._ok(s["terminal_growth"]) or not 0.01 <= s["terminal_growth"] <= 0.04):
            err.append(f"scenarios.{k}.terminal_growth: 0.01~0.04 (기본은 엔진의 무위험금리 − 1%p)")
        if s.get("tax_rate") is not None and (not V._ok(s["tax_rate"]) or not 0.05 <= s["tax_rate"] <= 0.35):
            err.append(f"scenarios.{k}.tax_rate: 0.05~0.35")
        if s.get("extra_investment_noncore") not in (None, True, False):
            err.append(f"scenarios.{k}.extra_investment_noncore: true/false")
        xi = s.get("extra_investment")
        if xi is not None and (not isinstance(xi, list) or len(xi) > V.MAX_HORIZON or any(not V._ok(x) or x < 0 for x in xi)):
            err.append(f"scenarios.{k}.extra_investment: 연도별 추가 투자 유출(달러, 0 이상) 최대 {V.MAX_HORIZON}개")
    if len(probs) == 3 and abs(sum(probs) - 1) > 0.011:
        err.append(f"시나리오 확률 합 = {sum(probs):.3f} (1.0 이어야 함)")
    dep = R.get("depends_on")
    if dep is not None and (not isinstance(dep, list) or any(not isinstance(x, str) or not x.strip() for x in dep)):
        err.append("depends_on: 이 리서치의 값이 의존하는 다른 종목 티커 목록 (예: [\"MRVL\"])")
    ra = R.get("refresh_after")
    if ra is not None:
        try:
            date.fromisoformat(str(ra))
        except ValueError:
            err.append("refresh_after: YYYY-MM-DD (가정을 바꿀 예정 이벤트 — 실적 발표 외의 인베스터데이·규제 결정 등 — 다음 날짜)")
    if len(probs) == 3 and abs(probs[0] - probs[2]) > 0.011 and len(str(R.get("prob_basis") or "").strip()) < 40:
        warn.append(f"확률이 비대칭(약세 {probs[0]:.2f} · 강세 {probs[2]:.2f})인데 prob_basis(40자 이상)가 없음 — 기본은 25/50/25. "
                    "한쪽이 더 일어나기 쉽다고 볼 증거(가이던스 달성 이력·업종 기저율 등)를 적거나 대칭으로 둘 것")
    adj = R.get("adjustments") or {}
    if adj.get("start_margin") is not None:
        if not V._ok(adj["start_margin"]) or not -3.0 <= adj["start_margin"] <= 0.7 or len(str(adj.get("start_margin_basis") or "").strip()) < 40:
            err.append("adjustments.start_margin: 일회성 항목을 뺀 출발 GAAP 영업이익률(−3.0~0.7) + start_margin_basis(40자 이상: 어떤 항목을 얼마나 뺐는지)")
    if adj.get("revenue_base") is not None:
        if not V._ok(adj["revenue_base"]) or adj["revenue_base"] <= 0 or len(str(adj.get("revenue_base_basis") or "").strip()) < 40:
            err.append("adjustments.revenue_base: 분할·매각·종결된 인수를 반영한 pro forma 매출(달러) + revenue_base_basis(40자 이상)")
    if adj.get("minority_economic_share") is not None:
        if not V._ok(adj["minority_economic_share"]) or not 0 < adj["minority_economic_share"] < 0.9:
            err.append("adjustments.minority_economic_share: 0~0.9 (연결 이익·가치 중 소수주주 몫)")
        if adj.get("minority_in_share_count"):
            err.append("adjustments: minority_economic_share 와 minority_in_share_count 는 함께 쓸 수 없음")
    for key in ("nonop_assets", "operating_leases_in_debt", "debt_like_liabilities"):
        if adj.get(key) is not None and (not V._ok(adj[key]) or adj[key] < 0):
            err.append(f"adjustments.{key}: 0 이상의 달러 금액")
    if adj.get("shares") is not None and (not V._ok(adj["shares"]) or adj["shares"] <= 0):
        err.append("adjustments.shares: 희석 주식수(주)")
    if adj.get("extra_dilution_pct") is not None and (not V._ok(adj["extra_dilution_pct"]) or not 0 <= adj["extra_dilution_pct"] <= 0.5):
        err.append("adjustments.extra_dilution_pct: 0~0.5 (희석 외 용도로 쓰지 말 것)")
    for i, o in enumerate(R.get("options") or []):
        if not isinstance(o, dict) or not o.get("name") or not V._ok(o.get("prob"), o.get("value_per_share")) \
                or not 0 < o["prob"] <= 1 or o["value_per_share"] <= 0 or len(str(o.get("basis") or "")) < 30:
            err.append(f"options[{i}]: name · prob(0~1) · value_per_share(>0) · basis(산출 근거 30자 이상)")
    return err, warn


def _order_check(v: Dict[str, Any]) -> List[str]:
    sc = v["research"]["scenarios"]
    w = []
    if not sc["bear"]["value"] <= sc["base"]["value"] <= sc["bull"]["value"]:
        w.append("시나리오 가치가 약세 ≤ 기본 ≤ 강세 순서가 아님 — 가정을 다시 확인")
    return w


# ─── 발행 / 조회 ─────────────────────────────────────────────────────────────
def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def load_research(ticker: str) -> Optional[Dict[str, Any]]:
    try:
        v = json.loads((RESEARCH_DIR / f"{ticker.upper()}.json").read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def load_anchor(ticker: str) -> Optional[Dict[str, Any]]:
    """리서치를 발행할 때 함께 저장한 재무 스냅샷 (리서치 가정과 한 쌍인 재무 기준)."""
    try:
        v = json.loads((RESEARCH_DIR / f"{ticker.upper()}.fin.json").read_text(encoding="utf-8"))
        return v if isinstance(v, dict) and v.get("ttm") else None
    except (OSError, ValueError):
        return None


def compute(ticker: str, research: Optional[Dict[str, Any]] = None, refresh: bool = False,
            price: Optional[float] = None) -> Dict[str, Any]:
    """재무 스냅샷(+리서치) → 전체 평가 결과. 리서치가 주어지지 않으면 저장된 리서치를 쓴다.

    저장된 리서치로 다시 계산할 때는 **리서치를 쓴 시점의 재무 스냅샷**을 쓴다. 리서치의 성장 경로("TTM 종료 후 12개월")와
    재무 조정(달러 금액)은 그 재무제표와 한 쌍이므로, 새 분기 실적 위에 같은 경로를 다시 얹으면 한 분기 성장이 두 번 들어가고
    이미 재무제표에 반영된 조정이 또 차감된다. 주가·금리·위험프리미엄만 최신으로 바꾸고, 새 재무제표가 나왔으면 '갱신 필요'로 표시한다."""
    T = ticker.upper()
    F = FN.load(T, force=refresh)
    if price is None:
        price = FN.last_price(T) or (F.get("market") or {}).get("price")
    r10, aaa = V.market_rates()
    R = research if research is not None else load_research(T)
    if research is None and R:
        Fa = load_anchor(T)
        if Fa:
            newer = str((F.get("ttm") or {}).get("end") or "") > str((Fa.get("ttm") or {}).get("end") or "")
            R = {**R, "_new_financials": (F.get("ttm") or {}).get("end") if newer else None}
            F = Fa
    today = datetime.now(timezone.utc).date().isoformat()
    v = V.evaluate(F, price, r10, aaa, today, R, V.market_erp())
    v["name"] = (F.get("profile") or {}).get("name")
    return v


def publish(ticker: str, v: Dict[str, Any], research: Optional[Dict[str, Any]] = None) -> Dict[str, Path]:
    """평가 결과를 발행. research 가 주어지면 리서치 원본도 저장한다."""
    from .render_valuation import page
    T = ticker.upper()
    out = {"numbers": PUB_DIR / f"{T}.json", "html": REPORTS_DIR / "tickers" / T / "valuation.html"}
    pub = V.compact(v)
    pub["ticker"], pub["name"] = T, v.get("name")
    pub["published"] = datetime.now(timezone.utc).date().isoformat()
    _write(out["numbers"], json.dumps(pub, ensure_ascii=False, indent=1))
    _write(out["html"], page(v, v.get("name")))
    if research is not None:
        out["research"] = RESEARCH_DIR / f"{T}.json"
        _write(out["research"], json.dumps({k: x for k, x in research.items() if not k.startswith("_")}, ensure_ascii=False, indent=1))
        snap = FN.read_snapshot(T)                          # 리서치와 한 쌍인 재무 기준을 함께 보존 (compute 참고)
        if snap:
            out["anchor"] = RESEARCH_DIR / f"{T}.fin.json"
            _write(out["anchor"], json.dumps(snap, ensure_ascii=False))
    return out


PLAN_ORDER = {"NEW": 0, "EARNINGS": 1, "EVENT": 1, "DEPENDENCY": 2, "STALE": 3, "OK": 9}
PLAN_KO = {"NEW": "신규 — 리서치 없음", "EARNINGS": "실적 발표 이후 — 갱신", "EVENT": "예정된 이벤트 경과 — 갱신",
           "DEPENDENCY": "의존 종목 값 변경 — 재계산", "STALE": "100일 경과 — 갱신", "OK": "유효 — 주가·금리만 재계산"}
PLAN_COST = {"NEW": 2, "EARNINGS": 1, "EVENT": 1, "DEPENDENCY": 1, "STALE": 1}      # 신규 조사는 갱신의 약 2배 작업량


def plan(tickers: List[str], today: Optional[str] = None, fetch: bool = True) -> List[Dict[str, Any]]:
    """관심종목마다 이번 주에 할 일을 정한다 (주간 예약 작업의 입력).

    NEW        발행된 리서치가 없다 → 조사·발행
    EARNINGS   리서치 이후 새 분기 재무제표가 나왔거나, 예정돼 있던 실적 발표일이 지났다 → 리서치 갱신
    EVENT      리서치가 적어 둔 `refresh_after`(인베스터데이·규제 결정 등 가정을 바꿀 예정 이벤트 날짜)가 지났다 → 리서치 갱신
    DEPENDENCY 이 리서치가 값에 쓰는 다른 종목(`depends_on`)이 이후에 다시 발행됐다 → 같은 식으로 재계산
    STALE      리서치 100일 경과 → 갱신
    OK         유효 → 주가·금리·ERP 만 반영해 재계산
    반환은 우선순위(NEW → EARNINGS → DEPENDENCY → STALE, 같은 등급 안에서는 오래된 리서치부터) 순."""
    day = date.fromisoformat(today) if today else datetime.now(timezone.utc).date()
    out = []
    for t in tickers:
        T = t.upper()
        R, pub = load_research(T), None
        try:
            pub = json.loads((PUB_DIR / f"{T}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if not R or not pub or pub.get("method") != "RESEARCH":
            out.append({"ticker": T, "action": "NEW", "why": PLAN_KO["NEW"], "research_as_of": None, "age_days": None})
            continue
        as_of = str(R.get("as_of") or pub.get("published") or "")
        try:
            age = (day - date.fromisoformat(as_of)).days
        except ValueError:
            age = None
        Fa = load_anchor(T) or {}
        anchor_end = str((Fa.get("ttm") or {}).get("end") or "")
        why, action = "", "OK"
        latest_end = anchor_end
        dates = [str(Fa.get("next_earnings") or "")[:10]]
        if fetch:
            try:
                Fl = FN.load(T)
                latest_end = str((Fl.get("ttm") or {}).get("end") or "")
                dates.append(str(Fl.get("next_earnings") or "")[:10])
            except Exception as e:  # noqa: BLE001
                why = f"최신 재무 조회 실패({str(e)[:60]}) — 실적 여부는 예정일로만 판단"
        passed = sorted(d for d in dates if d and as_of and as_of <= d < day.isoformat())
        ne = passed[-1] if passed else (max([d for d in dates if d] or [""]))
        ra = str(R.get("refresh_after") or "")[:10]
        if latest_end and anchor_end and latest_end > anchor_end:
            action, why = "EARNINGS", f"새 분기 재무제표 {latest_end} (리서치 기준 {anchor_end})"
        elif passed:
            action, why = "EARNINGS", f"예정된 실적 발표일 {passed[-1]} 경과"
        elif ra and as_of and as_of < ra < day.isoformat():
            action, why = "EVENT", f"리서치가 지정한 갱신 시점 {ra} 경과"
        else:
            for d in R.get("depends_on") or []:
                try:
                    dp = json.loads((PUB_DIR / f"{str(d).upper()}.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if str(dp.get("published") or "") > str(pub.get("published") or "") or \
                        str((dp.get("research") or {}).get("as_of") or "") > as_of:
                    action, why = "DEPENDENCY", f"{str(d).upper()} 가 이후에 다시 발행됨"
                    break
            if action == "OK" and age is not None and age > V.RESEARCH_STALE_DAYS:
                action, why = "STALE", f"리서치 {age}일 경과"
        out.append({"ticker": T, "action": action, "why": why or PLAN_KO[action], "research_as_of": as_of or None, "age_days": age,
                    "next_earnings": ne or None, "depends_on": R.get("depends_on") or None})
    out.sort(key=lambda x: (PLAN_ORDER[x["action"]], -(x["age_days"] or 0), x["ticker"]))
    return out


def read_published(ticker: str, price: Optional[float] = None, as_of: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """SMA 용 접점: 발행된 수치만 읽고, 주어진 가격으로 괴리율·판정을 다시 매긴다 (모델 재계산 없음).

    기본은 리서치 기반(method=RESEARCH) 수치만 반환한다. 재무제표만 본 기계적 기준선은 사업 내용을 읽지 못하므로
    적정가로 내보이지 않는다 — `SMA_VALUATION_ALLOW_MECHANICAL=1` 일 때만 '참고'로 반환."""
    try:
        pub = json.loads((PUB_DIR / f"{ticker.upper()}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(pub, dict) or pub.get("fair_value") is None:
        return None
    if pub.get("method") != "RESEARCH" and os.environ.get("SMA_VALUATION_ALLOW_MECHANICAL", "0") != "1":
        return None
    fair = pub["fair_value"]
    if V._ok(price) and price > 0:
        band = (pub.get("mos_band_pct") or 25) / 100
        verdict = V._verdict(fair, price, band)
        pub.update(price=round(price, 2), upside_pct=round((fair / price - 1) * 100, 2), verdict=verdict,
                   verdict_ko=V.VERDICT_KO[verdict], priced_at=as_of)
    try:
        ref = (pub.get("research") or {}).get("as_of") or pub.get("published")
        base = date.fromisoformat(as_of) if as_of else datetime.now(timezone.utc).date()
        pub["age_days"] = max((base - date.fromisoformat(ref)).days, 0)
        pub["stale"] = pub["age_days"] > V.RESEARCH_STALE_DAYS or bool((pub.get("research") or {}).get("stale"))
    except (ValueError, TypeError):
        pass
    return pub


# ─── 리서치 패키지 ───────────────────────────────────────────────────────────
def sec_filings(ticker: str) -> List[Dict[str, str]]:
    """최근 10-K · 10-Q · 8-K · 합병/위임장 관련 문서의 원문과 제출 폴더(첨부 EX-99 등) 링크."""
    cik = FN._sec_cik(ticker)
    if not cik:
        return []
    r = get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=FN.SEC_UA, timeout=30)
    rec = ((r.json() or {}).get("filings") or {}).get("recent") or {}
    cap = {"10-K": 1, "10-Q": 3, "8-K": 14, "20-F": 1, "6-K": 6, "425": 3, "DEF 14A": 1, "S-4": 1, "424B5": 2}
    out, seen = [], {k: 0 for k in cap}
    for form, acc, doc, fdate, items in zip(rec.get("form", []), rec.get("accessionNumber", []), rec.get("primaryDocument", []),
                                            rec.get("filingDate", []), rec.get("items", [""] * 9999)):
        if form in cap and seen[form] < cap[form]:
            seen[form] += 1
            base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/"
            out.append({"form": form, "date": fdate, "items": items or "", "url": base + doc, "index": base})
    return out


_ITEMS = {"1.01": "중요 계약 체결", "1.02": "중요 계약 해지", "2.01": "자산 인수·처분 완료", "2.02": "실적·재무 상태 발표", "2.03": "채무 부담",
          "2.05": "구조조정", "2.06": "손상", "3.02": "미등록 증권 발행", "5.02": "임원 변동", "5.07": "주총 결과", "7.01": "Reg FD 공시",
          "8.01": "기타 사건"}


def _b(x: Optional[float]) -> str:
    return "N/A" if x is None else f"{x / 1e9:,.2f}B" if abs(x) >= 1e8 else f"{x / 1e6:,.1f}M"


def _p(x: Optional[float]) -> str:
    return "N/A" if x is None else f"{x * 100:.1f}%"


def _price_moves(ticker: str) -> str:
    """최근 주가 변동 — 큰 이벤트(급락·급등)가 있었는지 리서치가 바로 알 수 있게."""
    try:
        import yfinance as yf
        c = yf.Ticker(ticker).history(period="6mo", interval="1d")["Close"].dropna()
        if len(c) < 30:
            return ""
        ch = lambda n: (c.iloc[-1] / c.iloc[-1 - n] - 1) * 100 if len(c) > n else None  # noqa: E731
        parts = [f"{lab} {x:+.0f}%" for lab, x in (("1주", ch(5)), ("1개월", ch(21)), ("3개월", ch(63))) if x is not None]
        return f" ({' · '.join(parts)}, 6개월 고점 {c.max():,.2f} / 저점 {c.min():,.2f})"
    except Exception:  # noqa: BLE001
        return ""


def template(ticker: str, direct: bool) -> Dict[str, Any]:
    scen = ({"prob": 0.0, "story": "", "value_per_share": 0.0, "value_basis": ""} if direct else
            {"prob": 0.0, "story": "", "rev_growth": [0.0, 0.0, 0.0, 0.0, 0.0], "target_margin": 0.0, "margin_year": 5,
             "wacc_adj": 0.0})
    return {
        "schema": 1, "ticker": ticker.upper(), "as_of": datetime.now(timezone.utc).date().isoformat(), "analyst": "",
        "thesis": "", "confidence": "MEDIUM",
        "business": {"summary": "", "segments": [{"name": "", "revenue_share": 0.0, "growth_view": "", "margin_view": ""}],
                     "moat": "", "moat_rating": "NONE|NARROW|WIDE"},
        "performance": {"summary": "", "guidance": "", "execution_rating": "WEAK|MIXED|STRONG"},
        "plans": [{"item": "", "timing": "", "impact": ""}],
        "news": [{"date": "", "headline": "", "impact": "POS|NEG|NEU", "why": ""}],
        "risks": [""], "catalysts": [""],
        "scenarios": {k: {**scen, "prob": pr} for k, pr in zip(SCEN, (0.25, 0.50, 0.25))},
        "prob_basis": "", "refresh_after": None, "depends_on": None,
        "options": [], "adjustments": {"nonop_assets": 0, "extra_dilution_pct": 0.0, "note": ""},
        "valuation_notes": "", "sources": [{"title": "", "url": "", "date": ""}],
    }


def prepare(ticker: str, work_dir: str, refresh: bool = False) -> Path:
    T = ticker.upper()
    F = FN.load(T, force=refresh)
    price = FN.last_price(T) or (F.get("market") or {}).get("price")
    r10, aaa = V.market_rates()
    v = V.evaluate(F, price, r10, aaa, datetime.now(timezone.utc).date().isoformat(), None, V.market_erp())
    A, K, P, M = v["archetype"], v["wacc"], v["assumptions"], v["metrics"]
    direct = A["code"] in ("BANK", "INSURANCE", "REIT")
    try:
        filings = sec_filings(T)
    except Exception as e:  # noqa: BLE001
        filings = []
        log(f"{T} SEC filings: {str(e)[:120]}")
    est = F.get("estimates") or {}
    L: List[str] = [
        f"# {T} 적정가치 리서치 패키지", "",
        f"- 회사: {(F.get('profile') or {}).get('name')} · {A.get('sector')} / {A.get('industry')}",
        f"- 현재가: {v['price']:,.2f}{_price_moves(T)} · 시가총액 {_b(M.get('mcap'))} · EV {_b(M.get('ev'))} · 재무 스냅샷 {F.get('fetched')}"
        f" (스냅샷 당시 주가 {(F.get('market') or {}).get('price')})",
        f"- 작성 지침: `docs/VALUATION_RESEARCH.md` 를 먼저 읽을 것. 결과물: `{T}_research.json` (아래 템플릿)", "",
        "## 1. 재무 실적 (Yahoo 표준화 · 연간)", "", "| 회계연도 | 매출 | 영업이익 | 영업이익률 | 순이익 | EPS | FCF |", "|---|---|---|---|---|---|---|",
    ]
    for a in F.get("annual") or []:
        m = a["ebit"] / a["revenue"] if V._ok(a.get("ebit"), a.get("revenue")) and a["revenue"] else None
        L.append(f"| {a['end']} | {_b(a.get('revenue'))} | {_b(a.get('ebit'))} | {_p(m)} | {_b(a.get('net_income'))} | {a.get('eps')} | {_b(a.get('fcf'))} |")
    t = F.get("ttm") or {}
    L += [f"| **TTM ({t.get('end')})** | {_b(t.get('revenue'))} | {_b(t.get('ebit'))} | {_p(M.get('m_ttm'))} | {_b(t.get('net_income'))} | {t.get('eps')} | {_b(t.get('fcf'))} |", "",
          "분기 매출: " + " · ".join(f"{q['end']} {_b(q.get('revenue'))}" for q in (F.get("quarters") or [])[-6:]), "",
          f"재무상태(최근 분기): 현금 {_b(t.get('cash'))} · 총차입 {_b(t.get('total_debt'))} · 자기자본 {_b(t.get('equity'))} · "
          f"무형자산·영업권 {_b(t.get('intangibles'))} · 주식보상(TTM) {_b(t.get('sbc'))}", "",
          *([f"⚠ {w}" for w in v.get("warnings") or []] + [""] if v.get("warnings") else []),
          "## 2. 컨센서스 (Yahoo)", "",
          f"- **NTM 환산(엔진)**: TTM 매출 {_b(M.get('rev'))} → 향후 12개월 {_b(M.get('rev_ntm'))} (성장 {_p(M.get('g1'))}), 그 다음 12개월 성장 {_p(M.get('g2'))}. "
          "`rev_growth` 의 1년차는 이 NTM 기준(TTM 종료 시점부터 12개월)이다 — 회계연도 가이던스를 쓸 때는 이 기간으로 환산할 것."]
    for key, lab in (("rev", "매출"), ("eps", "EPS")):
        for per in ("0y", "+1y"):
            b = (est.get(key) or {}).get(per)
            if b:
                val = _b(b["avg"]) if key == "rev" else f"{b['avg']:.2f}"
                rng = f"{_b(b['low'])}~{_b(b['high'])}" if key == "rev" else f"{b['low']:.2f}~{b['high']:.2f}"
                L.append(f"- {lab} {per}: {val} (범위 {rng}, 성장 {_p(b.get('growth'))}, {int(b['n']) if b.get('n') else '?'}명)")
    an = v.get("analyst") or {}
    L += ["",
          "## 3. 기계적 기준선 (재무제표만 본 결과 — 리서치가 고쳐야 할 출발점)", "",
          f"- 분류: **{A['label']}** — " + " / ".join(A.get("reasons") or []),
          f"- 할인율: WACC {_p(K['wacc'])} (자기자본비용 {_p(K['ke'])}, 베타 {K['beta']:.2f}, rf {_p(K['rf'])}, ERP {_p(K['erp'])}) → 10년차 {_p(K['wacc_t10'])}",
          f"- 기계적 가정: 매출 성장 1·2년차 {_p(P['g1'])} / {_p(P['g2'])} 후 영구성장 {_p(P['gT'])} 로 감쇠 · 영업이익률 {_p(P['m0'])} → {_p(P['mT'])} "
          f"({P['margin_how']}) · Sales-to-Capital {P['s2c']:.2f}",
          f"- 기계적 결과: {v.get('summary')}"]
    L += ["", "| 모델 | 보수 | 기준 | 낙관 | 가중치 |", "|---|---|---|---|---|"]
    for m in v["models"]:
        if m.get("included"):
            L.append(f"| {m['name']} | {m['low']:,.2f} | {m['mid']:,.2f} | {m['high']:,.2f} | {m['weight'] * 100:.0f}% |")
    ps = (v.get("peers") or {}).get("stats") or {}
    L += ["", "peer 중앙값: " + " · ".join(f"{k} {ps[k]['median']:.2f}" for k in ("fpe", "ev_ebitda", "ev_rev", "rev_growth", "op_margin") if k in ps),
          "peer: " + ", ".join(p["symbol"] for p in (v.get("peers") or {}).get("table") or []), "",
          "## 4. 1차 자료 (SEC EDGAR — 반드시 직접 읽을 것)", ""]
    for f in filings:
        its = ", ".join(f"{x} {_ITEMS.get(x, '')}".strip() for x in (f.get("items") or "").split(",") if x)
        L.append(f"- {f['form']} {f['date']}" + (f" ({its})" if its else "") + f": {f['url']}  · 첨부 목록: {f['index']}")
    if not filings:
        L.append("- (SEC 공시 목록 수집 실패 — EDGAR 에서 직접 검색)")
    L += ["", "주의: 8-K item 번호만으로 내용을 단정하지 말 것(2.02 가 합병 발표와 함께 나오기도 한다). 실적 보도자료·투자자 자료는 첨부(EX-99.x)에 있으니 "
          "'첨부 목록' 폴더를 열어 확인한다. 전체 제출 목록: "
          f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={FN._sec_cik(T) or T}&type=&dateb=&owner=include&count=40"]
    L += ["", "## 5. 결과물 템플릿", "",
          ("이 종목은 금융·REIT 유형이라 FCFF DCF 를 쓰지 않는다. 각 시나리오의 `value_per_share` 를 직접 산출하고 `value_basis` 에 방법과 입력값을 적는다."
           if direct else
           "각 시나리오의 `rev_growth`(연도별 매출 성장률, 3~20개) · `target_margin`(GAAP 영업이익률) · `margin_year` 를 채우면 엔진이 위 할인율로 DCF 를 계산한다. 손익이 직선으로 움직이지 않으면(상용화 전 적자 지속 등) `margin_path`(연도별 영업이익률) + `margin_path_basis` 를 준다 "
           "(성장률이 영구성장률에 수렴할 때까지 10~20년). 확률의 기본값은 25/50/25 — 비대칭으로 두려면 `prob_basis` 에 증거를 적는다."),
          "", "```json", json.dumps(template(T, direct), ensure_ascii=False, indent=1), "```", "",
          "## 6. 검증·발행", "", "```bash",
          f"python3 valuation.py publish {T} --research WORK_DIR/{T}_research.json --dry-run   # 검증 + 계산 결과 미리보기",
          f"python3 valuation.py publish {T} --research WORK_DIR/{T}_research.json             # 발행", "```", ""]
    out = Path(work_dir) / f"{T}_package.md"
    _write(out, "\n".join(L))
    return out
