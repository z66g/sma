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
    tv = rev * 1.03 * 0.20 * 0.75 * (1 - 0.03 / 0.12) / (0.09 - 0.03)
    want = pv + tv * df
    got = float(V.dcf(P, 5, "growth"))
    check("DCF(영구성장) = 독립 계산", abs(got / want - 1) < 1e-9, f"{got} vs {want}")
    d = V.dcf(P, 5, "ebitda", 10.0, detail=True)
    check("DCF(EBITDA Exit) 잔존가치 = EBITDA_N × 배수", abs(d["tv"] - (rev * 0.20 + rev * 0.05) * 10.0) < 1e-6)
    check("할인율↑ → 가치↓", float(V.dcf({**P, "w0": 0.10, "wT5": 0.10}, 5, "growth")) < got)
    check("목표마진↑ → 가치↑", float(V.dcf({**P, "mT": 0.25}, 5, "growth")) > got)
    check("영구성장률↑ → 가치↑ (ROIC > WACC)", float(V.dcf({**P, "gT": 0.035}, 5, "growth")) > got)
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

    print(f"\n{FAILS} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
