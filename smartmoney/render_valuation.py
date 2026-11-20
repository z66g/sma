"""적정가치 리포트 렌더러 — 상세 페이지(독립 실행형 HTML · Chart.js 4.4) + 요약 카드(인라인 스타일, 어디든 삽입 가능).

디자인 시스템은 3-Layer 리포트와 동일 (라이트 테마 · HEX 고정 · 시스템 폰트).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .render_html import (A_BG, B_BG, BEAR, BORDER, BULL, CARD, CSS, FONT, G_BG, INFO, MUTED, PANEL, R_BG, TXT, WARN,
                          alert, e, kpi, pill, table)

VERDICT_KIND = {"DEEP_UNDERVALUED": "pos", "UNDERVALUED": "pos", "FAIR": "info", "OVERVALUED": "neg",
                "DEEP_OVERVALUED": "neg"}
UNC_KIND = {"LOW": "pos", "MEDIUM": "info", "HIGH": "warn", "VERY_HIGH": "neg"}
KIND_COL = {"pos": (G_BG, BULL), "neg": (R_BG, BEAR), "warn": (A_BG, WARN), "info": (B_BG, INFO)}


def _sym(ccy: Optional[str]) -> str:
    return "$" if (ccy or "USD").upper() == "USD" else f"{(ccy or '').upper()} "


def _px(x: Optional[float], ccy: Optional[str] = "USD") -> str:
    if x is None:
        return "N/A"
    return f"{_sym(ccy)}{x:,.0f}" if abs(x) >= 1000 else f"{_sym(ccy)}{x:,.2f}"


def _big(x: Optional[float], ccy: Optional[str] = "USD") -> str:
    if x is None:
        return "N/A"
    a, s = abs(x), "-" if x < 0 else ""
    return (f"{s}{_sym(ccy)}{a / 1e12:,.2f}T" if a >= 1e12 else f"{s}{_sym(ccy)}{a / 1e9:,.2f}B" if a >= 1e9
            else f"{s}{_sym(ccy)}{a / 1e6:,.1f}M")


def _pc(x: Optional[float], d: int = 1, signed: bool = False) -> str:
    return "N/A" if x is None else (f"{x * 100:+.{d}f}%" if signed else f"{x * 100:.{d}f}%")


def _x(x: Optional[float], d: int = 1) -> str:
    return "N/A" if x is None else f"{x:,.{d}f}x"


def _updown(up: Optional[float]) -> str:
    if up is None:
        return "N/A"
    c = BULL if up > 0 else BEAR if up < 0 else TXT
    return f'<span style="color:{c};font-weight:700;">{up:+.1f}%</span>'


# ─── 범위 바 (통합 범위 · 적정가 · 현재가) — 순수 HTML/CSS ───────────────────────
def range_bar(low: float, fair: float, high: float, price: float, ccy: str = "USD", height: int = 46) -> str:
    lo, hi = min(low, price) * 0.88, max(high, price) * 1.08
    span = max(hi - lo, 1e-9)
    pos = lambda v: max(0.0, min(100.0, (v - lo) / span * 100))  # noqa: E731
    fl, fh, ff, pp = pos(low), pos(high), pos(fair), pos(price)
    lab_side = "left" if pp < 50 else "right"
    lab_css = f"left:{pp:.2f}%;margin-left:6px;" if lab_side == "left" else f"right:{100 - pp:.2f}%;margin-right:6px;"
    return (
        f'<div style="position:relative;height:{height}px;font-family:{FONT};">'
        f'<div style="position:absolute;left:0;right:0;top:20px;height:8px;background:{PANEL};border-radius:4px;"></div>'
        f'<div title="모델 통합 범위" style="position:absolute;left:{fl:.2f}%;width:{max(fh - fl, 0.6):.2f}%;top:20px;height:8px;'
        f'background:{INFO};opacity:.85;border-radius:4px;"></div>'
        f'<div title="적정가" style="position:absolute;left:{ff:.2f}%;top:15px;width:3px;height:18px;margin-left:-1.5px;'
        f'background:{TXT};border-radius:2px;"></div>'
        f'<div title="현재가" style="position:absolute;left:{pp:.2f}%;top:11px;width:0;height:26px;border-left:2px dashed {WARN};"></div>'
        f'<div style="position:absolute;{lab_css}top:-2px;font-size:10.5px;color:{WARN};font-weight:700;white-space:nowrap;">현재가 {_px(price, ccy)}</div>'
        f'<div style="position:absolute;left:{fl:.2f}%;top:32px;font-size:10.5px;color:{MUTED};transform:translateX(-50%);white-space:nowrap;">{_px(low, ccy)}</div>'
        f'<div style="position:absolute;left:{fh:.2f}%;top:32px;font-size:10.5px;color:{MUTED};transform:translateX(-50%);white-space:nowrap;">{_px(high, ccy)}</div>'
        f'</div>')


# ─── 요약 카드 (3-Layer 리포트·종목 페이지에 삽입) ───────────────────────────────
def card(vc: Optional[Dict[str, Any]], href: Optional[str] = None) -> str:
    """compact() 결과 → 인라인 스타일 카드. 값이 없으면 빈 문자열."""
    if not vc or vc.get("fair_value") is None:
        return ""
    ccy = vc.get("currency") or "USD"
    a, u = vc.get("archetype") or {}, vc.get("uncertainty") or {}
    vk = VERDICT_KIND.get(vc.get("verdict"), "info")
    bg, fg = KIND_COL[vk]
    pl = lambda t, k: (f'<span style="display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;'  # noqa: E731
                       f'white-space:nowrap;background:{KIND_COL[k][0]};color:{KIND_COL[k][1]};">{e(t)}</span>')
    R = vc.get("research") or {}
    is_r = vc.get("method") == "RESEARCH"
    SK = {"bear": "약세", "base": "기본", "bull": "강세"}
    if is_r and R.get("scenarios"):
        models = " · ".join(f"{SK[k]} {_px(R['scenarios'][k]['value'], ccy)} ({R['scenarios'][k]['prob'] * 100:.0f}%)"
                            for k in ("bear", "base", "bull") if k in R["scenarios"])
        head = f"적정가치 · 리서치 기반 시나리오 확률가중 ({e(R.get('as_of'))}" + (" · 갱신 필요" if vc.get("stale") else "") + ")"
    else:
        models = " · ".join(f"{e(m['name'])} {_px(m.get('mid'), ccy)}" for m in (vc.get("models") or [])[:3])
        head = "기계적 기준선 (재무제표만 반영 · 사업 내용 미반영 — 참고용)"
    extra = []
    if is_r and vc.get("mechanical_fair") is not None:
        extra.append(f"기계적 기준선 {_px(vc['mechanical_fair'], ccy)}")
    if is_r and (R.get("second_opinion") or {}).get("fair_value"):
        extra.append(f"독립 2차 의견 {_px(R['second_opinion']['fair_value'], ccy)}")
    rs = R.get("rate_sensitivity") if is_r else None
    if rs:
        extra.append(f"할인율 ±1%p {_px(rs['plus_1pt'], ccy)}~{_px(rs['minus_1pt'], ccy)}"
                     + ("" if R.get("verdict_robust", True) else f' <b style="color:{WARN};">(판정 민감)</b>'))
    if vc.get("model_gap"):
        extra.append(f'<b style="color:{WARN};">⚠ 시장과 3배 이상 괴리 — 상세의 "현재가가 맞으려면" 참고</b>')
    if vc.get("implied_cagr_5y") is not None and vc.get("base_cagr_5y") is not None:
        extra.append(f"시장 내재 5년 매출성장 <b>{vc['implied_cagr_5y'] * 100:.0f}%</b> vs 모델 가정 {vc['base_cagr_5y'] * 100:.0f}%")
    if vc.get("analyst_target") is not None:
        extra.append(f"애널리스트 목표가 {_px(vc['analyst_target'], ccy)}")
    link = (f'<a href="{e(href)}" style="color:{INFO};text-decoration:none;font-weight:600;white-space:nowrap;">모델별 상세 →</a>'
            if href else "")
    return (
        f'<div style="background:#FFFFFF;border:0.5px solid {BORDER};border-left:4px solid {fg};border-radius:0 6px 6px 0;'
        f'padding:10px 14px;font-family:{FONT};color:{TXT};">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:.06em;color:{MUTED};">{head}</div>'
        f'<div style="display:flex;gap:6px;flex-wrap:wrap;">{pl(a.get("label") or "N/A", "info")}'
        f'{pl("불확실성 " + (u.get("label") or "N/A"), UNC_KIND.get(u.get("level"), "warn"))}{pl(vc.get("verdict_ko") or "", vk)}</div></div>'
        f'<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:6px 0 2px;">'
        f'<span style="font-size:22px;font-weight:700;">{_px(vc["fair_value"], ccy)}</span>'
        f'<span style="font-size:13px;">현재가 대비 {_updown(vc.get("upside_pct"))}</span>'
        f'<span style="font-size:11.5px;color:{MUTED};">범위 {_px(vc.get("fair_low"), ccy)} ~ {_px(vc.get("fair_high"), ccy)}</span></div>'
        f'{range_bar(vc["fair_low"], vc["fair_value"], vc["fair_high"], vc["price"], ccy)}'
        f'<div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:11.5px;color:{MUTED};margin-top:4px;">'
        f'<span>{models}{" · " if models and extra else ""}{" · ".join(extra)}</span>{link}</div>'
        + (f'<div style="font-size:12px;margin-top:6px;padding-top:6px;border-top:0.5px dashed {BORDER};">{e(R.get("thesis"))}</div>'
           if is_r and R.get("thesis") else "") + '</div>')


# ─── 상세 페이지 ─────────────────────────────────────────────────────────────
def _vsec(anchor: str, n: int, name: str, badges: List[str]) -> str:
    return (f'<div class="sec" id="{anchor}"><span class="sec-t">▶ VALUATION {n} [{e(name)}]</span>'
            f'<span class="sec-b">{"".join(badges)}</span></div>')


def _model_rows(v: Dict[str, Any], ccy: str) -> tuple:
    price = v["price"]
    rows, hl = [], {}
    inc = [m for m in v["models"] if m.get("included")]
    ref = [m for m in v["models"] if m["applicable"] and not m.get("included")]
    for m in inc + ref:
        bad = [c for c in m["checks"] if not c["ok"]]
        memo = "".join(f"<div>{e(n_)}</div>" for n_ in m["notes"][:3])
        memo += "".join(f'<div style="color:{WARN};">⚠ {e(c["text"])}</div>' for c in bad)
        if not m.get("included") and m.get("reason"):
            memo += f'<div class="muted">{e(m["reason"])}</div>'
        w = f'<b>{m["weight"] * 100:.0f}%</b>' if m.get("included") else '<span class="muted">참고</span>'
        rows.append([f'<b>{e(m["name"])}</b>', e(m["family"]), _px(m["low"], ccy), f'<b>{_px(m["mid"], ccy)}</b>',
                     _px(m["high"], ccy), w, _updown((m["mid"] / price - 1) * 100), memo or "—"])
    na = [[f'{e(m["name"])}', e(m["family"]), e(m["reason"] or "—")] for m in v["models"] if not m["applicable"]]
    return rows, hl, na


def page(v: Dict[str, Any], name: Optional[str] = None, home: str = "./", dash: str = "../../") -> str:
    T, ccy = v.get("ticker") or "", v.get("currency") or "USD"
    A, K, P, M = v["archetype"], v["wacc"], v["assumptions"], v["metrics"]
    price = v["price"]
    ok = v.get("fair_value") is not None
    u = v.get("uncertainty") or {}
    vk = VERDICT_KIND.get(v.get("verdict"), "info")
    fin = A["code"] in ("BANK", "INSURANCE")

    header = f"""
