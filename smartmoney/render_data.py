"""JSON(v3, 레거시 키 유지) 및 Markdown 아카이브 렌더러."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from . import commentary as C


def _r(x, d=4):
    return round(x, d) if isinstance(x, float) else x


def _pick(L: Dict, keys: List[str]) -> Dict[str, Any]:
    return {k: _r(L.get(k)) for k in keys}


def render_json(a: Dict[str, Any]) -> str:
    m, l1, l2, l3, l4, s = a["meta"], a["l1"], a["l2"], a["l3"], a["l4"], a["scenarios"]
    out = {
        "schema": 3,
        "ticker": m["ticker"], "date": m["date"], "price": _r(m["price"]), "is_index": m["is_index"],
        "generated_at": m["generated_at"], "prev_close": _r(l4.get("prev_close")), "change_pct": _r(l4.get("change_pct")),
        "headline": a.get("headline"), "one_liner": a.get("one_liner"), "short_take": a.get("short_take"),
        "flow_board": C.flow_board(a), "watch_points": C.watch_points(a),
        "quality": a["quality"],
        "warnings": m.get("warnings") or [],
        "patterns": a["patterns"], "pattern_detail": a.get("pattern_detail"),
        "macro_env": a["macro"]["env"],
        "macro": {"score": a["macro"]["score"], "rows": [{k: _r(v) for k, v in r.items()} for r in a["macro"]["rows"]]},
        "scenarios": {k: s[k] for k in ("A_bullish", "B_neutral", "C_bearish", "raw_score", "dispersion", "contributions")},
        "phase": {k: _r(v) for k, v in a["phase"].items()},
        "levels": [{k: _r(v) for k, v in lv.items()} for lv in a["levels"]],
        "l1": {**_pick(l1, ["scenario", "signal", "confidence", "score", "dp_pct", "dp_avg20", "dp_ratio", "dp_class",
                            "delta_total", "obv_strength", "divergence", "obv_source", "window_days", "acc20",
                            "vwap5", "vwap_dist_pct", "delta_inst", "delta_pro", "delta_retail", "iar",
                            "dp_vwap", "dp_vwap_dist_pct", "off_vol_ratio"]),
               "cumulative_daily": l1.get("cumulative_daily"), "dp_series": l1.get("dp_series"), "ats": l1.get("ats"),
               "flow": ({k: v for k, v in l1["flow"].items() if k != "cumulative_daily"} if l1.get("flow") else None)},
        "l2": {**_pick(l2, ["scenario", "signal", "confidence", "score", "case", "short_pct_latest", "short_avg_14d",
                            "short_slope", "slope_dir", "anomaly_z", "short_shares", "est_short_total", "ctb_fee",
                            "ctb_delta_pct", "shares_available", "available_capped", "avail_delta_pct",
                            "borrow_source", "htb_class", "low_ctb_paradox"]),
               "short_series": l2.get("short_series"), "borrow_series": l2.get("borrow_series"),
               "short_interest": l2.get("short_interest")},
        "l3": _pick(l3, ["scenario", "signal", "confidence", "score", "expiry", "dte", "max_pain", "max_pain_dist_pct",
                         "monthly_expiry", "monthly_max_pain", "pc_oi", "pc_vol", "pc_vol_adj", "skew", "skew_25d",
                         "atm_iv", "iv30", "term_structure", "expected_move", "expected_move_pct", "net_gex",
                         "net_gex_weighted", "flip_zone", "flip_status", "gamma_regime", "call_wall", "put_wall", "oi_total",
                         "oi_trend_pct", "binary_event", "source"]),
        "l4": _pick(l4, ["scenario", "signal", "confidence", "score", "ma_alignment", "sma20", "sma50", "sma200",
                         "ema9", "ema21", "rsi14", "atr14", "atr_pct", "bb_upper", "bb_lower", "bb_width_pct",
                         "bb_width_pctile", "bb_position", "immediate_resistance", "immediate_support",
                         "key_resistance", "key_support", "support_source", "resistance_source", "below_all_swing_lows", "vol_ratio30", "high_52w", "low_52w"]),
        "events": a["events"],
    }
    if l3.get("pinning"):
        out["l3"]["pinning"] = l3["pinning"]
    return json.dumps(out, ensure_ascii=False, indent=1, default=str)


def _m(x: Optional[float]) -> str:
    return "N/A" if x is None else C.ssh(x)


def _p(x: Optional[float], d: int = 1) -> str:
    return "N/A" if x is None else f"{x:.{d}f}%"


def _d(x: Optional[float]) -> str:
    return "N/A" if x is None else f"${x:,.2f}"


def _hist_row(h: Dict[str, Any]) -> str:
    l1, l2, l3 = h.get("l1") or {}, h.get("l2") or {}, h.get("l3") or {}
    avail = l2.get("shares_available")
    av = "10M+" if l2.get("available_capped") else (C.sh(avail) if avail else "N/A")
    return (f"| {h['_session']} | {_d(h.get('price'))} | {_m(l1.get('delta_inst'))} | {_m(l1.get('delta_pro'))} | "
            f"{_m(l1.get('delta_retail'))} | {_m(l1.get('delta_total'))} | {_p(l2.get('short_pct_latest'))} | "
            f"{_p(l2.get('ctb_fee'), 2)} | {av} | {_d(l3.get('flip_zone'))} | {_d(l3.get('max_pain'))} |")


def _z(x):
    return None if x is None else (0.0 if abs(x) < 0.5 else x)


def _md_phase(a: Dict[str, Any]) -> List[str]:
    ph, l1, l3 = a["phase"], a["l1"], a["l3"]
    tgt = f"Target: {_d(ph.get('p3_target_lo'))}–{_d(ph.get('p3_target_hi'))} (2차 {_d(ph.get('p3_target_lo2'))}–{_d(ph.get('p3_target_hi2'))})"
    rg = ph.get("regime")
    if not rg:
        return [f"- **Phase 1 (Completed)**: {ph['p1_date']} — 구간 시작 이후 {C.pc(ph.get('p1_return_pct'))}",
                f"- **Phase 2 (Current)**: {ph['p2_date']} — L1 {C.ko(l1.get('scenario'))} · L3 {C.ko(l3.get('scenario'))}",
                f"- **Phase 3 (Target)**: {ph['p3_date']} — {C.kt(C.KO_DIR, ph.get('p3_direction'))} | {tgt}"]
    pv, cu, nx = rg["previous"], rg["current"], rg["next"]
    def seg(s):
        return (f"{s['state_ko']} · {s['days']}거래일 · 가격 {C.pc(s.get('ret_pct'))} · 순체결 일평균 {C.pc(_z(s.get('flow_per_day')), 0)} ADV"
                + (f" · 누적 근거 {s['confirm_n']} 일치" if s.get("confirm") is not None else " · 누적 근거 없음"))
    out = [f"- **Phase 1 (Completed)**: {pv['start']} ~ {pv['end']} — {seg(pv)}",
           f"- **Phase 2 (Current)**: {cu['start']} ~ (예상) {ph['p2_end']} — {seg(cu)} · {cu['days']}일째 (평균 {cu['median_days']}일)"
           + (" · ⚠ 누적 근거 불일치" if cu.get("contradicted") else "")
           + (f" · 최근 {cu['emerging']['days']}일 '{cu['emerging']['state_ko']}' 전환 조짐" if cu.get("emerging") else ""),
           f"- **Phase 3 (Target)**: {ph['p3_date']} — {nx['state_ko']} 예상 {nx['prob']:.0f}% (과거 전이 {nx['emp_hits']}/{nx['emp_n']}회) · "
           f"{C.kt(C.KO_DIR, ph.get('p3_direction'))} | {tgt}", "", "### 수급 국면 이력", "",
           "| 기간 | 국면 | 일수 | 가격 | 순체결/일 | 다크풀 | 공매도 | 근거 일치 |", "|---|---|---|---|---|---|---|---|"]
    for s in rg["segments"]:
        out.append(f"| {s['start']} ~ {s['end']} | {s['state_ko']} | {s['days']} | {C.pc(s.get('ret_pct'))} | {C.pc(_z(s.get('flow_per_day')), 0)} | "
                   f"{_p(s.get('dp_avg'))} | {_p(s.get('short_avg'))} | {s.get('confirm_n')} |")
    return out


def render_markdown(a: Dict[str, Any]) -> str:
    m, l1, l2, l3, l4, s, ph, q = (a["meta"], a["l1"], a["l2"], a["l3"], a["l4"], a["scenarios"], a["phase"], a["quality"])
    T = m["ticker"]
    si = l2.get("short_interest") or {}
    lines = [
        "---", f"ticker: {T}", f"date: {m['date']}", "schema: 3", f"quality: {q['grade']} ({q['score']})", "---", "",
        f"# Smart Money Analysis: {T}",
        f"**Analysis Date (US session)**: {m['date']}  ",
        f"**Current Price**: ${m['price']:.2f} ({C.pc(l4.get('change_pct'), 2)})  ",
        f"**Options Expiry**: {l3.get('expiry', 'N/A')} (DTE {l3.get('dte', 'N/A')}) · Monthly {l3.get('monthly_expiry', 'N/A')}  ",
        f"**Data Quality**: {q['grade']} ({q['score']}/100)  ",
        f"**Analyst**: Smart Money Analyzer v3.0", "",
        f"> **한줄평** {a.get('one_liner') or C.one_liner(a)}", ">", f"> {a.get('headline')}", "", "---", "",
        "## 수급 주체별 동향", "", "| 주체 | 동향 | 압력 | 근거 |", "|------|------|------|------|",
        *[f"| {r['actor']} | {r['action']} | {r['tag']} | {r['evidence']} |" for r in C.flow_board(a)], "",
        "## 다음 확인 포인트", "", *[f"- **{w['label']}**: {w['text']}" for w in C.watch_points(a)], "",
        "---", "", "## Key Metrics Summary", "",
        "| Metric | Value |", "|--------|-------|",
        f"| Institutional OBV Δ | {_m(l1.get('delta_inst'))} |",
        f"| Professional OBV Δ | {_m(l1.get('delta_pro'))} |",
        f"| Retail OBV Δ | {_m(l1.get('delta_retail'))} |",
        f"| Total OBV Δ (5d, CLV) | {_m(l1.get('delta_total'))} ({C.pc((l1.get('obv_strength') or 0) * 100)} ADV) |",
        f"| Divergence | {C.kt(C.KO_DIV, l1.get('divergence'))} |",
        f"| Dark Pool % | {_p(l1.get('dp_pct'))} (20d avg {_p(l1.get('dp_avg20'))}) |",
        f"| Institutional Absorption Ratio | {'N/A' if l1.get('iar') is None else round(l1['iar'], 2)} |",
        f"| VWAP Anchor (5d) | {_d(l1.get('vwap5'))} ({C.pc(l1.get('vwap_dist_pct'), 2)}) |",
        f"| ATS share (FINRA weekly) | {_p((l1.get('ats') or {}).get('ats_share_pct'))} |",
        f"| Short % (off-exchange) | {_p(l2.get('short_pct_latest'))} (z {(l2.get('anomaly_z') or 0):+.2f}) |",
        f"| Short Case | {C.KO_CASE.get(l2.get('case'), l2.get('case'))} |",
        f"| CTB Fee | {_p(l2.get('ctb_fee'), 2)} (Δ {C.pc(l2.get('ctb_delta_pct'))}) |",
        f"| Available Shares | {'10M+' if l2.get('available_capped') else C.sh(l2.get('shares_available'))} (Δ {C.pc(l2.get('avail_delta_pct'))}) |",
        f"| Short Interest / Float | {_p(si.get('pct_float'), 2)} · DTC {_r(si.get('days_to_cover'), 2) or 'N/A'} |",
        f"| Max Pain | {_d(l3.get('max_pain'))} ({C.pc(-(l3.get('max_pain_dist_pct') or 0), 2)}) |",
        f"| GEX Flip Zone | {_d(l3.get('flip_zone'))} ({l3.get('gamma_regime') or 'N/A'}) |",
        f"| Call / Put Wall | {_d(l3.get('call_wall'))} / {_d(l3.get('put_wall'))} |",
        f"| Net GEX (per 1%) | {C.money_big(l3.get('net_gex'))} |",
        f"| P/C OI · Vol | {l3.get('pc_oi') and round(l3['pc_oi'], 2)} · {l3.get('pc_vol') and round(l3['pc_vol'], 2)} |",
        f"| Expected Move (wk) | ±{_d(l3.get('expected_move'))} |",
        f"| Scenario [A] Bullish | {s['A_bullish']}% |", f"| Scenario [B] Neutral | {s['B_neutral']}% |",
        f"| Scenario [C] Bearish | {s['C_bearish']}% |",
        f"| Raw Score · Macro | {s['raw_score']:+.3f} · {s.get('macro')} |",
        f"| Patterns | {', '.join(C.KO_PAT.get(p, p) for p in a['patterns']) or 'none'} |", "",
        "---", "", "## Architect Phase Structure",
        *_md_phase(a), "",
        "---", "", "## 3-Line Summary",
    ]
    for i, t in enumerate(C.three_lines(a), 1):
        lines.append(f"{'①②③'[i-1]} {t}")
    lines += ["", "---", "", "## Core Conclusion"]
    for p in C.conclusions(a):
        lines += ["", "**" + p.replace("<u>", "").replace("</u>", "") + "**"]
    lines += ["", "---", "", "## Cumulative History", "",
              "| 날짜 | 종가 | 기관OBV | 프로OBV | 리테일OBV | 전체OBV | Short% | CTB | 가용잔고 | GEX플립 | MaxPain |",
              "|------|------|---------|---------|-----------|---------|--------|-----|---------|---------|---------|"]
    cur = {"_session": m["date"], "price": m["price"], "l1": l1, "l2": l2, "l3": l3}
    for h in (a.get("history") or []) + [cur]:
        lines.append(_hist_row(h))
    lines += ["", "> v3 방법론(2026-11-13~) 리포트만 누적합니다. 이전 v2 리포트는 OBV 정의·대차 출처·판정 규칙이 달라 "
              "이력과 분석에서 제외했으며, 원본은 reports/ 아카이브에 그대로 보존됩니다. "
              "과거 다크풀·공매도 비중은 수급 국면 분석에서 FINRA 원천 파일로 v3 공식에 맞춰 재계산해 사용합니다."]
    if m.get("warnings"):
        lines += ["", "## Data Warnings"] + [f"- {w}" for w in m["warnings"]]
    return "\n".join(lines) + "\n"
