"""3-Layer Forensic HTML 리포트 (독립 실행형 · Chart.js 4.4 + annotation 3.0 · 라이트 테마 HEX 고정)."""
from __future__ import annotations

import html
import json
from typing import Any, Dict, List, Optional, Sequence

from . import commentary as C

FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
BULL, BEAR, WARN, INFO = "#1A7F5A", "#CF222B", "#9A6700", "#0969DA"
TXT, MUTED, CARD, PANEL, BORDER = "#1F2328", "#656D76", "#F6F8FA", "#EAEEF2", "#D0D7DE"
G_BG, R_BG, A_BG, B_BG = "#DAFBE1", "#FFEBE9", "#FFF8C5", "#DDF4FF"
SIG_KIND = {"BULLISH": "pos", "BEARISH": "neg", "NEUTRAL": "warn"}
KIND = {"pos": (G_BG, BULL), "neg": (R_BG, BEAR), "warn": (A_BG, WARN), "info": (B_BG, INFO), "muted": (PANEL, MUTED)}


def e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def pill(text: str, kind: str = "info", title: str = "") -> str:
    bg, fg = KIND.get(kind, KIND["info"])
    t = f' title="{e(title)}"' if title else ""
    return f'<span class="pill" style="background:{bg};color:{fg};"{t}>{e(text)}</span>'


def sec(n: int, name: str, badges: Sequence[str] = ()) -> str:
    return (f'<div class="sec" id="s{n}"><span class="sec-t">▶ SECTION {n} [{e(name)}]</span>'
            f'<span class="sec-b">{"".join(badges)}</span></div>')