<div class="top"><a href="{e(home)}">← {e(T)} 종목 페이지</a><a href="{e(dash)}">대시보드</a></div>
<div class="hdr">
  <div><h1>{e(T)} <span class="nm">{e(name or '')} · 적정가치 분석</span></h1>
  <div class="sub">기준 세션 <b>{e(v.get('as_of') or 'N/A')}</b> · 재무 데이터 {e(v.get('fundamentals_asof') or 'N/A')} 수집 · {e(A.get('sector') or '')} / {e(A.get('industry') or '')}</div></div>
  <div class="px"><div class="pv">{_px(price, ccy)}</div><div class="muted">현재가</div></div>
</div>"""
    if not ok:
        body = alert(e(v.get("summary") or "적정가치를 산출하지 못했습니다."), "warn", "산출 불가")
        hero = ""
    else:
        bg, fg = KIND_COL[vk]
        an = v.get("analyst") or {}
        R_ = v.get("research")
        hero_label = (f"적정가치 판정 · 리서치 기반 시나리오 확률가중 (리서치 {e(R_.get('as_of'))}" + (" · 갱신 필요" if R_.get("stale") else "") + ")") if R_ \
            else "기계적 기준선 · 재무제표만 반영 (사업모델·계획·뉴스 미반영 — 리서치 전 참고용)"
        gap = ""
        if v.get("model_gap") and v.get("research"):
            gap = alert("리서치 적정가와 시장 가격의 괴리가 3배 이상입니다. 리서치가 검토한 사업·계획·신사업 옵션으로는 현재가가 설명되지 않는다는 뜻이며, "
                        "<b>현재가가 맞으려면 무엇이 실현돼야 하는지</b>는 아래 '가정에 대한 메모'에 있습니다. 시장이 옳을 수 있습니다 — "
                        "이 수치는 '공개 증거로 뒷받침되는 가치'이지 주가 예측이 아닙니다.", "warn", "시장과 리서치의 큰 괴리")
        elif v.get("model_gap"):
            gap = alert("현 재무·컨센서스 기반 모델과 시장 가격의 괴리가 3배 이상입니다. 이 경우 적정가 수치 자체보다 "
                        "<b>시장이 어떤 가정을 가격에 넣고 있는지</b>(아래 VALUATION 4)가 핵심 정보입니다.", "warn", "모델로 설명되지 않는 가격")
        hero = f"""
