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

from smartmoney.calendar import legacy_session_for, parse_date

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
    return re.sub(r"^CASE_\d+_|^CASE_", "", c) if c else "—"


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
    div = l1.get("divergence") or "N/A"
    tt2 = ("<b>FINRA 오프거래소 거래량 ÷ 총거래량</b><br>"
           "다크풀·ATS·내부화 비중. 30-50% 정상 범위, 50%+ 기관 heavy.<br><br>"
           "<b>Total OBV (5d)</b>: CLV 가중 signed volume 누적 — 전체 시장 방향.<br>"
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
      <div class="card-sub">Score {E(score_str)} · Macro {E(latest.get('macro_env') or 'N/A')}</div>
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
      <div class="card-sub">{E(dte_str)} · {E(l3.get('scenario') or 'N/A')}</div>
    </div>
    <div class="card"><div class="card-label">Call / Put Wall · Exp. Move {tt_icon(tt5)}</div>
      <div class="card-value">{E(cw)} / {E(pw)}</div>
      <div class="card-sub">Expected move {E(em_str)}</div>
    </div>
    """


def render_ticker_page(ticker: str, series: list) -> str:
    latest = series[-1] if series else {}
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
            f'<td>{E(sl1.get("scenario") or "—")}</td><td>{E(case_pretty(sl2.get("case")))}</td>'
            f'<td>{E(sl3.get("scenario") or "—")}</td><td>{E(q_str if not s.get("legacy") else "v2")}</td>'
            f'<td>{link}</td></tr>'
        )

    patterns = latest.get("patterns") or []
    pattern_badges = "".join(
        f'<span style="background:{COLOR["alert_amber"]};color:{COLOR["warn"]};padding:2px 8px;border-radius:10px;font-size:11px;margin-right:4px;">{E(p)}</span>'
        for p in patterns
    ) or f"<span style='color:{COLOR['muted']};font-size:11px;'>패턴 없음</span>"
    headline = latest.get("headline")
    headline_html = (f'<div class="headline">{E(headline)}</div>' if isinstance(headline, str) and headline.strip() else "")
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
            {"label": "Total OBV (5d)", "data": col("l1", "delta_total"), "borderColor": COLOR["bull"], "yAxisID": "y"},
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

  <h2>시계열 추이</h2>
  <div class="chart-grid">
    <div class="card" style="grid-column:1/-1;"><div class="card-label">Price · Max Pain · GEX Flip · Walls</div><canvas id="priceChart"></canvas></div>
    <div class="card"><div class="card-label">Total OBV (5d, 좌) vs Dark Pool % (우)</div><canvas id="obvChart"></canvas></div>
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
        "report_count": len(series),
    }


TABLE_HEAD = """<thead><tr>
    <th>Ticker</th><th>Session</th><th>Quality</th><th>Price</th><th>Top Scenario</th><th>Score</th>
    <th>Macro</th><th>Darkpool</th><th>Short Case</th><th>Options</th><th>DP %</th><th>Short %</th>
    <th>CTB % (Δ)</th><th>DTC</th><th>±Move</th><th>Max Pain</th><th>Call/Put Wall</th><th>Patterns</th>
    <th>📄</th><th></th>
  </tr></thead>"""


def render_dashboard(db: dict, watchlist: set = None) -> tuple[str, str]:
    """HTML + app.js 생성. watchlist 티커는 'Active', 그 외 과거 데이터는 'Archive'."""
    watchlist = watchlist or set()
    summary = [summarize(t, s, watchlist) for t, s in sorted(db.items()) if s]
    latest_session = max((r["latest_date"] for r in summary if r["latest_date"]), default="N/A")
    meta = f"최신 세션 {latest_session} (미국 정규장, ET) · {len(summary)}개 종목"

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
  body {{ font-family:{FONT}; background:{COLOR['bg']}; color:{COLOR['text']}; margin:0; padding:24px; font-size:13px;
         -webkit-tap-highlight-color:transparent; }}
  .wrap {{ max-width:1400px; margin:0 auto; }}
  header {{ display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid {COLOR['border']}; padding-bottom:12px; margin-bottom:16px; flex-wrap:wrap; gap:12px; }}
  h1 {{ font-size:20px; margin:0; }}
  .meta {{ color:{COLOR['muted']}; font-size:11px; }}
  input, select {{ padding:6px 10px; border:0.5px solid {COLOR['border']}; border-radius:4px; font-family:{FONT}; font-size:13px; background:{COLOR['bg']}; color:{COLOR['text']}; }}
  input {{ min-width:220px; }}
  table {{ border-collapse:collapse; width:100%; font-size:12px; }}
  th,td {{ border:0.5px solid {COLOR['border']}; padding:6px 8px; text-align:left; white-space:nowrap; }}
  th {{ background:{COLOR['panel']}; color:{COLOR['muted']}; font-weight:600; position:sticky; top:0; }}
  tr:nth-child(even) {{ background:{COLOR['card']}; }}
  tr:hover {{ background:{COLOR['alert_blue']}; }}
  a {{ color:{COLOR['info']}; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .pill {{ padding:1px 8px; border-radius:10px; font-size:10px; font-weight:600; }}
  .pill-A {{ background:{COLOR['alert_green']}; color:{COLOR['bull']}; }}
  .pill-B {{ background:{COLOR['alert_amber']}; color:{COLOR['warn']}; }}
  .pill-C {{ background:{COLOR['alert_red']}; color:{COLOR['bear']}; }}
  .pill-N {{ background:{COLOR['panel']}; color:{COLOR['muted']}; }}
  .q-good {{ background:{COLOR['alert_green']}; color:{COLOR['bull']}; }}
  .q-mid  {{ background:{COLOR['alert_amber']}; color:{COLOR['warn']}; }}
  .q-bad  {{ background:{COLOR['alert_red']}; color:{COLOR['bear']}; }}
  .pattern {{ font-size:9px; background:{COLOR['alert_amber']}; color:{COLOR['warn']}; padding:1px 4px; border-radius:6px; margin-right:2px; }}
  .muted {{ color:{COLOR['muted']}; }}
  .tbl-wrap {{ max-height:70vh; overflow:auto; border:0.5px solid {COLOR['border']}; border-radius:6px; -webkit-overflow-scrolling:touch; }}
  .empty {{ padding:32px; text-align:center; color:{COLOR['muted']}; }}
  @media (max-width:1024px) {{
    .wrap {{ max-width:100% !important; }}
    header {{ flex-direction:column; align-items:flex-start; gap:6px; }}
  }}
  @media (max-width:720px) {{
    body {{ padding:14px !important; font-size:12.5px; }}
    h1 {{ font-size:18px !important; }}
    #mgmt {{ padding:10px 12px !important; }}
    #mgmt input[type="text"], #mgmt input[type="password"] {{ width:100% !important; max-width:320px; }}
    #mgmt button {{ white-space:nowrap; }}
    .tbl-wrap {{ max-height:none; }}
    th, td {{ padding:4px 6px; font-size:11px; }}
    .pill {{ font-size:9px; padding:1px 6px; }}
    .pattern {{ font-size:8px; }}
  }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1><span style="color:{COLOR['info']};">S</span>mart <span style="color:{COLOR['info']};">M</span>oney <span style="color:{COLOR['info']};">A</span>nalyzer</h1>
    <div class="meta">{E(meta)}</div>
  </div>
  <div>
    <a href="https://github.com/z66g/sma" target="_blank" rel="noopener" aria-label="GitHub" title="GitHub" style="color:{COLOR['muted']};display:inline-flex;align-items:center;">
      <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M12 .5C5.73.5.75 5.48.75 11.75c0 4.98 3.23 9.2 7.71 10.7.56.1.77-.24.77-.54 0-.27-.01-1.16-.02-2.1-3.14.68-3.8-1.34-3.8-1.34-.51-1.3-1.25-1.65-1.25-1.65-1.03-.7.08-.69.08-.69 1.13.08 1.73 1.16 1.73 1.16 1.01 1.73 2.65 1.23 3.3.94.1-.73.4-1.23.72-1.51-2.5-.28-5.14-1.25-5.14-5.57 0-1.23.44-2.23 1.16-3.02-.12-.29-.5-1.43.11-2.98 0 0 .94-.3 3.09 1.15.9-.25 1.86-.37 2.82-.38.96.01 1.92.13 2.82.38 2.15-1.45 3.09-1.15 3.09-1.15.61 1.55.23 2.69.11 2.98.72.79 1.16 1.79 1.16 3.02 0 4.33-2.64 5.29-5.15 5.56.4.35.77 1.04.77 2.1 0 1.52-.01 2.75-.01 3.13 0 .3.2.65.78.54 4.47-1.5 7.7-5.72 7.7-10.7C23.25 5.48 18.27.5 12 .5z"/>
      </svg>
    </a>
  </div>
</header>

<div id="mgmt" style="margin-bottom:16px;background:{COLOR['card']};border:0.5px solid {COLOR['border']};border-radius:6px;padding:14px 16px;">
  <div id="mgmt-need-token" style="display:none;">
    <div style="margin-bottom:8px;font-size:12px;color:{COLOR['muted']};line-height:1.6;">
      워치리스트 편집·즉시 분석에는 GitHub 토큰이 필요합니다. 토큰은 <b>이 브라우저 localStorage 에만</b> 저장되며 repo 에 커밋되거나 다른 사이트로 전송되지 않습니다.<br>
      권장: <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">Fine-grained PAT</a> —
      Repository access: <b>Only select repositories → z66g/sma</b>, Permissions: <b>Contents: Read and write</b> + <b>Actions: Read and write</b>, 만료일 설정.
      (repo 전체 권한을 주는 Classic PAT 은 권장하지 않음)
    </div>
    <input id="pat" type="password" placeholder="github_pat_…" autocomplete="off" style="width:360px;max-width:100%;">
    <button id="pat-save" style="padding:6px 14px;background:{COLOR['info']};color:#fff;border:0;border-radius:4px;cursor:pointer;">저장</button>
    <button id="pat-clear" style="padding:6px 10px;background:none;color:{COLOR['muted']};border:0.5px solid {COLOR['border']};border-radius:4px;cursor:pointer;">지우기</button>
  </div>

  <div id="mgmt-panel" style="display:none;">
    <div style="margin-bottom:14px;">
      <div style="font-size:11px;color:{COLOR['muted']};text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:6px;">단일 종목 즉시 분석</div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <input id="adhoc-ticker" type="text" placeholder="티커 (예: PLTR)" style="text-transform:uppercase;width:160px;">
        <button id="adhoc-btn" style="padding:4px 10px;background:{COLOR['panel']};color:{COLOR['text']};border:0.5px solid {COLOR['border']};border-radius:4px;cursor:pointer;font-size:12px;">개별분석</button>
      </div>
    </div>
    <div style="border-top:0.5px solid {COLOR['border']};padding-top:14px;">
      <div style="font-size:11px;color:{COLOR['muted']};text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:6px;">
        워치리스트 — 화~토 오전(KST) 자동분석 · 직전 미국 정규장 세션 데이터 (휴장일 자동 건너뜀)
      </div>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap;">
        <input id="new-ticker" type="text" placeholder="티커 추가 (예: PLTR)" style="text-transform:uppercase;width:160px;">
        <button id="add-btn" style="padding:4px 10px;background:{COLOR['panel']};color:{COLOR['text']};border:0.5px solid {COLOR['border']};border-radius:4px;cursor:pointer;font-size:12px;">추가</button>
        <button id="run-btn" style="padding:4px 10px;background:{COLOR['panel']};color:{COLOR['text']};border:0.5px solid {COLOR['border']};border-radius:4px;cursor:pointer;font-size:12px;">리스트 전체 분석</button>
        <button id="pat-edit" style="padding:6px 10px;background:none;color:{COLOR['muted']};border:0.5px solid {COLOR['border']};border-radius:4px;cursor:pointer;font-size:11px;margin-left:auto;">PAT 변경</button>
      </div>
      <div id="wl-list" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px;"></div>
    </div>
    <div id="mgmt-status" style="font-size:11px;color:{COLOR['muted']};min-height:16px;margin-top:8px;"></div>
  </div>
</div>

<h2 style="font-size:14px;color:{COLOR['warn']};margin:18px 0 6px;border-bottom:0.5px solid {COLOR['warn']};padding-bottom:4px;">▶ 감시 중 (Active Watchlist) <span id="active-count" style="font-size:11px;color:{COLOR['muted']};font-weight:400;"></span></h2>
<div class="tbl-wrap">
<table id="tbl-active">
  {TABLE_HEAD}
  <tbody id="tbody-active"></tbody>
</table>
</div>

<details id="archive-wrap" style="margin-top:24px;">
<summary style="cursor:pointer;font-size:14px;font-weight:700;color:{COLOR['muted']};border-bottom:0.5px solid {COLOR['border']};padding-bottom:4px;">히스토리 (Archive) <span id="archive-count" style="font-size:11px;font-weight:400;"></span></summary>
<div class="tbl-wrap" style="margin-top:8px;">
<table id="tbl-archive">
  {TABLE_HEAD}
  <tbody id="tbody-archive"></tbody>
</table>
</div>
</details>
</div>
<script>
window.__DB__ = {_js(summary)};
</script>
<script src="app.js"></script>
</body>
</html>
"""
    return html_out, APP_JS


APP_JS = r"""
// Dashboard logic — operates on window.__DB__ (array of ticker summaries)
const DB = window.__DB__ || [];
const COLS = 20;
const bodyActive  = document.getElementById('tbody-active');
const bodyArchive = document.getElementById('tbody-archive');
const archiveWrap = document.getElementById('archive-wrap');
const activeCnt   = document.getElementById('active-count');
const archiveCnt  = document.getElementById('archive-count');

// ── 안전한 문자열 처리 ──
function esc(v) {
  return v == null ? '' : String(v).replace(/[&<>"']/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function isNum(v) { return typeof v === 'number' && isFinite(v); }
function safePath(p) { return (typeof p === 'string' && /^[\w.\-\/]+$/.test(p) && !p.includes('..')) ? p : null; }
const NA = '<span class="muted">N/A</span>';

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
function casePretty(c) { return c ? esc(String(c).replace(/^CASE_\d+_/, '').replace(/^CASE_/, '')) : NA; }
function txt(v) { return v ? esc(v) : NA; }

function rowHTML(r, mode) {
  const t = esc(r.ticker), tq = encodeURIComponent(r.ticker);
  const patBadges = (r.patterns || []).map(p => `<span class="pattern">${esc(p)}</span>`).join('') || '-';
  const tickerLink = `<a href="tickers/${tq}/" title="${esc(r.headline || '')}"><b>${t}</b></a>`;
  const url = safePath(r.report_url);
  const dateCell = url ? `<a href="${esc(url)}">${esc(r.latest_date)}</a>` : esc(r.latest_date || 'N/A');
  const ctb = isNum(r.ctb_fee) ? r.ctb_fee.toFixed(2) + '%' + (isNum(r.ctb_delta_pct) ? ` <span class="muted">(${fmtSigned(r.ctb_delta_pct)}%)</span>` : '') : NA;
  const walls = (isNum(r.call_wall) || isNum(r.put_wall)) ? `${fmtDol(r.call_wall)} / ${fmtDol(r.put_wall)}` : NA;
  let actionBtn = '';
  if (mode === 'archive') {
    actionBtn = `<button class="add-wl-btn" data-t="${t}" title="워치리스트에 추가" style="padding:1px 7px;background:#1A7F5A;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:12px;font-weight:700;margin-right:4px;">＋</button>`;
  } else if (mode === 'active') {
    actionBtn = `<button class="rm-wl-btn" data-t="${t}" title="워치리스트에서 제거 (아카이브로 이동)" style="padding:1px 7px;background:#CF222B;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:12px;font-weight:700;margin-right:4px;">×</button>`;
  }
  return `<tr>
    <td>${tickerLink}</td>
    <td>${dateCell}</td>
    <td>${qualityPill(r)}</td>
    <td>${fmtDol(r.price)}</td>
    <td>${scenarioPill(r.scenario, r.scenario_pct)}</td>
    <td>${isNum(r.raw_score) ? r.raw_score.toFixed(2) : NA}</td>
    <td>${txt(r.macro_env)}</td>
    <td>${txt(r.l1_scenario)}</td>
    <td>${casePretty(r.l2_case)}</td>
    <td>${txt(r.l3_scenario)}</td>
    <td>${fmtPct(r.dp_pct)}</td>
    <td>${fmtPct(r.short_pct)}</td>
    <td>${ctb}</td>
    <td>${isNum(r.dtc) ? r.dtc.toFixed(1) + 'd' : NA}</td>
    <td>${isNum(r.exp_move_pct) ? '±' + r.exp_move_pct.toFixed(1) + '%' : NA}</td>
    <td>${fmtDol(r.max_pain)}</td>
    <td>${walls}</td>
    <td>${patBadges}</td>
    <td><a href="tickers/${tq}/" style="font-weight:600;">${esc(r.report_count)}</a></td>
    <td style="white-space:nowrap;">${actionBtn}</td>
  </tr>`;
}

function render() {
  const rows = DB.slice().sort((a, b) => a.ticker.localeCompare(b.ticker));
  const active  = rows.filter(r => r.is_active);
  const archive = rows.filter(r => !r.is_active);
  activeCnt.textContent  = `· ${active.length}개`;
  archiveCnt.textContent = `· ${archive.length}개`;
  bodyActive.innerHTML = active.length
    ? active.map(r => rowHTML(r, 'active')).join('')
    : `<tr><td colspan="${COLS}" class="empty">감시 중 종목이 없습니다. 위 패널에서 추가하세요.</td></tr>`;
  bodyArchive.innerHTML = archive.length
    ? archive.map(r => rowHTML(r, 'archive')).join('')
    : `<tr><td colspan="${COLS}" class="empty">아카이브 비어있음.</td></tr>`;
  archiveWrap.style.display = archive.length ? '' : 'none';
}

render();

// ───── Watchlist management via GitHub API ─────
// PAT 는 이 브라우저 localStorage 에만 저장된다 (repo/다른 사이트로 전송 X).
// 권장: z66g/sma 한 저장소로 범위를 제한한 Fine-grained PAT (Contents RW + Actions RW).
const REPO = 'z66g/sma';
const API  = 'https://api.github.com';
const LS_KEY = 'sma_github_pat';
const TICKER_RE = /^[A-Z][A-Z0-9.\-]{0,9}$/;

const mgmtNeedToken = document.getElementById('mgmt-need-token');
const mgmtPanel     = document.getElementById('mgmt-panel');
const patInput      = document.getElementById('pat');
const patSaveBtn    = document.getElementById('pat-save');
const patClearBtn   = document.getElementById('pat-clear');
const patEditBtn    = document.getElementById('pat-edit');
const newTickerEl   = document.getElementById('new-ticker');
const addBtn        = document.getElementById('add-btn');
const runBtn        = document.getElementById('run-btn');
const wlList        = document.getElementById('wl-list');
const statusEl      = document.getElementById('mgmt-status');

let ticksCache = [];
let txtSha = null;

function lsGet(k) { try { return localStorage.getItem(k) || ''; } catch (_) { return ''; } }
function getPAT() { return lsGet(LS_KEY); }
function setPAT(v) { try { localStorage.setItem(LS_KEY, v); } catch (_) {} }
function clearPAT() { try { localStorage.removeItem(LS_KEY); } catch (_) {} }

function showPanel(hasToken) {
  mgmtNeedToken.style.display = hasToken ? 'none' : 'block';
  mgmtPanel.style.display     = hasToken ? 'block' : 'none';
}
// 기본: 텍스트로만 출력 (escape 불필요)
function setStatus(msg, isError) {
  statusEl.textContent = msg || '';
  statusEl.style.color = isError ? '#CF222B' : '#656D76';
}
// 신뢰된 HTML 조각 전용 — 호출부에서 모든 동적 값을 esc() 해야 함
function setStatusHTML(html, isError) {
  statusEl.innerHTML = html || '';
  statusEl.style.color = isError ? '#CF222B' : '#656D76';
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
    throw new Error(`GitHub ${res.status}: ${err.message || res.statusText}`);
  }
  return res.status === 204 ? null : res.json();
}

function parseTickers(text) {
  return text.split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('#'));
}

async function loadTickers() {
  setStatus('tickers.txt 로드 중...');
  const j = await gh(`/repos/${REPO}/contents/tickers.txt?ref=main&_=${Date.now()}`);
  txtSha = j.sha;
  const text = decodeURIComponent(escape(atob(j.content.replace(/\n/g, ''))));
  ticksCache = parseTickers(text);
  const active = new Set(ticksCache);
  let flipped = 0;
  for (const r of DB) {
    const shouldBeActive = active.has(r.ticker);
    if (r.is_active !== shouldBeActive) { r.is_active = shouldBeActive; flipped++; }
  }
  renderWL();
  if (flipped > 0) render();
  setStatus(`${ticksCache.length}개 감시 중 · last commit ${String(j.sha).slice(0,7)}` + (flipped ? ` · ${flipped}개 행 동기화` : ''));
}

function renderWL() {
  if (!ticksCache.length) {
    wlList.innerHTML = '<span style="color:#656D76;font-size:11px;">감시 종목 없음</span>';
    return;
  }
  wlList.innerHTML = ticksCache.map(t =>
    `<span style="background:#DDF4FF;color:#0969DA;padding:3px 4px 3px 10px;border-radius:12px;font-size:12px;font-weight:600;display:inline-flex;align-items:center;gap:6px;">
       ${esc(t)}
       <button data-t="${esc(t)}" class="rm-btn" style="background:none;border:0;color:#CF222B;cursor:pointer;font-size:14px;padding:0 6px;">×</button>
     </span>`
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

async function commitTickers(message) {
  setStatus('커밋 중...');
  const content = btoa(unescape(encodeURIComponent(buildFileContent())));
  const j = await gh(`/repos/${REPO}/contents/tickers.txt`, {
    method: 'PUT',
    body: JSON.stringify({ message, content, sha: txtSha })
  });
  txtSha = j.content.sha;
  setStatus(`✅ 커밋 완료: ${message} (${String(txtSha).slice(0,7)})`);
}

async function addTicker() {
  const t = (newTickerEl.value || '').trim().toUpperCase();
  if (!t) return;
  if (!TICKER_RE.test(t)) { setStatus(`"${t}"는 유효한 티커 형식이 아닙니다`, true); return; }
  if (ticksCache.includes(t)) { setStatus(`${t}는 이미 등록됨`, true); return; }
  ticksCache.push(t);
  ticksCache.sort();
  try {
    await commitTickers(`watchlist: add ${t}`);
    renderWL();
    newTickerEl.value = '';
  } catch (e) {
    ticksCache = ticksCache.filter(x => x !== t);
    setStatus('실패: ' + e.message, true);
  }
}

async function removeTicker(t) {
  if (!confirm(`${t} 를 워치리스트에서 제거하고 아카이브로 이동할까요? (자동 분석에서 제외됨)`)) return;
  const prev = ticksCache.slice();
  ticksCache = ticksCache.filter(x => x !== t);
  try {
    await commitTickers(`watchlist: remove ${t}`);
    const row = DB.find(x => x.ticker === t);
    if (row) row.is_active = false;
    renderWL();
    render();
    setStatus(`✅ ${t} 워치리스트에서 제거됨 (아카이브로 이동)`);
  } catch (e) {
    ticksCache = prev;
    setStatus('실패: ' + e.message, true);
  }
}

// ── 워크플로우 dispatch + status polling ─────────────────────────
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
  const label = singleTicker ? `${singleTicker} 분석` : `워치리스트 전체(${ticksCache.length}종목) 분석`;
  const msg = singleTicker
    ? `${singleTicker} 한 종목만 분석할까요? (1~3분 소요, 자동으로 진행 추적)`
    : `워치리스트 전체(${ticksCache.length}개)를 지금 분석할까요? (5~10분 소요)`;
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
  const el = document.getElementById('adhoc-ticker');
  const t = (el.value || '').trim().toUpperCase();
  if (!t) { setStatus('티커를 입력하세요', true); return; }
  if (!TICKER_RE.test(t)) { setStatus(`"${t}"는 유효한 티커 형식이 아닙니다`, true); return; }
  el.value = '';
  await runWorkflow(t);
}

document.addEventListener('click', async (e) => {
  const add = e.target.closest('.add-wl-btn');
  if (add) {
    e.preventDefault();
    const t = add.dataset.t;
    if (!t || !getPAT()) { setStatus('PAT 필요', true); return; }
    if (ticksCache.includes(t)) { setStatus(`${t}는 이미 워치리스트에 있음`); return; }
    add.disabled = true;
    add.textContent = '…';
    try {
      ticksCache.push(t); ticksCache.sort();
      await commitTickers(`watchlist: promote ${t} from archive`);
      const row = DB.find(x => x.ticker === t);
      if (row) row.is_active = true;
      renderWL();
      render();
      setStatus(`✅ ${t} 워치리스트로 이동됨 (화~토 자동분석 포함)`);
    } catch (err) {
      ticksCache = ticksCache.filter(x => x !== t);
      add.disabled = false;
      add.textContent = '＋';
      setStatus('실패: ' + err.message, true);
    }
    return;
  }
  const rm = e.target.closest('.rm-wl-btn');
  if (rm) {
    e.preventDefault();
    const t = rm.dataset.t;
    if (!t || !getPAT()) { setStatus('PAT 필요', true); return; }
    await removeTicker(t);
  }
});

patSaveBtn.addEventListener('click', () => {
  const v = patInput.value.trim();
  if (!v.startsWith('github_pat_') && !v.startsWith('ghp_')) {
    setStatus('유효한 PAT 형식이 아님 (github_pat_ 권장, ghp_ 도 허용)', true);
    return;
  }
  setPAT(v);
  patInput.value = '';
  showPanel(true);
  loadTickers().catch(e => setStatus('PAT 검증 실패: ' + e.message, true));
});
patClearBtn.addEventListener('click', () => { clearPAT(); showPanel(false); });
patEditBtn.addEventListener('click', () => { showPanel(false); });
addBtn.addEventListener('click', addTicker);
document.getElementById('adhoc-btn').addEventListener('click', runAdhoc);
document.getElementById('adhoc-ticker').addEventListener('keydown', e => { if (e.key === 'Enter') runAdhoc(); });
newTickerEl.addEventListener('keydown', e => { if (e.key === 'Enter') addTicker(); });
runBtn.addEventListener('click', () => runWorkflow());

if (getPAT()) {
  showPanel(true);
  loadTickers().catch(e => {
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