def table(headers: List[str], rows: List[List[str]], hl: Optional[Dict[int, str]] = None, cls: str = "") -> str:
    hl = hl or {}
    th = "".join(f"<th>{e(h)}</th>" for h in headers)
    body = []
    for i, r in enumerate(rows):
        st = f' style="background:{hl[i]};"' if i in hl else ""
        body.append(f"<tr{st}>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    return f'<div class="tw"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def kpi(label: str, value: str, sub: str = "", color: str = TXT) -> str:
    return (f'<div class="kpi"><div class="kl">{e(label)}</div><div class="kv" style="color:{color};">{value}</div>'
            f'<div class="ks">{sub}</div></div>')


def colored(v: Optional[float], text: str, invert: bool = False) -> str:
    if v is None:
        return f'<span style="color:{MUTED};">{text}</span>'
    good = v > 0 if not invert else v < 0
    c = BULL if good else BEAR if v != 0 else TXT
    return f'<span style="color:{c};font-weight:600;">{text}</span>'


def alert(body: str, kind: str = "warn", title: str = "") -> str:
    bg, fg = KIND[kind]
    t = f'<b style="color:{fg};">{e(title)}</b><br>' if title else ""
    return f'<div class="alert" style="background:{bg};border-left:3px solid {fg};">{t}{body}</div>'


def interp(paras: List[str], xv: str) -> str:
    lis = "".join(f"<li>{p}</li>" for p in paras)
    return (f'<div class="interp"><div class="ih">해석</div><ul>{lis}</ul>'
            f'<div class="xv"><b>교차검증</b> · {xv}</div></div>')


def sig_kind(sig: Optional[str]) -> str:
    return SIG_KIND.get(sig or "", "muted")


def scen_label(L: Dict) -> str:
    """중립형 시나리오인데 점수가 한쪽으로 기울면 그 기울기를 함께 표기."""
    t = C.ko(L.get("scenario"))
    if L.get("scenario") in ("RANGE_BOUND", "NEUTRAL", "AMBIGUOUS", "MM_HEDGE") and L.get("signal") in ("BULLISH", "BEARISH"):
        t += " · " + ("상방 우위" if L["signal"] == "BULLISH" else "하방 우위")
    return t


def _layer_tile(code: str, name: str, L: Dict, weight: float) -> str:
    s = L.get("score") or 0.0
    conf = L.get("confidence") or 0.0
    color = BULL if s > 0.2 else BEAR if s < -0.2 else WARN
    left = 50 + min(s, 0) * 50
    width = abs(s) * 50
    return (f'<div class="tile"><div class="tl">{code} · {e(name)} <span style="color:{MUTED};">w{weight:.2f}</span></div>'
            f'<div class="tv" style="color:{color};">{e(scen_label(L))}</div>'
            f'<div class="bar"><div class="mid"></div><div style="position:absolute;left:{left:.1f}%;width:{width:.1f}%;'
            f'top:0;bottom:0;background:{color};opacity:.85;border-radius:3px;"></div></div>'
            f'<div class="ts">score {s:+.2f} · conf {conf:.0%}</div>'
            + (f'<div class="ts">기관 {C.ssh(L.get("delta_inst"))} · 프로 {C.ssh(L.get("delta_pro"))} · 리테일 '
               f'{C.ssh(L.get("delta_retail"))} · IAR {_num(L.get("iar"))}</div>' if L.get("delta_inst") is not None else "")
            + '</div>')


def _ladder(levels: List[Dict], price: float) -> str:
    keys = {"Put Wall": BEAR, "GEX Flip": WARN, "Max Pain": WARN, "Call Wall": BULL, "Current Price": INFO,
            "Expected Move ↑": MUTED, "Expected Move ↓": MUTED}
    pts = sorted([lv for lv in levels if lv["type"] in keys], key=lambda x: x["price"])
    if len(pts) < 2:
        return ""
    lo, hi = pts[0]["price"], pts[-1]["price"]
    span = (hi - lo) or 1
    # 라벨 충돌 회피: 4개 레인(위2·아래2)에 탐욕적 배치 (같은 레인 내 최소 간격 11%)
    lanes_last = [-1e9] * 4
    tops = [0, 26, 62, 88]
    marks = []
    for p in pts:
        x = (p["price"] - lo) / span * 100
        order = [0, 2, 1, 3]
        lane = next((ln for ln in order if x - lanes_last[ln] >= 11), min(range(4), key=lambda ln: lanes_last[ln]))
        lanes_last[lane] = x
        c = keys[p["type"]]
        big = p["type"] == "Current Price"
        marks.append(
            f'<div class="lm" style="left:{x:.2f}%;"><div class="lt" style="top:{tops[lane]}px;color:{c};">'
            f'{e(p["type"].replace("Current Price", "현재가"))} <b>${p["price"]:,.2f}</b></div>'
            f'<div class="ld" style="background:{c};{"width:12px;height:12px;margin-left:-6px;top:52px;" if big else ""}"></div></div>')
    items = "".join(f'<span style="color:{keys[p["type"]]};"><b>{e(p["type"].replace("Current Price", "현재가"))}</b> '
                    f'${p["price"]:,.2f}</span>' for p in reversed(pts))
    return (f'<div class="ladder"><div class="lline"></div>{"".join(marks)}</div>'
            f'<div class="llist">{items}</div>')


def _l1_kpis(l1: Dict, ats: Dict) -> str:
    def sc(v):
        return BULL if (v or 0) > 0 else BEAR if (v or 0) < 0 else MUTED
    adv = l1.get("avg_vol20") or 0
    def rel(v):
        return f"ADV 대비 {C.pc(v / adv * 100)}" if (v is not None and adv) else "체결 단위 데이터 필요"
    fl = l1.get("flow")
    share = (fl or {}).get("cohort_vol_share") or {}
    if fl:
        row1 = (kpi("기관 OBV (5d)", C.ssh(l1.get("delta_inst")), f"{rel(l1.get('delta_inst'))} · 거래량 {_num(share.get('inst'), 1, suffix='%')}", sc(l1.get("delta_inst")))
                + kpi("프로 OBV (5d)", C.ssh(l1.get("delta_pro")), f"{rel(l1.get('delta_pro'))} · 거래량 {_num(share.get('pro'), 1, suffix='%')}", sc(l1.get("delta_pro")))
                + kpi("리테일 OBV (5d)", C.ssh(l1.get("delta_retail")), f"{rel(l1.get('delta_retail'))} · 거래량 {_num(share.get('ret'), 1, suffix='%')}", sc(l1.get("delta_retail")))
                + kpi("Net OBV (합계)", C.ssh(l1.get("delta_total")), rel(l1.get("delta_total")), sc(l1.get("delta_total")))
                + kpi("기관 흡수율 IAR", _num(l1.get("iar")), e(fl.get("iar_class") or ""))
                + kpi("다크풀 VWAP", _num(l1.get("dp_vwap"), 2, "$"), f"종가 대비 {C.pc(l1.get('dp_vwap_dist_pct'), 2)}"))
    else:
        row1 = (kpi("Net OBV (5d)", C.ssh(l1.get("delta_total")), rel(l1.get("delta_total")), sc(l1.get("delta_total")))
                + kpi("기관 OBV", "N/A", "체결 단위 데이터 필요", MUTED) + kpi("프로 OBV", "N/A", "체결 단위 데이터 필요", MUTED)
                + kpi("리테일 OBV", "N/A", "체결 단위 데이터 필요", MUTED) + kpi("기관 흡수율 IAR", "N/A", "체결 단위 데이터 필요", MUTED)
                + kpi("다크풀 VWAP", "N/A", "체결 단위 데이터 필요", MUTED))
    row2 = (kpi("다크풀 비중", _num(l1.get("dp_pct"), 1, suffix="%"), f"20일 평균 {_num(l1.get('dp_avg20'), 1, suffix='%')} · {_num(l1.get('dp_ratio'))}x")
            + kpi("VWAP 앵커 (5d)", _num(l1.get("vwap5"), 2, "$"), f"종가 {C.pc(l1.get('vwap_dist_pct'), 2)}")
            + kpi("20일 누적 A/D", C.ssh(l1.get("acc20")), f"ADV {_num(l1.get('acc20_strength'))}x")
            + kpi("블록 체결", (f"{fl.get('blocks'):,}" if fl and fl.get("blocks") is not None else "N/A"),
                  f"장외 블록 {fl.get('dark_blocks'):,}" if fl and fl.get("dark_blocks") is not None else "체결 단위 데이터 필요")
            + kpi("ATS 주간 비중", _num(ats.get("ats_share_pct"), 1, suffix="%"), f"{e(ats.get('week_start') or '')}주 · 평균 {_num(ats.get('ats_trades_avg_size'), 0)}주/건")
            + kpi("리테일 교차검증", _num((fl or {}).get("bjzz_imbalance")), "BJZZ 서브페니 매수−매도 불균형" if fl else "체결 단위 데이터 필요"))
    return f'<div class="kpis">{row1}</div><div class="kpis">{row2}</div>'


def _flow_session_card(l1: Dict) -> str:
    fl = l1.get("flow")
    if not fl or not fl.get("sessions"):
        return ""
    rows = []
    for x in fl["sessions"]:
        fg = x.get("flag") or ""
        rows.append([e(x["session"]), C.sh(x.get("volume")), C.sh(x.get("dark")), _num(x.get("dark_pct"), 1, suffix="%"),
                     C.sh(x.get("avg20_dark")), _num(x.get("ratio"), 2, suffix="x"),
                     pill(fg, {"EXTREME": "neg", "HIGH": "warn", "MODERATE": "info"}[fg]) if fg else "—"])
    return (f'<div class="card"><div class="ct">다크풀 세션별 이상 탐지 — {e(fl["days"][-1])} (체결 단위 · 프리/정규/애프터, 누적 평균 대비)</div>'
            + table(["세션", "총 거래량", "장외(다크풀)", "장외 %", "장외 평균", "배수", "플래그"], rows)
            + f'<div class="muted" style="margin-top:6px;">블록 {fl.get("blocks") or 0}건 (장외 블록 {fl.get("dark_blocks") or 0}건) · '
              f'체결 {C.sh(fl.get("trades"))}건 · 거래량 비중 기관 {_num(fl["cohort_vol_share"]["inst"], 1, suffix="%")} / '
              f'프로 {_num(fl["cohort_vol_share"]["pro"], 1, suffix="%")} / 리테일 {_num(fl["cohort_vol_share"]["ret"], 1, suffix="%")}</div></div>')


def _num(x: Optional[float], d: int = 2, prefix: str = "", suffix: str = "") -> str:
    return "N/A" if x is None else f"{prefix}{x:,.{d}f}{suffix}"


# ─── 본문 ────────────────────────────────────────────────────────────────────
def render(a: Dict[str, Any], md_text: str = "") -> str:
    m, l1, l2, l3, l4 = a["meta"], a["l1"], a["l2"], a["l3"], a["l4"]
    s, ph, q, mac = a["scenarios"], a["phase"], a["quality"], a["macro"]
    price = m["price"]
    T = m["ticker"]
    is_idx = m.get("is_index")
    chg = l4.get("change_pct")
    qk = {"A": "pos", "B": "pos", "C": "warn", "D": "neg", "F": "neg"}[q["grade"]]

    # ── 헤더 & 요약 ──
    header = f"""
<div class="top"><a href="../tickers/{e(T)}/">← {e(T)} 종목 페이지</a><a href="../">대시보드</a></div>
<div class="hdr">
  <div><h1>{e(T)} <span class="nm">{e(m.get('name') or ('Index Options' if is_idx else ''))}</span></h1>
  <div class="sub">세션 <b>{e(m['date'])}</b> (미 정규장 마감 기준{' · 조기폐장' if m.get('early_close') else ''}) · 생성 {e(m['generated_at'])} · 주 만기 {e(l3.get('expiry') or 'N/A')}</div></div>
  <div class="px"><div class="pv">${price:,.2f}</div><div style="color:{BULL if (chg or 0) >= 0 else BEAR};font-weight:600;">{C.pc(chg, 2)}</div>
  {pill(f"데이터 품질 {q['grade']} · {q['score']}", qk, "레이어별 커버리지 가중 점수")}</div>
</div>"""
    prob_mini = (f'<div class="pm"><div style="width:{s["A_bullish"]}%;background:{BULL};">A {s["A_bullish"]:.0f}%</div>'
                 f'<div style="width:{s["B_neutral"]}%;background:{WARN};">B {s["B_neutral"]:.0f}%</div>'
                 f'<div style="width:{s["C_bearish"]}%;background:{BEAR};">C {s["C_bearish"]:.0f}%</div></div>')
    tiles = "".join(_layer_tile(k, n_, L, w) for k, n_, L, w in (
        ("L1", "Dark Pool", l1, 0.35), ("L2", "Short/CTB", l2, 0.20), ("L3", "Options", l3, 0.30), ("L4", "Chart", l4, 0.15)))
    summary = f"""
<div class="exec">
  <div class="eh">EXECUTIVE SUMMARY</div>
  <div class="hl">{e(a.get('headline'))}</div>
  {prob_mini}
  <div class="tiles">{tiles}</div>
  {_ladder(a['levels'], price)}
</div>"""

    # ── 품질 패널 ──
    qrows = []
    for k, L in q["layers"].items():
        kind = {"OK": "pos", "PARTIAL": "warn", "MISSING": "neg"}[L["status"]]
        qrows.append([f"<b>{k.upper()}</b>", pill(L["status"], kind), f'{L["coverage"]}%',
                      e(", ".join(L["missing"]) or "—"), e(" · ".join(L["sources"]) or "—")])
    warn_li = "".join(f"<li>{e(w)}</li>" for w in m.get("warnings") or [])
    quality = f"""
<details class="dq" {'open' if q['grade'] in ('D', 'F') else ''}><summary>데이터 품질 · 출처 · 수집 경고 ({len(m.get('warnings') or [])}건) — 등급 {q['grade']} ({q['score']}/100)</summary>
{table(["Layer", "상태", "커버리지", "누락 필드", "출처"], qrows)}
{f'<ul class="wl">{warn_li}</ul>' if warn_li else '<div class="muted" style="margin-top:6px;">수집 경고 없음</div>'}
</details>"""

    # ── SECTION 0 ──
    ev_rows = [[f'{e(x["date"])} <span class="muted">(D-{x["days_until"]})</span>', e(x["event"]),
                pill(x["significance"], {"HIGH": "neg", "MED": "warn", "LOW": "info"}[x["significance"]])]
               for x in a["events"]] or [["—", "향후 30일 예정 이벤트 없음", "—"]]
    nw_rows = []
    for x in a["news"][:5]:
        t = e(x["title"][:110])
        link = x.get("link") or ""
        if link.startswith("http"):
            t = f'<a href="{e(link)}" target="_blank" rel="noopener">{t}</a>'
        nw_rows.append([e(x["date"]), t, e(x.get("publisher") or "—")])
    nw_rows = nw_rows or [["—", "최근 7일 뉴스 없음", "—"]]
    fl = a["filings"]
    key_f = [f for f in fl if f["flag"] in ("INSIDER", "MATERIAL", "DILUTION", "OWNERSHIP")] or fl
    fi_rows = []
    for f in key_f[:3]:
        extra = ""
        f4 = f.get("form4")
        if f4:
            bits = []
            if f4.get("buy_usd"):
                bits.append(f'<span style="color:{BULL};">매수 ${f4["buy_usd"]:,.0f}</span>')
            if f4.get("sell_usd"):
                bits.append(f'<span style="color:{BEAR};">매도 ${f4["sell_usd"]:,.0f}</span>')
            extra = f' — {e(f4.get("owner"))}{(" (" + e(f4["title"]) + ")") if f4.get("title") else ""} ' + " / ".join(bits)
        items = f' <span class="muted">Item {e(f["items"])}</span>' if f.get("items") else ""
        fi_rows.append([e(f["date"]), f'<a href="{e(f["link"])}" target="_blank" rel="noopener">{e(f["form"])}</a>',
                        e(f["label"]) + items + extra,
                        pill(f["flag"], {"INSIDER": "warn", "DILUTION": "neg", "MATERIAL": "info", "OWNERSHIP": "info"}.get(f["flag"], "muted"))])
    f4s = [f["form4"] for f in fl if f.get("form4")]
    ins_buy = sum(x.get("buy_usd") or 0 for x in f4s)
    ins_sell = sum(x.get("sell_usd") or 0 for x in f4s)
    fi_note = (f'<div class="muted" style="margin-top:6px;">30일 공시 {len(fl)}건 · Form 4 {len(f4s)}건 분석: 내부자 매수 '
               f'<b style="color:{BULL};">${ins_buy:,.0f}</b> / 매도 <b style="color:{BEAR};">${ins_sell:,.0f}</b></div>') if fl else ""
    fi_rows = fi_rows or [["—", "—", "최근 30일 주요 공시 없음", "—"]]
    mrows = []
    for r in mac["rows"]:
        st = r["status"]
        unit = r["unit"]
        v = r["value"]
        val = ("N/A" if v is None else (f'${v:,.0f}B' if unit == "$B" and abs(v) >= 10 else f'${v:,.2f}B' if unit == "$B"
               else f'${v:,.2f}' if unit == "$" else f'{v:,.2f}{unit}'))
        ch = r["change"]
        chs = "—" if ch is None else (f"{ch:+.0f}bp" if r["name"] in ("UST 10Y", "HY OAS (20d)") else
                                      f"{ch:+,.1f}B" if unit == "$B" else f"{ch:+.2f}pp" if unit == "%" else f"{ch:+.2f}%")
        mrows.append([e(r["name"]), val, chs, pill(C.KO_ENV.get(st, "—"), {"FAVORABLE": "pos", "RESTRICTED": "neg", "NEUTRAL": "warn"}.get(st, "muted"))])
    env = mac["env"]
    s0 = sec(0, "뉴스·이벤트·공시·거시 컨텍스트", [
        pill(f"거시 {C.KO_ENV.get(env)}", {"FAVORABLE": "pos", "RESTRICTED": "neg"}.get(env, "warn")),
        pill(f"이벤트 {len(a['events'])}건", "info"), pill(f"공시 {len(fl)}건", "info")]) + f"""
<div class="g3">
  <div class="card"><div class="ct">향후 1개월 이벤트</div>{table(["날짜", "이벤트", "중요도"], ev_rows)}</div>
  <div class="card"><div class="ct">최근 1주일 뉴스</div>{table(["날짜", "헤드라인", "출처"], nw_rows)}</div>
  <div class="card"><div class="ct">최근 공시 (8-K · Form 4 · 희석)</div>{table(["날짜", "Form", "내용", "플래그"], fi_rows)}{fi_note}</div>
</div>
<div class="card" style="margin-top:12px;"><div class="ct">거시 유동성 (Financial Plumbing) — 종합 <b style="color:{ {'FAVORABLE': BULL, 'RESTRICTED': BEAR}.get(env, WARN) };">{C.KO_ENV.get(env)}</b> (점수 {mac['score']:+d})</div>
{table(["지표", "값", "변화", "평가"], mrows)}
<div class="muted" style="margin-top:6px;">{'(거시 유동성 검색 실패) — 미시 수급만으로 확률 산출.' if mac.get('failed') else '우호 시 상방 +5~10%p, 제한 시 하방 +5~10%p 확률 보정. 관세·지정학 등 정성 요인은 주간 AI 내러티브에서 다룬다.'}</div></div>"""

    # ── SECTION 1 ──
    if is_idx:
        s1 = sec(1, "다크풀 레이어", [pill("지수 — 해당 없음", "muted")]) + '<div class="muted">지수에는 개별 체결 기반 다크풀 데이터가 없다.</div>'
        s2 = sec(2, "공매도 레이어", [pill("지수 — 해당 없음", "muted")]) + '<div class="muted">지수에는 공매도·대차 데이터가 없다.</div>'
    else:
        p1, x1 = C.l1_text(l1, l2, l3)
        dp_rows = []
        for r in reversed(l1.get("dp_series") or []):
            fl_ = r.get("anomaly") or ""
            dp_rows.append([e(r["date"]), C.sh(r["off_exchange_volume"]), C.sh(r.get("market_volume")),
                            _num(r.get("dp_pct"), 1, suffix="%"), _num(r.get("vol_ratio"), 2, suffix="x"),
                            pill(fl_, {"EXTREME": "neg", "HIGH": "warn", "MODERATE": "info"}[fl_]) if fl_ else "—"])
        dp_rows = dp_rows[:10]
        ats = l1.get("ats") or {}
        s1 = sec(1, "다크풀 레이어 (L1)", [pill(scen_label(l1), sig_kind(l1.get("signal"))),
                                       pill(f"DP {C.pc(l1.get('dp_pct'), sign=False)}", "info"),
                                       pill(l1.get("divergence") or "N/A", {"BULLISH_DIVERGENCE": "pos", "BEARISH_DIVERGENCE": "neg"}.get(l1.get("divergence"), "muted"))]) + f"""
{_l1_kpis(l1, ats)}
<div class="g2">
  <div class="card"><div class="ct">다크풀 비중 & 장외 거래량 (20거래일)</div><div class="ch"><canvas id="cDp"></canvas></div></div>
  <div class="card"><div class="ct">{'구성원별 일별 OBV (기관·프로·리테일) + 누적 — 체결 단위 tick rule' if l1.get('flow') else 'Net OBV 누적 (CLV 가중)'} · 5거래일</div><div class="ch"><canvas id="cObv"></canvas></div></div>
</div>
<div class="g2">
  <div class="card"><div class="ct">OBV 4-Way Decomposition (Δ, 5거래일)</div><div class="ch sm"><canvas id="cObv4"></canvas></div>
  <div class="muted">{"체결 단위(SIP 전 체결) 분류: 기관 ≥10,000주 또는 $200k · 리테일 <100주 또는 <$5k · 프로 그 외. Total = 세 코호트 합(계산값). 다크풀 VWAP " + _num(l1.get('dp_vwap'), 2, '$') + " (" + C.pc(l1.get('dp_vwap_dist_pct'), 2) + ")" if l1.get('flow') else "기관·프로·리테일 분해는 체결(Trade-level) 분류가 필요해 미표기. Total = 계산값."}</div></div>
  <div class="card"><div class="ct">체결 주체 구성 (FINRA 주간 실측)</div><div class="ch sm"><canvas id="cCoh"></canvas></div>
  <div class="muted">ATS = 다크풀(기관 블록) · 홀세일러 = 리테일 주문 내부화(PFOF) · Lit = 거래소(프로/HFT).</div></div>
</div>
{_flow_session_card(l1)}
<div class="card"><div class="ct">일별 장외 볼륨 이상 탐지 (20일 평균 대비 배수)</div>{table(["세션", "장외 거래량", "총 거래량", "DP %", "배수", "플래그"], dp_rows)}</div>
{interp(p1, x1)}"""

        # ── SECTION 2 ──
        p2, x2 = C.l2_text(l2, l1, l3)
        ss = l2.get("short_series") or []
        bs = {b["date"]: b for b in (l2.get("borrow_series") or [])}
        srows = []
        for i in range(len(ss) - 1, max(len(ss) - 15, -1), -1):
            r = ss[i]
            prev = ss[i - 1]["short_pct"] if i > 0 else None
            dlt = (r["short_pct"] - prev) if (prev is not None and r["short_pct"] is not None) else None
            b = bs.get(r["date"]) or {}
            srows.append([e(r["date"]), _num(r["short_pct"], 1, suffix="%"), colored(dlt, _num(dlt, 1, suffix="pp"), True),
                          C.sh(r["short_vol"]), C.sh(r["total_vol"]), C.sh(r.get("est_short_total")),
                          _num(b.get("fee"), 2, suffix="%") if b else "—"])
        si = l2.get("short_interest") or {}
        fee = l2.get("ctb_fee")
        s2 = sec(2, "공매도 레이어 (L2)", [pill(scen_label(l2), sig_kind(l2.get("signal"))),
                                       pill(f"z {(l2.get('anomaly_z') or 0):+.2f} · {l2.get('slope_dir') or 'N/A'}", "info"),
                                       pill(f"CTB {_num(fee, 2, suffix='%')}" if fee is not None else "CTB 미수집", "info" if fee is not None else "neg")]) + f"""
<div class="kpis">
 {kpi("Short % (장외)", _num(l2.get('short_pct_latest'), 1, suffix='%'), f"14일 평균 {_num(l2.get('short_avg_14d'), 1, suffix='%')} · z {_num(l2.get('anomaly_z'))}")}
 {kpi("Short 기울기", _num(l2.get('short_slope'), 2, suffix='pp/일'), e(l2.get('slope_dir') or ''))}
 {kpi("추정 총 공매도", C.sh(l2.get('est_short_total')), f"장외 실측 {C.sh(l2.get('short_shares'))}")}
 {kpi("CTB 수수료", _num(fee, 2, suffix='%'), f"Δ {C.pc(l2.get('ctb_delta_pct'))} · {e(l2.get('htb_class') or '')}", WARN if fee is None else TXT)}
 {kpi("가용 잔고", ('10M+' if l2.get('available_capped') else C.sh(l2.get('shares_available'))), f"Δ {C.pc(l2.get('avail_delta_pct'))}")}
 {kpi("SI / Float", _num(si.get('pct_float'), 2, suffix='%'), f"DTC {_num(si.get('days_to_cover'))}일 · Δ {C.pc(si.get('change_pct'))}")}
</div>
<div class="g2">
  <div class="card"><div class="ct">Short % 추세 (20거래일) · CTB</div><div class="ch"><canvas id="cShort"></canvas></div></div>
  <div class="card"><div class="ct">Short Volume 히스토리 (14일)</div>{table(["세션", "Short %", "Δ", "Short Vol", "Total(장외)", "추정 총숏", "CTB"], srows)}</div>
</div>
{alert('CTB 수수료가 1% 미만인데 가용잔고가 감소 중 — 기술적 반등 신호만으로 진입 시 무한 핀 리스크.', 'neg', '저CTB 역설 경고') if l2.get('low_ctb_paradox') else ''}
{interp(p2, x2)}"""

    # ── SECTION 3 ──
    p3, x3 = C.l3_text(l3, l1, l4, price)
    l3rows = [
        ["주 만기 / DTE", f"{e(l3.get('expiry'))} / {e(l3.get('dte'))}"],
        ["Max Pain (주)", f"{_num(l3.get('max_pain'), 2, '$')} ({C.pc(-(l3.get('max_pain_dist_pct') or 0), 2)})"],
        ["Max Pain (월물 " + e(l3.get('monthly_expiry') or '-') + ")", _num(l3.get('monthly_max_pain'), 2, '$')],
        ["GEX 플립 / 레짐", f"{_num(l3.get('flip_zone'), 2, '$')} / {e(l3.get('gamma_regime') or 'N/A')}"],
        ["콜월 / 풋월", f"{_num(l3.get('call_wall'), 2, '$')} / {_num(l3.get('put_wall'), 2, '$')}"],
        ["Net GEX (1% 이동당)", C.money_big(l3.get('net_gex'))],
        ["P/C OI · P/C Vol (보정)", f"{_num(l3.get('pc_oi'))} · {_num(l3.get('pc_vol'))} ({_num(l3.get('pc_vol_adj'))})"],
        ["ATM IV · IV30(CBOE)", f"{_num((l3.get('atm_iv') or 0) * 100 if l3.get('atm_iv') else None, 1, suffix='%')} · {_num((l3.get('iv30') or 0) * 100 if l3.get('iv30') else None, 1, suffix='%')}"],
        ["25Δ 스큐 · 기간구조", f"{_num(l3.get('skew_25d'), 1, suffix=' vol')} · {e(l3.get('term_structure') or 'N/A')}"],
        ["예상 변동폭 (주/월)", f"±{_num(l3.get('expected_move'), 2, '$')} ({_num(l3.get('expected_move_pct'), 1, suffix='%')}) / ±{_num(l3.get('monthly_expected_move'), 2, '$')}"],
        ["OI 합계 (60D) · 5일 Δ", f"{C.sh(l3.get('oi_total'))} · {C.pc(l3.get('oi_trend_pct'))}"],
    ]
    pin = l3.get("pinning")
    s3 = sec(3, "옵션 레이어 (L3)", [pill(scen_label(l3), sig_kind(l3.get("signal"))),
                                  pill(f"{'양' if (l3.get('flip_zone') and price > l3['flip_zone']) or (not l3.get('flip_zone') and l3.get('gamma_regime') == 'POSITIVE') else '음'}감마", "info"),
                                  pill("이진 이벤트", "neg") if l3.get("binary_event") else pill(f"DTE {l3.get('dte')}", "muted")]) + f"""
<div class="g2 w">
  <div class="card"><div class="ct">Gamma Exposure (GEX) Distribution — 60일 내 만기 합산 · 거리 가중 · ±30% 밖 행사가는 양끝 합산</div><div class="ch lg"><canvas id="cGex"></canvas></div>
  <div class="muted">콜 GEX=양(+, 위) · 풋 GEX=음(−, 아래). 가중치: ±2.5%=1.0 · ±5%=0.9 · ±10%=0.65 · ±15%=0.35 · ±20%=0.18 · 원거리=0.07</div></div>
  <div class="card">{table(["지표", "값"], l3rows, {1: R_BG, 3: A_BG})}
  {f'<div class="muted" style="margin-top:6px;">핀닝 사후검증({e(pin["expiry"])}): {e(pin["status"])} · 괴리 {pin["dist_pct"]:.2f}%</div>' if pin else ''}</div>
</div>
<div class="g2">
  <div class="card"><div class="ct">감마 프로파일 (가상 현재가별 총 GEX)</div><div class="ch"><canvas id="cProf"></canvas></div></div>
  <div class="card"><div class="ct">OI 분포 — 주 만기 {e(l3.get('expiry'))} (콜 위 · 풋 아래)</div><div class="ch"><canvas id="cOi"></canvas></div></div>
</div>
{interp(p3, x3)}"""

    # ── SECTION 4 ──
    p4, x4 = C.l4_text(l4, l1, l3)
    l4rows = [["SMA 20 / 50 / 200", f"{_num(l4.get('sma20'), 2, '$')} / {_num(l4.get('sma50'), 2, '$')} / {_num(l4.get('sma200'), 2, '$')}"],
              ["EMA 9 / 21", f"{_num(l4.get('ema9'), 2, '$')} / {_num(l4.get('ema21'), 2, '$')}"],
              ["BB 상단 / 하단 · 폭", f"{_num(l4.get('bb_upper'), 2, '$')} / {_num(l4.get('bb_lower'), 2, '$')} · {_num(l4.get('bb_width_pct'), 1, suffix='%')}"],
              ["RSI(14) · ATR(14)", f"{_num(l4.get('rsi14'), 1)} · {_num(l4.get('atr14'), 2, '$')}"],
              ["지지 (즉시 / 핵심)", f"{_num(l4.get('immediate_support'), 2, '$')} / {_num(l4.get('key_support'), 2, '$')}"],
              ["저항 (즉시 / 핵심)", f"{_num(l4.get('immediate_resistance'), 2, '$')} / {_num(l4.get('key_resistance'), 2, '$')}"],
              ["52주 고 / 저", f"{_num(l4.get('high_52w'), 2, '$')} / {_num(l4.get('low_52w'), 2, '$')}"]]
    s4 = sec(4, "차트 레이어 (L4 · 일봉)", [pill(scen_label(l4), sig_kind(l4.get("signal"))),
                                       pill(l4.get("ma_alignment") or "N/A", "muted"),
                                       pill(f"BB {l4.get('bb_width_trend') or ''}", "info")]) + f"""
<div class="g2 w">
  <div class="card"><div class="ct">가격 · 이동평균 · 볼린저밴드 (최근 120거래일)</div><div class="ch lg"><canvas id="cPx"></canvas></div></div>
  <div class="card">{table(["지표", "값"], l4rows)}</div>
</div>
{interp(p4, x4)}"""

    # ── SECTION 5 ──
    contrib = s.get("contributions") or {}
    mrows5 = []
    for k, nm, w in (("L1", "Dark Pool", .35), ("L2", "Short/CTB", .20), ("L3", "Options", .30), ("L4", "Chart", .15)):
        L = a[k.lower()]
        sig = L.get("signal") or "NEUTRAL"
        mrows5.append([f"<b>{k}</b> {nm}", pill(scen_label(L), sig_kind(sig)),
                       "●" if sig == "BULLISH" else "", "●" if sig == "NEUTRAL" else "", "●" if sig == "BEARISH" else "",
                       f"{(L.get('score') or 0):+.2f}", f"{(L.get('confidence') or 0):.0%}", f"{w:.2f}",
                       colored(contrib.get(k), f"{contrib.get(k, 0):+.3f}")])
    phase_rows = [
        ["<b>Phase 1</b> · Setup", e(ph["p1_date"]), f"레짐 시작 이후 {C.pc(ph.get('p1_return_pct'))} · L1 {C.ko(l1.get('scenario'))} 전개", pill("COMPLETE", "muted")],
        ["<b>Phase 2</b> · Transition", e(ph["p2_date"]), f"{C.ko(l1.get('scenario'))} × {C.ko(l3.get('scenario'))} · 주 만기 {e(l3.get('expiry'))}", pill("IN PROGRESS", "warn")],
        ["<b>Phase 3</b> · Resolution", e(ph["p3_date"]),
         f"{e(ph['p3_direction'])} · 상단 {_num(ph.get('p3_target_hi'), 2, '$')} / {_num(ph.get('p3_target_hi2'), 2, '$')} · 하단 {_num(ph.get('p3_target_lo'), 2, '$')} / {_num(ph.get('p3_target_lo2'), 2, '$')}"
         + (" · 촉매: " + ", ".join(f"{c['date']} {c['event']}" for c in ph.get("catalysts") or []) if ph.get("catalysts") else ""),
         pill("PENDING", "info")]]
    lv_rows, lv_hl = [], {}
    for i, lv in enumerate(a["levels"]):
        if lv["type"] == "Current Price":
            lv_hl[i] = B_BG
        elif lv["type"] in ("GEX Flip", "Max Pain"):
            lv_hl[i] = A_BG
        elif lv["type"] in ("Call Wall", "Put Wall"):
            lv_hl[i] = G_BG
        lv_rows.append([e(lv["type"]), f"${lv['price']:,.2f}", colored(lv["dist_pct"] or None, C.pc(lv["dist_pct"])) if lv["dist_pct"] else "0%",
                        e(lv["significance"])])
    s5 = sec(5, "3-Layer 통합 시나리오", [pill(f"Score {s['raw_score']:+.3f}", "info"),
                                      pill(f"거시 {C.KO_ENV.get(s.get('macro'))}", {"FAVORABLE": "pos", "RESTRICTED": "neg"}.get(s.get("macro"), "warn")),
                                      pill(", ".join(C.KO_PAT.get(p, p) for p in a["patterns"]) or "정형 패턴 없음", "warn" if a["patterns"] else "muted")]) + f"""
<div class="card"><div class="ct">레이어 신호 매트릭스 (가중 점수 = score × confidence × weight)</div>
{table(["Layer", "시나리오", "Bull", "Neut", "Bear", "Score", "Conf", "Weight", "기여"], mrows5)}</div>
<div class="card"><div class="ct">설계자 각본 — Phase 구조 (거래일 기준)</div>{table(["Phase", "기간", "내용", "상태"], phase_rows)}</div>
<div class="g2">
  <div class="card"><div class="ct">시나리오 확률 매트릭스</div><div class="ch sm"><canvas id="cProb"></canvas></div>
  <div class="muted">Raw Score {s['raw_score']:+.3f} · 분산 {s.get('dispersion', 0):.2f} · 거시 보정 {contrib.get('macro', 0) * 100:+.1f}%p · 패턴 보정 {contrib.get('patterns', 0) * 100:+.1f}%p · 상한 80%</div></div>
  <div class="card"><div class="ct">점수 기여도 분해</div><div class="ch sm"><canvas id="cContrib"></canvas></div></div>
</div>
<div class="card"><div class="ct">핵심 가격 레벨 지도</div>{table(["레벨", "가격", "거리", "중요도"], lv_rows, lv_hl)}</div>"""

    # ── SECTION 6 ──
    lines = C.three_lines(a)
    trig = C.triggers(a)
    trows = [[e(t["trigger"]), e(t["type"]),
              pill(t["status"], {"MET": "pos", "NOT MET": "muted", "N/A": "warn"}[t["status"]]), e(t["implication"])] for t in trig]
    met_b = sum(1 for t in trig if t["status"] == "MET" and t["type"] == "BULLISH")
    met_r = sum(1 for t in trig if t["status"] == "MET" and t["type"] == "BEARISH")
    rlis = "".join(f"<li>{e(r)}</li>" for r in C.risks(a))
    s6 = sec(6, "요약 및 대응 포인트", [pill(f"Bull 트리거 {met_b}", "pos"), pill(f"Bear 트리거 {met_r}", "neg")]) + f"""
<div class="three">
  <div><b style="color:{BEAR};">①</b> <b>[DARK POOL]</b> {e(lines[0])}</div>
  <div><b style="color:{BEAR};">②</b> <b>[SHORT/CTB]</b> {e(lines[1])}</div>
  <div><b style="color:{BEAR};">③</b> <b>[OPTIONS]</b> {e(lines[2])}</div>
</div>
<div class="card"><div class="ct">트리거 체크리스트</div>{table(["트리거", "유형", "상태", "함의"], trows)}</div>
<div class="card"><div class="ct">리스크 요인</div><ul class="risk">{rlis}</ul></div>"""

    # ── SECTION 7 ──
    concl = "".join(f'<p class="cc">{p}</p>' for p in C.conclusions(a))
    s7 = sec(7, "핵심 결론 요약", [pill(f"[{max((('A', s['A_bullish']), ('B', s['B_neutral']), ('C', s['C_bearish'])), key=lambda x: x[1])[0]}] 우세", "info")]) + \
        f'<div class="concl">{concl}</div>'

    footer = f"""
<div class="foot">데이터: FINRA RegSHO · FINRA OTC Transparency · CBOE delayed options · IBKR stock-loan · Nasdaq short interest · Yahoo Finance · SEC EDGAR · FRED.
FINRA Short%는 TRF 보고(장외) 체결 기준이며 포지션 잔고가 아니다. 본 리포트는 구조적 자금 흐름 분석 자료이며 투자 권유가 아니다. Smart Money Analyzer v3 · {e(m.get('elapsed_s'))}s</div>"""

    charts = _chart_payload(a)
    md_block = f'<div id="md-archive" style="display:none;"><pre id="md-archive-text">{e(md_text)}</pre></div>' if md_text else ""
    title = f"{T} 3-Layer Forensic {m['date']}"
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(a.get('headline'))}">
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
<style>{CSS}</style>
</head>
<body><div class="wrap">
{header}{summary}{quality}
{s0}{s1}{s2}{s3}{s4}{s5}{s6}{s7}
{footer}
</div>
<script id="sma-data" type="application/json">{charts}</script>
<script>{JS}</script>
{md_block}
</body></html>"""


def _shift(v: Optional[float], far: float) -> Optional[float]:
    return None if v is None else v + (1 if far else 0)


def _chart_payload(a: Dict) -> str:
    l1, l2, l3, l4, s = a["l1"], a["l2"], a["l3"], a["l4"], a["scenarios"]
    price = a["meta"]["price"]
    gw_all = l3.get("gex_weighted") or {}
    # 전체 행사가 포함: ±30% 밖 원거리 행사가는 양 끝 합산 버킷으로 표기 (합계 보존)
    lo_b, hi_b = price * 0.7, price * 1.3
    gw = {k: v for k, v in gw_all.items() if lo_b <= k <= hi_b and abs(v) > 0}
    far_lo = sum(v for k, v in gw_all.items() if k < lo_b)
    far_hi = sum(v for k, v in gw_all.items() if k > hi_b)
    strikes = sorted(gw)
    def frac_index(v: Optional[float]) -> Optional[float]:
        if v is None or not strikes:
            return None
        if v <= strikes[0]:
            return 0.0
        for i in range(len(strikes) - 1):
            if strikes[i] <= v <= strikes[i + 1]:
                return i + (v - strikes[i]) / (strikes[i + 1] - strikes[i])
        return float(len(strikes) - 1)
    chain = l3.get("chain_primary") or {}
    oi_k: Dict[float, List[float]] = {}
    for o in chain.get("calls", []):
        if abs(o["strike"] - price) / price <= 0.25:
            oi_k.setdefault(o["strike"], [0, 0])[0] += o["oi"]
    for o in chain.get("puts", []):
        if abs(o["strike"] - price) / price <= 0.25:
            oi_k.setdefault(o["strike"], [0, 0])[1] += o["oi"]
    oks = sorted(k for k, v in oi_k.items() if v[0] or v[1])
    oh = a.get("ohlcv") or []
    closes = [c["close"] for c in oh]
    def sma_series(n_: int) -> List[Optional[float]]:
        return [None if i + 1 < n_ else sum(closes[i + 1 - n_:i + 1]) / n_ for i in range(len(closes))]
    bbu, bbl = [], []
    for i in range(len(closes)):
        if i < 19:
            bbu.append(None); bbl.append(None); continue
        w = closes[i - 19:i + 1]
        mu = sum(w) / 20
        sd = (sum((x - mu) ** 2 for x in w) / 20) ** 0.5
        bbu.append(mu + 2 * sd); bbl.append(mu - 2 * sd)
    N = 120
    hist_full = a.get("history") or []
    data = {
        "price": price,
        "dp": {"labels": [r["date"][5:] for r in l1.get("dp_series") or []],
               "dp": [r.get("dp_pct") for r in l1.get("dp_series") or []],
               "off": [r.get("off_exchange_volume") for r in l1.get("dp_series") or []], "avg": l1.get("dp_avg20")},
        "obv": {"inst": [c.get("inst") for c in l1.get("cumulative_daily") or []],
                "pro": [c.get("pro") for c in l1.get("cumulative_daily") or []],
                "ret": [c.get("ret") for c in l1.get("cumulative_daily") or []],
                "inst_cum": [c.get("inst_cum") for c in l1.get("cumulative_daily") or []],
                "labels": [c["date"][5:] for c in l1.get("cumulative_daily") or []],
                "cum": [c["cum"] for c in l1.get("cumulative_daily") or []],
                "day": [c["day"] for c in l1.get("cumulative_daily") or []]},
        "obv4": {"inst": l1.get("delta_inst"), "pro": l1.get("delta_pro"), "ret": l1.get("delta_retail"),
                 "total": l1.get("delta_total")},
        "coh": l1.get("ats"),
        "short": {"labels": [r["date"][5:] for r in l2.get("short_series") or []],
                  "sp": [r.get("short_pct") for r in l2.get("short_series") or []], "avg": l2.get("short_avg_14d"),
                  "ctb": [({b["date"]: b for b in (l2.get("borrow_series") or [])}.get(r["date"]) or {}).get("fee")
                          for r in l2.get("short_series") or []]},
        "gex": {"labels": ([f"<{lo_b:.0f}"] if far_lo else []) + [f"{k:g}" for k in strikes] + ([f">{hi_b:.0f}"] if far_hi else []),
                "vals": ([far_lo] if far_lo else []) + [gw[k] for k in strikes] + ([far_hi] if far_hi else []),
                "flip": _shift(frac_index(l3.get("flip_zone")), far_lo), "spot": _shift(frac_index(price), far_lo),
                "mp": _shift(frac_index(l3.get("max_pain")), far_lo),
                "flipv": l3.get("flip_zone"), "mpv": l3.get("max_pain")},
        "prof": l3.get("gamma_profile") or [],
        "oi": {"labels": [f"{k:g}" for k in oks], "c": [oi_k[k][0] for k in oks], "p": [-oi_k[k][1] for k in oks],
               "spot": None},
        "px": {"labels": [c["date"][5:] for c in oh[-N:]], "c": closes[-N:], "s20": sma_series(20)[-N:],
               "s50": sma_series(50)[-N:], "s200": sma_series(200)[-N:], "bbu": bbu[-N:], "bbl": bbl[-N:],
               "v": [c["volume"] for c in oh[-N:]]},
        "prob": [s["A_bullish"], s["B_neutral"], s["C_bearish"]],
        "contrib": s.get("contributions") or {},
        "hist_n": len(hist_full),
    }
    if oks:
        lo = [i for i, k in enumerate(oks) if k <= price]
        data["oi"]["spot"] = (lo[-1] + ((price - oks[lo[-1]]) / (oks[lo[-1] + 1] - oks[lo[-1]]) if lo[-1] + 1 < len(oks) else 0)) if lo else 0
    return json.dumps(data, separators=(",", ":"), default=lambda o: None).replace("</", "<\\/")


CSS = f"""
*{{box-sizing:border-box}}
body{{margin:0;background:#FFFFFF;color:{TXT};font-family:{FONT};font-size:13px;line-height:1.55}}
.wrap{{max-width:1200px;margin:0 auto;padding:20px 16px 40px}}
a{{color:{INFO};text-decoration:none}} a:hover{{text-decoration:underline}}
.top{{display:flex;gap:14px;font-size:12px;margin-bottom:10px}} .top a{{color:{MUTED}}}
.hdr{{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;border-bottom:1px solid {BORDER};padding-bottom:12px;margin-bottom:14px;flex-wrap:wrap}}
.hdr h1{{font-size:24px;margin:0}} .nm{{font-size:14px;color:{MUTED};font-weight:500}}
.sub{{font-size:12px;color:{MUTED};margin-top:4px}}
.px{{text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:4px}} .pv{{font-size:26px;font-weight:700}}
.pill{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;white-space:nowrap;font-family:{FONT}}}
.exec{{background:{CARD};border:0.5px solid {BORDER};border-radius:8px;padding:14px 16px;margin-bottom:12px}}
.eh{{font-size:10px;letter-spacing:.12em;color:{MUTED};font-weight:700}}
.hl{{font-size:15px;font-weight:700;margin:6px 0 10px}}
.pm{{display:flex;height:22px;border-radius:5px;overflow:hidden;font-size:11px;font-weight:700;color:#FFFFFF}}
.pm div{{display:flex;align-items:center;justify-content:center;opacity:.9;min-width:34px}}
.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:12px}}
.tile{{background:#FFFFFF;border:0.5px solid {BORDER};border-radius:6px;padding:8px 10px}}
.tl{{font-size:11px;color:{MUTED};font-weight:600}} .tv{{font-size:14px;font-weight:700;margin:2px 0 6px}}
.bar{{position:relative;height:8px;background:{PANEL};border-radius:4px}} .bar .mid{{position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:{MUTED}}}
.ts{{font-size:10px;color:{MUTED};margin-top:4px}}
.ladder{{position:relative;height:112px;margin:14px 60px 4px}}
.lline{{position:absolute;left:0;right:0;top:57px;height:2px;background:{BORDER}}}
.llist{{display:none}}
.lm{{position:absolute;top:0;bottom:0}} .ld{{position:absolute;top:53px;width:10px;height:10px;margin-left:-5px;border-radius:50%}}
.lt{{position:absolute;transform:translateX(-50%);font-size:10px;line-height:1.25;white-space:nowrap;background:{CARD};padding:0 2px}}
details.dq{{background:#FFFFFF;border:0.5px solid {BORDER};border-radius:6px;padding:8px 12px;margin-bottom:6px}}
details.dq summary{{cursor:pointer;font-weight:600;font-size:12px;color:{MUTED}}}
.wl{{font-size:11px;color:{WARN};margin:8px 0 0 18px;padding:0}}
.sec{{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;border-bottom:0.5px solid {WARN};margin:26px 0 12px;padding-bottom:5px}}
.sec-t{{color:{WARN};font-weight:700;font-size:14px}} .sec-b{{display:flex;gap:6px;flex-wrap:wrap}}
.card{{background:{CARD};border:0.5px solid {BORDER};border-radius:6px;padding:12px;margin-bottom:12px;min-width:0}}
.ct{{font-weight:600;font-size:12px;color:{MUTED};margin-bottom:8px}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} .g2.w{{grid-template-columns:1.55fr 1fr}}
.g3{{display:grid;grid-template-columns:1fr 1.25fr 1.1fr;gap:12px;align-items:start}}
.kpis{{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:12px}}
.kpi{{background:{CARD};border:0.5px solid {BORDER};border-radius:6px;padding:8px 10px;min-width:0}}
.kl{{font-size:10px;color:{MUTED};text-transform:uppercase;letter-spacing:.04em}} .kv{{font-size:17px;font-weight:700;margin:2px 0}}
.ks{{font-size:10.5px;color:{MUTED}}}
.ch{{position:relative;height:230px;background:#FFFFFF;border-radius:4px}} .ch.sm{{height:170px}} .ch.lg{{height:330px}}
.tw{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
table{{border-collapse:collapse;width:100%;font-size:12px;font-family:{FONT}}}
th{{background:{PANEL};color:{MUTED};padding:4px 6px;border:0.5px solid {BORDER};text-align:left;font-weight:600;white-space:nowrap}}
td{{padding:4px 6px;border:0.5px solid {BORDER};color:{TXT};vertical-align:top}}
tbody tr:nth-child(odd){{background:#FFFFFF}} tbody tr:nth-child(even){{background:{CARD}}}
.muted{{color:{MUTED};font-size:11px}}
.alert{{padding:8px 12px;border-radius:4px;font-size:12px;margin:0 0 12px}}
.interp{{background:#FFFFFF;border:0.5px solid {BORDER};border-left:3px solid {INFO};border-radius:0 6px 6px 0;padding:10px 14px;margin-bottom:8px}}
.ih{{font-size:11px;font-weight:700;color:{INFO};letter-spacing:.06em;margin-bottom:4px}}
.interp ul{{margin:0;padding-left:18px}} .interp li{{margin:3px 0}}
.xv{{margin-top:8px;padding-top:8px;border-top:0.5px dashed {BORDER};font-size:12px}}
.three{{font-size:11px;line-height:1.8;background:{CARD};border:0.5px solid {BORDER};border-radius:6px;padding:10px 12px;margin-bottom:12px}}
.risk{{margin:0;padding-left:18px;font-size:12px}} .risk li{{margin:3px 0}}
.concl{{border-top:2px solid {WARN};padding-top:6px}}
.cc{{font-weight:700;font-size:13.5px;line-height:1.85;margin:10px 0}} .cc u{{text-decoration-color:{WARN};text-underline-offset:3px}}
.foot{{margin-top:30px;font-size:10.5px;color:{MUTED};text-align:center;line-height:1.6}}
@media (max-width:1024px){{.kpis{{grid-template-columns:repeat(3,1fr)}} .g3,.g2,.g2.w{{grid-template-columns:1fr}}}}
@media (max-width:640px){{.wrap{{padding:14px 16px 30px}} .tiles{{grid-template-columns:1fr 1fr}} .kpis{{grid-template-columns:1fr 1fr}}
 .hdr h1{{font-size:20px}} .pv{{font-size:22px}} .ladder{{display:none}} .llist{{display:flex;flex-direction:column;gap:2px;font-size:11.5px;margin-top:10px;
 border-top:0.5px solid {BORDER};padding-top:8px}}}}
@media print{{.wrap{{max-width:100%}} details.dq{{display:none}} .ch{{height:200px}} .card{{break-inside:avoid}}}}
"""

JS = """
(function(){
const D=JSON.parse(document.getElementById('sma-data').textContent);
const F='-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
Chart.defaults.font.family=F;Chart.defaults.font.size=11;Chart.defaults.color='#1F2328';
Chart.defaults.plugins.legend.labels.font={family:F,size:11};
Chart.defaults.maintainAspectRatio=false;
const G='#EAEEF2',BU='#1A7F5A',BE='#CF222B',WA='#9A6700',IN='#0969DA';
const a=(h,o)=>{const n=parseInt(h.slice(1),16);return `rgba(${n>>16&255},${n>>8&255},${n&255},${o})`;};
const fmt=v=>{if(v==null)return'N/A';const x=Math.abs(v),s=v<0?'-':'';return x>=1e9?s+(x/1e9).toFixed(2)+'B':x>=1e6?s+(x/1e6).toFixed(2)+'M':x>=1e3?s+(x/1e3).toFixed(1)+'K':s+x.toFixed(0);};
const grid={color:G};
const endLabels={id:'endLabels',afterDatasetsDraw(c,_,o){if(!o||!o.on)return;const ctx=c.ctx;ctx.save();ctx.font='600 11px '+F;
 c.data.datasets.forEach((ds,i)=>{c.getDatasetMeta(i).data.forEach((b,j)=>{const v=ds.data[j];if(v==null)return;const t=o.f?o.f(v):v;
 const hz=c.options.indexAxis==='y';ctx.textBaseline='middle';
 if(hz){const w=ctx.measureText(t).width;let x=v<0?b.x-4:b.x+4,al=v<0?'right':'left',col=o.color||'#1F2328';
  if(v<0&&x-w<c.chartArea.left){x=b.x+4;al='left';col='#FFFFFF';}
  if(v>=0&&x+w>c.chartArea.right){x=b.x-4;al='right';col='#FFFFFF';}
  ctx.textAlign=al;ctx.fillStyle=col;ctx.fillText(t,x,b.y);}
 else{ctx.textAlign='center';ctx.fillStyle=o.color||'#1F2328';ctx.fillText(t,b.x,b.y-8);}});});ctx.restore();}};
Chart.register(endLabels);
const mk=(id,cfg)=>{const el=document.getElementById(id);if(el)new Chart(el,cfg);};
const vline=(v,c,label,dash,pos)=>v==null?null:{type:'line',scaleID:'x',value:v,borderColor:c,borderWidth:1.5,borderDash:dash||[],
 label:{display:true,content:label,position:pos||'start',backgroundColor:a(c,.9),color:'#fff',font:{family:F,size:10,weight:'600'},padding:3}};
const anns=arr=>Object.fromEntries(arr.filter(Boolean).map((x,i)=>['a'+i,x]));
// L1
if(D.dp.labels.length){mk('cDp',{data:{labels:D.dp.labels,datasets:[
 {type:'bar',label:'DP %',data:D.dp.dp,backgroundColor:a(IN,.85),yAxisID:'y'},
 {type:'line',label:'장외 거래량',data:D.dp.off,borderColor:WA,backgroundColor:WA,pointRadius:2,borderWidth:1.5,yAxisID:'y1'}]},
 options:{plugins:{annotation:{annotations:anns([D.dp.avg==null?null:{type:'line',scaleID:'y',value:D.dp.avg,borderColor:MUTED(),borderDash:[4,4],borderWidth:1,label:{display:true,content:'20일 평균 '+D.dp.avg.toFixed(1)+'%',position:'end',font:{size:10},backgroundColor:'rgba(101,109,118,.85)'}}])}},
 scales:{y:{grid,title:{display:true,text:'DP %'},suggestedMin:20},y1:{position:'right',grid:{display:false},ticks:{callback:fmt}},x:{grid:{display:false}}}}});}
function MUTED(){return '#656D76';}
if(D.obv.labels.length){const end=D.obv.cum[D.obv.cum.length-1]||0,c=end>=0?BU:BE;
 const coh=D.obv.inst.some(v=>v!=null);
 const ds=coh?[
  {type:'bar',label:'기관',data:D.obv.inst,backgroundColor:a(BU,.85),stack:'d'},
  {type:'bar',label:'프로',data:D.obv.pro,backgroundColor:a(WA,.85),stack:'d'},
  {type:'bar',label:'리테일',data:D.obv.ret,backgroundColor:a(BE,.85),stack:'d'},
  {type:'line',label:'기관 누적',stack:'l1',data:D.obv.inst_cum,borderColor:BU,borderWidth:2.5,pointRadius:3,tension:.25},
  {type:'line',label:'Net 누적',stack:'l2',data:D.obv.cum,borderColor:'#A32D2D',borderDash:[5,4],borderWidth:2,pointRadius:3,tension:.25}]
 :[{type:'bar',label:'일별 순체결',data:D.obv.day,backgroundColor:D.obv.day.map(v=>a(v>=0?BU:BE,.85))},
  {type:'line',label:'누적',data:D.obv.cum,borderColor:c,backgroundColor:a(c,.12),fill:true,tension:.25,pointRadius:3}];
 mk('cObv',{data:{labels:D.obv.labels,datasets:ds},
 options:{interaction:{mode:'index',intersect:false},plugins:{legend:{position:'bottom',labels:{boxWidth:10}},tooltip:{callbacks:{label:t=>t.dataset.label+': '+fmt(t.raw)}}},
 scales:{y:{grid,stacked:coh,ticks:{callback:fmt}},x:{grid:{display:false},stacked:coh}}}});}
{const o=D.obv4,labels=['기관','프로','리테일','전체(Total)'],vals=[o.inst,o.pro,o.ret,o.total];
 mk('cObv4',{type:'bar',data:{labels,datasets:[{data:vals,backgroundColor:[a(BU,.85),a(WA,.85),a(BE,.85),a('#A32D2D',.85)]}]},
 options:{indexAxis:'y',plugins:{legend:{display:false},endLabels:{on:true,f:v=>(v>=0?'+':'')+fmt(v)}},
 scales:{x:{grid,ticks:{callback:fmt},suggestedMin:-Math.abs(o.total||1)*1.4,suggestedMax:Math.abs(o.total||1)*1.4},y:{grid:{display:false}}}}});}
if(D.coh){const c=D.coh;mk('cCoh',{type:'bar',data:{labels:[c.week_start+'주'],datasets:[
 {label:'ATS(다크풀)',data:[c.ats_share_pct],backgroundColor:a(BU,.85)},{label:'홀세일러(리테일)',data:[c.otc_share_pct],backgroundColor:a(BE,.85)},
 {label:'거래소(Lit)',data:[c.lit_share_pct],backgroundColor:a(WA,.85)}]},
 options:{indexAxis:'y',plugins:{legend:{position:'bottom'},tooltip:{callbacks:{label:t=>t.dataset.label+': '+t.raw.toFixed(1)+'%'}}},
 scales:{x:{stacked:true,max:100,grid,ticks:{callback:v=>v+'%'}},y:{stacked:true,grid:{display:false}}}}});}
// L2
if(D.short.labels.length){const hasC=D.short.ctb.some(v=>v!=null);const ds=[{type:'line',label:'Short %',data:D.short.sp,borderColor:BE,backgroundColor:a(BE,.1),fill:true,tension:.2,pointRadius:2,yAxisID:'y'}];
 if(hasC)ds.push({type:'line',label:'CTB %',data:D.short.ctb,borderColor:IN,pointRadius:2,borderWidth:1.5,spanGaps:true,yAxisID:'y1'});
 mk('cShort',{data:{labels:D.short.labels,datasets:ds},options:{plugins:{annotation:{annotations:anns([D.short.avg==null?null:{type:'line',scaleID:'y',value:D.short.avg,borderColor:WA,borderDash:[4,4],borderWidth:1,label:{display:true,content:'14일 평균 '+D.short.avg.toFixed(1)+'%',position:'end',font:{size:10},backgroundColor:a(WA,.9)}}])}},
 scales:{y:{grid,title:{display:true,text:'Short %'}},y1:{display:hasC,position:'right',grid:{display:false},title:{display:hasC,text:'CTB %'}},x:{grid:{display:false}}}}});}
// L3 GEX
if(D.gex.labels.length){const g=D.gex;mk('cGex',{type:'bar',data:{labels:g.labels,datasets:[{label:'GEX (weighted)',data:g.vals,
 backgroundColor:g.vals.map(v=>a(v>=0?BU:BE,.85)),borderWidth:0,barPercentage:1,categoryPercentage:.9}]},
 options:{plugins:{legend:{display:false},tooltip:{callbacks:{title:t=>'Strike $'+t[0].label,label:t=>'GEX '+fmt(t.raw)+' $/1%'}},
 annotation:{annotations:anns([vline(g.flip,WA,'Flip $'+(g.flipv||0).toFixed(2),[],'start'),vline(g.spot,IN,'Spot $'+D.price.toFixed(2),[6,4],'end'),vline(g.mp,WA,'Max Pain $'+(g.mpv||0).toFixed(2),[2,3],'center')])}},
 scales:{y:{grid,ticks:{callback:fmt}},x:{grid:{display:false},ticks:{autoSkip:true,maxRotation:0,autoSkipPadding:6}}}}});}
if(D.prof.length){mk('cProf',{type:'line',data:{labels:D.prof.map(p=>p.s.toFixed(0)),datasets:[{label:'총 GEX',data:D.prof.map(p=>p.g),
 borderColor:WA,borderWidth:2,pointRadius:0,fill:{target:'origin',above:a(BU,.15),below:a(BE,.15)}}]},
 options:{plugins:{legend:{display:false},annotation:{annotations:anns([{type:'line',scaleID:'y',value:0,borderColor:'#656D76',borderWidth:1},
 vline(D.prof.reduce((b,p,i)=>Math.abs(p.s-D.price)<Math.abs(D.prof[b].s-D.price)?i:b,0),IN,'Spot',[6,4],'end')])}},
 scales:{y:{grid,ticks:{callback:fmt}},x:{grid:{display:false},ticks:{maxTicksLimit:9}}}}});}
if(D.oi.labels.length){mk('cOi',{type:'bar',data:{labels:D.oi.labels,datasets:[{label:'Call OI',data:D.oi.c,backgroundColor:a(BU,.85)},{label:'Put OI',data:D.oi.p,backgroundColor:a(BE,.85)}]},
 options:{plugins:{tooltip:{callbacks:{label:t=>t.dataset.label+': '+fmt(Math.abs(t.raw))}},annotation:{annotations:anns([vline(D.oi.spot,IN,'Spot',[6,4],'end')])}},
 scales:{x:{stacked:true,grid:{display:false},ticks:{maxRotation:0,autoSkipPadding:6}},y:{stacked:true,grid,ticks:{callback:v=>fmt(Math.abs(v))}}}}});}
// L4
if(D.px.labels.length){const p=D.px;mk('cPx',{data:{labels:p.labels,datasets:[
 {type:'line',label:'BB 상단',data:p.bbu,borderColor:a(IN,.35),borderWidth:1,pointRadius:0,fill:'+1',backgroundColor:a(IN,.06)},
 {type:'line',label:'BB 하단',data:p.bbl,borderColor:a(IN,.35),borderWidth:1,pointRadius:0},
 {type:'line',label:'종가',data:p.c,borderColor:'#1F2328',borderWidth:1.8,pointRadius:0},
 {type:'line',label:'SMA20',data:p.s20,borderColor:WA,borderWidth:1.2,pointRadius:0},
 {type:'line',label:'SMA50',data:p.s50,borderColor:IN,borderWidth:1.2,pointRadius:0},
 {type:'line',label:'SMA200',data:p.s200,borderColor:BE,borderWidth:1.2,pointRadius:0},
 {type:'bar',label:'거래량',data:p.v,backgroundColor:a('#656D76',.25),yAxisID:'yv'}]},
 options:{interaction:{mode:'index',intersect:false},plugins:{legend:{position:'bottom',labels:{boxWidth:10}}},
 scales:{y:{grid},yv:{position:'right',grid:{display:false},ticks:{callback:fmt},max:Math.max(...p.v)*4},x:{grid:{display:false},ticks:{maxTicksLimit:10,maxRotation:0}}}}});}
// S5
mk('cProb',{type:'bar',data:{labels:['[A] 상승','[B] 중립','[C] 하락'],datasets:[{data:D.prob,backgroundColor:[a(BU,.85),a(WA,.85),a(BE,.85)],barThickness:18}]},
 options:{indexAxis:'y',plugins:{legend:{display:false},endLabels:{on:true,f:v=>v.toFixed(1)+'%'}},scales:{x:{min:0,max:80,grid,ticks:{callback:v=>v+'%'}},y:{grid:{display:false}}}}});
{const k=['L1','L2','L3','L4','macro','patterns'],lb=['L1 다크풀','L2 공매도','L3 옵션','L4 차트','거시','패턴'],v=k.map(x=>D.contrib[x]||0);
 mk('cContrib',{type:'bar',data:{labels:lb,datasets:[{data:v,backgroundColor:v.map(x=>a(x>=0?BU:BE,.85))}]},
 options:{indexAxis:'y',plugins:{legend:{display:false},endLabels:{on:true,f:x=>(x>=0?'+':'')+x.toFixed(3)}},scales:{x:{grid,suggestedMin:-.15,suggestedMax:.15},y:{grid:{display:false}}}}});}
})();
"""