<div class="verdict" style="background:{bg};border-left-color:{fg};">
  <div class="vl" style="color:{fg};">{hero_label}</div>
  <div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;">
    <span style="font-size:30px;font-weight:700;">{_px(v['fair_value'], ccy)}</span>
    <span style="font-size:15px;">현재가 대비 {_updown(v['upside_pct'])}</span>
    {pill(v['verdict_ko'], vk)} {pill('불확실성 ' + u.get('label', ''), UNC_KIND.get(u.get('level'), 'warn'), ' · '.join(u.get('reasons') or []))}
    {pill(A['label'], 'info')}
  </div>
  <div style="max-width:720px;margin-top:8px;">{range_bar(v['fair_low'], v['fair_value'], v['fair_high'], price, ccy)}</div>
  <div style="font-size:12.5px;margin-top:6px;">{e(v.get('summary'))}</div>
  <div class="muted" style="margin-top:4px;">판정 밴드: 불확실성 '{e(u.get('label'))}' → 적정가 대비 ±{v['mos_band_pct']:.0f}% 이내는 '적정 범위'로 본다.
  참고 — 애널리스트 평균 목표가 {_px(an.get('target_mean'), ccy)} ({'N/A' if an.get('upside_pct') is None else f"{an['upside_pct']:+.1f}%"}, {int(an['n']) if an.get('n') else 'N/A'}명) 는 통합에 포함하지 않는다.</div>
</div>{gap}"""

    research_html = _research_html(v, ccy) if v.get("research") else alert(
        "이 종목은 아직 애널리스트 리서치(사업모델·성과·계획·뉴스 → 시나리오)가 발행되지 않았습니다. 아래 수치는 재무제표와 컨센서스만으로 계산한 "
        "<b>기계적 기준선</b>이며 SMA 대시보드에는 적정가로 표시되지 않습니다.", "warn", "리서치 미발행")
    nav = ('<nav class="snav">' + "".join(f'<a href="#{h}">{t}</a>' for h, t in (
        ("v0", "0 리서치"), ("v1", "1 성격 분류"), ("v2", "2 모델별 적정가"), ("v3", "3 핵심 가정"), ("v4", "4 시장 내재 가정"),
        ("v5", "5 Peer"), ("v6", "6 방법·한계"))) + "</nav>")

    # ── V1 성격 분류 ──
    traits = "".join(pill(t, "warn") for t in A.get("traits") or []) or '<span class="muted">특이 속성 없음</span>'
    reasons = "".join(f"<li>{e(r)}</li>" for r in A.get("reasons") or [])
    kp = "".join([
        kpi("시가총액", _big(M.get("mcap"), ccy), f"EV {_big(M.get('ev'), ccy)}"),
        kpi("매출 (TTM)", _big(M.get("rev"), ccy), f"NTM 성장 {_pc(M.get('g1'), 0, True)} · 과거 CAGR {_pc(M.get('g_hist'), 0, True)}"),
        kpi("영업이익률", _pc(M.get("m_ttm")), f"5년 중앙값 {_pc(M.get('m_med5'))} · 변동성 {_pc(M.get('m_std'))}",
            BULL if (M.get("m_ttm") or 0) > 0 else BEAR),
        kpi("ROIC / ROE", f"{_pc(M.get('roic'))} / {_pc(M.get('roe'))}", f"WACC {_pc(K.get('wacc'))}"),
        kpi("FCF 수익률", _pc(M.get("fcf_yield")), f"FCF {_big(M.get('fcf'), ccy)}" + (f" · 현금 여력 {M['runway']:.1f}년" if M.get("runway") else ""),
            BULL if (M.get("fcf") or 0) > 0 else BEAR),
        kpi("순부채", _big(M.get("net_debt"), ccy), f"배당수익률 {_pc(M.get('div_yield'))} · 성향 {_pc(M.get('payout'), 0)}"),
    ])
    v1 = _vsec("v1", 1, "종목 성격 분류 — 어떤 모델로 평가해야 하는가", [pill(A["label"], "info")]) + f"""
<div class="tk" style="border-left-color:{INFO};"><div class="tk-h" style="color:{INFO};">분류 근거</div>
<div class="tk-t">{e(A['label'])}</div><ul style="margin:6px 0 0;padding-left:18px;">{reasons}</ul>
<div class="tk-x"><b>속성</b> · {traits}</div></div>
<div class="kpis">{kp}</div>"""
    if not ok:
        return _wrap(T, v, header + body + nav + research_html + v1 + _foot(v), "{}")

    # ── V2 모델별 ──
    rows, hl, na = _model_rows(v, ccy)
    fam = v.get("family_values") or {}
    fam_txt = " · ".join(f"{e(k)} 평균 <b>{_px(val, ccy)}</b>" for k, val in fam.items())
    mech = v.get("mechanical") or v
    v2_name = "기계적 모델 (교차검증) — 재무제표·컨센서스만으로 계산한 범위" if v.get("research") else "모델별 적정가 — 범위와 가중치"
    v2_badges = [pill(f"통합 {v['n_models']}개", "info"),
                 pill(f"모델 간 편차 {v['dispersion_cv'] * 100:.0f}%", "warn" if v["dispersion_cv"] > 0.35 else "pos")] if v.get("n_models") else []
    if v.get("research") and mech.get("fair_value") is not None:
        v2_badges.insert(0, pill(f"기계적 기준선 {_px(mech['fair_value'], ccy)}", "warn"))
    v2 = _vsec("v2", 2, v2_name, v2_badges) + f"""
