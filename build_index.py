#!/usr/bin/env python3
"""
reports/ 디렉토리를 스캔해 정적 대시보드 사이트를 빌드.

생성물:
  reports/index.html                ← 메인 대시보드 (검색/필터)
  reports/app.js                    ← 대시보드 프론트 로직
  reports/data/{TICKER}.json        ← 종목별 시계열 (minified, 세션 날짜 기준)
  reports/tickers/{TICKER}/index.html   ← 종목 전용 페이지 (Chart.js 시계열)

원본:
  reports/YYYY-MM-DD/{TICKER}_{DATE}.json            ← sma.py 가 생성
  reports/YYYY-MM-DD/{TICKER}_3Layer_Forensic_{DATE}.html
  reports/YYYY-MM-DD/_v2/...                          ← 세션 폴더와 충돌해 옮겨진 레거시 파일

날짜 의미:
  - v3 (`schema>=3`): 파일명/`date` = 미국 정규장 세션 날짜 (그대로 사용)
  - v2 (schema 키 없음): 파일명 = KST 실행일, 데이터는 직전 거래일 세션
    → `smartmoney.calendar.legacy_session_for(file_date)` 로 세션 날짜에 매핑
  - (ticker, session_date) 중복은 가장 최근 생성본만 유지 (v3 > v2, 그 다음 늦은 실행일)
  - 각 행의 `report_url` 은 실제 디스크상의 HTML 경로 (reports/ 기준 상대경로, 없으면 null)

출력은 결정적(deterministic)이다 — 실행 시각 같은 휘발성 값을 쓰지 않아
워크플로 간 rebase 충돌을 줄인다.
"""
from __future__ import annotations

import html
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from smartmoney import commentary as C
from smartmoney.calendar import legacy_session_for, parse_date
from smartmoney.render_valuation import card as valuation_card

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
DATA_DIR = REPORTS / "data"
TICKERS_DIR = REPORTS / "tickers"

