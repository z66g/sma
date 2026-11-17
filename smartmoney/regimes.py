"""수급 국면(Regime) 엔진 — 누적 데이터로 Phase 1/2/3 을 '날짜가 아닌 흐름'으로 판정.

1) 국면 분류 (2년 일봉, 매일 동일 규칙)
   10거래일 창에서
     - 가격 z  = 10일 수익률 ÷ (60일 일간 변동성 × √10)
     - 수급 z  = 10일 CLV 순체결(÷ADV) ÷ 최근 120일 같은 값의 표준편차
   임계 ±0.5 로 5개 국면에 배정한다 (상호 배타):
     매집(ACCUMULATION)  수급 유입 · 가격 상승 아님      ← 가격과 수급의 강세 괴리
     상승 진행(MARKUP)    가격 상승 · 수급 유출 아님
     분산(DISTRIBUTION)  수급 유출 · 가격 하락 아님      ← 약세 괴리
     하락 진행(MARKDOWN)  가격 하락 · 수급 유입 아님
     관망(NEUTRAL)       그 외
   3거래일 미만의 짧은 구간은 이웃 구간에 흡수(노이즈 제거)하고 같은 국면은 병합한다.

2) 누적 리포트 근거 (일별 원장)
   과거 리포트 JSON(v2·v3) + 상태 파일에서 세션별 다크풀 비중·공매도 비중·대차 수수료·L1 판정·
   기관 체결 순매수(체결 단위)를 모아, 각 구간이 실제 데이터로 뒷받침되는지(일치/불일치) 표시한다.

3) Phase 매핑
   Phase 1 = 직전에 끝난 구간 · Phase 2 = 지금 진행 중인 구간(시작일은 데이터가 정한 날) ·
   Phase 3 = 다음 국면 예상. 다음 국면 확률은 이 종목의 과거 국면 전이 빈도(2년)와
   현재 시나리오 확률을 반반 섞고, 시작 시점은 현재 국면의 과거 평균 지속일수로 추정한다.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date
from statistics import median
from typing import Any, Dict, List, Optional

import numpy as np

from . import calendar as cal

STATES = ("ACCUMULATION", "MARKUP", "DISTRIBUTION", "MARKDOWN", "NEUTRAL")
KO_STATE = {"ACCUMULATION": "매집", "MARKUP": "상승 진행", "DISTRIBUTION": "분산", "MARKDOWN": "하락 진행",
            "NEUTRAL": "관망(횡보)"}
BULLISH_STATES = {"ACCUMULATION", "MARKUP"}
BEARISH_STATES = {"DISTRIBUTION", "MARKDOWN"}
# 전형적 순환(데이터가 부족할 때의 사전 분포): 매집 → 상승 → 분산 → 하락 → 매집
CYCLE_NEXT = {"ACCUMULATION": "MARKUP", "MARKUP": "DISTRIBUTION", "DISTRIBUTION": "MARKDOWN",
              "MARKDOWN": "ACCUMULATION", "NEUTRAL": None}
W = 10
MIN_RUN = 3
THR = 0.5


# ─── 1) 일별 국면 라벨 ───────────────────────────────────────────────────────
def _labels(ohlcv: List[Dict[str, Any]]) -> List[Optional[str]]:
    """일별 국면. 과거 구간은 중심 창(t−5 … t+4)으로 경계 지연을 없애고, 최근 5일은 후행 창을 쓴다."""
    n = len(ohlcv)
    c = np.array([b["close"] for b in ohlcv], float)
    h = np.array([b["high"] for b in ohlcv], float)
    lo = np.array([b["low"] for b in ohlcv], float)
    v = np.array([b["volume"] or 0 for b in ohlcv], float)
    rng = np.where(h > lo, h - lo, np.nan)
    clv = np.nan_to_num(((c - lo) - (h - c)) / rng) * v
    r = np.zeros(n)
    r[1:] = c[1:] / np.where(c[:-1] > 0, c[:-1], np.nan) - 1
    r = np.nan_to_num(r)

    def flow(a: int, b: int) -> float:          # [a, b] 구간 CLV 순체결 ÷ 직전 20일 ADV
        adv = v[max(0, a - 20):a].mean() if a > 0 else v[:20].mean()
        return clv[a:b + 1].sum() / adv if adv > 0 else 0.0
    trail = np.full(n, np.nan)                  # 수급 z 정규화용 분포 (후행 10일 창)
    for t in range(W + 20, n):
        trail[t] = flow(t - W + 1, t)
    half = W // 2
    out: List[Optional[str]] = [None] * n
    for t in range(W + 40, n):
        if t + half - 1 < n:
            a, b = t - half, t + half - 1
        else:                                   # 최근 구간: 끝까지 붙인 창(5~10일)
            b = n - 1
            a = min(t - half, b - 4)
        L = b - a + 1
        sd = r[max(1, a - 60):a].std()
        prev = trail[max(0, a - 120):a]
        prev = prev[~np.isnan(prev)]
        if sd <= 0 or len(prev) < 30:
            continue
        fsd = prev.std()
        zp = (c[b] / c[a - 1] - 1) / (sd * math.sqrt(L))
        zf = flow(a, b) / (fsd * math.sqrt(L / W)) if fsd > 0 else 0.0
        up, dn, fin, fout = zp > THR, zp < -THR, zf > THR, zf < -THR
        out[t] = ("ACCUMULATION" if fin and not up else "MARKUP" if up and not fout else
                  "DISTRIBUTION" if fout and not dn else "MARKDOWN" if dn and not fin else "NEUTRAL")
    return out


def _runs(labels: List[Optional[str]]) -> List[List[int]]:
    """[state_idx, start, end] (end 포함) 구간 목록."""
    runs: List[List[Any]] = []
    for i, s in enumerate(labels):
        if s is None:
            continue
        if runs and runs[-1][0] == s and runs[-1][2] == i - 1:
            runs[-1][2] = i
        else:
            runs.append([s, i, i])
    # 짧은 구간 흡수 (마지막=현재 구간은 보존)
    changed = True
    while changed and len(runs) > 2:
        changed = False
        for k in range(len(runs) - 1):
            ln = runs[k][2] - runs[k][1] + 1
            if ln >= MIN_RUN:
                continue
            if k == 0:
                runs[1][1] = runs[0][1]
            else:
                runs[k - 1][2] = runs[k][2]
            runs.pop(k)
            changed = True
            break
        # 같은 국면 병합
        merged: List[List[Any]] = []
        for rr in runs:
            if merged and merged[-1][0] == rr[0]:
                merged[-1][2] = rr[2]
            else:
                merged.append(rr)
        runs = merged
    return runs


# ─── 2) 누적 원장 ────────────────────────────────────────────────────────────
def build_ledger(history: List[Dict[str, Any]], l1: Dict, l2: Dict, session: str,
                 flow_rows: List[Dict[str, Any]], borrow_rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """세션 → {dp, short, ctb, l1, inst} — 과거 리포트(v2·v3)와 상태 파일을 합친 일별 원장."""
    led: Dict[str, Dict[str, Any]] = defaultdict(dict)

    def put(d: Optional[str], k: str, val: Any, force: bool = False) -> None:
        if d and val is not None and (force or k not in led[d]):
            led[d][k] = val
    for h in history:                       # 오래된 것 → 최신 순
        d = h.get("_session") or h.get("date")
        hl1, hl2 = h.get("l1") or {}, h.get("l2") or {}
        for r in hl1.get("dp_series") or []:
            put(r.get("date"), "dp", r.get("dp_pct"), force=True)
        for r in hl2.get("short_series") or []:
            put(r.get("date"), "short", r.get("short_pct"), force=True)
        put(d, "dp", hl1.get("dp_pct"), force=True)
        put(d, "short", hl2.get("short_pct_latest"), force=True)
        put(d, "ctb", hl2.get("ctb_fee"), force=True)
        put(d, "l1", hl1.get("scenario"), force=True)
    for r in l1.get("dp_series") or []:
        put(r.get("date"), "dp", r.get("dp_pct"), force=True)
    for r in l2.get("short_series") or []:
        put(r.get("date"), "short", r.get("short_pct"), force=True)
    put(session, "l1", l1.get("scenario"), force=True)
    put(session, "ctb", l2.get("ctb_fee"), force=True)
    for r in borrow_rows or []:
        put(r.get("date"), "ctb", r.get("fee"), force=True)
    for r in flow_rows or []:
        put(r.get("date"), "inst", r.get("inst"), force=True)
    return dict(led)


def _seg_stats(seg: Dict[str, Any], ohlcv: List[Dict], led: Dict[str, Dict], base: Dict[str, float]) -> None:
    i0, i1 = seg["i0"], seg["i1"]
    days = [ohlcv[i]["date"] for i in range(i0, i1 + 1)]
    pre = ohlcv[i0 - 1]["close"] if i0 > 0 else ohlcv[i0]["close"]
    seg["ret_pct"] = (ohlcv[i1]["close"] / pre - 1) * 100 if pre else None
    v = [ohlcv[i]["volume"] or 0 for i in range(max(0, i0 - 20), i0)] or [1]
    adv = float(np.mean(v)) or 1.0
    clv = 0.0
    for i in range(i0, i1 + 1):
        b = ohlcv[i]
        if b["high"] > b["low"]:
            clv += ((b["close"] - b["low"]) - (b["high"] - b["close"])) / (b["high"] - b["low"]) * (b["volume"] or 0)
    seg["flow_adv_pct"] = clv / adv * 100
    seg["flow_per_day"] = seg["flow_adv_pct"] / seg["days"]
    rows = [led.get(d) or {} for d in days]
    def avg(k):
        xs = [r[k] for r in rows if isinstance(r.get(k), (int, float))]
        return (float(np.mean(xs)), len(xs)) if xs else (None, 0)
    seg["dp_avg"], n_dp = avg("dp")
    seg["short_avg"], n_sh = avg("short")
    ctbs = [r["ctb"] for r in rows if isinstance(r.get("ctb"), (int, float))]
    seg["ctb_chg"] = (ctbs[-1] - ctbs[0]) if len(ctbs) >= 2 else None
    inst = [r["inst"] for r in rows if isinstance(r.get("inst"), (int, float))]
    seg["inst_sum"] = float(sum(inst)) if inst else None
    seg["inst_days"] = len(inst)
    l1s = Counter(r["l1"] for r in rows if r.get("l1") in ("ACCUMULATION", "DISTRIBUTION", "NEUTRAL", "AMBIGUOUS"))
    seg["l1_major"], seg["l1_major_n"] = (l1s.most_common(1)[0] if l1s else (None, 0))
    seg["report_days"] = sum(1 for r in rows if r.get("l1"))
    # 누적 근거 일치 검사 — 구간 국면과 같은 방향을 가리키는 독립 증거의 비율
    st = seg["state"]
    want = 1 if st in BULLISH_STATES else -1 if st in BEARISH_STATES else 0
    ev: List[str] = []
    agree = total = 0
    if want and seg["l1_major"] in ("ACCUMULATION", "DISTRIBUTION") and seg["report_days"]:
        total += 1
        ok = (seg["l1_major"] == "ACCUMULATION") == (want > 0)
        agree += ok
        ev.append(f"리포트 L1 판정 {'매집' if seg['l1_major'] == 'ACCUMULATION' else '분산'} {seg['l1_major_n']}/{seg['report_days']}일"
                  + (" ✓" if ok else " ✗"))
    if want and seg["inst_sum"] is not None and seg["inst_days"] >= 2:
        total += 1
        ok = (seg["inst_sum"] > 0) == (want > 0)
        agree += ok
        ev.append(f"기관 체결 {'+' if seg['inst_sum'] >= 0 else ''}{seg['inst_sum'] / 1e6:.2f}M주({seg['inst_days']}일)" + (" ✓" if ok else " ✗"))
    if want and seg["dp_avg"] is not None and base.get("dp") and n_dp >= 2:
        total += 1
        ok = seg["dp_avg"] > base["dp"]          # 장외 비중이 평소보다 높음 = 기관이 움직인 구간 (방향 중립 증거)
        agree += ok
        ev.append(f"다크풀 평균 {seg['dp_avg']:.1f}% (전체 {base['dp']:.1f}%)" + (" ✓" if ok else " ✗"))
    if want and seg["short_avg"] is not None and base.get("short") and n_sh >= 2:
        total += 1
        ok = (seg["short_avg"] < base["short"]) == (want > 0)
        agree += ok
        ev.append(f"공매도 평균 {seg['short_avg']:.1f}% (전체 {base['short']:.1f}%)" + (" ✓" if ok else " ✗"))
    seg["evidence"] = ev
    seg["confirm"] = (agree / total) if total else None
    seg["confirm_n"] = f"{agree}/{total}" if total else "근거 없음"


# ─── 3) Phase 산출 ───────────────────────────────────────────────────────────
def analyze(session: date, ohlcv: List[Dict[str, Any]], ledger: Dict[str, Dict[str, Any]],
            scen: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if len(ohlcv) < 80:
        return None
    labels = _labels(ohlcv)
    runs = _runs(labels)
    if len(runs) < 2:
        return None
    dps = [r["dp"] for r in ledger.values() if isinstance(r.get("dp"), (int, float))]
    shs = [r["short"] for r in ledger.values() if isinstance(r.get("short"), (int, float))]
    base = {"dp": float(np.mean(dps)) if dps else None, "short": float(np.mean(shs)) if shs else None}
    segs: List[Dict[str, Any]] = []
    for st, i0, i1 in runs:
        seg = {"state": st, "state_ko": KO_STATE[st], "i0": i0, "i1": i1, "start": ohlcv[i0]["date"],
               "end": ohlcv[i1]["date"], "days": i1 - i0 + 1}
        segs.append(seg)
    emerging = None
    if segs[-1]["days"] < MIN_RUN and len(segs) >= 3:      # 1~2일짜리 새 신호는 '전환 조짐'으로만 표시
        emerging = segs.pop()
        _seg_stats(emerging, ohlcv, ledger, base)
        last = segs[-1]
        last["i1"], last["end"] = emerging["i1"], emerging["end"]
        last["days"] = last["i1"] - last["i0"] + 1
    for seg in segs[-8:]:
        _seg_stats(seg, ohlcv, ledger, base)
    if emerging and emerging["state"] != "NEUTRAL":     # 조짐 자체의 가격·순체결이 라벨 방향과 맞을 때만 표시
        sgn = 1 if emerging["state"] in BULLISH_STATES else -1
        if not ((emerging.get("ret_pct") or 0) * sgn > 0 or (emerging.get("flow_per_day") or 0) * sgn > 0):
            emerging = None
    cur, prev = segs[-1], segs[-2]
    # Phase 1 = 직전의 '방향이 있던' 완료 구간 (짧은 관망 구간은 건너뜀, 최대 3구간 전까지)
    for sg in reversed(segs[-4:-1]):
        if sg["state"] != "NEUTRAL":
            prev = sg
            break
    if prev not in segs[-8:]:
        _seg_stats(prev, ohlcv, ledger, base)
    # 과거 전이·지속일수 (현재 구간 제외)
    trans: Dict[str, Counter] = defaultdict(Counter)
    durs: Dict[str, List[int]] = defaultdict(list)
    for a_, b_ in zip(segs[:-1], segs[1:]):
        trans[a_["state"]][b_["state"]] += 1
    for sg in segs[:-1]:
        durs[sg["state"]].append(sg["days"])
    st = cur["state"]
    emp = trans.get(st, Counter())
    n_emp = sum(emp.values())
    prior = {s: (1.0 if s == CYCLE_NEXT.get(st) else 0.25) for s in STATES if s != st}
    tot = sum(emp.values()) + sum(prior.values())
    p_emp = {s: (emp.get(s, 0) + prior[s]) / tot for s in prior}
    A, B, Cb = scen["A_bullish"] / 100, scen["B_neutral"] / 100, scen["C_bearish"] / 100
    scen_map = {"MARKUP": A * 0.7, "ACCUMULATION": A * 0.3 + B * 0.25, "NEUTRAL": B * 0.5,
                "DISTRIBUTION": Cb * 0.3 + B * 0.25, "MARKDOWN": Cb * 0.7}
    sm = {s: scen_map[s] for s in prior}
    sms = sum(sm.values()) or 1
    p_next = {s: 0.5 * p_emp[s] + 0.5 * sm[s] / sms for s in prior}
    nxt = max(p_next, key=p_next.get)
    emp_top = emp.most_common(1)[0] if n_emp else None
    med_cur = int(median(durs[st])) if len(durs[st]) >= 2 else 10
    med_nxt = int(median(durs[nxt])) if len(durs[nxt]) >= 2 else 10
    elapsed = cur["days"]
    remaining = max(1, med_cur - elapsed)
    p2_end = cal.next_trading_day(session, remaining) if remaining > 1 else cal.next_trading_day(session)
    p3_start = cal.next_trading_day(p2_end)
    p3_end = cal.next_trading_day(p3_start, max(5, min(20, med_nxt)) - 1)
    clean = [{k: v for k, v in s.items() if k not in ("i0", "i1")} for s in segs[-8:]]
    return {
        "segments": clean,
        "timeline": [{"start": s["start"], "end": s["end"], "state": s["state"]} for s in segs if s["i1"] >= len(ohlcv) - 160],
        "current": {**{k: v for k, v in cur.items() if k not in ("i0", "i1")}, "median_days": med_cur,
                    "overdue": elapsed >= med_cur, "tentative": elapsed < MIN_RUN, "samples": len(durs[st]),
                    "contradicted": cur.get("confirm") is not None and cur["confirm"] < 0.5,
                    "emerging": ({"state": emerging["state"], "state_ko": emerging["state_ko"], "days": emerging["days"],
                                  "start": emerging["start"], "ret_pct": emerging.get("ret_pct")} if emerging else None)},
        "previous": {k: v for k, v in prev.items() if k not in ("i0", "i1")},
        "next": {"state": nxt, "state_ko": KO_STATE[nxt], "prob": round(p_next[nxt] * 100, 1),
                 "probs": {s: round(p * 100, 1) for s, p in sorted(p_next.items(), key=lambda x: -x[1])},
                 "emp_n": n_emp, "emp_top": (emp_top[0], emp_top[1]) if emp_top else None,
                 "emp_hits": emp.get(nxt, 0), "median_days": med_nxt},
        "p2_end": p2_end, "p3_start": p3_start, "p3_end": p3_end,
        "base": base, "n_segments": len(segs),
    }