<div class="card"><div class="ct">모델별 적정가 범위 (보수~낙관) · 세로선 = 현재가 / 통합 적정가</div>
<div class="rg-l"><span class="rg-k"><i style="background:{BULL};opacity:.85;"></i>기준값이 현재가보다 높음</span>
<span class="rg-k"><i style="background:{BEAR};opacity:.85;"></i>기준값이 현재가보다 낮음</span>
<span class="rg-k"><i style="background:{INFO};opacity:.85;"></i>통합 범위</span>
<span class="rg-k"><i style="background:{TXT};width:3px;"></i>모델 기준값</span>
<span class="rg-k"><i style="background:none;border-left:2px dashed {WARN};width:2px;height:12px;"></i>현재가</span></div>
<div class="ch" id="ffwrap"><canvas id="cFF"></canvas></div>
<div class="muted" style="margin-top:6px;">{fam_txt} — 두 계열의 차이가 클수록 '시장 멀티플'과 '현금흐름 가치'가 다른 이야기를 하고 있다는 뜻.</div></div>
<div class="card"><div class="ct">통합에 사용한 모델과 참고 모델</div>
{table(["모델", "계열", "보수", "기준", "낙관", "가중치", "현재가 대비", "산출 근거 · 검증"], rows, mode="card")}</div>
<details class="dq"><summary>적용하지 않은 모델 {len(na)}개와 그 이유</summary>
{table(["모델", "계열", "미적용 사유"], na, mode="card")}</details>"""

    # ── V3 가정 ──
    krows = [
        ["무위험금리 (미 10년물)", _pc(K["rf"], 2), "FRED DGS10"],
        ["주식위험프리미엄 (ERP)", _pc(K["erp"], 2), e(K.get("erp_source") or "") + " — 시장이 지금 위험에 매기는 가격을 그대로 사용(시장 중립)"],
        ["베타", f"{K['beta']:.2f}", e(K["beta_source"]) + f" · 회귀 {K['beta_raw'] if K.get('beta_raw') is not None else 'N/A'}"
         + (f" · peer 무부채 {K['beta_peer_unlevered']:.2f}" if K.get("beta_peer_unlevered") else "")],
        ["규모 프리미엄", _pc(K["size_premium"], 2), "시가총액 구간별"],
        ["<b>자기자본비용</b>", f"<b>{_pc(K['ke'], 2)}</b>", "rf + β×ERP + 규모"],
    ]
    if not fin:
        krows += [
            ["세전 타인자본비용", _pc(K["kd"], 2), f"rf + 신용 스프레드 {_pc(K['spread'], 2)} (이자보상배율 "
             + (f"{K['coverage']:.1f}배" if K.get("coverage") is not None else "N/A") + " 기준 합성등급"
             + ("; 영업적자 기업은 실효 차입금리를 B~CCC 범위로 제한" if (K.get("coverage") or 0) < 0 or K.get("coverage") is None else "")
             + ") · 자기자본비용을 넘지 않음"],
            ["자본 구성 (시가)", f"자기자본 {_pc(K['we'], 0)} / 부채 {_pc(K['wd'], 0)}", "시가총액 · 총차입금(리스 포함)"],
            ["<b>WACC</b>", f"<b>{_pc(K['wacc'], 2)}</b>", f"안정 성장기(잔존가치 구간) {_pc(K['wacc_t10'], 2)} — 베타 {K['beta']:.2f} → {K.get('beta_stable', 1.0):.2f} (성숙 기업 범위 0.8~1.2), 10년에 걸쳐 수렴"],
        ]
    if fin or A["code"] == "REIT":
        proj = ""
        arows = [["평가 기준", "자기자본 기반" if fin else "FFO·배당 기반", e(A["reasons"][0] if A.get("reasons") else "")],
                 ["영구성장률", _pc(P["gT"], 1), "무위험금리 − 1%p (2~4% 범위): 장기 명목 성장률은 무위험금리를 넘지 못한다"],
                 ["ROE / 주당순자산", f"{_pc(M.get('roe'))} / {_px(M.get('bvps'), ccy)}", "초과이익·P/B 모델 입력"],
                 ["주당배당 / 배당성향", f"{_px(M.get('dps'), ccy)} / {_pc(M.get('payout'), 0)}", "DDM 입력"]]
    else:
        arows = [
            ["매출 성장 1년차 / 2년차", f"{_pc(P['g1'], 1, True)} / {_pc(P['g2'], 1, True)}", e(P["growth_source"]) + " → NTM 환산"],
            ["3년차 이후", f"영구성장률 {_pc(P['gT'], 1)} 로 감쇠", "초과 성장률이 매년 65% 씩만 잔존 (고성장의 평균회귀)"],
            ["영업이익률 현재 → 목표", f"{_pc(P['m0'])} → {_pc(P['mT'])}", e(P["margin_how"])],
            ["세율 현재 → 장기", f"{_pc(P['tax0'], 0)} → {_pc(P['taxT'], 0)}", "유효세율 → 법정 한계세율 방향"],
            ["Sales-to-Capital", f"{P['s2c']:.2f}", "매출 1 증가에 필요한 재투자 = 1 ÷ 이 값 (유형 투하자본 · 업종 기준 혼합)"],
            ["잔존가치 재투자율", "g ÷ ROIC", f"성숙기 ROIC = WACC + {_pc(P['roic_prem'], 1)} (현 초과수익의 절반만 지속 가정)"],
        ]
        if P.get("p_fail"):
            arows.append(["자금조달 실패 확률", _pc(P["p_fail"], 0), "현금 소진 속도 대비 보유 현금 → 주주가치에 직접 차감"])
        if P.get("amort0"):
            arows.append(["인수 무형자산 상각 가산", _big(P["amort0"], ccy), "현금 유출 없는 비용 — 매년 25% 씩 소멸 가정"])
        prow = [[f"{r['t']}년차", _pc(r["g"], 1, True), _big(r["rev"], ccy), _pc(r["margin"]), _big(r["ebit"], ccy),
                 _big(r["reinv"], ccy), f'<b>{_big(r["fcff"], ccy)}</b>', _pc(r["wacc"], 1)] for r in v["projection"]["rows"]]
        proj = f"""
<div class="g2"><div class="card"><div class="ct">매출·잉여현금흐름(FCFF) 전망 — 기본 시나리오</div><div class="ch"><canvas id="cPJ"></canvas></div></div>
<div class="card"><div class="ct">성장률·영업이익률 경로</div><div class="ch"><canvas id="cGM"></canvas></div></div></div>
<details class="dq"><summary>연도별 전망 표 (10년)</summary>
{table(["연차", "성장률", "매출", "영업이익률", "영업이익", "재투자", "FCFF", "할인율"], prow, mode="ts")}
<div class="muted" style="margin-top:6px;">10년 모델의 잔존가치 비중 {_pc(v['projection'].get('tv_share_10y'), 0)} · 5년 모델 {_pc(v['projection'].get('tv_share_5y'), 0)}</div></details>"""
    v3 = _vsec("v3", 3, "핵심 가정 — 할인율과 전망", [pill(("자기자본비용 " + _pc(K["ke"], 1)) if fin else ("WACC " + _pc(K["wacc"], 1)), "info"),
               pill(f"영구성장 {_pc(P['gT'], 1)}", "info")]) + f"""
<div class="g2"><div class="card"><div class="ct">자본비용</div>{table(["항목", "값", "근거"], krows, mode="card")}</div>
<div class="card"><div class="ct">전망 가정</div>{table(["항목", "값", "근거"], arows, mode="card")}</div></div>{proj}"""

    # ── V4 시장 내재 가정 ──
    rd, mc, sn = v.get("reverse_dcf"), v.get("monte_carlo"), v.get("sensitivity")
    v4 = ""
    if rd or mc or sn:
        badges = []
        if rd and rd.get("status") == "OK":
            badges.append(pill(f"내재 5년 성장 {_pc(rd['implied_cagr_5y'], 0)}", "warn" if (rd.get("gap_vs_base") or 0) > 0.03 else "pos"))
        if mc:
            badges.append(pill(f"적정가 > 현재가 확률 {mc['prob_above_price'] * 100:.0f}%", "pos" if mc["prob_above_price"] > 0.5 else "neg"))
        v4 = _vsec("v4", 4, "시장이 가격에 넣은 가정 · 분포 · 민감도", badges)
        if rd:
            cmp_ = ""
            if rd.get("status") == "OK":
                cmp_ = (f'<div class="tk-x">모델 기본 가정 5년 CAGR <b>{_pc(rd.get("base_cagr_5y"))}</b> · 과거 실적 CAGR <b>{_pc(rd.get("hist_cagr"))}</b> · '
                        f'내재 성장률이 기본 가정보다 높을수록 현재가는 컨센서스 이상의 성과를 선반영한 것.</div>')
            v4 += (f'<div class="tk" style="border-left-color:{WARN};"><div class="tk-h" style="color:{WARN};">역(逆) DCF — 현재가를 정당화하는 조건</div>'
                   f'<div class="tk-t">{e(rd.get("text"))}</div>{cmp_}</div>')
        cols = []
        if mc:
            cols.append(f"""<div class="card"><div class="ct">몬테카를로 분포 ({mc['sims']:,}회 · {mc['horizon']}년 DCF) — 성장·마진·할인율·재투자 동시 변동</div>