DATE_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"
FILE_RE = re.compile(r"^([A-Z0-9.\-]+)_(\d{4}-\d{2}-\d{2})\.json$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LEGACY_SUBDIR = "_v2"

# 최신 행이 아닌 과거 행에서 제거할 무거운 시계열 필드 (data/{T}.json 크기 절감)
HEAVY_KEYS = {
    "l1": ("cumulative_daily", "dp_series"),
    "l2": ("short_series", "borrow_series"),
}

FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
COLOR = {
    "bg": "#FFFFFF", "card": "#F6F8FA", "panel": "#EAEEF2",
    "border": "#D0D7DE", "text": "#1F2328", "muted": "#656D76",
    "bull": "#1A7F5A", "bear": "#CF222B", "warn": "#9A6700", "info": "#0969DA",
    "alert_green": "#DAFBE1", "alert_red": "#FFEBE9",
    "alert_amber": "#FFF8C5", "alert_blue": "#DDF4FF",
}

E = lambda v: html.escape("" if v is None else str(v), quote=True)  # noqa: E731


# ───────────────────────── helpers ─────────────────────────
def _clean(obj: Any) -> Any:
    """NaN/Inf → None (브라우저 JSON.parse 가 NaN 을 거부함)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def _num(v) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    return None


def _js(obj: Any) -> str:
    """<script> 안에 안전하게 박을 수 있는 JSON."""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def fmt_money(v) -> str:
    n = _num(v)
    return f"${n:,.2f}" if n is not None else "N/A"


def fmt_pct(v, dec: int = 1, signed: bool = False) -> str:
    n = _num(v)
    if n is None:
        return "N/A"
    return f"{n:+.{dec}f}%" if signed else f"{n:.{dec}f}%"


def fmt_compact(v, signed: bool = True) -> str:
    n = _num(v)
    if n is None:
        return "N/A"
    a = abs(n)
    sign = ("+" if n > 0 else "-" if n < 0 else "") if signed else ("-" if n < 0 else "")
    if a >= 1e9:
        body = f"{a/1e9:.2f}B"
    elif a >= 1e6:
        body = f"{a/1e6:.1f}M"
    elif a >= 1e3:
        body = f"{a/1e3:.0f}k"
    else:
        body = f"{a:.0f}"
    return sign + body


def top_scenario(sc: dict):
    """(key, pct, label, color) 또는 확률이 하나도 없으면 None."""
    pairs = [("A", _num(sc.get("A_bullish")), "Bullish", COLOR["bull"]),
             ("B", _num(sc.get("B_neutral")), "Neutral", COLOR["warn"]),
             ("C", _num(sc.get("C_bearish")), "Bearish", COLOR["bear"])]
    pairs = [p for p in pairs if p[1] is not None]
    if not pairs:
        return None
    return max(pairs, key=lambda x: x[1])


def case_pretty(c) -> str:
    return C.KO_CASE.get(c, re.sub(r"^CASE_\d+_|^CASE_", "", c)) if c else "—"


def quality_of(row: dict) -> dict:
    q = row.get("quality") or {}
    grade = q.get("grade") if isinstance(q.get("grade"), str) else None
    score = _num(q.get("score"))
    missing = []
    for lname, layer in (q.get("layers") or {}).items():
        if not isinstance(layer, dict):
            continue
        st = layer.get("status")
        if st and st != "OK":
            miss = layer.get("missing") or []
            missing.append(f"{lname}:{st}" + (f"({', '.join(map(str, miss[:4]))})" if miss else ""))
    return {"grade": grade, "score": score, "missing": missing}


GRADE_COLORS = {
    "A": (COLOR["alert_green"], COLOR["bull"]), "B": (COLOR["alert_green"], COLOR["bull"]),
    "C": (COLOR["alert_amber"], COLOR["warn"]),
    "D": (COLOR["alert_red"], COLOR["bear"]), "F": (COLOR["alert_red"], COLOR["bear"]),
}


def quality_badge(row: dict) -> str:
    q = quality_of(row)
    if not q["grade"]:
        label = "레거시 v2" if row.get("legacy") else "품질 N/A"
        return (f'<span style="background:{COLOR["panel"]};color:{COLOR["muted"]};padding:2px 8px;'
                f'border-radius:10px;font-size:11px;">{E(label)}</span>')
    bg, fg = GRADE_COLORS.get(q["grade"][:1].upper(), (COLOR["panel"], COLOR["muted"]))
    score = f" {q['score']:.0f}" if q["score"] is not None else ""
    title = "; ".join(q["missing"]) or "모든 레이어 OK"
    return (f'<span title="{E(title)}" style="background:{bg};color:{fg};padding:2px 8px;border-radius:10px;'
            f'font-size:11px;font-weight:600;">데이터 품질 {E(q["grade"])}{E(score)}</span>')


# ───────────────────────── scanning ─────────────────────────
def _find_html(folder: Path, ticker: str, *dates: str) -> Optional[str]:
    for d in dict.fromkeys(dates):
        p = folder / f"{ticker}_3Layer_Forensic_{d}.html"
        if p.exists():
            return p.relative_to(REPORTS).as_posix()
    return None


def load_report(jpath: Path) -> Optional[dict]:
    """JSON 1개 → 정규화된 행 (date=세션 날짜). 파싱 실패/형식 불일치 시 None."""
    m = FILE_RE.match(jpath.name)
    if not m:
        return None
    ticker, name_date = m.group(1), m.group(2)
    try:
        j = json.loads(jpath.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"WARN: failed to parse {jpath}: {e}")
        return None
    if not isinstance(j, dict):
        return None
    j = _clean(j)
    try:
        schema = int(j.get("schema") or 2)
    except (TypeError, ValueError):
        schema = 2
    if schema >= 3:
        d = j.get("date")
        session = d if isinstance(d, str) and DATE_RE.match(d) else name_date
    else:
        session = legacy_session_for(parse_date(name_date)).isoformat()
    j["ticker"] = ticker
    j["schema"] = schema
    j["legacy"] = schema < 3
    j["run_date"] = name_date
    j["date"] = session
    j["report_url"] = _find_html(jpath.parent, ticker, name_date, session)
    j["source_file"] = jpath.relative_to(REPORTS).as_posix()
    return j


def _rank(row: dict, jpath: Path) -> tuple:
    folder = jpath.parent.name
    return (
        1 if row.get("schema", 2) >= 3 else 0,
        str(row.get("generated_at") or ""),
        row.get("run_date") or "",
        0 if folder == LEGACY_SUBDIR else 1,                 # 원위치 파일 우선
        1 if folder == row.get("run_date") else 0,           # 폴더=파일명 일치 우선
        str(jpath),
    )


def iter_report_paths(ticker: Optional[str] = None):
    name = f"{ticker}_*.json" if ticker else "*.json"
    yield from REPORTS.glob(f"{DATE_GLOB}/{name}")
    yield from REPORTS.glob(f"{DATE_GLOB}/{LEGACY_SUBDIR}/{name}")


def scan_reports(ticker: Optional[str] = None, include_index: bool = False) -> Dict[str, List[dict]]:
    """reports/**.json 스캔 → {ticker: [row, ...]} (세션 날짜 오름차순, 세션당 1행)."""
    best: Dict[tuple, tuple] = {}
    for jpath in iter_report_paths(ticker):
        row = load_report(jpath)
        if row is None:
            continue
        if ticker and row["ticker"] != ticker:
            continue
        if row.get("is_index") and not include_index:
            continue  # 지수 리포트는 종목 시계열에서 제외
        key = (row["ticker"], row["date"])
        rk = _rank(row, jpath)
        if key not in best or rk > best[key][0]:
            best[key] = (rk, row)
    by_ticker: Dict[str, List[dict]] = defaultdict(list)
    for (t, _d), (_rk, row) in best.items():
        by_ticker[t].append(row)
    for t in by_ticker:
        by_ticker[t].sort(key=lambda x: x["date"])
    return dict(by_ticker)


def scan_ticker(ticker: str) -> List[dict]:
    return scan_reports(ticker=ticker).get(ticker, [])


def slim_series(series: List[dict]) -> List[dict]:
    """마지막 행만 전체 시계열 필드를 유지하고 과거 행은 무거운 배열 제거."""
    out = []
    for i, row in enumerate(series):
        if i == len(series) - 1:
            out.append(row)
            continue
        r = dict(row)
        for layer, keys in HEAVY_KEYS.items():
            if isinstance(r.get(layer), dict):
                r[layer] = {k: v for k, v in r[layer].items() if k not in keys}
        out.append(r)
    return out


# ───────────────────────── narratives ─────────────────────────
def scan_narratives(ticker: str) -> list:
    """reports/tickers/{T}/narratives/*.html → [{date, mode, filename}, ...] 최신순"""
    ndir = TICKERS_DIR / ticker / "narratives"
    if not ndir.exists():
        return []
    items = []
    for p in ndir.glob("*.html"):
        m = re.match(r"(\d{4}-\d{2}-\d{2})-(weekly|daily|adhoc)(?:-[\w\-]+)?\.html$", p.name)
        if m:
            items.append({"date": m.group(1), "mode": m.group(2), "filename": p.name})
    items.sort(key=lambda x: (x["date"], x["filename"]), reverse=True)
    return items


def _render_narrative_archive(ticker: str) -> str:
    items = scan_narratives(ticker)
    if not items:
        return f'<p style="font-size:12px;color:{COLOR["muted"]};">아직 생성된 AI 내러티브 없음. 아래 버튼으로 즉석 생성 가능.</p>'
    mode_meta = {
        "weekly": ("주간 자동",   COLOR["alert_blue"],  COLOR["info"]),
        "daily":  ("일일 자동",   COLOR["alert_green"], COLOR["bull"]),
        "adhoc":  ("즉석(사용자)", COLOR["alert_amber"], COLOR["warn"]),
    }
    rows = []
    for it in items[:40]:
        label, bg, fg = mode_meta.get(it["mode"], ("?", COLOR["panel"], COLOR["muted"]))
        rows.append(
            f'<tr><td>{E(it["date"])}</td>'
            f'<td><span style="background:{bg};color:{fg};padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;">{E(label)}</span></td>'
            f'<td><a href="narratives/{E(it["filename"])}">열기</a></td></tr>'
        )
    return ("<table><thead><tr><th>Date</th><th>Mode</th><th>Link</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


# ───────────────────────── ticker page ─────────────────────────
def _overview_cards(latest: dict) -> str:
    l1, l2, l3 = latest.get("l1") or {}, latest.get("l2") or {}, latest.get("l3") or {}
    sc = latest.get("scenarios") or {}

    def tt_icon(body_html):
        return f'<span class="tt tt-i">ⓘ<span class="tt-body">{body_html}</span></span>'

    # Block 1: TOP SCENARIO
    top = top_scenario(sc)
    if top:
        _k, top_val, top_label, top_color = top
        top_str = f"{top_label} · {top_val:.1f}%"
        others = " · ".join(
            f"{lbl} {_num(sc.get(key)):.0f}%"
            for key, lbl in (("A_bullish", "Bullish"), ("B_neutral", "Neutral"), ("C_bearish", "Bearish"))
            if lbl != top_label and _num(sc.get(key)) is not None)
    else:
        top_str, top_color, others = "N/A", COLOR["muted"], ""
    rs = _num(sc.get("raw_score"))
    score_str = f"{rs:+.2f}" if rs is not None else "N/A"
    tt1 = ("<b>가중 signal 합산 시나리오 확률</b><br>"
           "Bullish/Neutral/Bearish 중 가장 높은 확률이 대표 시나리오. "
           "Score는 −1.00~+1.00 범위, Macro는 FRED 기반 유동성 분류." + (f"<br><br>{E(others)}" if others else ""))

    # Block 2: Dark Pool %
    dp_str = fmt_pct(l1.get("dp_pct"))
    ratio = _num(l1.get("dp_ratio"))
    ratio_str = f" · 20d 대비 {ratio:.2f}×" if ratio is not None else ""
    obv_str = fmt_compact(l1.get("delta_total"))
    if l1.get("delta_inst") is not None:
        obv_str = (f"기관 {fmt_compact(l1.get('delta_inst'))} · 프로 {fmt_compact(l1.get('delta_pro'))} · "
                   f"리테일 {fmt_compact(l1.get('delta_retail'))} · IAR {_num(l1.get('iar')) or 0:.2f}")
    div = C.kt(C.KO_DIV, l1.get("divergence"))
    tt2 = ("<b>FINRA 오프거래소 거래량 ÷ 총거래량</b><br>"
           "다크풀·ATS·내부화 비중. 30-50% 정상 범위, 50%+ 기관 heavy.<br><br>"
           "<b>구성원별 OBV (5d)</b>: SIP 전 체결 tick rule — 기관 ≥10,000주/$200k · 리테일 <100주/$5k · 프로 그 외. "
           "체결 데이터 없던 과거 세션은 Total(CLV) 만 표시.<br>"
           "<b>Divergence</b>: 가격 slope vs OBV 누적 slope (5일 회귀).")

    # Block 3: Short % · CTB
    sp_str = fmt_pct(l2.get("short_pct_latest"))
    cb_str = fmt_pct(l2.get("ctb_fee"), 2)
    cbd = _num(l2.get("ctb_delta_pct"))
    cbd_str = f" (Δ {cbd:+.1f}%)" if cbd is not None else ""
    si = l2.get("short_interest") or {}
    dtc = _num(si.get("days_to_cover"))
    dtc_str = f" · DTC {dtc:.1f}d" if dtc is not None else ""
    tt3 = ("<b>FINRA Reg SHO 공매도 비율</b> · <b>차입 비용(CTB)</b><br>"
           "Short%는 당일 공매도 체결 비율 — 포지션(short interest) 아님. "
           "MM 헤지 포함이라 40-55% 구조적 정상. <b>절대값으로 bearish 판단 금지</b>, "
           "slope + CTB 방향 + Case 조합으로만 해석.<br><br>"
           "<b>CTB</b>: &lt;1% ETB · 5-15% HTB · &gt;15% Squeeze risk zone<br>"
           "<b>DTC</b>: 공매도 잔고 ÷ 평균 거래량 (FINRA 격주, 지연 있음)")

    # Block 4: Max Pain · GEX Flip
    mp_str = fmt_money(l3.get("max_pain"))
    fl_str = fmt_money(l3.get("flip_zone"))
    dte = l3.get("dte")
    dte_str = f"DTE {dte}" if _num(dte) is not None else "DTE N/A"
    tt4 = ("<b>Max Pain</b>: 만기일 MM 헤지 비용이 최소화되는 주가 — 핀닝 중력점.<br>"
           "<b>GEX Flip Zone</b>: MM gamma 부호가 +에서 −로 바뀌는 spot 근처 strike.<br>"
           "spot &gt; Flip → 핀닝 regime(변동성 억제) · spot &lt; Flip → 증폭 regime.<br>"
           "DTE ≤ 5 일 때만 pinning 신호 유효.")

    # Block 5: Walls · Expected move (v3)
    cw, pw = fmt_money(l3.get("call_wall")), fmt_money(l3.get("put_wall"))
    em = _num(l3.get("expected_move_pct"))
    em_str = f"±{em:.1f}%" if em is not None else "N/A"
    tt5 = ("<b>Call / Put Wall</b>: 콜·풋 OI(또는 GEX)가 가장 큰 행사가 — 저항/지지 후보.<br>"
           "<b>Expected Move</b>: ATM 스트래들 가격 기반 만기까지 예상 변동폭.")

    return f"""
    <div class="card"><div class="card-label">Top Scenario {tt_icon(tt1)}</div>
      <div class="card-value" style="color:{top_color};">{E(top_str)}</div>
      <div class="card-sub">점수 {E(score_str)} · 거시 {E(C.KO_ENV.get(latest.get('macro_env'), latest.get('macro_env') or 'N/A'))}</div>
    </div>
    <div class="card"><div class="card-label">Dark Pool % {tt_icon(tt2)}</div>
      <div class="card-value">{E(dp_str)}</div>
      <div class="card-sub">OBV {E(obv_str)} · {E(div)}{E(ratio_str)}</div>
    </div>
    <div class="card"><div class="card-label">Short % · CTB {tt_icon(tt3)}</div>
      <div class="card-value">{E(sp_str)} · {E(cb_str)}</div>
      <div class="card-sub">{E(case_pretty(l2.get('case')))}{E(cbd_str)}{E(dtc_str)}</div>
    </div>
    <div class="card"><div class="card-label">Max Pain · GEX Flip {tt_icon(tt4)}</div>
      <div class="card-value">{E(mp_str)} · {E(fl_str)}</div>
      <div class="card-sub">{E(dte_str)} · {E(C.ko(l3.get('scenario')))}</div>
    </div>
    <div class="card"><div class="card-label">Call / Put Wall · Exp. Move {tt_icon(tt5)}</div>
      <div class="card-value">{E(cw)} / {E(pw)}</div>
      <div class="card-sub">Expected move {E(em_str)}</div>
    </div>
    """


def _legacy_archive(legacy: list) -> str:
    if not legacy:
        return ""
    def link(s: dict) -> str:
        return f'<a href="../../{E(s["report_url"])}">리포트</a>' if s.get("report_url") else "—"
    rows = "".join(f'<tr><td>{E(s["date"])}</td><td>{E(fmt_money(s.get("price")))}</td><td>{link(s)}</td></tr>'
                   for s in reversed(legacy))
    return (f'<details style="margin-top:18px;"><summary style="cursor:pointer;font-size:13px;font-weight:700;color:{COLOR["muted"]};">'
            f'이전 버전(v2) 리포트 아카이브 · {len(legacy)}개 세션 — 방법론이 달라 시계열·분석에서 제외 (원본 보존)</summary>'
            f'<div class="t-scroll" style="margin-top:8px;"><table><thead><tr><th>Session</th><th>Price</th><th>Link</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div></details>')


def render_ticker_page(ticker: str, series: list) -> str:
    # v3(2026-11-13~) 만 시계열·해석에 사용. v2 는 방법론이 달라 아래 아카이브 목록으로만 노출.
    legacy = [s for s in series if s.get("legacy")]
    series = [s for s in series if not s.get("legacy")]
    latest = series[-1] if series else (legacy[-1] if legacy else {})
    dates = [s["date"] for s in series]

    def col(layer, key):
        return [_num((s.get(layer) or {}).get(key)) for s in series]

    prices = [_num(s.get("price")) for s in series]
    bull_probs = [_num((s.get("scenarios") or {}).get("A_bullish")) for s in series]
    bear_probs = [_num((s.get("scenarios") or {}).get("C_bearish")) for s in series]
    call_wall, put_wall = col("l3", "call_wall"), col("l3", "put_wall")

    rows = []
    for s in reversed(series):
        sc = s.get("scenarios") or {}
        sl1, sl2, sl3 = s.get("l1") or {}, s.get("l2") or {}, s.get("l3") or {}
        top = top_scenario(sc)
        if top:
            key, val, label, color = top
            top_cell = f'<td style="color:{color};font-weight:600;">{E(label[:4])} {val:.0f}%</td>'
        else:
            top_cell = "<td>N/A</td>"
        link = (f'<a href="../../{E(s["report_url"])}">리포트</a>' if s.get("report_url") else "—")
        q = quality_of(s)
        q_str = (q["grade"] or "—") + (f" {q['score']:.0f}" if q["score"] is not None else "")
        rows.append(
            f'<tr><td>{E(s["date"])}</td><td>{E(fmt_money(s.get("price")))}</td>{top_cell}'
            f'<td>{E(C.ko(sl1.get("scenario")) if sl1.get("scenario") else "—")}</td><td>{E(case_pretty(sl2.get("case")))}</td>'
            f'<td>{E(C.ko(sl3.get("scenario")) if sl3.get("scenario") else "—")}</td><td>{E(q_str if not s.get("legacy") else "v2")}</td>'
            f'<td>{link}</td></tr>'
        )

    patterns = latest.get("patterns") or []
    pattern_badges = "".join(
        f'<span style="background:{COLOR["alert_amber"]};color:{COLOR["warn"]};padding:2px 8px;border-radius:10px;font-size:11px;margin-right:4px;">{E(C.KO_PAT.get(p, p))}</span>'
        for p in patterns
    ) or f"<span style='color:{COLOR['muted']};font-size:11px;'>패턴 없음</span>"
    headline = latest.get("headline")
    ol = latest.get("one_liner")
    headline_html = ((f'<div class="oneliner"><span>한줄평</span>{E(ol)}</div>' if isinstance(ol, str) and ol.strip() else "")
                     + (f'<div class="headline">{E(headline)}</div>' if isinstance(headline, str) and headline.strip() else ""))
    warn_n = len(latest.get("warnings") or [])
    warn_html = (f' · <span title="{E("; ".join(map(str, latest.get("warnings") or []))[:1500])}" '
                 f'style="color:{COLOR["warn"]};">경고 {warn_n}건</span>') if warn_n else ""

    price_ds = [
        {"label": "Price", "data": prices, "borderColor": COLOR["info"], "borderWidth": 2.5},
        {"label": "Max Pain", "data": col("l3", "max_pain"), "borderColor": COLOR["warn"], "borderDash": [4, 3]},
        {"label": "GEX Flip", "data": col("l3", "flip_zone"), "borderColor": COLOR["bull"], "borderDash": [2, 2]},
    ]
    if any(v is not None for v in call_wall):
        price_ds.append({"label": "Call Wall", "data": call_wall, "borderColor": "#57AB5A", "borderDash": [6, 3]})
    if any(v is not None for v in put_wall):
        price_ds.append({"label": "Put Wall", "data": put_wall, "borderColor": "#E5534B", "borderDash": [6, 3]})
    fair_hist = [_num((s.get("valuation") or {}).get("fair_value")) if isinstance(s.get("valuation"), dict) else None for s in series]
    val = _current_valuation(ticker, latest)
    if val and val.get("fair_value") is not None and fair_hist:
        fair_hist[-1] = _num(val.get("fair_value"))        # 최신 세션에는 발행된 최신 적정가
    if any(v is not None for v in fair_hist):
        price_ds.append({"label": "적정가 (펀더멘털)", "data": fair_hist, "borderColor": "#1F2328", "borderWidth": 2, "borderDash": [1, 3],
                         "pointRadius": 2, "spanGaps": False})
    val_html = ""
    if val and val.get("fair_value") is not None:
        href = "valuation.html" if (TICKERS_DIR / ticker / "valuation.html").exists() else None
        val_html = f'<h2>펀더멘털 적정가치 <span style="font-size:11px;color:{COLOR["muted"]};font-weight:400;">수급 분석과 독립된 축 · {E(latest.get("date"))} 세션 기준</span></h2>' + valuation_card(val, href)
    datasets = {
        "priceChart": (price_ds, False),
        "probChart": ([
            {"label": "Bullish", "data": bull_probs, "borderColor": COLOR["bull"]},
            {"label": "Bearish", "data": bear_probs, "borderColor": COLOR["bear"]},
        ], False),
        "shortChart": ([
            {"label": "Short %", "data": col("l2", "short_pct_latest"), "borderColor": COLOR["bear"], "yAxisID": "y"},
            {"label": "CTB %", "data": col("l2", "ctb_fee"), "borderColor": COLOR["warn"], "yAxisID": "y1"},
        ], True),
        "obvChart": ([
            {"label": "기관 OBV (5d)", "data": col("l1", "delta_inst"), "borderColor": COLOR["bull"], "yAxisID": "y"},
            {"label": "프로 OBV (5d)", "data": col("l1", "delta_pro"), "borderColor": COLOR["warn"], "yAxisID": "y"},
            {"label": "리테일 OBV (5d)", "data": col("l1", "delta_retail"), "borderColor": COLOR["bear"], "yAxisID": "y"},
            {"label": "Total OBV (5d)", "data": col("l1", "delta_total"), "borderColor": "#A32D2D", "borderDash": [5, 4], "yAxisID": "y"},
            {"label": "Dark Pool %", "data": col("l1", "dp_pct"), "borderColor": COLOR["info"], "yAxisID": "y1"},
        ], True),
    }
    latest_price = fmt_money(latest.get("price"))
    t = E(ticker)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16.png">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#000000">
<title>{t} · Smart Money History</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body {{ font-family:{FONT}; background:{COLOR['bg']}; color:{COLOR['text']}; margin:0; padding:24px; font-size:13px;
         -webkit-tap-highlight-color:transparent; }}
  .wrap {{ max-width:1200px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px 0; }}
  h2 {{ font-size:14px; color:{COLOR['warn']}; border-bottom:0.5px solid {COLOR['warn']}; padding-bottom:4px; margin-top:24px; }}
  .meta {{ color:{COLOR['muted']}; font-size:12px; margin-bottom:12px; }}
  .oneliner {{ background:{COLOR['alert_amber']}; border-left:4px solid {COLOR['warn']}; border-radius:0 6px 6px 0; padding:10px 14px; font-size:15px; font-weight:700; line-height:1.55; margin-bottom:8px; }}
  .oneliner span {{ display:block; font-size:11px; color:{COLOR['warn']}; letter-spacing:.06em; margin-bottom:2px; }}
  .headline {{ background:{COLOR['alert_blue']}; color:{COLOR['text']}; border-radius:6px; padding:8px 12px; font-size:13px; margin-bottom:12px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:12px; }}
  .card {{ background:{COLOR['card']}; border:0.5px solid {COLOR['border']}; border-radius:6px; padding:12px; }}
  .card-label {{ font-size:11px; color:{COLOR['muted']}; margin-bottom:4px; text-transform:uppercase; letter-spacing:0.05em; }}
  .card-value {{ font-size:18px; font-weight:600; }}
  .card-sub {{ font-size:11px; color:{COLOR['muted']}; margin-top:4px; }}
  table {{ border-collapse:collapse; width:100%; font-size:12px; }}
  th,td {{ border:0.5px solid {COLOR['border']}; padding:6px 8px; text-align:left; }}
  th {{ background:{COLOR['panel']}; color:{COLOR['muted']}; font-weight:600; }}
  tr:nth-child(even) {{ background:{COLOR['card']}; }}
  a {{ color:{COLOR['info']}; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  canvas {{ max-height:200px; }}
  .chart-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:12px; }}
  .nav {{ margin-bottom:16px; font-size:12px; }}
  .nav a {{ color:{COLOR['muted']}; }}
  .t-scroll {{ overflow-x:auto; -webkit-overflow-scrolling:touch; max-width:100%; }}
  .t-scroll table {{ min-width:100%; }}
  @media (max-width:1024px) {{
    .chart-grid {{ grid-template-columns:1fr; }}
  }}
  @media (max-width:720px) {{
    body {{ padding:14px !important; font-size:12.5px; }}
    .wrap {{ max-width:100% !important; }}
    h1 {{ font-size:18px; }}
    h2 {{ font-size:13px; }}
    .grid {{ grid-template-columns:1fr; }}
    canvas {{ max-height:180px; }}
    .tt .tt-body {{ max-width:88vw; font-size:10px; }}
    #narrative-root div[style*="grid-template-columns:repeat(auto-fit"] {{ grid-template-columns:1fr !important; }}
  }}
  .tt {{ position:relative; display:inline-block; cursor:help; }}
  .tt-i {{ color:{COLOR['info']}; margin-left:4px; font-weight:600; font-size:11px; }}
  .tt .tt-body {{
    display:none;
    position:absolute; left:50%; transform:translateX(-50%);
    bottom:calc(100% + 8px); z-index:20;
    background:#1F2328; color:#FFFFFF; padding:8px 10px; border-radius:6px;
    font-size:11px; line-height:1.5; text-align:left; text-transform:none; letter-spacing:0;
    white-space:normal; width:max-content; max-width:320px;
    box-shadow:0 4px 12px rgba(0,0,0,0.15); font-weight:400;
  }}
  .tt .tt-body::after {{ content:''; position:absolute; top:100%; left:50%; margin-left:-5px; border:5px solid transparent; border-top-color:#1F2328; }}
  .tt:hover .tt-body {{ display:block; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="nav"><a href="../../">← 전체 대시보드</a></div>
  <h1>{t}</h1>
  <div class="meta">최신 세션 {E(latest.get('date') or 'N/A')} (미국 정규장) · {E(latest_price)} · {len(series)}개 세션 · {quality_badge(latest)}{warn_html} · {pattern_badges}</div>
  {headline_html}

  <h2>Overview (latest)</h2>
  <div class="grid">
    {_overview_cards(latest)}
  </div>

  {val_html}

  <h2>시계열 추이</h2>
  <div class="chart-grid">
    <div class="card" style="grid-column:1/-1;"><div class="card-label">Price · Max Pain · GEX Flip · Walls · 적정가</div><canvas id="priceChart"></canvas></div>
    <div class="card"><div class="card-label">구성원별 OBV (기관·프로·리테일·Total, 5d, 좌) vs Dark Pool % (우)</div><canvas id="obvChart"></canvas></div>
    <div class="card"><div class="card-label">Short % (좌) · CTB Fee % (우)</div><canvas id="shortChart"></canvas></div>
    <div class="card" style="grid-column:1/-1;"><div class="card-label">Scenario Probabilities (%)</div><canvas id="probChart"></canvas></div>
  </div>

  <h2>전체 히스토리 (세션 날짜 기준)</h2>
  <div class="t-scroll">
  <table>
    <thead><tr><th>Session</th><th>Price</th><th>Top Scenario</th><th>Darkpool</th><th>Short Case</th><th>Options</th><th>Quality</th><th>Link</th></tr></thead>
    <tbody>{''.join(rows) if rows else '<tr><td colspan="8">아직 리포트 없음</td></tr>'}</tbody>
  </table>
  </div>

  {_legacy_archive(legacy)}

  <h2>주간 AI 분석 리포트 히스토리</h2>
  {_render_narrative_archive(ticker)}

  <div id="narrative-root"></div>

  <div style="margin-top:24px;font-size:10px;color:{COLOR['muted']};text-align:center;">
    Data: <a href="../../data/{t}.json">{t}.json</a> · <a href="../../">Back to dashboard</a>
  </div>
</div>
<script src="../../narrative.js"></script>
<script>
  document.addEventListener('DOMContentLoaded', () => {{
    if (window.SMA_Narrative) {{
      window.SMA_Narrative.mount(document.getElementById('narrative-root'), {_js(ticker)});
    }}
  }});
</script>
<script>
Chart.defaults.font.family = {_js(FONT)};
Chart.defaults.color = {_js(COLOR['text'])};
Chart.defaults.plugins.legend.labels.usePointStyle = true;
const DATES = {_js(dates)};
const DATASETS = {_js(datasets)};
function lineChart(id, datasets, dual) {{
  const scales = {{ y:{{ grid:{{color:{_js(COLOR['panel'])}}} }}, x:{{ grid:{{display:false}} }} }};
  if (dual) scales.y1 = {{ position:'right', grid:{{drawOnChartArea:false}} }};
  new Chart(document.getElementById(id), {{
    type:'line',
    data:{{ labels:DATES, datasets:datasets.map(d => Object.assign({{backgroundColor:'transparent', spanGaps:false}}, d)) }},
    options:{{ responsive:true, maintainAspectRatio:false,
      plugins:{{
        legend:{{
          display:datasets.length>1, position:'bottom',
          labels:{{
            usePointStyle:true, boxWidth:40, boxHeight:10, padding:14,
            generateLabels(chart) {{
              return chart.data.datasets.map((ds, i) => ({{
                text: ds.label, fillStyle: ds.borderColor, strokeStyle: ds.borderColor,
                lineWidth: ds.borderWidth || 2, lineDash: ds.borderDash || [],
                pointStyle: 'line', hidden: !chart.isDatasetVisible(i), datasetIndex: i
              }}));
            }}
          }},
          onClick(e, item, legend) {{
            const ci = legend.chart;
            ci.setDatasetVisibility(item.datasetIndex, !ci.isDatasetVisible(item.datasetIndex));
            ci.update();
          }}
        }},
        tooltip:{{ usePointStyle:true, callbacks:{{ labelPointStyle() {{ return {{ pointStyle:'line', rotation:0 }}; }} }} }}
      }},
      scales:scales,
      elements:{{point:{{radius:2}},line:{{tension:0.3,borderWidth:2}}}},
    }}
  }});
}}
for (const [id, pair] of Object.entries(DATASETS)) lineChart(id, pair[0], pair[1]);
</script>
</body>
</html>
"""


# ───────────────────────── dashboard ─────────────────────────
def load_watchlist() -> set:
    """tickers.txt에서 현재 감시 종목 집합 로드 (주석/빈 줄 제외)."""
    p = ROOT / "tickers.txt"
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.add(s.upper())
    return out


def summarize(t: str, series: list, watchlist: set) -> dict:
    latest = series[-1]
    sc = latest.get("scenarios") or {}
    l1, l2, l3 = latest.get("l1") or {}, latest.get("l2") or {}, latest.get("l3") or {}
    top = top_scenario(sc)
    q = quality_of(latest)
    si = l2.get("short_interest") or {}
    headline = latest.get("headline")
    return {
        "ticker": t,
        "is_active": t in watchlist,
        "latest_date": latest.get("date"),
        "report_url": latest.get("report_url"),
        "legacy": bool(latest.get("legacy")),
        "price": _num(latest.get("price")),
        "scenario": top[0] if top else None,
        "scenario_pct": top[1] if top else None,
        "raw_score": _num(sc.get("raw_score")),
        "patterns": latest.get("patterns") or [],
        "l1_scenario": l1.get("scenario"),
        "l2_case": l2.get("case"),
        "l3_scenario": l3.get("scenario"),
        "macro_env": latest.get("macro_env"),
        "dp_pct": _num(l1.get("dp_pct")),
        "ctb_fee": _num(l2.get("ctb_fee")),
        "ctb_delta_pct": _num(l2.get("ctb_delta_pct")),
        "short_pct": _num(l2.get("short_pct_latest")),
        "dtc": _num(si.get("days_to_cover")),
        "max_pain": _num(l3.get("max_pain")),
        "call_wall": _num(l3.get("call_wall")),
        "put_wall": _num(l3.get("put_wall")),
        "exp_move_pct": _num(l3.get("expected_move_pct")),
        "quality_grade": q["grade"],
        "quality_score": q["score"],
        "quality_missing": q["missing"],
        "warnings_count": len(latest.get("warnings") or []),
        "headline": headline if isinstance(headline, str) else None,
        "one_liner": latest.get("one_liner") if isinstance(latest.get("one_liner"), str) else None,
        "report_count": len(series),
        **_valuation_summary(t, latest),
    }


def _current_valuation(t: str, latest: dict):
    """표시할 적정가: Valuation Desk 가 발행한 최신 수치를 최신 세션 종가로 다시 매긴 것.

    리포트 JSON 의 `valuation` 키는 그 세션을 분석한 시점의 값이다. 적정가는 수급 분석과 따로 갱신되므로(주간 예약 작업),
    발행 파일이 있으면 그쪽이 최신이다. 없으면 리포트에 붙어 있던 값을 쓴다. 계산은 하지 않는다 — 읽고 괴리율만 다시 매긴다."""
    try:
        from smartmoney import valuation_desk as VD
        pub = VD.read_published(t, _num(latest.get("price")), latest.get("date")) if not str(t).startswith("^") else None
    except Exception:  # noqa: BLE001
        pub = None
    if isinstance(pub, dict) and pub.get("fair_value") is not None:
        return pub
    v = latest.get("valuation")
    return v if isinstance(v, dict) else None


def _valuation_summary(t: str, latest: dict) -> dict:
    """대시보드용 적정가치 요약 (발행된 최신 수치 → 없으면 리포트의 `valuation` 키 → 없으면 전부 None)."""
    v = _current_valuation(t, latest) or {}
    fair = _num(v.get("fair_value"))
    return {
        "fair_value": fair,
        "fair_low": _num(v.get("fair_low")), "fair_high": _num(v.get("fair_high")),
        "fair_upside": _num(v.get("upside_pct")) if fair is not None else None,
        "fair_verdict": v.get("verdict") if fair is not None else None,
        "fair_verdict_ko": v.get("verdict_ko") if fair is not None else None,
        "fair_unc": (v.get("uncertainty") or {}).get("label") if fair is not None else None,
        "fair_type": (v.get("archetype") or {}).get("label") if fair is not None else None,
        "fair_models": v.get("n_models") if fair is not None else None,
        "fair_method": v.get("method") if fair is not None else None,
        "fair_research": (v.get("research") or {}).get("as_of") if fair is not None else None,
        "fair_stale": bool(v.get("stale")) if fair is not None else None,
        "fair_gap": bool(v.get("model_gap")) if fair is not None else None,
        "val_url": f"tickers/{t}/valuation.html" if fair is not None and (TICKERS_DIR / t / "valuation.html").exists() else None,
        "name": v.get("name") if fair is not None else None,
        "fair_base_price": _num(v.get("price")) if fair is not None else None,     # 괴리 계산에 쓴 주가 (세션이 오래됐으면 발행 시점 주가)
    }


def review_rows(watchlist: set) -> list:
    """검토 종목(review.txt) — 관심종목에 넣기 전에 적정가만 먼저 보는 종목. 발행 파일을 읽기만 한다."""
    try:
        from smartmoney import review_list as RL
    except Exception:  # noqa: BLE001
        return []
    out = []
    for e in RL.load():
        t = e["ticker"]
        if t in watchlist:
            continue
        st = RL.status(e)
        pub = RL.published(t) or {}
        R = pub.get("research") or {}
        has = bool(pub)
        out.append({
            "ticker": t, "status": st["status"], "requested_at": e.get("requested_at"),
            "research_as_of": st["research_as_of"], "age_days": st["age_days"], "name": pub.get("name"),
            "price": _num(pub.get("price")), "price_date": pub.get("published"),
            "published_at": pub.get("published_at") or (f"{pub['published']}T00:00:00Z" if pub.get("published") else None),
            "fair_value": _num(pub.get("fair_value")) if has else None,
            "fair_low": _num(pub.get("fair_low")), "fair_high": _num(pub.get("fair_high")),
            "fair_upside": _num(pub.get("upside_pct")) if has else None,
            "fair_verdict": pub.get("verdict"), "fair_verdict_ko": pub.get("verdict_ko"),
            "fair_unc": (pub.get("uncertainty") or {}).get("label"), "confidence": R.get("confidence"),
            "audit": (R.get("audit") or {}).get("verdict") if isinstance(R.get("audit"), dict) else None,
            "val_url": f"tickers/{t}/valuation.html" if has and (TICKERS_DIR / t / "valuation.html").exists() else None,
        })
    return out


TABLE_HEAD = """<thead>
  <tr class="grp">
    <th class="stk" rowspan="2" data-sort="ticker">종목</th>
    <th colspan="4" class="gs">가격 · 적정가</th>
    <th colspan="4" class="gs">수급 종합</th>
    <th colspan="4" class="gs">레이어 판정</th>
    <th colspan="5" class="gs">지표</th>
    <th colspan="2" class="gs">옵션 레벨</th>
    <th colspan="3" class="gs"></th>
  </tr>
  <tr class="cols">
    <th class="gs" data-sort="price">현재가</th><th data-sort="fair_value" title="리서치 기반 펀더멘털 적정가 (Valuation Desk)">적정가</th>
    <th data-sort="fair_upside" title="현재가 대비 적정가 괴리">괴리</th><th data-sort="fair_verdict">판정</th>
    <th class="gs" data-sort="scenario">시나리오</th><th data-sort="raw_score">Score</th><th data-sort="latest_date">세션</th><th data-sort="quality_score">품질</th>
    <th class="gs">매크로</th><th>다크풀</th><th>공매도</th><th>옵션</th>
    <th class="gs" data-sort="dp_pct">DP %</th><th data-sort="short_pct">Short %</th><th data-sort="ctb_fee">CTB % (Δ)</th><th data-sort="dtc">DTC</th><th data-sort="exp_move_pct">±Move</th>
    <th class="gs" data-sort="max_pain">Max Pain</th><th>Call / Put Wall</th>
    <th class="gs">패턴</th><th data-sort="report_count" title="리포트 수">📄</th><th></th>
  </tr>
</thead>"""

REVIEW_HEAD = """<thead><tr class="cols1">
    <th class="stk" data-sort="ticker">종목</th><th data-sort="status">상태</th><th data-sort="price" title="마지막 재계산 시점의 주가">기준가</th>
    <th data-sort="fair_value">적정가</th><th data-sort="fair_upside">괴리</th><th data-sort="fair_verdict">판정</th>
    <th>범위 (약세 ~ 강세)</th><th>불확실성</th><th>확신도</th><th data-sort="research_as_of">분석일</th><th>상세</th><th>작업</th>
  </tr></thead>"""


def render_dashboard(db: dict, watchlist: set = None) -> tuple[str, str]:
    """HTML + app.js 생성. watchlist 티커는 '관심종목', review.txt 는 '검토 종목', 그 외 과거 데이터는 '히스토리'."""
    watchlist = watchlist or set()
    summary = [summarize(t, s, watchlist) for t, s in sorted(db.items()) if s]
    review = review_rows(watchlist)
    latest_session = max((r["latest_date"] for r in summary if r["latest_date"]), default="N/A")
    meta = f"최신 세션 {latest_session} (미국 정규장, ET) · {len(summary)}개 종목"
    c = COLOR
    btn = (f"padding:5px 12px;background:{c['panel']};color:{c['text']};border:0.5px solid {c['border']};"
           "border-radius:4px;cursor:pointer;font-size:12px;")

    html_out = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16.png">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#000000">
<title>Smart Money Analyzer — Dashboard</title>
<style>
  body {{ font-family:{FONT}; background:{c['bg']}; color:{c['text']}; margin:0; padding:24px; font-size:13px;
         -webkit-tap-highlight-color:transparent; }}
  .wrap {{ max-width:1400px; margin:0 auto; }}
  header {{ display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid {c['border']}; padding-bottom:12px; margin-bottom:0; flex-wrap:wrap; gap:12px; }}
  h1 {{ font-size:20px; margin:0; }}
  .meta {{ color:{c['muted']}; font-size:11px; }}
  a {{ color:{c['info']}; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  input, select {{ padding:6px 10px; border:0.5px solid {c['border']}; border-radius:4px; font-family:{FONT}; font-size:13px; background:{c['bg']}; color:{c['text']}; }}
  button {{ font-family:{FONT}; }}

  /* 섹션 바로가기 — 화면 위에 고정 */
  .secnav {{ position:sticky; top:0; z-index:30; background:{c['bg']}; display:flex; gap:6px; padding:10px 0 8px; margin-bottom:12px;
             border-bottom:0.5px solid {c['border']}; overflow-x:auto; -webkit-overflow-scrolling:touch; scrollbar-width:none; }}
  .secnav::-webkit-scrollbar {{ display:none; }}
  .secnav a {{ flex:0 0 auto; padding:4px 11px; border-radius:14px; background:{c['panel']}; color:{c['text']}; font-size:12px; font-weight:600; }}
  .secnav a:hover {{ text-decoration:none; background:{c['alert_blue']}; color:{c['info']}; }}
  .secnav a .c {{ color:{c['muted']}; font-weight:400; margin-left:4px; }}
  h2.sec {{ font-size:14px; color:{c['warn']}; margin:20px 0 6px; border-bottom:0.5px solid {c['warn']}; padding-bottom:4px;
            scroll-margin-top:56px; display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }}
  h2.sec .sub {{ font-size:11px; color:{c['muted']}; font-weight:400; }}
  .hint {{ font-size:11px; color:{c['muted']}; margin:0 0 8px; line-height:1.5; }}
  .tgl {{ margin-left:auto; background:none; border:0.5px solid {c['border']}; border-radius:4px; color:{c['muted']}; font-size:11px; padding:2px 8px; cursor:pointer; }}

  /* 관리 패널 */
  #mgmt {{ margin-bottom:8px; background:{c['card']}; border:0.5px solid {c['border']}; border-radius:6px; padding:14px 16px; }}
  .mg-grid {{ display:grid; grid-template-columns:minmax(0,1.6fr) minmax(0,1.2fr) minmax(0,1fr); gap:14px; }}
  .mg {{ min-width:0; }}
  .mg + .mg {{ border-left:0.5px solid {c['border']}; padding-left:14px; }}
  .mg-h {{ font-size:11px; color:{c['muted']}; letter-spacing:0.04em; font-weight:700; margin-bottom:6px; }}
  .mg-row {{ display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }}
  .mg-row input {{ width:140px; text-transform:uppercase; }}
  .mg-d {{ font-size:11px; color:{c['muted']}; line-height:1.5; }}
  .chip {{ background:{c['alert_blue']}; color:{c['info']}; padding:3px 4px 3px 10px; border-radius:12px; font-size:12px; font-weight:600; display:inline-flex; align-items:center; gap:4px; }}
  .chip button {{ background:none; border:0; color:{c['bear']}; cursor:pointer; font-size:14px; padding:0 6px; }}

  /* 한줄평 */
  .ol-list {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(420px,1fr)); gap:6px; }}
  .ol {{ display:grid; grid-template-columns:92px 1fr; gap:4px 10px; align-items:start; background:{c['card']}; border:0.5px solid {c['border']};
         border-radius:6px; padding:8px 12px; color:{c['text']}; text-decoration:none; font-size:12.5px; line-height:1.55; }}
  .ol:hover {{ border-color:{c['warn']}; text-decoration:none; }}
  .ol b {{ font-size:13px; }} .ol .olp {{ display:block; margin-top:3px; white-space:nowrap; }}

  /* 표: 머리글·종목 열 고정 */
  .tbl-wrap {{ max-height:calc(100vh - 96px); overflow:auto; border:0.5px solid {c['border']}; border-radius:6px; -webkit-overflow-scrolling:touch; background:{c['bg']}; }}
  table.dt {{ border-collapse:separate; border-spacing:0; width:100%; font-size:12px; }}
  table.dt th, table.dt td {{ border:0; border-right:0.5px solid {c['border']}; border-bottom:0.5px solid {c['border']}; padding:6px 8px; text-align:left; white-space:nowrap; }}
  table.dt th:last-child, table.dt td:last-child {{ border-right:0; }}
  table.dt thead th {{ position:sticky; top:0; z-index:2; background:{c['panel']}; color:{c['muted']}; font-weight:600; }}
  table.dt thead tr.grp th {{ box-sizing:border-box; height:28px; padding:0 8px; font-size:10.5px; letter-spacing:.03em; text-align:center; color:#57606A; }}
  table.dt thead tr.cols th {{ top:28px; }}
  table.dt tbody tr {{ background:{c['bg']}; }}
  table.dt tbody tr:nth-child(even) {{ background:{c['card']}; }}
  table.dt tbody tr:hover {{ background:{c['alert_blue']}; }}
  table.dt tbody td {{ background:inherit; }}
  table.dt .stk {{ position:sticky; left:0; z-index:1; min-width:84px; box-shadow:6px 0 6px -6px rgba(31,35,40,.28); }}
  table.dt thead th.stk {{ z-index:3; }}
  table.dt .gs {{ border-left:1px solid #AFB8C1; }}
  table.dt th[data-sort] {{ cursor:pointer; user-select:none; }}
  table.dt th[data-sort]:hover {{ color:{c['text']}; }}
  table.dt th .arr {{ font-size:9px; margin-left:3px; color:{c['info']}; }}
  .tk b {{ font-size:13px; }}
  .nm {{ display:block; font-size:10px; color:{c['muted']}; max-width:128px; overflow:hidden; text-overflow:ellipsis; font-weight:400; margin-top:1px; }}
  .up {{ font-weight:700; }}

  .pill {{ padding:1px 8px; border-radius:10px; font-size:10px; font-weight:600; white-space:nowrap; }}
  .pill-A {{ background:{c['alert_green']}; color:{c['bull']}; }}
  .pill-B {{ background:{c['alert_amber']}; color:{c['warn']}; }}
  .pill-C {{ background:{c['alert_red']}; color:{c['bear']}; }}
  .pill-N {{ background:{c['panel']}; color:{c['muted']}; }}
  .q-good {{ background:{c['alert_green']}; color:{c['bull']}; }}
  .q-mid  {{ background:{c['alert_amber']}; color:{c['warn']}; }}
  .q-bad  {{ background:{c['alert_red']}; color:{c['bear']}; }}
  .q-info {{ background:{c['alert_blue']}; color:{c['info']}; }}
  .pattern {{ font-size:9px; background:{c['alert_amber']}; color:{c['warn']}; padding:1px 4px; border-radius:6px; margin-right:2px; }}
  .muted {{ color:{c['muted']}; }}
  .empty {{ padding:28px; text-align:center; color:{c['muted']}; white-space:normal !important; }}
  .act {{ padding:2px 8px; border:0; border-radius:4px; cursor:pointer; font-size:11px; font-weight:700; margin-right:4px; color:#fff; }}
  .act-add {{ background:{c['bull']}; }} .act-rm {{ background:{c['bear']}; }} .act-re {{ background:{c['info']}; }}
  .act:disabled {{ opacity:.5; cursor:default; }}

  @media (max-width:1100px) {{
    .mg-grid {{ grid-template-columns:1fr; }}
    .mg + .mg {{ border-left:0; padding-left:0; border-top:0.5px solid {c['border']}; padding-top:12px; }}
  }}
  @media (max-width:1024px) {{
    .wrap {{ max-width:100% !important; }}
    header {{ flex-direction:column; align-items:flex-start; gap:6px; }}
  }}
  @media (max-width:720px) {{
    body {{ padding:14px !important; font-size:12.5px; }}
    h1 {{ font-size:18px !important; }}
    #mgmt {{ padding:10px 12px !important; }}
    #mgmt input[type="password"] {{ width:100% !important; max-width:320px; }}
    .mg-row input {{ width:120px; }}
    .ol-list {{ grid-template-columns:1fr; }} .ol {{ grid-template-columns:84px 1fr; padding:8px 10px; }}
    .tbl-wrap {{ max-height:none; }}
    table.dt th, table.dt td {{ padding:5px 6px; font-size:11px; }}
    .tk b {{ font-size:12px; }} .nm {{ max-width:92px; }}
    table.dt .stk {{ min-width:70px; }}
    .pill {{ font-size:9px; padding:1px 6px; }}
    .pattern {{ font-size:8px; }}
  }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1><span style="color:{c['info']};">S</span>mart <span style="color:{c['info']};">M</span>oney <span style="color:{c['info']};">A</span>nalyzer</h1>
    <div class="meta">{E(meta)}</div>
  </div>
  <div>
    <a href="https://github.com/z66g/sma" target="_blank" rel="noopener" aria-label="GitHub" title="GitHub" style="color:{c['muted']};display:inline-flex;align-items:center;">
      <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M12 .5C5.73.5.75 5.48.75 11.75c0 4.98 3.23 9.2 7.71 10.7.56.1.77-.24.77-.54 0-.27-.01-1.16-.02-2.1-3.14.68-3.8-1.34-3.8-1.34-.51-1.3-1.25-1.65-1.25-1.65-1.03-.7.08-.69.08-.69 1.13.08 1.73 1.16 1.73 1.16 1.01 1.73 2.65 1.23 3.3.94.1-.73.4-1.23.72-1.51-2.5-.28-5.14-1.25-5.14-5.57 0-1.23.44-2.23 1.16-3.02-.12-.29-.5-1.43.11-2.98 0 0 .94-.3 3.09 1.15.9-.25 1.86-.37 2.82-.38.96.01 1.92.13 2.82.38 2.15-1.45 3.09-1.15 3.09-1.15.61 1.55.23 2.69.11 2.98.72.79 1.16 1.79 1.16 3.02 0 4.33-2.64 5.29-5.15 5.56.4.35.77 1.04.77 2.1 0 1.52-.01 2.75-.01 3.13 0 .3.2.65.78.54 4.47-1.5 7.7-5.72 7.7-10.7C23.25 5.48 18.27.5 12 .5z"/>
      </svg>
    </a>
  </div>
</header>

<nav class="secnav" aria-label="섹션 바로가기">
  <a href="#sec-ol" id="nav-ol">한줄평</a>
  <a href="#sec-active">관심종목<span class="c" id="nav-active-c"></span></a>
  <a href="#sec-review">검토 종목<span class="c" id="nav-review-c"></span></a>
  <a href="#sec-archive" id="nav-archive">히스토리<span class="c" id="nav-archive-c"></span></a>
  <a href="#mgmt">관리</a>
</nav>

<div id="mgmt">
  <div id="mgmt-need-token" style="display:none;">
    <div style="margin-bottom:8px;font-size:12px;color:{c['muted']};line-height:1.6;">
      관심종목·검토 종목 편집과 분석 요청에는 GitHub 토큰이 필요합니다. 토큰은 <b>이 브라우저 localStorage 에만</b> 저장되며 repo 에 커밋되거나 다른 사이트로 전송되지 않습니다.<br>
      권장: <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">Fine-grained PAT</a> —
      Repository access: <b>Only select repositories → z66g/sma</b>, Permissions: <b>Contents: Read and write</b> + <b>Actions: Read and write</b>, 만료일 설정.
      (repo 전체 권한을 주는 Classic PAT 은 권장하지 않음)
    </div>
    <input id="pat" type="password" placeholder="github_pat_…" autocomplete="off" style="width:360px;max-width:100%;">
    <button id="pat-save" style="padding:6px 14px;background:{c['info']};color:#fff;border:0;border-radius:4px;cursor:pointer;">저장</button>
    <button id="pat-clear" style="padding:6px 10px;background:none;color:{c['muted']};border:0.5px solid {c['border']};border-radius:4px;cursor:pointer;">지우기</button>
  </div>

  <div id="mgmt-panel" style="display:none;">
    <div class="mg-grid">
      <div class="mg">
        <div class="mg-h">관심종목 — 화~토 오전(KST) 자동 수급 분석 · 직전 미국 정규장 세션 (휴장일 자동 건너뜀)</div>
        <div class="mg-row">
          <input id="new-ticker" type="text" placeholder="티커 (예: PLTR)" autocomplete="off">
          <button id="add-btn" style="{btn}">추가</button>
          <button id="run-btn" style="{btn}">리스트 전체 분석</button>
        </div>
        <div id="wl-list" style="display:flex;gap:6px;flex-wrap:wrap;"></div>
      </div>
      <div class="mg">
        <div class="mg-h">적정가 분석 — 관심종목에 넣기 전에 먼저</div>
        <div class="mg-row">
          <input id="rv-ticker" type="text" placeholder="티커 (예: NVDA)" autocomplete="off">
          <button id="rv-btn" style="{btn}background:{c['info']};color:#fff;border-color:{c['info']};">분석 요청</button>
        </div>
        <div class="mg-d">검토 종목에 추가되고 약 2분 뒤 분석 세션이 시작됩니다. 신규 조사는 보통 20~40분 걸리고, 끝나면 아래 '검토 종목' 표에 적정가가 나타납니다.
          매일 수급 분석은 하지 않습니다 — 마음에 들면 표에서 <b>편입</b>.</div>
      </div>
      <div class="mg">
        <div class="mg-h">단일 종목 즉시 수급 분석</div>
        <div class="mg-row">
          <input id="adhoc-ticker" type="text" placeholder="티커 (예: PLTR)" autocomplete="off">
          <button id="adhoc-btn" style="{btn}">개별분석</button>
        </div>
        <div class="mg-d">관심종목 여부와 상관없이 직전 세션 3-Layer 리포트를 한 번 만듭니다 (1~3분).</div>
        <button id="pat-edit" style="margin-top:8px;padding:3px 10px;background:none;color:{c['muted']};border:0.5px solid {c['border']};border-radius:4px;cursor:pointer;font-size:11px;">PAT 변경</button>
      </div>
    </div>
    <div id="mgmt-status" style="font-size:11px;color:{c['muted']};min-height:16px;margin-top:10px;"></div>
  </div>
</div>

<h2 class="sec" id="sec-ol">▶ 종목별 한줄평 <span class="sub">{E(latest_session)} 세션</span><button class="tgl" id="ol-tgl" type="button">접기</button></h2>
<div id="ol-list" class="ol-list"></div>

<h2 class="sec" id="sec-active">▶ 관심종목 <span class="sub" id="active-count"></span></h2>
<div class="hint">종목 열은 가로로 스크롤해도 고정됩니다. 열 제목을 누르면 정렬하고, 한 번 더 누르면 반대로 정렬합니다.</div>
<div class="tbl-wrap">
<table id="tbl-active" class="dt">
  {TABLE_HEAD}
  <tbody id="tbody-active"></tbody>
</table>
</div>

<h2 class="sec" id="sec-review">▶ 검토 종목 <span class="sub" id="review-count"></span>
  <span class="sub">— 적정가만 먼저 보는 종목 · 분석 후 4주까지 매주 재계산</span></h2>
<div class="tbl-wrap">
<table id="tbl-review" class="dt">
  {REVIEW_HEAD}
  <tbody id="tbody-review"></tbody>
</table>
</div>

<details id="archive-wrap" style="margin-top:24px;">
<summary id="sec-archive" style="cursor:pointer;font-size:14px;font-weight:700;color:{c['muted']};border-bottom:0.5px solid {c['border']};padding-bottom:4px;scroll-margin-top:56px;">히스토리 (Archive) <span id="archive-count" style="font-size:11px;font-weight:400;"></span></summary>
<div class="tbl-wrap" style="margin-top:8px;">
<table id="tbl-archive" class="dt">
  {TABLE_HEAD}
  <tbody id="tbody-archive"></tbody>
</table>
</div>
</details>
</div>
<script>
window.__DB__ = {_js(summary)};
window.__REVIEW__ = {_js(review)};
window.__KO__ = {_js({**C.KO_SCEN, **C.KO_CASE, **C.KO_ENV, **C.KO_PAT})};
</script>
<script src="app.js"></script>
</body>
</html>
"""
    return html_out, APP_JS


APP_JS = r"""
// Dashboard logic — window.__DB__ (관심종목·히스토리 요약), window.__REVIEW__ (검토 종목)
const DB = window.__DB__ || [];
let REVIEW = (window.__REVIEW__ || []).map(r => Object.assign({}, r));
const KO = window.__KO__ || {};
const COLS = 23, RCOLS = 12;
const $ = id => document.getElementById(id);
const bodyActive = $('tbody-active'), bodyArchive = $('tbody-archive'), bodyReview = $('tbody-review');
const archiveWrap = $('archive-wrap');

// ── 안전한 문자열 처리 ──
function esc(v) {
  return v == null ? '' : String(v).replace(/[&<>"']/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function isNum(v) { return typeof v === 'number' && isFinite(v); }
function safePath(p) { return (typeof p === 'string' && /^[\w.\-\/]+$/.test(p) && !p.includes('..')) ? p : null; }
const NA = '<span class="muted">N/A</span>';
const DASH = '<span class="muted">—</span>';
function lsGet(k) { try { return localStorage.getItem(k) || ''; } catch (_) { return ''; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (_) {} }
function lsDel(k) { try { localStorage.removeItem(k); } catch (_) {} }

function fmtPct(v, dec) { return isNum(v) ? v.toFixed(dec == null ? 1 : dec) + '%' : NA; }
function fmtDol(v) { return isNum(v) ? '$' + v.toFixed(2) : NA; }
function fmtSigned(v, dec) { return isNum(v) ? (v > 0 ? '+' : '') + v.toFixed(dec == null ? 1 : dec) : ''; }
function scenarioPill(s, v) {
  if (!s) return `<span class="pill pill-N">N/A</span>`;
  const lbl = { A: '[A] Bull', B: '[B] Neut', C: '[C] Bear' }[s] || s;
  return `<span class="pill pill-${esc(s)}">${esc(lbl)} ${isNum(v) ? v.toFixed(0) + '%' : ''}</span>`;
}
function qualityPill(r) {
  if (!r.quality_grade) return `<span class="pill pill-N">${r.legacy ? 'v2' : 'N/A'}</span>`;
  const g = String(r.quality_grade).toUpperCase().charAt(0);
  const cls = (g === 'A' || g === 'B') ? 'q-good' : (g === 'C' ? 'q-mid' : 'q-bad');
  const title = (r.quality_missing || []).join('; ') || '모든 레이어 OK';
  const warn = r.warnings_count ? ` · ⚠${r.warnings_count}` : '';
  return `<span class="pill ${cls}" title="${esc(title)}">${esc(r.quality_grade)}${isNum(r.quality_score) ? ' ' + r.quality_score.toFixed(0) : ''}${esc(warn)}</span>`;
}
function verdictColor(v) { v = v || ''; return v.indexOf('UNDER') >= 0 ? '#1A7F5A' : v.indexOf('OVER') >= 0 ? '#CF222B' : '#656D76'; }
function verdictPill(r) {
  if (!isNum(r.fair_value) || !r.fair_verdict_ko) return DASH;
  const v = r.fair_verdict || '';
  const cls = v.indexOf('UNDER') >= 0 ? 'q-good' : v.indexOf('OVER') >= 0 ? 'q-bad' : 'pill-N';
  return `<span class="pill ${cls}" title="${esc(r.fair_unc ? '불확실성 ' + r.fair_unc : '')}">${esc(r.fair_verdict_ko)}</span>`;
}
function fairTitle(r) {
  return [r.fair_type, r.fair_verdict_ko, r.fair_unc ? '불확실성 ' + r.fair_unc : '',
    (isNum(r.fair_low) && isNum(r.fair_high)) ? '범위 $' + r.fair_low.toFixed(2) + '~$' + r.fair_high.toFixed(2) : '',
    r.fair_method === 'RESEARCH' ? '리서치 ' + (r.fair_research || '') + (r.fair_stale ? ' (갱신 필요)' : '') : ''].filter(Boolean).join(' · ');
}
function fairCell(r) {
  if (!isNum(r.fair_value)) return DASH;
  const url = safePath(r.val_url);
  const body = fmtDol(r.fair_value) + (r.fair_stale ? ' <span title="리서치 갱신 필요" style="color:#9A6700;">⏳</span>' : '');
  return url ? `<a href="${esc(url)}" title="${esc(fairTitle(r))}">${body}</a>` : `<span title="${esc(fairTitle(r))}">${body}</span>`;
}
function upsideCell(r) {
  if (!isNum(r.fair_value) || !isNum(r.fair_upside)) return DASH;
  const base = isNum(r.fair_base_price) && isNum(r.price) && Math.abs(r.fair_base_price - r.price) > 0.005
    ? `세션 주가가 오래되어 발행 시점 주가 $${r.fair_base_price.toFixed(2)} 기준` : '현재가 대비';
  return `<span class="up" style="color:${verdictColor(r.fair_verdict)};" title="${esc(base)}">${fmtSigned(r.fair_upside, 0)}%</span>`
    + (base !== '현재가 대비' ? ' <span class="muted" title="' + esc(base) + '">ⓘ</span>' : '')
    + (r.fair_gap ? ' <span title="시장 가격과 3배 이상 괴리 — 상세 페이지의 전제 조건 참고" style="color:#9A6700;">⚠</span>' : '');
}
function fairChip(r) {
  if (!isNum(r.fair_value) || !isNum(r.fair_upside)) return '';
  const px = '$' + (r.fair_value >= 100 ? r.fair_value.toFixed(0) : r.fair_value.toFixed(2));
  return `<span class="olp" style="font-size:10.5px;color:#656D76;" title="펀더멘털 적정가 ${esc(fmtDol(r.fair_value))} · ${esc(r.fair_verdict_ko || '')}">적정 ${px} `
    + `<b style="color:${verdictColor(r.fair_verdict)};font-size:10.5px;">${fmtSigned(r.fair_upside, 0)}%</b></span>`;
}
function casePretty(c) { return c ? esc(KO[c] || String(c).replace(/^CASE_\d+_/, '').replace(/^CASE_/, '')) : NA; }
function txt(v) { return v ? esc(KO[v] || v) : NA; }
function tickerCell(t, href, name, title) {
  const h = safePath(href);
  const label = `<b>${esc(t)}</b>`;
  return `<td class="stk">${h ? `<a class="tk" href="${esc(h)}" title="${esc(title || '')}">${label}</a>` : `<span class="tk">${label}</span>`}`
    + (name ? `<span class="nm" title="${esc(name)}">${esc(name)}</span>` : '') + `</td>`;
}

function rowHTML(r, mode) {
  const t = esc(r.ticker), tq = encodeURIComponent(r.ticker);
  const patBadges = (r.patterns || []).map(p => `<span class="pattern">${esc(KO[p] || p)}</span>`).join('') || '-';
  const url = safePath(r.report_url);
  const dateCell = url ? `<a href="${esc(url)}">${esc(r.latest_date)}</a>` : esc(r.latest_date || 'N/A');
  const ctb = isNum(r.ctb_fee) ? r.ctb_fee.toFixed(2) + '%' + (isNum(r.ctb_delta_pct) ? ` <span class="muted">(${fmtSigned(r.ctb_delta_pct)}%)</span>` : '') : NA;
  const walls = (isNum(r.call_wall) || isNum(r.put_wall)) ? `${fmtDol(r.call_wall)} / ${fmtDol(r.put_wall)}` : NA;
  let actionBtn = '';
  if (mode === 'archive') {
    actionBtn = `<button class="act act-add add-wl-btn" data-t="${t}" title="관심종목에 추가">＋</button>`;
  } else if (mode === 'active') {
    actionBtn = `<button class="act act-rm rm-wl-btn" data-t="${t}" title="관심종목에서 제거 (적정가가 있으면 검토 종목으로 이동)">×</button>`;
  }
  return `<tr>
    ${tickerCell(r.ticker, 'tickers/' + tq + '/', r.name, r.one_liner || r.headline || '')}
    <td class="gs">${fmtDol(r.price)}</td>
    <td>${fairCell(r)}</td>
    <td>${upsideCell(r)}</td>
    <td>${verdictPill(r)}</td>
    <td class="gs">${scenarioPill(r.scenario, r.scenario_pct)}</td>
    <td>${isNum(r.raw_score) ? r.raw_score.toFixed(2) : NA}</td>
    <td>${dateCell}</td>
    <td>${qualityPill(r)}</td>
    <td class="gs">${txt(r.macro_env)}</td>
    <td>${txt(r.l1_scenario)}</td>
    <td>${casePretty(r.l2_case)}</td>
    <td>${txt(r.l3_scenario)}</td>
    <td class="gs">${fmtPct(r.dp_pct)}</td>
    <td>${fmtPct(r.short_pct)}</td>
    <td>${ctb}</td>
    <td>${isNum(r.dtc) ? r.dtc.toFixed(1) + 'd' : NA}</td>
    <td>${isNum(r.exp_move_pct) ? '±' + r.exp_move_pct.toFixed(1) + '%' : NA}</td>
    <td class="gs">${fmtDol(r.max_pain)}</td>
    <td>${walls}</td>
    <td class="gs">${patBadges}</td>
    <td><a href="tickers/${tq}/" style="font-weight:600;">${esc(r.report_count)}</a></td>
    <td>${actionBtn}</td>
  </tr>`;
}

// ── 검토 종목 ──
const ST = { PENDING: ['분석 대기', 'q-mid'], FRESH: ['완료', 'q-good'], EXPIRED: ['오래됨', 'pill-N'] };
function ts(s) { const d = s ? new Date(String(s).length === 10 ? s + 'T00:00:00Z' : s) : null; return d && !isNaN(d) ? d : null; }
function reviewStatus(r) {
  if (!isNum(r.fair_value)) return 'PENDING';
  const req = ts(r.requested_at), pub = ts(r.published_at);
  if (req && (!pub || req > pub)) return 'PENDING';
  if (isNum(r.age_days) && r.age_days > 28) return 'EXPIRED';
  return 'FRESH';
}
function kst(s) {
  const d = ts(s); if (!d) return '';
  const k = new Date(d.getTime() + 9 * 3600e3);
  return `${k.getUTCMonth() + 1}/${k.getUTCDate()} ${String(k.getUTCHours()).padStart(2, '0')}:${String(k.getUTCMinutes()).padStart(2, '0')} KST`;
}
function reviewRowHTML(r) {
  const t = esc(r.ticker);
  const st = reviewStatus(r);
  const [lbl, cls] = ST[st];
  const stTitle = st === 'PENDING' ? (r.requested_at ? '분석 요청 ' + kst(r.requested_at) + ' — 조사가 끝나면 적정가가 나타납니다' : '리서치 없음 — 다음 분석 실행에서 조사')
    : st === 'EXPIRED' ? '분석 후 4주 경과 — 매주 재계산을 멈췄습니다. 다시 보려면 재분석' : '분석 후 4주까지 매주 주가·금리 반영';
  const url = safePath(r.val_url);
  const range = (isNum(r.fair_low) && isNum(r.fair_high)) ? `${fmtDol(r.fair_low)} ~ ${fmtDol(r.fair_high)}` : DASH;
  const conf = r.confidence ? `<span class="pill ${{HIGH: 'q-good', MEDIUM: 'q-info', LOW: 'q-mid'}[String(r.confidence).toUpperCase()] || 'pill-N'}">${esc(r.confidence)}</span>` : DASH;
  return `<tr>
    ${tickerCell(r.ticker, url, r.name, '')}
    <td><span class="pill ${cls}" title="${esc(stTitle)}">${esc(lbl)}</span>${st === 'PENDING' && r.requested_at ? ` <span class="muted" style="font-size:10px;">${esc(kst(r.requested_at))}</span>` : ''}</td>
    <td title="${esc(r.price_date ? r.price_date + ' 기준' : '')}">${isNum(r.price) ? fmtDol(r.price) : DASH}</td>
    <td>${isNum(r.fair_value) ? (url ? `<a href="${esc(url)}">${fmtDol(r.fair_value)}</a>` : fmtDol(r.fair_value)) : DASH}</td>
    <td>${upsideCell(r)}</td>
    <td>${verdictPill(r)}</td>
    <td>${range}</td>
    <td>${r.fair_unc ? esc(r.fair_unc) : DASH}</td>
    <td>${conf}${r.audit ? ` <span class="pill q-info" title="독립 감사 ${esc(r.audit)}">감사</span>` : ''}</td>
    <td>${r.research_as_of ? esc(r.research_as_of) : DASH}</td>
    <td>${url ? `<a href="${esc(url)}">보기</a>` : DASH}</td>
    <td style="white-space:nowrap;">
      <button class="act act-add rv-promote" data-t="${t}" title="관심종목으로 편입 (매일 수급 분석 시작)" ${isNum(r.fair_value) ? '' : 'disabled'}>편입</button><button
        class="act act-re rv-again" data-t="${t}" title="다시 조사 (최신 공시·실적 반영)" ${st === 'PENDING' ? 'disabled' : ''}>재분석</button><button
        class="act act-rm rv-del" data-t="${t}" title="검토 종목에서 삭제 (발행 파일은 남음)">삭제</button>
    </td>
  </tr>`;
}

// ── 정렬 (열 제목 클릭) ──
const SORT_KEYS = {
  ticker: r => r.ticker, price: r => r.price, fair_value: r => r.fair_value, fair_upside: r => r.fair_upside,
  fair_verdict: r => ({ DEEP_UNDERVALUED: 5, UNDERVALUED: 4, FAIR: 3, OVERVALUED: 2, DEEP_OVERVALUED: 1 })[r.fair_verdict] ?? null,
  scenario: r => r.scenario ? ({ A: 3, B: 2, C: 1 })[r.scenario] * 1000 + (r.scenario_pct || 0) : null,
  raw_score: r => r.raw_score, latest_date: r => r.latest_date, quality_score: r => r.quality_score,
  dp_pct: r => r.dp_pct, short_pct: r => r.short_pct, ctb_fee: r => r.ctb_fee, dtc: r => r.dtc, exp_move_pct: r => r.exp_move_pct,
  max_pain: r => r.max_pain, report_count: r => r.report_count,
  status: r => ({ PENDING: 0, FRESH: 1, EXPIRED: 2 })[reviewStatus(r)], research_as_of: r => r.research_as_of,
};
const sortState = {};
function loadSort(id) { try { return JSON.parse(lsGet('sma_sort_' + id) || 'null') || { key: 'ticker', dir: 1 }; } catch (_) { return { key: 'ticker', dir: 1 }; } }
function sorted(rows, id) {
  const s = sortState[id] || (sortState[id] = loadSort(id));
  const f = SORT_KEYS[s.key] || SORT_KEYS.ticker;
  return rows.slice().sort((a, b) => {
    const x = f(a), y = f(b);
    const nx = x == null || x === '', ny = y == null || y === '';
    if (nx || ny) return nx && ny ? a.ticker.localeCompare(b.ticker) : (nx ? 1 : -1);      // 값 없는 행은 항상 아래
    const c = typeof x === 'string' ? x.localeCompare(y) : x - y;
    return (c || a.ticker.localeCompare(b.ticker) * s.dir) * s.dir;
  });
}
function markSort(table) {
  const s = sortState[table.id] || loadSort(table.id);
  table.querySelectorAll('th[data-sort]').forEach(th => {
    const old = th.querySelector('.arr'); if (old) old.remove();
    if (th.dataset.sort === s.key) th.insertAdjacentHTML('beforeend', `<span class="arr">${s.dir > 0 ? '▲' : '▼'}</span>`);
  });
}
document.querySelectorAll('table.dt').forEach(table => {
  table.querySelector('thead').addEventListener('click', e => {
    const th = e.target.closest('th[data-sort]'); if (!th) return;
    const s = sortState[table.id] || loadSort(table.id);
    const key = th.dataset.sort;
    sortState[table.id] = { key, dir: s.key === key ? -s.dir : (key === 'ticker' || key === 'status' ? 1 : -1) };
    lsSet('sma_sort_' + table.id, JSON.stringify(sortState[table.id]));
    render();
  });
});

// ── 한줄평 접기 ──
const olList = $('ol-list'), olHead = $('sec-ol'), olTgl = $('ol-tgl');
function applyOl() {
  const closed = lsGet('sma_ol_closed') === '1';
  olList.style.display = closed ? 'none' : '';
  olTgl.textContent = closed ? '펼치기' : '접기';
}
olTgl.addEventListener('click', () => { lsGet('sma_ol_closed') === '1' ? lsDel('sma_ol_closed') : lsSet('sma_ol_closed', '1'); applyOl(); });

function render() {
  const active = DB.filter(r => r.is_active), archive = DB.filter(r => !r.is_active);
  const rv = REVIEW;
  $('active-count').textContent = `· ${active.length}개`;
  $('archive-count').textContent = `· ${archive.length}개`;
  $('review-count').textContent = `· ${rv.length}개`;
  $('nav-active-c').textContent = active.length;
  $('nav-review-c').textContent = rv.length;
  $('nav-archive-c').textContent = archive.length;
  bodyActive.innerHTML = active.length
    ? sorted(active, 'tbl-active').map(r => rowHTML(r, 'active')).join('')
    : `<tr><td colspan="${COLS}" class="empty">관심종목이 없습니다. 위 관리 패널에서 추가하세요.</td></tr>`;
  bodyArchive.innerHTML = archive.length
    ? sorted(archive, 'tbl-archive').map(r => rowHTML(r, 'archive')).join('')
    : `<tr><td colspan="${COLS}" class="empty">아카이브 비어있음.</td></tr>`;
  bodyReview.innerHTML = rv.length
    ? sorted(rv, 'tbl-review').map(reviewRowHTML).join('')
    : `<tr><td colspan="${RCOLS}" class="empty">검토 종목이 없습니다. 관리 패널의 '적정가 분석'에 티커를 넣거나, 채팅 세션에서 "티커 분석해줘"라고 요청하세요.</td></tr>`;
  archiveWrap.style.display = archive.length ? '' : 'none';
  $('nav-archive').style.display = archive.length ? '' : 'none';
  document.querySelectorAll('table.dt').forEach(markSort);
  const ols = active.filter(r => r.one_liner);
  olList.innerHTML = ols.map(r => {
    const u = safePath(r.report_url);
    return `<a class="ol" href="${u ? esc(u) : 'tickers/' + encodeURIComponent(r.ticker) + '/'}"><div><b>${esc(r.ticker)}</b>`
      + `<span class="olp">${scenarioPill(r.scenario, r.scenario_pct)}</span>${fairChip(r)}</div><div>${esc(r.one_liner)}</div></a>`;
  }).join('');
  olHead.style.display = ols.length ? '' : 'none';
  $('nav-ol').style.display = ols.length ? '' : 'none';
  applyOl();
}

render();

// ───── 관심종목·검토 종목 관리 (GitHub API) ─────
// PAT 는 이 브라우저 localStorage 에만 저장된다 (repo/다른 사이트로 전송 X).
// 권장: z66g/sma 한 저장소로 범위를 제한한 Fine-grained PAT (Contents RW + Actions RW).
const REPO = 'z66g/sma';
const API  = 'https://api.github.com';
const LS_KEY = 'sma_github_pat';
const TICKER_RE = /^[A-Z][A-Z0-9.\-]{0,9}$/;

const mgmtNeedToken = $('mgmt-need-token'), mgmtPanel = $('mgmt-panel');
const patInput = $('pat'), newTickerEl = $('new-ticker'), wlList = $('wl-list'), statusEl = $('mgmt-status');

let ticksCache = [];
let txtSha = null;
let reviewEntries = null;      // [{ticker, requested_at}] — GitHub 에서 읽은 review.txt (null = 아직 못 읽음)
let reviewSha = null;

function getPAT() { return lsGet(LS_KEY); }
function showPanel(hasToken) {
  mgmtNeedToken.style.display = hasToken ? 'none' : 'block';
  mgmtPanel.style.display     = hasToken ? 'block' : 'none';
}
function setStatus(msg, isError) {
  statusEl.textContent = msg || '';
  statusEl.style.color = isError ? '#CF222B' : '#656D76';
}
// 신뢰된 HTML 조각 전용 — 호출부에서 모든 동적 값을 esc() 해야 함
function setStatusHTML(html, isError) {
  statusEl.innerHTML = html || '';
  statusEl.style.color = isError ? '#CF222B' : '#656D76';
}
function needPAT() {
  if (getPAT()) return false;
  setStatus('GitHub 토큰(PAT)이 필요합니다 — 관리 패널에서 저장하세요', true);
  document.getElementById('mgmt').scrollIntoView({ behavior: 'smooth' });
  return true;
}

async function gh(path, opts={}) {
  const pat = getPAT();
  if (!pat) throw new Error('PAT 없음');
  const res = await fetch(API + path, {
    ...opts,
    headers: {
      'Accept': 'application/vnd.github+json',
      'Authorization': 'Bearer ' + pat,
      'X-GitHub-Api-Version': '2022-11-28',
      ...(opts.headers || {})
    }
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const e = new Error(`GitHub ${res.status}: ${err.message || res.statusText}`);
    e.status = res.status;
    throw e;
  }
  return res.status === 204 ? null : res.json();
}
function b64decode(s) { return decodeURIComponent(escape(atob(s.replace(/\n/g, '')))); }
function b64encode(s) { return btoa(unescape(encodeURIComponent(s))); }
function parseTickers(text) {
  return text.split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('#'));
}
function parseReview(text) {
  const out = [], seen = new Set();
  for (const line of text.split('\n')) {
    const s = line.trim(); if (!s || s.startsWith('#')) continue;
    const p = s.split(/\s+/), t = p[0].toUpperCase();
    if (!TICKER_RE.test(t) || seen.has(t)) continue;
    seen.add(t);
    out.push({ ticker: t, requested_at: (p[1] && /^\d{4}-\d{2}-\d{2}(T[\d:.]+Z?)?$/.test(p[1])) ? p[1] : null });
  }
  return out;
}

async function loadTickers() {
  setStatus('tickers.txt 로드 중...');
  const j = await gh(`/repos/${REPO}/contents/tickers.txt?ref=main&_=${Date.now()}`);
  txtSha = j.sha;
  ticksCache = parseTickers(b64decode(j.content));
  const active = new Set(ticksCache);
  let flipped = 0;
  for (const r of DB) {
    const shouldBeActive = active.has(r.ticker);
    if (r.is_active !== shouldBeActive) { r.is_active = shouldBeActive; flipped++; }
  }
  renderWL();
  if (flipped > 0) render();
  setStatus(`관심종목 ${ticksCache.length}개 · last commit ${String(j.sha).slice(0,7)}` + (flipped ? ` · ${flipped}개 행 동기화` : ''));
}
async function loadReview() {
  try {
    const j = await gh(`/repos/${REPO}/contents/review.txt?ref=main&_=${Date.now()}`);
    reviewSha = j.sha;
    reviewEntries = parseReview(b64decode(j.content));
  } catch (e) {
    if (e.status !== 404) throw e;
    reviewSha = null; reviewEntries = [];
  }
  syncReviewRows();
}
// 화면의 검토 종목 표를 저장소의 review.txt 에 맞춘다 (빌드 이후의 요청·삭제 반영)
function syncReviewRows() {
  if (!reviewEntries) return;
  const byT = new Map(REVIEW.map(r => [r.ticker, r]));
  const wl = new Set(ticksCache);
  REVIEW = reviewEntries.filter(e => !wl.has(e.ticker)).map(e => Object.assign({ ticker: e.ticker }, byT.get(e.ticker) || {}, { requested_at: e.requested_at }));
  render();
}

function renderWL() {
  if (!ticksCache.length) {
    wlList.innerHTML = '<span style="color:#656D76;font-size:11px;">관심종목 없음</span>';
    return;
  }
  wlList.innerHTML = ticksCache.map(t =>
    `<span class="chip">${esc(t)}<button data-t="${esc(t)}" class="rm-btn" title="관심종목에서 제거">×</button></span>`
  ).join('');
  wlList.querySelectorAll('.rm-btn').forEach(b => b.addEventListener('click', () => removeTicker(b.dataset.t)));
}

function buildFileContent() {
  const header = [
    '# Smart Money Analyzer — Watchlist',
    '# Managed via dashboard UI (reports/index.html).',
    '# 한 줄에 티커 하나. #으로 시작하는 줄은 주석.',
    ''
  ];
  return header.concat(ticksCache).join('\n') + '\n';
}
function buildReviewContent() {
  const header = [
    '# Smart Money Analyzer — 검토 종목 (적정가 먼저 보는 종목)',
    '# 대시보드에서 관리. 한 줄에 `TICKER [분석 요청 시각(UTC ISO)]`. #으로 시작하는 줄은 주석.',
    '# 관심종목(tickers.txt)과 달리 매일 수급 분석을 하지 않는다. 적정가만 발행·재계산한다.',
    ''
  ];
  const rows = reviewEntries.slice().sort((a, b) => a.ticker.localeCompare(b.ticker)).map(e => e.ticker + (e.requested_at ? ' ' + e.requested_at : ''));
  return header.concat(rows).join('\n') + '\n';
}

async function commitTickers(message) {
  setStatus('커밋 중...');
  const j = await gh(`/repos/${REPO}/contents/tickers.txt`, {
    method: 'PUT',
    body: JSON.stringify({ message, content: b64encode(buildFileContent()), sha: txtSha })
  });
  txtSha = j.content.sha;
  setStatus(`✅ 커밋 완료: ${message} (${String(txtSha).slice(0,7)})`);
}
async function commitReview(message) {
  setStatus('커밋 중...');
  const body = { message, content: b64encode(buildReviewContent()) };
  if (reviewSha) body.sha = reviewSha;
  const j = await gh(`/repos/${REPO}/contents/review.txt`, { method: 'PUT', body: JSON.stringify(body) });
  reviewSha = j.content.sha;
}
async function ensureLoaded() {
  if (!txtSha) await loadTickers();
  if (reviewEntries === null) await loadReview();
}
// 같은 파일을 다른 곳(다른 기기·예약 작업)에서 먼저 바꿨으면 409/422 — 최신본을 다시 읽고 한 번 더 시도한다
async function withRetry(fn) {
  try { return await fn(); }
  catch (e) {
    if (e.status !== 409 && e.status !== 422) throw e;
    txtSha = null; reviewEntries = null;
    await ensureLoaded();
    return await fn();
  }
}

async function addTicker() {
  const t = (newTickerEl.value || '').trim().toUpperCase();
  if (!t) return;
  if (!TICKER_RE.test(t)) { setStatus(`"${t}"는 유효한 티커 형식이 아닙니다`, true); return; }
  try {
    await ensureLoaded();
    if (ticksCache.includes(t)) { setStatus(`${t}는 이미 관심종목입니다`, true); return; }
    await promote(t, 'add');
    newTickerEl.value = '';
  } catch (e) { setStatus('실패: ' + e.message, true); }
}

// 관심종목으로 (검토 종목에 있으면 거기서 뺀다)
async function promote(t, how) {
  await withRetry(async () => {
    if (!ticksCache.includes(t)) {
      ticksCache.push(t); ticksCache.sort();
      try { await commitTickers(`watchlist: ${how === 'review' ? 'promote ' + t + ' from review' : how === 'archive' ? 'promote ' + t + ' from archive' : 'add ' + t}`); }
      catch (e) { ticksCache = ticksCache.filter(x => x !== t); throw e; }
    }
  });
  if (reviewEntries && reviewEntries.some(e => e.ticker === t)) {
    await withRetry(async () => {
      reviewEntries = reviewEntries.filter(e => e.ticker !== t);
      await commitReview(`review: ${t} moved to watchlist`);
    });
  }
  const row = DB.find(x => x.ticker === t);
  if (row) row.is_active = true;
  REVIEW = REVIEW.filter(r => r.ticker !== t);
  renderWL(); render();
  setStatus(`✅ ${t} 관심종목에 추가 — 다음 화~토 오전 자동 수급 분석부터 표에 나타납니다`
    + (row || how === 'review' ? '' : ' (적정가 리서치가 없으면 곧 조사가 시작됩니다)'));
}

async function removeTicker(t) {
  const row = DB.find(x => x.ticker === t);
  const hasFair = (row && isNum(row.fair_value)) || REVIEW.some(r => r.ticker === t && isNum(r.fair_value));
  if (!confirm(`${t} 를 관심종목에서 제거할까요?\n매일 수급 분석에서 빠집니다.` + (hasFair ? '\n적정가가 있으므로 검토 종목으로 옮겨 계속 볼 수 있습니다.' : ''))) return;
  try {
    await ensureLoaded();
    await withRetry(async () => {
      const prev = ticksCache.slice();
      ticksCache = ticksCache.filter(x => x !== t);
      try { await commitTickers(`watchlist: remove ${t}`); } catch (e) { ticksCache = prev; throw e; }
    });
    if (hasFair && !reviewEntries.some(e => e.ticker === t)) {
      await withRetry(async () => {
        reviewEntries = reviewEntries.concat([{ ticker: t, requested_at: null }]);
        await commitReview(`review: ${t} moved from watchlist`);
      });
      if (row && !REVIEW.some(r => r.ticker === t)) {
        REVIEW.push({ ticker: t, name: row.name, price: row.price, fair_value: row.fair_value, fair_low: row.fair_low, fair_high: row.fair_high,
          fair_upside: row.fair_upside, fair_verdict: row.fair_verdict, fair_verdict_ko: row.fair_verdict_ko, fair_unc: row.fair_unc,
          research_as_of: row.fair_research, val_url: row.val_url, requested_at: null, published_at: null, age_days: null });
      }
    }
    if (row) row.is_active = false;
    renderWL(); render();
    setStatus(`✅ ${t} 관심종목에서 제거` + (hasFair ? ' — 검토 종목으로 이동' : ''));
  } catch (e) { setStatus('실패: ' + e.message, true); }
}

// 적정가 분석 요청 (신규 또는 재분석): review.txt 에 요청 시각을 적는다 → 워크플로가 조사 세션을 깨운다
async function requestAnalysis(t, again) {
  if (!TICKER_RE.test(t)) { setStatus(`"${t}"는 유효한 티커 형식이 아닙니다`, true); return false; }
  await ensureLoaded();
  if (ticksCache.includes(t)) { setStatus(`${t}는 관심종목입니다 — 적정가는 매주 자동으로 갱신됩니다`, true); return false; }
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
  await withRetry(async () => {
    reviewEntries = reviewEntries.filter(e => e.ticker !== t).concat([{ ticker: t, requested_at: now }]);
    await commitReview(`review: ${again ? 're-analyze' : 'analyze'} ${t}`);
  });
  const r = REVIEW.find(x => x.ticker === t);
  if (r) r.requested_at = now; else REVIEW.push({ ticker: t, requested_at: now });
  render();
  setStatusHTML(`✅ ${esc(t)} ${again ? '재분석' : '적정가 분석'} 요청 — 약 2분 뒤 분석 세션이 시작됩니다. `
    + `${again ? '갱신은 보통 10~20분' : '신규 조사는 보통 20~40분'} 걸리고, 끝나면 새로고침했을 때 '검토 종목' 표에 반영됩니다. `
    + `<a href="https://github.com/${REPO}/actions/workflows/valuation-on-add.yml" target="_blank" rel="noopener">호출 기록</a>`);
  return true;
}
async function requestFromInput() {
  const el = $('rv-ticker');
  const t = (el.value || '').trim().toUpperCase();
  if (!t) { setStatus('티커를 입력하세요', true); return; }
  try { if (await requestAnalysis(t, false)) el.value = ''; }
  catch (e) { setStatus('실패: ' + e.message, true); }
}
async function deleteReview(t) {
  if (!confirm(`${t} 를 검토 종목에서 삭제할까요?\n(적정가 발행 파일은 남아 있어 다시 추가하면 재사용됩니다)`)) return;
  try {
    await ensureLoaded();
    await withRetry(async () => {
      reviewEntries = reviewEntries.filter(e => e.ticker !== t);
      await commitReview(`review: remove ${t}`);
    });
    REVIEW = REVIEW.filter(r => r.ticker !== t);
    render();
    setStatus(`✅ ${t} 검토 종목에서 삭제`);
  } catch (e) { setStatus('실패: ' + e.message, true); }
}

// ── 수급 분석 워크플로우 dispatch + status polling ─────────────────
async function dispatchWorkflow(inputs) {
  const dispatchTime = Date.now();
  await gh(`/repos/${REPO}/actions/workflows/daily-analysis.yml/dispatches`, {
    method: 'POST',
    body: JSON.stringify({ ref: 'main', inputs: inputs || {} })
  });
  return dispatchTime;
}

async function pollRun(dispatchTime, label, timeoutMs) {
  const deadline = Date.now() + (timeoutMs || 15 * 60 * 1000);
  let runId = null, runUrl = null;
  setStatus(`${label}: 워크플로우 등록 대기 중...`);
  while (!runId && Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 4000));
    try {
      const j = await gh(`/repos/${REPO}/actions/workflows/daily-analysis.yml/runs?event=workflow_dispatch&per_page=5`);
      const found = (j.workflow_runs || []).find(r => new Date(r.created_at).getTime() >= dispatchTime - 3000);
      if (found) { runId = found.id; runUrl = found.html_url; }
    } catch (_) {}
  }
  if (!runId) throw new Error('워크플로우 run ID 조회 실패');
  const safeUrl = /^https:\/\/github\.com\//.test(runUrl || '') ? runUrl : null;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 12000));
    try {
      const r = await gh(`/repos/${REPO}/actions/runs/${encodeURIComponent(runId)}`);
      const elapsed = Math.round((Date.now() - dispatchTime) / 1000);
      const statusStr = r.status === 'completed'
        ? (r.conclusion === 'success' ? '✅ 완료' : `❌ ${r.conclusion}`)
        : r.status;
      setStatusHTML(
        `${esc(label)}: ${esc(statusStr)} · ${elapsed}s 경과`
        + (safeUrl ? ` · <a href="${esc(safeUrl)}" target="_blank" rel="noopener" style="color:#0969DA;">Actions 보기</a>` : '')
        + (r.status === 'completed' && r.conclusion === 'success'
           ? ` · <button id="reload-btn" style="padding:2px 10px;background:#1A7F5A;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:11px;">새로고침</button>`
           : '')
      );
      const rb = document.getElementById('reload-btn');
      if (rb) rb.addEventListener('click', () => location.reload());
      if (r.status === 'completed') return r;
    } catch (_) {}
  }
  throw new Error('타임아웃 (15분 초과)');
}

async function runWorkflow(singleTicker) {
  const inputs = {};
  if (singleTicker) inputs.ticker = singleTicker;
  else inputs.force = 'true';   // 수동 전체 실행은 이미 완료된 세션도 재분석
  const label = singleTicker ? `${singleTicker} 분석` : `관심종목 전체(${ticksCache.length}종목) 분석`;
  const msg = singleTicker
    ? `${singleTicker} 한 종목만 수급 분석할까요? (1~3분 소요, 자동으로 진행 추적)`
    : `관심종목 전체(${ticksCache.length}개)를 지금 수급 분석할까요? (5~10분 소요)`;
  if (!confirm(msg)) return;
  try {
    const t0 = await dispatchWorkflow(inputs);
    setStatus(`${label}: dispatch 완료, 상태 추적 중...`);
    await pollRun(t0, label);
  } catch (e) {
    setStatus('실패: ' + e.message, true);
  }
}

async function runAdhoc() {
  const el = $('adhoc-ticker');
  const t = (el.value || '').trim().toUpperCase();
  if (!t) { setStatus('티커를 입력하세요', true); return; }
  if (!TICKER_RE.test(t)) { setStatus(`"${t}"는 유효한 티커 형식이 아닙니다`, true); return; }
  el.value = '';
  await runWorkflow(t);
}

// 표 안의 버튼들 (렌더링 때마다 새로 그려지므로 위임)
document.addEventListener('click', async (e) => {
  const b = e.target.closest('.add-wl-btn, .rm-wl-btn, .rv-promote, .rv-again, .rv-del');
  if (!b) return;
  e.preventDefault();
  const t = b.dataset.t;
  if (!t || needPAT()) return;
  const label = b.textContent;
  b.disabled = true; b.textContent = '…';
  try {
    if (b.classList.contains('add-wl-btn')) { await ensureLoaded(); await promote(t, 'archive'); }
    else if (b.classList.contains('rm-wl-btn')) await removeTicker(t);
    else if (b.classList.contains('rv-promote')) {
      if (confirm(`${t} 를 관심종목으로 편입할까요?\n화~토 오전 자동 수급 분석이 시작되고, 적정가는 매주 갱신됩니다.`)) { await ensureLoaded(); await promote(t, 'review'); }
    }
    else if (b.classList.contains('rv-again')) {
      if (confirm(`${t} 를 다시 조사할까요? (최신 공시·실적으로 리서치 갱신, 분석 세션 1회)`)) await requestAnalysis(t, true);
    }
    else if (b.classList.contains('rv-del')) await deleteReview(t);
  } catch (err) {
    setStatus('실패: ' + err.message, true);
  } finally {
    if (document.body.contains(b)) { b.disabled = false; b.textContent = label; }
  }
});

$('pat-save').addEventListener('click', () => {
  const v = patInput.value.trim();
  if (!v.startsWith('github_pat_') && !v.startsWith('ghp_')) {
    setStatus('유효한 PAT 형식이 아님 (github_pat_ 권장, ghp_ 도 허용)', true);
    return;
  }
  lsSet(LS_KEY, v);
  patInput.value = '';
  showPanel(true);
  Promise.all([loadTickers(), loadReview()]).catch(e => setStatus('PAT 검증 실패: ' + e.message, true));
});
$('pat-clear').addEventListener('click', () => { lsDel(LS_KEY); showPanel(false); });
$('pat-edit').addEventListener('click', () => { showPanel(false); });
$('add-btn').addEventListener('click', addTicker);
newTickerEl.addEventListener('keydown', e => { if (e.key === 'Enter') addTicker(); });
$('rv-btn').addEventListener('click', requestFromInput);
$('rv-ticker').addEventListener('keydown', e => { if (e.key === 'Enter') requestFromInput(); });
$('adhoc-btn').addEventListener('click', runAdhoc);
$('adhoc-ticker').addEventListener('keydown', e => { if (e.key === 'Enter') runAdhoc(); });
$('run-btn').addEventListener('click', () => runWorkflow());

if (getPAT()) {
  showPanel(true);
  loadTickers().then(loadReview).catch(e => {
    setStatus('PAT 무효/만료 — 다시 저장하세요: ' + e.message, true);
    showPanel(false);
  });
} else {
  showPanel(false);
}
"""


# ───────────────────────── main ─────────────────────────
def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TICKERS_DIR.mkdir(parents=True, exist_ok=True)
    db = scan_reports()
    tickers = sorted(db.keys())

    # 1) 종목별 JSON 덤프 (minified)
    for t, series in db.items():
        (DATA_DIR / f"{t}.json").write_text(
            json.dumps(slim_series(series), ensure_ascii=False, separators=(",", ":"), default=str),
            encoding="utf-8",
        )
    stale = DATA_DIR / "all.json"   # 더 이상 생성하지 않음 (아무도 읽지 않음)
    if stale.exists():
        stale.unlink()

    # 2) 종목 전용 페이지
    for t, series in db.items():
        tdir = TICKERS_DIR / t
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "index.html").write_text(render_ticker_page(t, series), encoding="utf-8")

    # 3) 메인 대시보드
    html_out, js = render_dashboard(db, load_watchlist())
    (REPORTS / "index.html").write_text(html_out, encoding="utf-8")
    (REPORTS / "app.js").write_text(js, encoding="utf-8")

    total = sum(len(v) for v in db.values())
    legacy = sum(1 for v in db.values() for r in v if r.get("legacy"))
    print(f"[index] {len(tickers)} tickers · {total} sessions ({legacy} legacy v2)")
    print("[index] wrote reports/index.html, reports/app.js, reports/data/{TICKER}.json, reports/tickers/{TICKER}/index.html")


if __name__ == "__main__":
    main()
