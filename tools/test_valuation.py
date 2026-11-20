#!/usr/bin/env python3
"""적정가치 엔진 회귀 테스트 — 네트워크 없이 합성 재무 데이터로 검증.

실행: python3 tools/test_valuation.py   (실패 시 exit 1)
검증: 성격 분류 · 모델 적용/제외 규칙 · DCF 수식(독립 구현과 대조) · 단조성 · 통합 가중치 · 역DCF 일관성 · 결정성.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from smartmoney import valuation as V  # noqa: E402
from smartmoney.render_valuation import card, page  # noqa: E402

FAILS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAILS
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS += 1
        print(f"[FAIL] {name} {detail}")


def make(rev=10e9, growth=0.05, margin=0.15, price=50.0, shares=1e9, debt=3e9, cash=2e9, equity=8e9, dps=0.0,
         industry="specialty-industrial-machinery", sector="industrials", da=0.04, capex=0.04, est=True,
         hist_margins=None, bank=False, ni_margin=None, intangibles=0.0):
    """매출·마진·성장률로 4개 연도 + TTM + 컨센서스를 일관되게 생성."""
    hist_margins = hist_margins or [margin] * 4
    annual = []
    for i, m in enumerate(hist_margins):
        r = rev / (1 + growth) ** (len(hist_margins) - i)
        ebit = None if bank else r * m
        pre = r * (m if not bank else 0.30) - 0.05 * debt * (0 if bank else 1)
        ni = pre * 0.78 if ni_margin is None else r * ni_margin
        annual.append({"end": f"{2022 + i}-12-31", "revenue": r, "ebit": ebit, "op_income": ebit, "pretax": pre,
                       "tax": pre * 0.22, "net_income": ni, "eps": ni / shares, "da": r * da, "capex": -r * capex})
    ttm_pre = rev * (margin if not bank else 0.30) - 0.05 * debt * (0 if bank else 1)
    ni = ttm_pre * 0.78 if ni_margin is None else rev * ni_margin
    ttm = {"end": "2026-09-30", "revenue": rev, "ebit": None if bank else rev * margin, "pretax": ttm_pre,
           "tax": ttm_pre * 0.22, "net_income": ni, "eps": ni / shares, "interest_expense": 0.05 * debt,
           "da": rev * da, "capex": -rev * capex, "cfo": ni + rev * da, "fcf": ni + rev * da - rev * capex,
           "gross_profit": rev * 0.5, "total_debt": debt, "cash": cash, "equity": equity, "total_assets": equity + debt + 2e9,
           "intangibles": intangibles, "shares_diluted": shares, "dividends_paid": -dps * shares}
    quarters = [{"end": e, "revenue": rev / 4} for e in ("2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30")]
    ra = annual[-1]["revenue"]
    estimates = {}
    if est:
        r0, r1 = ra * (1 + growth), ra * (1 + growth) ** 2
        estimates = {"rev": {"0y": {"avg": r0, "low": r0 * .97, "high": r0 * 1.03, "year_ago": ra},
                             "+1y": {"avg": r1, "low": r1 * .94, "high": r1 * 1.06, "year_ago": r0}},
                     "eps": {"0y": {"avg": ni / shares * (1 + growth if ni > 0 else 0.9)},
                             "+1y": {"avg": ni / shares * ((1 + growth) ** 2 if ni > 0 else 0.8)}}}
    peers = [{"symbol": f"P{i}", "mcap": 40e9 + i * 5e9, "pe": 18 + i, "fpe": 15 + i, "ev_ebitda": 10 + i * 0.5, "ev_rev": 2 + i * 0.2,
              "pb": 2.5 + i * 0.2, "ps": 2 + i * 0.2, "beta": 0.9 + i * 0.04, "rev_growth": 0.04 + i * 0.01, "op_margin": 0.12 + i * 0.01,
              "roe": 0.12 + i * 0.01, "debt": 5e9, "cash": 2e9, "revenue": 15e9, "book": 20.0, "shares": 5e8} for i in range(8)]
    return {"ticker": "TEST", "fetched": "2026-11-19", "profile": {"sector_key": sector, "industry_key": industry,
            "industry": industry, "sector": sector, "currency": "USD"},
            "market": {"price": price, "mcap": price * shares, "beta": 1.1, "div_rate": dps or None, "target_mean": price * 1.1, "n_analysts": 10},
            "annual": annual, "quarters": quarters, "ttm": ttm, "ttm_source": "quarters", "estimates": estimates,
            "peers": peers, "peer_scope": "industry", "long": {}, "sources": ["synthetic"], "warnings": []}


def models(v):
    return {m["id"]: m for m in v["models"]}


def main() -> int:
    rf = 0.043

    # ── 1. 분류 ──
    cases = [
        ("성숙·현금창출", make(), "MATURE_COMPOUNDER"),
        ("고성장 흑자", make(growth=0.30, margin=0.20, price=120), "HIGH_GROWTH"),
        ("고성장 적자", make(growth=0.40, margin=-0.10, price=30, industry="software-application", sector="technology"), "HYPERGROWTH_UNPROFITABLE"),
        ("초기 단계", make(rev=1e8, growth=1.5, margin=-3.0, price=20, debt=0, cash=1e9, equity=1.5e9,
                       industry="communication-equipment", sector="technology"), "PRE_REVENUE"),
        ("턴어라운드", make(growth=0.02, margin=-0.05, price=5, hist_margins=[-0.08, -0.06, -0.05, -0.05]), "TURNAROUND"),
        ("경기순환(업종)", make(growth=0.30, margin=0.25, industry="copper", sector="basic-materials"), "CYCLICAL"),
        ("경기 저점", make(growth=0.03, margin=0.0, industry="steel", sector="basic-materials", hist_margins=[0.15, 0.12, 0.02, 0.0]), "CYCLICAL"),
        ("배당·방어", make(growth=0.03, margin=0.20, dps=1.0, price=30), "DIVIDEND_DEFENSIVE"),
        ("은행", make(bank=True, industry="banks-regional", sector="financial-services", debt=30e9, equity=20e9, dps=0.8, price=30), "BANK"),
        ("REIT", make(industry="reit-retail", sector="real-estate", margin=0.4, da=0.3, dps=2.0, price=45, ni_margin=0.15), "REIT"),
    ]
    out = {}
    for name, F, want in cases:
        v = V.evaluate(F, None, rf, None, "2026-11-18")
        out[want + name] = v
        check(f"분류: {name} → {want}", v["archetype"]["code"] == want, f"got {v['archetype']['code']}")
        ok = v.get("fair_value") is not None and v["fair_value"] > 0 and v["fair_low"] <= v["fair_value"] <= v["fair_high"]
        check(f"  적정가 산출·범위 정합 ({name})", ok, str({k: v.get(k) for k in ("fair_low", "fair_value", "fair_high")}))
        ws = sum(m["weight"] for m in v["models"] if m.get("included"))
        check(f"  가중치 합 = 1 ({name})", abs(ws - 1) < 1e-9, str(ws))
        try:
            json.dumps(V.compact(v))
            html = page(v, name)
            check(f"  직렬화·렌더 ({name})", "VALUATION 1" in html and len(card(V.compact(v))) > 200)
        except Exception as e:  # noqa: BLE001
            check(f"  직렬화·렌더 ({name})", False, repr(e))

    # ── 2. 모델 적용 규칙 ──
    m = models(out["MATURE_COMPOUNDER성숙·현금창출"])
    check("무배당 → DDM 미적용", not m["ddm_stable"]["applicable"] and not m["ddm_multi"]["applicable"])
    check("비금융 → 초과이익·P/FFO 미적용", not m["residual_income"]["applicable"] and not m["p_ffo"]["applicable"])
    m = models(out["BANK은행"])
    check("은행 → FCFF DCF·EPV·EV 멀티플 미적용", not any(m[k]["applicable"] for k in ("dcf_growth_5y", "dcf_ebitda_10y", "epv", "ev_ebitda", "ev_rev")))
    check("은행 → 초과이익·P/B 통합 포함", m["residual_income"].get("included") and m["pb"].get("included"))
    m = models(out["REITREIT"])
    check("REIT → P/FFO·DDM 통합 포함", m["p_ffo"].get("included") and m["ddm_multi"].get("included"))
    m = models(out["PRE_REVENUE초기 단계"])
    check("초기 단계 → P/E·EV/EBITDA·EPV·5년 DCF 미적용",
          not any(m[k]["applicable"] for k in ("pe", "ev_ebitda", "epv", "dcf_growth_5y", "graham")))
    m = models(out["HYPERGROWTH_UNPROFITABLE고성장 적자"])
    check("고성장 적자 → 후행 이익 모델(그레이엄·EPV) 미적용", not m["graham"]["applicable"] and not m["epv"]["applicable"])
    kq = out["HYPERGROWTH_UNPROFITABLE고성장 적자"]["wacc"]
    check("적자 기업: 타인자본비용 ≤ 자기자본비용 (WACC ≤ ke)", kq["kd"] <= kq["ke"] + 1e-12 and kq["wacc"] <= kq["ke"] + 1e-12,
          f"kd={kq['kd']:.3f} ke={kq['ke']:.3f} wacc={kq['wacc']:.3f}")
    m = models(out["DIVIDEND_DEFENSIVE배당·방어"])
    check("배당주 → DDM 통합 포함", m["ddm_multi"].get("included") and m["ddm_stable"].get("included"))

    # ── 3. DCF 수식: 독립 구현과 대조 ──
    P = {"rev0": 1000.0, "g1": 0.10, "g2": 0.08, "gT": 0.03, "m0": 0.20, "mT": 0.20, "conv": 3, "tax0": 0.25, "taxT": 0.25,
         "s2c": 2.0, "roic_prem": 0.03, "da_pct": 0.05, "w0": 0.09, "wT5": 0.09, "wT10": 0.09, "amort0": 0.0}
    rev, pv, df = 1000.0, 0.0, 1.0
    for t in range(1, 6):
        g = 0.10 if t == 1 else 0.08 if t == 2 else 0.03 + (0.08 - 0.03) * V.GROWTH_DECAY ** (t - 2)
        new = rev * (1 + g)
        fcff = new * 0.20 * 0.75 - (new - rev) / 2.0
        df /= 1.09
        pv += fcff * df * 1.09 ** 0.5
        rev = new
    tv = rev * 1.03 * 0.20 * 0.75 * (1 - 0.03 / 0.12) / (0.09 - 0.03) * 1.09 ** 0.5     # 기중 할인: 영구 현금흐름도 연중에 들어온다
    want = pv + tv * df
    got = float(V.dcf(P, 5, "growth"))
    check("DCF(영구성장) = 독립 계산", abs(got / want - 1) < 1e-9, f"{got} vs {want}")
    d = V.dcf(P, 5, "ebitda", 10.0, detail=True)
    check("DCF(EBITDA Exit) 잔존가치 = EBITDA_N × 배수", abs(d["tv"] - (rev * 0.20 + rev * 0.05) * 10.0) < 1e-6)
    check("할인율↑ → 가치↓", float(V.dcf({**P, "w0": 0.10, "wT5": 0.10}, 5, "growth")) < got)
    check("목표마진↑ → 가치↑", float(V.dcf({**P, "mT": 0.25}, 5, "growth")) > got)
    check("영구성장률↑ → 가치↑ (ROIC > WACC)", float(V.dcf({**P, "gT": 0.035}, 5, "growth")) > got)
    q = {**P, "capex_pct": [0.08, 0.07, 0.06], "dep_pct": 0.05, "nwc_pct": 0.10}       # Finbox 식 명시적 현금흐름 빌드
    rev2, pv2, df2, caps = 1000.0, 0.0, 1.0, [0.08, 0.07, 0.06, 0.06, 0.06]
    for t in range(1, 6):
        g = 0.10 if t == 1 else 0.08 if t == 2 else 0.03 + (0.08 - 0.03) * V.GROWTH_DECAY ** (t - 2)
        new = rev2 * (1 + g)
        df2 /= 1.09
        pv2 += (new * 0.20 * 0.75 + 0.05 * new - caps[t - 1] * new - 0.10 * (new - rev2)) * df2 * 1.09 ** 0.5
        rev2 = new
    tv2 = rev2 * 0.20 * 0.75 * 1.03 * (1 - 0.03 / 0.12) / (0.09 - 0.03) * 1.09 ** 0.5     # 영구 구간: 두 방식 모두 재투자율 g ÷ ROIC
    check("DCF(명시적 빌드: NOPAT + D&A − CAPEX − ΔNWC) = 독립 계산", abs(float(V.dcf(q, 5, "growth")) / (pv2 + tv2 * df2) - 1) < 1e-9)
    check("CAPEX 비율↑ → 가치↓", float(V.dcf({**q, "capex_pct": 0.12}, 5, "growth")) < float(V.dcf({**q, "capex_pct": 0.06}, 5, "growth")))
    hi = float(V.dcf({**P, "g1": 3.0, "g2": 2.0}, 10, "growth", detail=True)["rev_n"])
    cap = float(V.dcf({**P, "g1": 3.0, "g2": 0.95}, 10, "growth", detail=True)["rev_n"])
    check("초고성장 완충: 2년차 200% 와 95% 의 10년 뒤 매출 차이 < 2.5배", hi / cap < 2.5, f"{hi / cap:.2f}")

    # ── 4. 통합·판정 ──
    v = out["HIGH_GROWTH고성장 흑자"]
    rd = v["reverse_dcf"]
    if rd.get("status") == "OK":
        F = make(growth=0.30, margin=0.20, price=120)
        D = V.derive(F, None, V._clip(0.6 * rf, 0.02, 0.03))
        c, gT = rd["implied_cagr_5y"], v["assumptions"]["gT"]
        path = [c] * 5 + [c + (gT - c) * k / 5 for k in range(1, 6)]
        val = float(V.per_share(V.dcf(v["assumptions"], 10, "growth", gpath=path), D, v["assumptions"]["p_fail"]))
        check("역DCF: 내재 성장률로 재계산하면 현재가", abs(val / v["price"] - 1) < 1e-3, f"{val} vs {v['price']}")
    else:
        check("역DCF 상태 산출", rd.get("status") in ("MARGIN", "ABOVE", "BELOW"), str(rd))
    v2 = V.evaluate(make(growth=0.30, margin=0.20, price=120), None, rf, None, "2026-11-18")
    check("결정성: 같은 입력 → 같은 몬테카를로", v["monte_carlo"]["p50"] == v2["monte_carlo"]["p50"])
    cheap = V.evaluate(make(price=10.0), None, rf, None, "x")
    rich = V.evaluate(make(price=400.0), None, rf, None, "x")
    check("판정: 저가 → 저평가 / 고가 → 고평가", "UNDER" in cheap["verdict"] and "OVER" in rich["verdict"],
          f"{cheap['verdict']} / {rich['verdict']}")
    lo, hi2 = V.evaluate(make(price=48.0), None, rf, None, "x"), V.evaluate(make(price=52.0), None, rf, None, "x")
    check("내재가치 모델은 주가에 거의 무관 (자본구성 가중치 경유 효과만)",
          abs(models(lo)["dcf_growth_5y"]["mid"] / models(hi2)["dcf_growth_5y"]["mid"] - 1) < 0.03)
    noest = V.evaluate(make(est=False), None, rf, None, "x")
    check("컨센서스 없음 → 과거 성장률 대체 + 불확실성 사유 기록",
          noest["assumptions"]["growth_source"].startswith("과거") and any("컨센서스" in r for r in noest["uncertainty"]["reasons"]))
    net = make()
    net["estimates"]["rev"]["0y"]["avg"] *= 0.8
    net["estimates"]["rev"]["0y"]["year_ago"] *= 0.8
    net["estimates"]["rev"]["+1y"]["avg"] *= 0.8
    g_net = V.evaluate(net, None, rf, None, "x")["assumptions"]["g1"]
    g_base = out["MATURE_COMPOUNDER성숙·현금창출"]["assumptions"]["g1"]
    check("컨센서스 매출 정의 차이(순매출 등) → 성장률 불변", abs(g_net - g_base) < 0.005, f"{g_net:.4f} vs {g_base:.4f}")
    one = make()
    one["ttm"]["net_income"] *= 4
    one["ttm"]["eps"] *= 4
    v1 = V.evaluate(one, None, rf, None, "x")
    check("일회성 이익으로 부푼 GAAP EPS → 핵심 EPS 사용", any("핵심" in w for w in v1["warnings"]))

    # ── 5. 리서치 기반 적정가 · Valuation Desk (분리 구조) ──
    import copy
    import tempfile
    from smartmoney import valuation_desk as VD
    R = {"schema": 1, "ticker": "TEST", "as_of": "2026-11-01", "analyst": "test", "confidence": "MEDIUM",
         "thesis": "핵심 변수는 신규 설비 가동률과 장기 공급계약의 갱신 여부이며 현재 증거는 기본 시나리오를 지지한다.",
         "business": {"summary": "산업용 장비 제조·서비스", "moat": "설치 기반과 서비스 계약", "moat_rating": "NARROW", "segments": []},
         "performance": {"summary": "최근 4개 분기 가이던스 상회", "guidance": "매출 +6~8%", "execution_rating": "STRONG"},
         "plans": [{"item": "신규 공장", "timing": "2027", "impact": "생산능력 +20%"}],
         "news": [{"date": "2026-10-20", "headline": "장기 공급계약 체결", "impact": "POS", "why": "기본 시나리오 성장률 상향"}],
         "risks": ["수요 둔화", "원가 상승"], "catalysts": ["대형 수주"],
         "scenarios": {
             "bear": {"prob": 0.25, "story": "수요 둔화로 신규 공장 가동률이 60%에 머물고 가격 인하 압력이 지속되는 경우", "rev_growth": [0.0, 0.01, 0.02, 0.02, 0.02], "target_margin": 0.11, "margin_year": 3},
             "base": {"prob": 0.50, "story": "가이던스대로 신규 공장이 2027년 가동되고 서비스 매출 비중이 완만히 상승하는 경우", "rev_growth": [0.07, 0.08, 0.07, 0.06, 0.05], "target_margin": 0.17, "margin_year": 4},
             "bull": {"prob": 0.25, "story": "대형 수주가 이어져 증설이 조기에 채워지고 서비스 믹스 개선으로 마진이 구조적으로 오르는 경우", "rev_growth": [0.12, 0.14, 0.12, 0.10, 0.08], "target_margin": 0.21, "margin_year": 5}},
         "options": [{"name": "신사업", "prob": 0.2, "value_per_share": 10.0, "basis": "시장 100억달러 × 점유율 10% × 마진 20% × 15배 ÷ 할인 1.6 ÷ 10억주"}],
         "sources": [{"title": f"출처 {i}", "url": f"https://www.sec.gov/doc{i}", "date": "2026-11-01"} for i in range(6)]}
    err, _w = VD.validate(R, "TEST")
    check("리서치 검증 통과 (정상 입력)", not err, str(err))
    vr = V.evaluate(make(), None, rf, None, "2026-11-18", R)
    sc = vr["research"]["scenarios"]
    check("리서치 방식으로 전환 + 기계적 기준선 보존", vr["method"] == "RESEARCH" and vr["mechanical"]["fair_value"] is not None)
    check("시나리오 가치: 약세 < 기본 < 강세", sc["bear"]["value"] < sc["base"]["value"] < sc["bull"]["value"],
          str([round(sc[k]["value"], 2) for k in sc]))
    want = sum(sc[k]["prob"] * sc[k]["value"] for k in sc)
    check("적정가 = 시나리오 확률가중", abs(vr["fair_value"] / want - 1) < 1e-9)
    check("옵션 기대값(0.2 × 10 = 2.0)이 각 시나리오에 가산", abs(sc["base"]["value"] - sc["base"]["core"] - 2.0) < 1e-9)
    check("범위 = 약세 ~ 강세", vr["fair_low"] == sc["bear"]["value"] and vr["fair_high"] == sc["bull"]["value"])
    hi_m = copy.deepcopy(R); hi_m["scenarios"]["base"]["target_margin"] = 0.25
    check("리서치 가정이 결과를 움직임 (기본 마진↑ → 적정가↑)", V.evaluate(make(), None, rf, None, "x", hi_m)["fair_value"] > vr["fair_value"])
    old = V.evaluate(make(), None, rf, None, "2027-06-01", R)
    check("오래된 리서치 → stale + 불확실성 사유", old["research"]["stale"] and any("경과" in x for x in old["uncertainty"]["reasons"]))
    for name, mut, key in (
        ("확률 합 ≠ 1", lambda r: r["scenarios"]["bull"].update(prob=0.5), "확률 합"),
        ("출처 부족", lambda r: r.update(sources=r["sources"][:3]), "sources"),
        ("시나리오 설명 누락", lambda r: r["scenarios"]["bear"].update(story="나쁨"), "story"),
        ("범위 밖 마진", lambda r: r["scenarios"]["base"].update(target_margin=0.95), "target_margin"),
        ("근거 없는 옵션", lambda r: r["options"][0].update(basis="감"), "options"),
        ("논지 누락", lambda r: r.update(thesis="좋음"), "thesis")):
        bad = copy.deepcopy(R); mut(bad)
        e2, _ = VD.validate(bad, "TEST")
        check(f"리서치 검증 거부: {name}", any(key in x for x in e2), str(e2))
    # ── 5-b. 할인율·잔존가치 원칙과 자체 점검 ──
    K = vr["wacc"]
    check("안정기 베타는 성숙 기업 범위(0.8~1.2)", V.STABLE_BETA[0] <= K["beta_stable"] <= V.STABLE_BETA[1])
    hb = make(growth=0.30, margin=0.20, price=120); hb["market"]["beta"] = 2.8
    Kh = V.evaluate(hb, None, rf, None, "x")["wacc"]
    check("고베타: 시작 할인율 > 안정기 할인율, 안정기 베타 = 1.2", Kh["wacc"] > Kh["wacc_t10"] and abs(Kh["beta_stable"] - 1.2) < 1e-9,
          f"{Kh['wacc']:.3f} → {Kh['wacc_t10']:.3f}, β {Kh['beta']:.2f} → {Kh['beta_stable']:.2f}")
    lo_e, hi_e = (V.evaluate(make(), None, rf, None, "x", R, {"erp": x})["fair_value"] for x in (0.035, 0.055))
    check("주식위험프리미엄↑ → 적정가↓ (시장 내재 ERP 입력 반영)", lo_e > hi_e)
    check("영구성장률 = 무위험금리 − 1%p (2~4%)", abs(vr["assumptions"]["gT"] - V._clip(rf - 0.01, 0.02, 0.04)) < 1e-12)
    gp = V._growth_path([0.30, 0.25, 0.20, 0.18, 0.16, 0.15, 0.14, 0.13, 0.12, 0.12], 0.03)
    check("예측 구간: 성장률이 영구성장률 ±1%p 에 들 때까지 연장(10~20년), 절벽 없음",
          10 < len(gp) <= V.MAX_HORIZON and abs(gp[-1] - 0.03) <= 0.01 and all(a >= b - 1e-12 for a, b in zip(gp[9:], gp[10:])), str(len(gp)))
    check("이미 수렴한 경로는 10년", len(V._growth_path([0.05, 0.04, 0.035], 0.03)) == 10)
    check("시나리오마다 예측 연수·잔존가치 비중·말년 ROIC 보고", all(sc[k].get("N") and sc[k].get("tv_share") is not None and "roic_T" in sc[k] for k in sc))
    rs = vr["research"]["rate_sensitivity"]
    check("할인율 민감도: −1%p > 기준 > +1%p", rs["minus_1pt"] > vr["fair_value"] > rs["plus_1pt"], str(rs))
    check("확률 대칭(25/50/25) 교차값 제공", abs(vr["research"]["fair_symmetric"] - vr["fair_value"]) < 1e-9)      # 이 리서치는 대칭
    skew = copy.deepcopy(R); skew["scenarios"]["bear"]["prob"], skew["scenarios"]["bull"]["prob"] = 0.35, 0.15
    vs = V.evaluate(make(), None, rf, None, "2026-11-18", skew)
    check("근거 없는 비대칭 확률 → 자체 점검 경고 + 검증 경고",
          any("비대칭" in c["text"] for c in vs["research"]["checks"]) and any("prob_basis" in w for w in VD.validate(skew, "TEST")[1]))
    skew["prob_basis"] = "최근 8개 분기 중 6개 분기에서 가이던스 하단을 밑돌았고 업종 내 증설 프로젝트의 지연 기저율이 60%를 넘는다."
    check("근거를 적으면 경고 없음", not any("비대칭" in c["text"] for c in V.evaluate(make(), None, rf, None, "2026-11-18", skew)["research"]["checks"]))
    burn = make(growth=0.40, margin=-0.30, price=30, industry="software-application", sector="technology", cash=0.4e9)
    vb = V.evaluate(burn, None, rf, None, "2026-11-18", R)
    if vb["assumptions"]["p_fail"] > 0:
        check("현금 소진 기업: fail_prob 미명시 → 자체 점검 ERROR (엔진 기본 실패확률을 몰래 곱하지 않음)",
              any(c["level"] == "ERROR" for c in vb["research"]["checks"]) and all(vb["research"]["scenarios"][k]["fail_prob"] == 0 for k in sc))
        Rf = copy.deepcopy(R)
        for k2, pf in (("bear", 0.0), ("base", 0.05), ("bull", 0.02)):
            Rf["scenarios"][k2]["fail_prob"] = pf
        vf = V.evaluate(burn, None, rf, None, "2026-11-18", Rf)
        check("fail_prob 명시 → ERROR 없음, 명시한 값만 적용", not any(c["level"] == "ERROR" for c in vf["research"]["checks"])
              and vf["research"]["scenarios"]["base"]["fail_prob"] == 0.05)
    else:
        check("현금 소진 픽스처가 실패확률을 만들지 못함", False)
    wa = copy.deepcopy(R); wa["scenarios"]["bear"]["wacc_adj"] = 0.02
    check("리서치 검증 거부: 근거 없는 할인율 가감(이중 반영)", any("wacc_adj_basis" in x for x in VD.validate(wa, "TEST")[0]))
    sm = copy.deepcopy(R); sm["adjustments"] = {"start_margin": 0.20}
    check("리서치 검증 거부: 근거 없는 출발 마진", any("start_margin" in x for x in VD.validate(sm, "TEST")[0]))
    sm["adjustments"]["start_margin_basis"] = "TTM 영업이익에서 일회성 소송 충당금 5억 달러(매출의 5%)를 제외한 반복 가능 수준"
    check("출발 마진 조정이 결과에 반영", not VD.validate(sm, "TEST")[0]
          and V.evaluate(make(), None, rf, None, "x", sm)["fair_value"] > vr["fair_value"])
    # 연도별 이익률 경로(margin_path): 상용화 전 적자가 이어지는 회사의 손익을 직선 가정 대신 그대로 넣는다
    mp = copy.deepcopy(R); mp["scenarios"]["base"]["margin_path"] = [-0.10, -0.05, 0.02]
    check("리서치 검증 거부: 근거 없는 margin_path", any("margin_path" in x for x in VD.validate(mp, "TEST")[0]))
    mp["scenarios"]["base"]["margin_path_basis"] = "신규 공장 가동 전 2년간 준비비로 영업적자, 3년차 손익분기 — 회사 가이던스의 비용 계획을 연도별로 환산"
    mp["scenarios"]["base"]["margin_year"] = 6
    vm = V.evaluate(make(), None, rf, None, "2026-11-18", mp)
    bm = vm["research"]["scenarios"]["base"]
    P0 = vm["assumptions"]
    rev, want_e = P0["rev0"], []
    for t, g in enumerate(_gp := V._growth_path(mp["scenarios"]["base"]["rev_growth"], bm["g_T"], 6), 1):
        rev *= 1 + g
        m = [-0.10, -0.05, 0.02][t - 1] if t <= 3 else 0.02 + (0.17 - 0.02) * min((t - 3) / 3, 1.0)
        want_e.append(rev * m)
    check("margin_path: 준 연차는 그대로, 이후 목표 마진으로 직선 수렴 (독립 계산 일치)",
          not VD.validate(mp, "TEST")[0] and len(bm["ebit_path"]) == len(want_e)
          and all(abs(a - b) <= 1e-6 * max(1.0, abs(b)) for a, b in zip(bm["ebit_path"], want_e)), str(bm["ebit_path"][:6]))
    check("margin_path: 흑자 전환 연차·누적 영업손실 산출 + 손실만큼 적정가 하락",
          bm["breakeven_year"] == 3 and abs(bm["loss_cum"] + want_e[0] + want_e[1]) < 1e-3 and bm["margin_path_given"]
          and bm["value"] < sc["base"]["value"], str((bm["breakeven_year"], bm["loss_cum"], bm["value"], sc["base"]["value"])))
    lp = copy.deepcopy(R); lp["scenarios"]["base"].update(margin_path=[0.10] * 14, margin_year=4,
                                                          margin_path_basis="14년간 10% 마진이 유지된다는 가정 — 예측 구간이 경로 길이만큼 늘어나는지 확인용 테스트 입력")
    check("margin_path 가 예측 구간보다 길면 구간을 경로 길이까지 연장",
          V.evaluate(make(), None, rf, None, "x", lp)["research"]["scenarios"]["base"]["N"] >= 14)
    ly = copy.deepcopy(R); ly["scenarios"]["base"]["margin_year"] = 14
    check("목표 마진 도달 연차(14년)가 지난 뒤에 잔존가치 계산", not VD.validate(ly, "TEST")[0]
          and V.evaluate(make(), None, rf, None, "x", ly)["research"]["scenarios"]["base"]["N"] >= 14)
    xl = copy.deepcopy(R); xl["scenarios"]["base"]["extra_investment"] = [3e8, 3e8]
    vx = V.evaluate(make(), None, rf, None, "x", xl)["research"]["scenarios"]["base"]
    ml = copy.deepcopy(R)
    revs, r_ = [], P0["rev0"]
    for g in mp["scenarios"]["base"]["rev_growth"][:4]:
        r_ *= 1 + g
        revs.append(r_)
    om = [sc["base"]["ebit_path"][i] / revs[i] for i in range(4)]       # 원래 경로의 1~4년차 마진 (4년차 = 목표 마진 도달)
    ml["scenarios"]["base"].update(margin_path=[om[0] - 3e8 / revs[0], om[1] - 3e8 / revs[1], om[2], om[3]],
                                   margin_path_basis="같은 3억 달러 지출을 투자가 아니라 영업손실로 넣는 경우 — 말년 ROIC 와 절세 효과 차이 확인용 테스트 입력")
    vl = V.evaluate(make(), None, rf, None, "x", ml)["research"]["scenarios"]["base"]
    check("같은 지출: 투자(extra_investment)로 넣으면 투하자본이 늘어 말년 ROIC 가 낮고, 손익(margin_path)으로 넣으면 세금이 줄어 가치가 더 높다",
          vx["roic_T"] < vl["roic_T"] and vl["value"] > vx["value"], str((vx["roic_T"], vl["roic_T"], vx["value"], vl["value"])))
    # 실패확률은 영업 외 자산까지 더한 전체 주주가치에 적용 (실패 = 주주가치 0)
    fo = copy.deepcopy(R); fo["adjustments"] = {"nonop_assets": 5e8}
    for k2 in fo["scenarios"]:
        fo["scenarios"][k2]["fail_prob"] = 0.2
    vfo = V.evaluate(make(), None, rf, None, "x", fo)
    nf = copy.deepcopy(fo)
    for k2 in nf["scenarios"]:
        nf["scenarios"][k2]["fail_prob"] = 0.0
    vnf = V.evaluate(make(), None, rf, None, "x", nf)
    check("실패확률: (DCF 주주가치 + 영업 외 자산 + 옵션) 전체에 (1 − 실패확률)",
          all(abs(vfo["research"]["scenarios"][k2]["value"] - 0.8 * vnf["research"]["scenarios"][k2]["value"]) < 1e-9 for k2 in sc),
          str([(vfo["research"]["scenarios"][k2]["value"], vnf["research"]["scenarios"][k2]["value"]) for k2 in sc]))
    # 말년 영업이익이 음수인 경로: 잔존가치 0
    ng = copy.deepcopy(R); ng["scenarios"]["bear"].update(target_margin=-0.05, margin_year=3)
    vng = V.evaluate(make(), None, rf, None, "x", ng)["research"]
    check("말년 영업이익 ≤ 0 → 잔존가치 0 + 자체 점검 표시",
          not vng["scenarios"]["bear"]["tv_share"] and any("잔존가치를 0" in c["text"] for c in vng["checks"]),
          str(vng["scenarios"]["bear"].get("tv_share")))
    nc = copy.deepcopy(R); nc["scenarios"]["base"].update(capex_pct=[0.05, -0.03, 0.04], da_pct=0.03)
    check("capex_pct: 순유입 연도(음수) 허용, 마지막 값은 0 이상", not VD.validate(nc, "TEST")[0]
          and any("capex_pct" in x for x in VD.validate({**nc, "scenarios": {**nc["scenarios"], "base": {**nc["scenarios"]["base"], "capex_pct": [0.05, -0.03]}}}, "TEST")[0]))
    check("영구성장률 ≤ 명시 구간 마지막 해 성장률 (약세 2% → 영구 2%, 강세 8% → 엔진 값)",
          abs(sc["bear"]["g_T"] - 0.02) < 1e-12 and abs(sc["bull"]["g_T"] - vr["assumptions"]["gT"]) < 1e-12, str([sc[k]["g_T"] for k in sc]))
    cmp_ = V.compact(vr)
    check("발행 요약에 할인율 출처·민감도·안정기 할인율 포함", cmp_["research"]["rate_sensitivity"] and cmp_["wacc_stable"] and cmp_["erp"] and cmp_["g_term"])
    e3, _ = VD.validate(R, "TEST", direct_value_only=True)
    check("금융·REIT 는 직접 산출(value_per_share) 요구", any("value_per_share" in x for x in e3))
    with tempfile.TemporaryDirectory() as td:
        from pathlib import Path as _P
        VD.PUB_DIR, VD.RESEARCH_DIR, VD.REPORTS_DIR = _P(td) / "reports" / "valuation", _P(td) / "state", _P(td) / "reports"
        paths = VD.publish("TEST", vr, R)
        check("발행: 수치 JSON · 상세 HTML · 리서치 원본", all(p.exists() for p in paths.values()) and "VALUATION 0" in paths["html"].read_text(encoding="utf-8"))
        pub = VD.read_published("TEST", vr["fair_value"] * 2, "2026-11-18")
        check("SMA 조회: 세션 종가로 괴리율·판정 재산정 (모델 재계산 없음)",
              pub is not None and abs(pub["upside_pct"] + 50) < 0.01 and "OVER" in pub["verdict"] and pub["fair_value"] == round(vr["fair_value"], 2))
        check("SMA 조회 요약에 시나리오·리서치 일자 포함", pub["method"] == "RESEARCH" and set(pub["research"]["scenarios"]) == {"bear", "base", "bull"}
              and len(card(pub)) > 300)
        # 주간 계획: 신규 · 실적 이후 · 예정 이벤트 · 의존 종목 · 유효
        (VD.RESEARCH_DIR / "TEST.fin.json").write_text(json.dumps({"ttm": {"end": "2026-09-30"}, "next_earnings": "2026-11-10"}), encoding="utf-8")
        pl = {r["ticker"]: r["action"] for r in VD.plan(["TEST", "NEWT"], today="2026-11-05", fetch=False)}
        check("주간 계획: 리서치 없는 종목 = NEW, 유효한 리서치 = OK", pl == {"TEST": "OK", "NEWT": "NEW"}, str(pl))
        check("주간 계획: 예정 실적일 경과 → EARNINGS", VD.plan(["TEST"], today="2026-11-12", fetch=False)[0]["action"] == "EARNINGS")
        (VD.RESEARCH_DIR / "TEST.fin.json").write_text(json.dumps({"ttm": {"end": "2026-09-30"}}), encoding="utf-8")
        check("주간 계획: 100일 경과 → STALE", VD.plan(["TEST"], today="2027-03-01", fetch=False)[0]["action"] == "STALE")
        Rv = copy.deepcopy(R); Rv["refresh_after"] = "2026-12-07"
        VD.publish("TEST", vr, Rv)
        (VD.RESEARCH_DIR / "TEST.fin.json").write_text(json.dumps({"ttm": {"end": "2026-09-30"}}), encoding="utf-8")
        check("주간 계획: refresh_after 경과 → EVENT", VD.plan(["TEST"], today="2026-12-10", fetch=False)[0]["action"] == "EVENT"
              and VD.plan(["TEST"], today="2026-12-05", fetch=False)[0]["action"] == "OK")
        VD.publish("MECH", V.evaluate(make(), None, rf, None, "x"))
        check("기계적 기준선만 있는 종목은 SMA 에 적정가로 노출되지 않음", VD.read_published("MECH", 50.0) is None)
        check("미발행 종목 → None", VD.read_published("NOPE", 10.0) is None)

    print(f"\n{FAILS} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