<div class="ch"><canvas id="cMC"></canvas></div>
<div class="muted" style="margin-top:6px;">하위 10% {_px(mc['p10'], ccy)} · 중앙값 <b>{_px(mc['p50'], ccy)}</b> · 상위 10% {_px(mc['p90'], ccy)} · 현재가를 넘는 시나리오 {mc['prob_above_price'] * 100:.0f}%.
DCF 입력의 불확실성만 반영한 분포이며 통합 적정가(상대가치 포함)와는 다른 값.</div></div>""")
        if sn:
            hdr = ["할인율 ＼ 영구성장"] + [_pc(g, 1) for g in sn["g"]]
            srows, shl = [], {}
            for i, w in enumerate(sn["wacc"]):
                cells = []
                for j, val in enumerate(sn["grid"][i]):
                    c = BULL if val > price else BEAR
                    b = "font-weight:700;" if i == 2 and j == 2 else ""
                    cells.append(f'<span style="color:{c};{b}">{_px(val, ccy)}</span>')
                srows.append([f"<b>{_pc(w, 1)}</b>"] + cells)
            cols.append(f"""<div class="card"><div class="ct">민감도 — {sn['horizon']}년 DCF(영구성장) 주당 가치</div>
{table(hdr, srows, {2: A_BG})}
<div class="muted" style="margin-top:6px;">가운데 행·열이 기본 가정. 초록 = 현재가보다 높음, 빨강 = 낮음. 할인율 1%p 가 결과를 얼마나 움직이는지가 이 종목의 금리 민감도.</div></div>""")
        if cols:
            v4 += f'<div class="g2">{"".join(cols)}</div>'

    # ── V5 Peer ──
    pt = v["peers"]["table"]
    ps = v["peers"]["stats"]
    f1 = lambda x, d=1: "—" if x is None else f"{x:,.{d}f}"  # noqa: E731
    prow5 = [[f'<b>{e(p_["symbol"])}</b> <span class="muted">{e((p_.get("name") or "")[:22])}</span>', _big(p_.get("mcap"), ccy),
              f1(p_.get("pe")), f1(p_.get("fpe")), f1(p_.get("ev_ebitda")), f1(p_.get("ev_rev")), f1(p_.get("pb"), 2),
              _pc(p_.get("rev_growth"), 0, True) if p_.get("rev_growth") is not None else "—",
              _pc(p_.get("op_margin"), 0) if p_.get("op_margin") is not None else "—"] for p_ in pt]
    med = lambda k, d=1: f1((ps.get(k) or {}).get("median"), d)  # noqa: E731
    prow5.append(["<b>peer 중앙값</b> <span class='muted'>(이상치 제외)</span>", "—", med("pe"), med("fpe"), med("ev_ebitda"), med("ev_rev"),
                  med("pb", 2), _pc((ps.get("rev_growth") or {}).get("median"), 0, True), _pc((ps.get("op_margin") or {}).get("median"), 0)])
    own = lambda a_, b_: f1(a_ / b_) if a_ is not None and b_ not in (None, 0) and a_ / b_ > 0 else "—"  # noqa: E731
    prow5.append([f"<b>{e(T)}</b> <span class='muted'>(현재가 기준)</span>", _big(M.get("mcap"), ccy), own(price, M.get("eps_gaap")),
                  own(price, M.get("eps_ntm")), own(M.get("ev"), M.get("ebitda")), own(M.get("ev"), M.get("rev")),
                  own(price, M.get("bvps")), _pc(M.get("g1"), 0, True), _pc(M.get("m_ttm"), 0)])
    v5 = _vsec("v5", 5, "Peer 비교 — 상대가치 모델의 기준", [pill(f"peer {len(pt)}개", "info" if len(pt) >= 5 else "warn")]) + f"""
<div class="card"><div class="ct">동종업계에서 시가총액이 가까운 순 ({e(v['peers'].get('scope') or 'N/A')})</div>
{table(["종목", "시총", "P/E", "선행 P/E", "EV/EBITDA", "EV/매출", "P/B", "매출성장", "영업이익률"], prow5,
       {len(prow5) - 2: PANEL, len(prow5) - 1: A_BG}, mode="ts")}
