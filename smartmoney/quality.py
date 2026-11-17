"""데이터 품질(커버리지) 평가 — 결측은 숨기지 않고 점수·등급·누락목록으로 노출."""
from __future__ import annotations

from typing import Any, Dict, List

# (필드 경로, 가중치, 사람이 읽는 이름)
REQUIRED = {
    "l1": [("dp_pct", 3, "다크풀 비중"), ("delta_total", 3, "Net OBV"), ("dp_avg20", 1, "다크풀 20일 평균"),
           ("vwap5", 1, "VWAP 앵커"), ("ats", 1, "FINRA ATS 주간"), ("iar", 1, "기관 흡수율(IAR)")],
    "l2": [("short_pct_latest", 3, "Short%"), ("ctb_fee", 3, "CTB 수수료"), ("shares_available", 1, "가용잔고"),
           ("ctb_delta_pct", 1, "CTB Δ%"), ("short_interest", 2, "공매도 잔고(SI)")],
    "l3": [("max_pain", 3, "Max Pain"), ("net_gex", 2, "Net GEX"), ("flip_zone|flip_status", 2, "GEX 플립"),
           ("pc_oi", 1, "P/C OI"), ("skew_25d", 1, "25Δ 스큐"), ("expected_move", 1, "예상 변동폭"),
           ("call_wall", 1, "콜월")],
    "l4": [("session_bar_ok", 2, "세션 일봉(당일)"), ("sma200", 1, "SMA200"), ("bb_width_pct", 1, "볼린저 폭"), ("rsi14", 1, "RSI"),
           ("immediate_support", 1, "지지선")],
}
LAYER_W = {"l1": 0.30, "l2": 0.25, "l3": 0.30, "l4": 0.10, "macro": 0.03, "news": 0.02}
INDEX_LAYERS = {"l3", "l4", "macro"}


def assess(layers: Dict[str, Dict], macro: Dict, news_ok: bool, sources: Dict[str, List[str]],
           is_index: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {"layers": {}}
    total_w = got_w = 0.0
    for key, fields in REQUIRED.items():
        if is_index and key not in INDEX_LAYERS:
            continue
        L = layers.get(key) or {}
        def has(f: str) -> bool:      # "a|b" = 둘 중 하나라도 유효하면 충족 (예: 플립 부재도 판정 결과)
            return any(L.get(k) not in (None, [], {}) for k in f.split("|"))
        missing = [name for f, w, name in fields if not has(f)]
        fw = sum(w for _, w, _ in fields)
        gw = sum(w for f, w, _ in fields if has(f))
        frac = gw / fw if fw else 0
        out["layers"][key] = {"status": "OK" if frac >= 0.85 else "PARTIAL" if frac > 0 else "MISSING",
                              "coverage": round(frac * 100), "missing": missing, "sources": sources.get(key, [])}
        total_w += LAYER_W[key]
        got_w += LAYER_W[key] * frac
    mfrac = (macro.get("available", 0) / max(len(macro.get("rows") or []), 1)) if macro else 0
    out["layers"]["macro"] = {"status": "OK" if mfrac >= 0.7 else "PARTIAL" if mfrac > 0 else "MISSING",
                              "coverage": round(mfrac * 100), "missing": [], "sources": sources.get("macro", [])}
    total_w += LAYER_W["macro"]; got_w += LAYER_W["macro"] * mfrac
    if not is_index:
        out["layers"]["news"] = {"status": "OK" if news_ok else "PARTIAL", "coverage": 100 if news_ok else 50,
                                 "missing": [], "sources": sources.get("news", [])}
        total_w += LAYER_W["news"]; got_w += LAYER_W["news"] * (1 if news_ok else 0.5)
    score = round(got_w / total_w * 100) if total_w else 0
    out["score"] = score
    out["grade"] = "A" if score >= 90 else "B" if score >= 78 else "C" if score >= 62 else "D" if score >= 45 else "F"
    return out