<div class="muted" style="margin-top:6px;">멀티플은 peer 중앙값을 그대로 쓰지 않고 성장률·마진 차이만큼 보정해 적용한다 (보정 폭 0.6~1.8배 한도).</div></div>"""

    body = header + hero + nav + research_html + v1 + v2 + v3 + v4 + v5 + _foot(v)
    return _wrap(T, v, body, _payload(v))


def _research_html(v: Dict[str, Any], ccy: str) -> str:
    """리서치 섹션: 논지 → 시나리오(가정·확률·가치) → 신사업 옵션 → 사업·성과·계획·뉴스·리스크 → 출처."""
    R, price = v["research"], v["price"]
    SK = {"bear": ("약세", "neg"), "base": ("기본", "info"), "bull": ("강세", "pos")}
    srows = []
    for k in ("bear", "base", "bull"):
        s = R["scenarios"][k]
        g = s.get("rev_growth") or []
        path = " → ".join(f"{x * 100:.0f}%" for x in g) if g else "—"
        assume = (f"매출 성장 {path}" + (f" (5년 CAGR {_pc(s.get('cagr5'), 0)}, 10년 뒤 매출 {_big(s.get('rev10'), ccy)})" if s.get("rev10") else "")
                  + f"<br>GAAP 영업이익률 {_pc(s.get('target_margin'), 0)} ({s.get('margin_year') or 5}년차 도달)"
                  + (f" · 할인율 {s['wacc_adj'] * 100:+.1f}%p" if s.get("wacc_adj") else "")
                  + (f"<br><span class='muted'>예측 {s['N']}년 · 할인율 {_pc(s.get('wacc'), 1)} → {_pc(s.get('wacc_T'), 1)} · 영구성장 {_pc(s.get('g_T'), 1)}"
                     + (f" · 잔존가치 비중 {_pc(s['tv_share'], 0)}" if s.get('tv_share') is not None else "")
                     + (f" · 실패확률 {_pc(s['fail_prob'], 0)}" if s.get('fail_prob') else "") + "</span>" if s.get("N") else "")) if g else e(s.get("how"))
        srows.append([pill(SK[k][0], SK[k][1]), f"<b>{s['prob'] * 100:.0f}%</b>", e(s.get("story")), assume,
                      f"<b>{_px(s['value'], ccy)}</b>", _updown((s["value"] / price - 1) * 100)])
    orows = [[f"<b>{e(o['name'])}</b>", f"{o['prob'] * 100:.0f}%", _px(o["value_per_share"], ccy), f"<b>{_px(o['expected'], ccy)}</b>", e(o.get("basis"))]
             for o in R.get("options") or []]
    opt_html = (f'<div class="card"><div class="ct">신사업 옵션가치 — 아직 매출이 없어 시나리오 매출에 넣지 않은 사업 (각 시나리오 가치에 기대값 {_px(R.get("option_value"), ccy)} 가산)</div>'
                + table(["사업", "성공 확률", "성공 시 주당 가치(현재가치)", "기대값", "산출 근거"], orows, mode="card") + "</div>") if orows else ""
    B, Pf = R.get("business") or {}, R.get("performance") or {}
    seg = [[f"<b>{e(x.get('name'))}</b>", _pc(x.get("revenue_share"), 0) if isinstance(x.get("revenue_share"), (int, float)) else "—",
            e(x.get("growth_view")), e(x.get("margin_view"))] for x in B.get("segments") or [] if x.get("name")]
    li = lambda xs: "".join(f"<li>{e(x)}</li>" for x in xs or [])  # noqa: E731
    plans = "".join(f"<li><b>{e(x.get('item'))}</b>" + (f" <span class='muted'>({e(x.get('timing'))})</span>" if x.get("timing") else "")
                    + (f" — {e(x.get('impact'))}" if x.get("impact") else "") + "</li>" for x in R.get("plans") or [])
    NK = {"POS": "pos", "NEG": "neg", "NEU": "muted"}
    news = [[e(x.get("date")), e(x.get("headline")), pill({"POS": "긍정", "NEG": "부정"}.get(str(x.get("impact")).upper(), "중립"),
             NK.get(str(x.get("impact")).upper(), "muted")), e(x.get("why"))] for x in R.get("news") or []]
    src = "".join(f'<li><a href="{e(x.get("url"))}" target="_blank" rel="noopener nofollow">{e(x.get("title"))}</a>'
                  f' <span class="muted">{e(x.get("date") or "")}</span></li>' for x in R.get("sources") or []
                  if str(x.get("url", "")).startswith("http"))
    mech = v.get("mechanical") or {}
    gap = ""
    if mech.get("fair_value"):
        gap = (f'<div class="tk-x"><b>기계적 기준선과의 차이</b> · 재무제표만 본 결과는 {_px(mech["fair_value"], ccy)}'
               f' — 리서치 적정가는 그 {v["fair_value"] / mech["fair_value"]:.1f}배. 차이의 근거는 아래 시나리오 가정과 옵션가치에 있다.</div>')
    so = R.get("second_opinion") or {}
    if so.get("fair_value"):
        sv = so.get("scenarios") or {}
        gap += (f'<div class="tk-x"><b>독립 2차 의견</b> · {e(so.get("analyst"))}: 적정가 <b>{_px(so["fair_value"], ccy)}</b>'
                + (f' (약세 {_px(sv.get("bear"), ccy)} · 기본 {_px(sv.get("base"), ccy)} · 강세 {_px(sv.get("bull"), ccy)})' if sv else "")
                + f' — 이 리서치와 {abs(so["fair_value"] / v["fair_value"] - 1) * 100:.0f}% 차이. {e(so.get("note") or "")}</div>')
    # 자체 점검 · 민감도: 결론이 어느 가정에 얼마나 기대고 있는지 숨기지 않는다
    sens, chk = R.get("rate_sensitivity"), R.get("checks") or []
    diag = []
    if sens:
        diag.append(f"<li><b>할인율 민감도</b> · 할인율이 1%p 낮으면 <b>{_px(sens['minus_1pt'], ccy)}</b>, 1%p 높으면 <b>{_px(sens['plus_1pt'], ccy)}</b>"
                    + ("" if R.get("verdict_robust", True) else f" — <b style='color:{WARN};'>이 범위 안에서 판정이 바뀐다</b>") + "</li>")
    if R.get("fair_symmetric") is not None:
        diag.append(f"<li><b>확률 가중</b> · 약세 {R['scenarios']['bear']['prob'] * 100:.0f}% / 기본 {R['scenarios']['base']['prob'] * 100:.0f}% / 강세 {R['scenarios']['bull']['prob'] * 100:.0f}%"
                    f" — 대칭(25/50/25)으로 두면 {_px(R['fair_symmetric'], ccy)}"
                    + (f"<br><span class='muted'>확률 근거: {e(R.get('prob_basis'))}</span>" if R.get("prob_basis") else "") + "</li>")
    LV = {"ERROR": ("오류", "neg"), "WARN": ("주의", "warn"), "INFO": ("참고", "info")}
    diag += [f"<li>{pill(LV.get(c['level'], ('참고', 'info'))[0], LV.get(c['level'], ('참고', 'info'))[1])} {e(c['text'])}</li>" for c in chk]
    diag_html = (f'<div class="card"><div class="ct">자체 점검 — 결론이 기대고 있는 가정과 계산의 내적 일관성</div><ul class="risk">{"".join(diag)}</ul></div>'
                 if diag else "")
    conf = str(R.get("confidence") or "").upper()
    return _vsec("v0", 0, "리서치 — 사업·성과·계획·뉴스를 시나리오로", [
        pill(f"리서치 {R.get('as_of')}", "warn" if R.get("stale") else "info"),
        pill(f"확신도 {conf}", {"HIGH": "pos", "MEDIUM": "info", "LOW": "warn"}.get(conf, "info")),
        pill(f"출처 {len(R.get('sources') or [])}건", "info")]) + f"""
<div class="tk" style="border-left-color:{INFO};"><div class="tk-h" style="color:{INFO};">투자 논지</div>
<div class="tk-t">{e(R.get('thesis'))}</div>{gap}</div>
<div class="card"><div class="ct">시나리오 — 서로 다른 세계에 확률을 부여하고 같은 DCF 로 계산</div>
{table(["시나리오", "확률", "실현 조건", "가정", "주당 가치", "현재가 대비"], srows, {1: B_BG}, mode="card")}</div>
{diag_html}
{opt_html}
<div class="g2"><div class="card"><div class="ct">사업모델 · 경쟁 우위 ({e(B.get('moat_rating') or 'N/A')})</div>
<div>{e(B.get('summary'))}</div><div style="margin-top:6px;"><b>우위</b> · {e(B.get('moat'))}</div>
{table(["부문", "매출 비중", "성장 전망", "수익성"], seg, mode="card") if seg else ""}</div>
<div class="card"><div class="ct">실제 성과 · 가이던스 (실행력 {e(Pf.get('execution_rating') or 'N/A')})</div>
<div>{e(Pf.get('summary'))}</div>{f'<div style="margin-top:6px;"><b>가이던스</b> · {e(Pf.get("guidance"))}</div>' if Pf.get('guidance') else ''}
<div class="ct" style="margin-top:10px;">경영 계획</div><ul class="risk">{plans}</ul></div></div>
<div class="card"><div class="ct">가정을 바꾼 뉴스·이벤트</div>{table(["날짜", "내용", "영향", "가정에 미친 영향"], news, mode="card")}</div>
<div class="g2"><div class="card"><div class="ct">리스크 (약세 시나리오로 가는 길)</div><ul class="risk">{li(R.get('risks'))}</ul></div>
<div class="card"><div class="ct">촉매 (강세 시나리오로 가는 길)</div><ul class="risk">{li(R.get('catalysts'))}</ul></div></div>
{f'<div class="interp"><div class="ih">가정에 대한 메모 · 확인하지 못한 것</div><div>{e(R.get("valuation_notes"))}</div></div>' if R.get('valuation_notes') else ''}
<details class="dq"><summary>출처 {len(R.get('sources') or [])}건 · 작성 {e(R.get('analyst') or 'N/A')}</summary><ul class="risk">{src}</ul></details>"""


def _foot(v: Dict[str, Any]) -> str:
    ws = "".join(f"<li>{e(w)}</li>" for w in v.get("warnings") or [])
    src = " · ".join(e(s) for s in v.get("sources") or [])
    return _vsec("v6", 6, "방법 · 한계 · 출처", []) + f"""
<div class="interp"><div class="ih">읽는 법</div><ul>
<li><b>분류 → 모델 선택</b>: 업종과 재무 특성(성장률·수익성·변동성·배당)으로 종목 성격을 먼저 정하고, 그 성격에 맞는 모델에만 가중치를 준다. 맞지 않는 모델은 계산하더라도 '참고' 또는 '미적용'으로 분리한다.</li>
<li><b>통합</b>: 성격별 기본 가중치 × 검증 체크(잔존가치 비중·Exit 내재 성장률·peer 표본 수) 신뢰도. 모델 간 중앙값에서 3배 이상 벗어난 모델은 가중치를 1/4 로 줄이고, 편차가 클수록 평균보다 중앙값에 무게를 둔다.</li>
<li><b>불확실성</b>: 모델 수 · 모델 간 편차 · 종목 성격 · 데이터 충실도로 산정. 불확실성이 클수록 '적정 범위' 판정 밴드가 넓어진다.</li>
<li><b>한계</b>: 재무제표와 컨센서스에 없는 것(신사업 옵션가치, 규제·소송, 경영진 변화)은 반영되지 않는다. 이월결손금·비영업자산·전환증권 희석은 단순화했다. 수급·옵션 포지션 분석(3-Layer 리포트)과는 독립된 축이다.</li>
</ul></div>
{f'<details class="dq" open><summary>데이터·가정 관련 주의 {len(v.get("warnings") or [])}건</summary><ul class="wl">{ws}</ul></details>' if ws else ''}
<div class="foot">데이터: {src or 'N/A'} · FRED (10년물·AAA 회사채). 이 페이지는 공개 재무 데이터에 기반한 모델 산출물이며 투자 권유가 아닙니다. Smart Money Analyzer · Valuation v{e(v.get('version'))}</div>"""


def _payload(v: Dict[str, Any]) -> str:
    inc = [m for m in v["models"] if m.get("included")]
    R = v.get("research")
    ff = [{"name": "리서치 적정가 (약세~강세)" if R else "통합 적정가", "low": v["fair_low"], "mid": v["fair_value"],
           "high": v["fair_high"], "w": None, "agg": True}]
    if R:
        for k, lab in (("bear", "약세"), ("base", "기본"), ("bull", "강세")):
            sv = R["scenarios"][k]
            ff.append({"name": f"시나리오 · {lab}", "low": sv["value"] * 0.985, "mid": sv["value"], "high": sv["value"] * 1.015,
                       "w": sv["prob"]})
        m_ = v.get("mechanical") or {}
        if m_.get("fair_value") is not None:
            ff.append({"name": "기계적 기준선 (통합)", "low": m_["fair_low"], "mid": m_["fair_value"], "high": m_["fair_high"], "w": None})
    ff += [{"name": ("기계 · " if R else "") + m["name"], "low": m["low"], "mid": m["mid"], "high": m["high"],
            "w": None if R else m["weight"]} for m in inc]
    out: Dict[str, Any] = {"price": v["price"], "fair": v["fair_value"], "ccy": _sym(v.get("currency")), "ff": ff}
    pj = (v.get("projection") or {}).get("rows")
    if pj:
        out["pj"] = [{"t": r["t"], "rev": r["rev"], "fcff": r["fcff"], "g": r["g"], "m": r["margin"]} for r in pj]
    mc = v.get("monte_carlo")
    if mc:
        out["mc"] = {"hist": mc["hist"], "edges": mc["edges"], "p50": mc["p50"]}
    return json.dumps(out, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _wrap(T: str, v: Dict[str, Any], body: str, payload: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(T)} 적정가치 분석</title>
<meta name="description" content="{e(v.get('summary'))}">
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>{CSS}
.verdict .muted{{color:{MUTED}}} .ch.ff{{height:auto}}
</style>
</head>
<body id="top"><div class="wrap">
{body}
</div>
<a href="#top" class="totop" aria-label="맨 위로">↑</a>
<script id="val-data" type="application/json">{payload}</script>
<script>{JS}</script>
</body></html>"""


JS = r"""
(function(){
const D=JSON.parse(document.getElementById('val-data').textContent||'{}');
if(!D.ff||typeof Chart==='undefined')return;
const F='-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
Chart.defaults.font.family=F;Chart.defaults.font.size=11;Chart.defaults.color='#1F2328';
Chart.defaults.plugins.legend.labels.font={family:F,size:11};
Chart.defaults.plugins.tooltip.titleFont={family:F};Chart.defaults.plugins.tooltip.bodyFont={family:F};
Chart.defaults.maintainAspectRatio=false;
const G='#EAEEF2',BU='#1A7F5A',BE='#CF222B',WA='#9A6700',IN='#0969DA',TX='#1F2328',MU='#656D76';
const a=(h,o)=>{const n=parseInt(h.slice(1),16);return `rgba(${n>>16&255},${n>>8&255},${n&255},${o})`;};
const px=v=>v==null?'N/A':D.ccy+(Math.abs(v)>=1000?v.toLocaleString('en-US',{maximumFractionDigits:0}):v.toFixed(2));
const big=v=>{if(v==null)return'N/A';const x=Math.abs(v),s=v<0?'-':'';return s+D.ccy+(x>=1e12?(x/1e12).toFixed(2)+'T':x>=1e9?(x/1e9).toFixed(1)+'B':(x/1e6).toFixed(0)+'M');};

// 세로 기준선 (선형 x축 값 기준) + 라벨
const vlines={id:'vlines',afterDatasetsDraw(c,_,o){if(!o||!o.lines)return;const ctx=c.ctx,ar=c.chartArea,sc=c.scales.x;ctx.save();
 o.lines.forEach(l=>{if(l.v==null)return;let x=o.frac?ar.left+(ar.right-ar.left)*l.v:sc.getPixelForValue(l.v);
  if(x<ar.left-1||x>ar.right+1)return;ctx.beginPath();ctx.setLineDash(l.dash||[]);ctx.lineWidth=l.w||2;ctx.strokeStyle=l.c;
  ctx.moveTo(x,ar.top);ctx.lineTo(x,ar.bottom);ctx.stroke();ctx.setLineDash([]);
  ctx.font='600 11px '+F;ctx.fillStyle=l.c;ctx.textBaseline='bottom';const w=ctx.measureText(l.t).width;
  const left=x+w+6>ar.right;ctx.textAlign=left?'right':'left';ctx.fillText(l.t,left?x-4:x+4,ar.top-(l.row?14:2));});ctx.restore();}};
// 모델 기준값(중앙) 눈금
const midTicks={id:'midTicks',afterDatasetsDraw(c,_,o){if(!o||!o.mids)return;const ctx=c.ctx,sc=c.scales.x,ar=c.chartArea,meta=c.getDatasetMeta(0);ctx.save();
 o.mids.forEach((v,i)=>{const b=meta.data[i];if(!b||v==null)return;const x=sc.getPixelForValue(v);if(x<ar.left||x>ar.right)return;
  ctx.fillStyle=TX;ctx.fillRect(x-1.5,b.y-b.height/2-3,3,b.height+6);});ctx.restore();}};

// ── 모델별 범위 (football field) ──
(function(){
 const ff=D.ff,el=document.getElementById('cFF');if(!el)return;
 document.getElementById('ffwrap').style.height=(ff.length*30+70)+'px';
 const his=ff.map(m=>m.high).sort((x,y)=>x-y);
 const cap=Math.max(D.price,ff[0].high,his[Math.floor(his.length*0.8)])*1.3;
 const cols=ff.map(m=>m.agg?a(IN,.85):a(m.mid>=D.price?BU:BE,.85));
 new Chart(el,{type:'bar',plugins:[vlines,midTicks],
  data:{labels:ff.map(m=>m.name+(m.w!=null?'  '+(m.w*100).toFixed(0)+'%':'')),
   datasets:[{label:'보수~낙관 범위',data:ff.map(m=>[Math.max(m.low,0),Math.min(m.high,cap)]),backgroundColor:cols,
    borderRadius:4,borderSkipped:false,barThickness:14}]},
  options:{indexAxis:'y',layout:{padding:{top:30,right:12}},
   scales:{x:{min:0,max:cap,grid:{color:G},ticks:{callback:v=>px(v)}},y:{grid:{display:false},ticks:{color:TX,font:{size:11.5}}}},
   plugins:{legend:{display:false},
    tooltip:{callbacks:{title:it=>ff[it[0].dataIndex].name,
     label:it=>{const m=ff[it.dataIndex];return ['보수 '+px(m.low)+'  ·  기준 '+px(m.mid)+'  ·  낙관 '+px(m.high),
      '현재가 대비 '+((m.mid/D.price-1)*100).toFixed(1)+'%'+(m.w!=null?'  ·  가중치 '+(m.w*100).toFixed(0)+'%':'')];}}},
    vlines:{lines:[{v:D.price,c:WA,dash:[5,4],t:'현재가 '+px(D.price),row:1},{v:D.fair,c:IN,t:'적정가 '+px(D.fair)}]},
    midTicks:{mids:ff.map(m=>m.mid)}}}});
})();

// ── 전망: 매출·FCFF (같은 단위·한 축) / 성장률·마진 (같은 % 축) ──
if(D.pj&&document.getElementById('cPJ')){
 const L=D.pj.map(r=>r.t+'Y');
 new Chart(document.getElementById('cPJ'),{type:'bar',
  data:{labels:L,datasets:[{label:'매출',data:D.pj.map(r=>r.rev),backgroundColor:a(IN,.85),borderRadius:4,maxBarThickness:22},
   {label:'잉여현금흐름(FCFF)',data:D.pj.map(r=>r.fcff),backgroundColor:a(BU,.85),borderRadius:4,maxBarThickness:22}]},
  options:{scales:{x:{grid:{display:false}},y:{grid:{color:G},ticks:{callback:v=>big(v)}}},
   plugins:{legend:{position:'bottom',labels:{boxWidth:12}},tooltip:{mode:'index',intersect:false,callbacks:{label:it=>it.dataset.label+': '+big(it.raw)}}}}});
 new Chart(document.getElementById('cGM'),{type:'line',
  data:{labels:L,datasets:[{label:'매출 성장률',data:D.pj.map(r=>r.g*100),borderColor:IN,backgroundColor:IN,borderWidth:2,pointRadius:3,tension:.25},
   {label:'영업이익률',data:D.pj.map(r=>r.m*100),borderColor:WA,backgroundColor:WA,borderWidth:2,pointRadius:3,tension:.25,borderDash:[5,3]}]},
  options:{interaction:{mode:'index',intersect:false},scales:{x:{grid:{display:false}},y:{grid:{color:G},ticks:{callback:v=>v+'%'}}},
   plugins:{legend:{position:'bottom',labels:{boxWidth:12}},tooltip:{callbacks:{label:it=>it.dataset.label+': '+it.raw.toFixed(1)+'%'}}}}});
}

// ── 몬테카를로 히스토그램 ──
if(D.mc&&document.getElementById('cMC')){
 const E=D.mc.edges,H=D.mc.hist,n=H.length,tot=H.reduce((s,x)=>s+x,0)||1;
 const mid=H.map((_,i)=>(E[i]+E[i+1])/2);
 const fr=v=>{if(v==null||v<E[0]||v>E[n])return null;return (v-E[0])/(E[n]-E[0]);};
 new Chart(document.getElementById('cMC'),{type:'bar',plugins:[vlines],
  data:{labels:mid.map(v=>px(v)),datasets:[{label:'시나리오 비중',data:H.map(x=>x/tot*100),
   backgroundColor:mid.map(v=>a(v>=D.price?BU:BE,.85)),borderRadius:2,barPercentage:1,categoryPercentage:.92}]},
  options:{layout:{padding:{top:30}},scales:{x:{grid:{display:false},ticks:{maxTicksLimit:7,maxRotation:0}},y:{grid:{color:G},ticks:{callback:v=>v+'%'}}},
   plugins:{legend:{display:false},tooltip:{callbacks:{title:it=>px(E[it[0].dataIndex])+' ~ '+px(E[it[0].dataIndex+1]),label:it=>'시나리오의 '+it.raw.toFixed(1)+'%'}},
    vlines:{frac:true,lines:[{v:fr(D.price),c:WA,dash:[5,4],t:'현재가 '+px(D.price),row:1},{v:fr(D.mc.p50),c:TX,t:'중앙값 '+px(D.mc.p50)}]}}}});
}
})();
"""
