#!/usr/bin/env python3
"""
reports/ 디렉토리를 스캔해 정적 대시보드 사이트를 빌드.

생성물:
  reports/index.html                ← 메인 대시보드 (검색/필터)
  reports/app.js                    ← 대시보드 프론트 로직
  reports/data/all.json             ← 전체 티커 × 전체 날짜 덤프
  reports/data/{TICKER}.json        ← 종목별 시계열
  reports/tickers/{TICKER}/index.html   ← 종목 전용 페이지 (Chart.js 시계열)

원본:
  reports/YYYY-MM-DD/{TICKER}_3Layer_Forensic_{DATE}.html
  reports/YYYY-MM-DD/SmartMoney_{TICKER}_{DATE}.md
  reports/YYYY-MM-DD/{TICKER}_{DATE}.json    ← sma.py가 생성
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
DATA_DIR = REPORTS / "data"
TICKERS_DIR = REPORTS / "tickers"
REPORTS.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
TICKERS_DIR.mkdir(exist_ok=True)

FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
COLOR = {
    "bg": "#FFFFFF", "card": "#F6F8FA", "panel": "#EAEEF2",
    "border": "#D0D7DE", "text": "#1F2328", "muted": "#656D76",
    "bull": "#1A7F5A", "bear": "#CF222B", "warn": "#9A6700", "info": "#0969DA",
    "alert_green": "#DAFBE1", "alert_red": "#FFEBE9",
    "alert_amber": "#FFF8C5", "alert_blue": "#DDF4FF",
}

def scan_reports():
    """reports/YYYY-MM-DD/*.json 스캔 → 시계열 DB 구축"""
    by_ticker = defaultdict(list)
    for jpath in sorted(REPORTS.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/*.json")):
        m = re.match(r"([A-Z.\-]+)_(\d{4}-\d{2}-\d{2})\.json", jpath.name)
        if not m: continue
        t, d = m.group(1), m.group(2)
        try:
            j = json.loads(jpath.read_text(encoding="utf-8"))
            # 지수 리포트는 메인 대시보드(종목 시계열)에서 제외 — 별도 섹션 예정
            if j.get("is_index"):
                continue
            by_ticker[t].append(j)
        except Exception as e:
            print(f"WARN: failed to parse {jpath}: {e}")
    # sort by date ascending within each ticker
    for t in by_ticker:
        by_ticker[t].sort(key=lambda x: x.get("date", ""))
    return dict(by_ticker)

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
            f'<tr>'
            f'<td>{it["date"]}</td>'
            f'<td><span style="background:{bg};color:{fg};padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;">{label}</span></td>'
            f'<td><a href="narratives/{it["filename"]}">열기</a></td>'
            f'</tr>'
        )
    return f"""<table>
<thead><tr><th>Date</th><th>Mode</th><th>Link</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>"""


def scan_narratives(ticker: str) -> list:
    """reports/tickers/{T}/narratives/*.html → [{date, mode, filename}, ...] 최신순"""
    ndir = TICKERS_DIR / ticker / "narratives"
    if not ndir.exists():
        return []
    items = []
    for p in ndir.glob("*.html"):
        m = re.match(r"(\d{4}-\d{2}-\d{2})-(weekly|daily|adhoc)(?:-[\w\-]+)?\.html", p.name)
        if m:
            items.append({"date": m.group(1), "mode": m.group(2), "filename": p.name})
    items.sort(key=lambda x: (x["date"], x["filename"]), reverse=True)
    return items


def _overview_cards(scen: dict, l1: dict, l2: dict, l3: dict, latest: dict) -> str:
    """Overview 4-card block with tooltips + dynamic top scenario label."""
    # Block 1: TOP SCENARIO (A/B/C 중 최대)
    sc_pairs = [("A", scen.get("A_bullish", 0), "Bullish", COLOR["bull"]),
                ("B", scen.get("B_neutral", 0), "Neutral", COLOR["warn"]),
                ("C", scen.get("C_bearish", 0), "Bearish", COLOR["bear"])]
    top = max(sc_pairs, key=lambda x: x[1])
    top_key, top_val, top_label, top_color = top
    others = " · ".join(f"{lbl} {v:.0f}%" for k,v,lbl,_ in sc_pairs if k != top_key)
    tt1 = (
        "<b>가중 signal 합산 시나리오 확률</b><br>"
        "Bullish/Neutral/Bearish 중 가장 높은 확률이 대표 시나리오. "
        "Score는 −1.00~+1.00 범위, Macro는 FRED 기반 유동성 분류."
        "<br><br>" + others
    )

    # Block 2: Dark Pool %
    dp = l1.get("dp_pct")
    dp_str = f"{dp:.1f}%" if dp is not None else "N/A"
    obv_total = l1.get("delta_total") or 0
    div = l1.get("divergence","-")
    obv_sign = "+" if obv_total >= 0 else ""
    # 천 단위 k/M 축약
    _av = abs(obv_total)
    if _av >= 1_000_000:   obv_fmt = f"{obv_total/1_000_000:.1f}M"
    elif _av >= 1_000:     obv_fmt = f"{obv_total/1_000:.0f}k"
    else:                  obv_fmt = f"{obv_total:.0f}"
    tt2 = (
        "<b>FINRA 오프거래소 거래량 ÷ 총거래량</b><br>"
        "다크풀·ATS·내부화 비중. 30-50% 정상 범위, 50%+ 기관 heavy.<br><br>"
        "<b>Total OBV (5d)</b>: CLV 가중 signed volume 누적 — 전체 시장 방향.<br>"
        "<b>Divergence</b>: 가격 slope vs OBV 누적 slope (5일 회귀)."
    )

    # Block 3: Short % · CTB
    sp = l2.get("short_pct_latest"); sp_str = f"{sp:.1f}%" if sp is not None else "N/A"
    cb = l2.get("ctb_fee"); cb_str = f"{cb:.2f}%" if cb is not None else "N/A"
    case = re.sub(r"^CASE_\d+_|^CASE_", "", l2.get("case") or "-")
    tt3 = (
        "<b>FINRA Reg SHO 공매도 비율</b> · <b>iborrowdesk 차입 비용</b><br>"
        "Short%는 당일 공매도 체결 비율 — 포지션(short interest) 아님. "
        "MM 헤지 포함이라 40-55% 구조적 정상. <b>절대값으로 bearish 판단 금지</b>, "
        "slope + CTB 방향 + Case 조합으로만 해석.<br><br>"
        "<b>CTB</b>: &lt;1% ETB · 5-15% HTB · &gt;15% Squeeze risk zone"
    )

    # Block 4: Max Pain · GEX Flip
    mp = l3.get("max_pain"); mp_str = f"${mp:.2f}" if mp else "N/A"
    fl = l3.get("flip_zone"); fl_str = f"${fl:.2f}" if fl else "N/A"
    dte = l3.get("dte","-")
    scn3 = l3.get("scenario","-")
    tt4 = (
        "<b>Max Pain</b>: 만기일 MM 헤지 비용이 최소화되는 주가 — 핀닝 중력점.<br>"
        "<b>GEX Flip Zone</b>: MM gamma 부호가 +에서 −로 바뀌는 spot 근처 strike.<br>"
        "spot &gt; Flip → 핀닝 regime(변동성 억제) · spot &lt; Flip → 증폭 regime.<br>"
        "DTE ≤ 5 일 때만 pinning 신호 유효 (§8.2)."
    )

    def tt_icon(body_html):
        return f'<span class="tt tt-i">ⓘ<span class="tt-body">{body_html}</span></span>'

    return f"""
    <div class="card"><div class="card-label">Top Scenario {tt_icon(tt1)}</div>
      <div class="card-value" style="color:{top_color};">{top_label} · {top_val:.1f}%</div>
      <div class="card-sub">Score {scen.get('raw_score',0):+.2f} · Macro {latest.get('macro_env','-')}</div>
    </div>
    <div class="card"><div class="card-label">Dark Pool % {tt_icon(tt2)}</div>
      <div class="card-value">{dp_str}</div>
      <div class="card-sub">OBV {obv_sign}{obv_fmt} · {div}</div>
    </div>
    <div class="card"><div class="card-label">Short % · CTB {tt_icon(tt3)}</div>
      <div class="card-value">{sp_str} · {cb_str}</div>
      <div class="card-sub">{case}</div>
    </div>
    <div class="card"><div class="card-label">Max Pain · GEX Flip {tt_icon(tt4)}</div>
      <div class="card-value">{mp_str} · {fl_str}</div>
      <div class="card-sub">DTE {dte} · {scn3}</div>
    </div>
    """


def render_ticker_page(ticker: str, series: list) -> str:
    latest = series[-1] if series else {}
    l1, l2, l3, l4 = latest.get("l1") or {}, latest.get("l2") or {}, latest.get("l3") or {}, latest.get("l4") or {}
    scen = latest.get("scenarios") or {}
    dates = [s["date"] for s in series]
    prices = [s.get("price") for s in series]
    bull_probs = [(s.get("scenarios") or {}).get("A_bullish") for s in series]
    bear_probs = [(s.get("scenarios") or {}).get("C_bearish") for s in series]
    ctb = [(s.get("l2") or {}).get("ctb_fee") for s in series]
    short_pct = [(s.get("l2") or {}).get("short_pct_latest") for s in series]
    max_pain = [(s.get("l3") or {}).get("max_pain") for s in series]
    flip = [(s.get("l3") or {}).get("flip_zone") for s in series]
    total_obv = [(s.get("l1") or {}).get("delta_total") for s in series]
    dp_pct_series = [(s.get("l1") or {}).get("dp_pct") for s in series]

    # 히스토리 테이블
    rows = []
    for s in reversed(series):
        sc = s.get("scenarios") or {}
        sl1 = s.get("l1") or {}; sl2 = s.get("l2") or {}; sl3 = s.get("l3") or {}
        d = s["date"]
        scen_label = "Bull" if sc.get("A_bullish",0) >= max(sc.get("B_neutral",0), sc.get("C_bearish",0)) \
                     else "Neut" if sc.get("B_neutral",0) >= sc.get("C_bearish",0) \
                     else "Bear"
        color = COLOR["bull"] if "Bull" in scen_label else COLOR["bear"] if "Bear" in scen_label else COLOR["warn"]
        _price = s.get("price")
        _price_str = f"${_price:.2f}" if isinstance(_price, (int, float)) else "—"
        rows.append(
            f'<tr>'
            f'<td>{d}</td>'
            f'<td>{_price_str}</td>'
            f'<td style="color:{color};font-weight:600;">{scen_label} {max(sc.get("A_bullish",0),sc.get("B_neutral",0),sc.get("C_bearish",0)):.0f}%</td>'
            f'<td>{sl1.get("scenario","-")}</td>'
            f'<td>{re.sub(r"^CASE_\\d+_|^CASE_", "", sl2.get("case") or "-")}</td>'
            f'<td>{sl3.get("scenario","-")}</td>'
            f'<td><a href="../../{d}/{ticker}_3Layer_Forensic_{d}.html">리포트</a></td>'
            f'</tr>'
        )

    patterns = latest.get("patterns") or []
    pattern_badges = "".join(
        f'<span style="background:{COLOR["alert_amber"]};color:{COLOR["warn"]};padding:2px 8px;border-radius:10px;font-size:11px;margin-right:4px;">{p}</span>'
        for p in patterns
    ) or "<span style='color:#656D76;font-size:11px;'>패턴 없음</span>"

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
<title>{ticker} · Smart Money History</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body {{ font-family:{FONT}; background:{COLOR['bg']}; color:{COLOR['text']}; margin:0; padding:24px; font-size:13px;
         -webkit-user-select:none; -moz-user-select:none; -ms-user-select:none; user-select:none;
         -webkit-touch-callout:none; -webkit-tap-highlight-color:transparent; }}
  .wrap {{ max-width:1200px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px 0; }}
  h2 {{ font-size:14px; color:{COLOR['warn']}; border-bottom:0.5px solid {COLOR['warn']}; padding-bottom:4px; margin-top:24px; }}
  .meta {{ color:{COLOR['muted']}; font-size:12px; margin-bottom:16px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:12px; }}
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

  /* 표 가로 스크롤 wrapper (긴 테이블 대응) */
  .t-scroll {{ overflow-x:auto; -webkit-overflow-scrolling:touch; max-width:100%; }}
  .t-scroll table {{ min-width:100%; }}

  /* ── 반응형 ────────────────────────────────────────── */
  @media (max-width:1024px) {{
    .chart-grid {{ grid-template-columns:1fr; }}
    .grid {{ grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); }}
  }}
  @media (max-width:720px) {{
    body {{ padding:14px !important; font-size:12.5px; }}
    .wrap {{ max-width:100% !important; }}
    h1 {{ font-size:18px; }}
    h2 {{ font-size:13px; }}
    .grid {{ grid-template-columns:1fr; }}
    canvas {{ max-height:180px; }}
    .tt .tt-body {{ max-width:88vw; font-size:10px; }}
    /* narrative에서 Claude가 생성한 2x2 카드 그리드도 모바일에서 1열 */
    #narrative-root div[style*="grid-template-columns:repeat(auto-fit"] {{
      grid-template-columns:1fr !important;
    }}
  }}

  /* Pure-CSS 툴팁 */
  .tt {{ position:relative; display:inline-block; cursor:help; }}
  .tt-i {{ color:{COLOR['info']}; margin-left:4px; font-weight:600; font-size:11px; }}
  .tt .tt-body {{
    visibility:hidden; opacity:0; transition:opacity 0.12s;
    position:absolute; left:50%; transform:translateX(-50%);
    bottom:calc(100% + 8px); z-index:20;
    background:#1F2328; color:#FFFFFF;
    padding:8px 10px; border-radius:6px;
    font-size:11px; line-height:1.5; text-align:left;
    white-space:normal; width:max-content; max-width:320px;
    box-shadow:0 4px 12px rgba(0,0,0,0.15); font-weight:400;
  }}
  .tt .tt-body::after {{
    content:''; position:absolute; top:100%; left:50%;
    margin-left:-5px; border:5px solid transparent; border-top-color:#1F2328;
  }}
  .tt:hover .tt-body {{ visibility:visible; opacity:1; }}
  .tt-left .tt-body {{ left:0 !important; transform:none !important; }}
  .tt-left .tt-body::after {{ left:14px !important; margin-left:0 !important; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="nav"><a href="../../">← 전체 대시보드</a></div>
  <h1>{ticker}</h1>
  <div class="meta">Latest: {latest.get('date','N/A')} · {('$'+format(latest.get('price'),'.2f')) if isinstance(latest.get('price'),(int,float)) else '—'} · {len(series)}개 리포트 · {pattern_badges}</div>

  <h2>Overview (latest)</h2>
  <div class="grid">
    {_overview_cards(scen, l1, l2, l3, latest)}
  </div>

  <h2>시계열 추이</h2>
  <div class="chart-grid">
    <div class="card" style="grid-column:1/-1;"><div class="card-label">Price · Max Pain · GEX Flip</div><canvas id="priceChart"></canvas></div>
    <div class="card"><div class="card-label">Total OBV (5d) vs Dark Pool %</div><canvas id="obvChart"></canvas></div>
    <div class="card"><div class="card-label">Short % · CTB Fee</div><canvas id="shortChart"></canvas></div>
    <div class="card" style="grid-column:1/-1;"><div class="card-label">Scenario Probabilities (%)</div><canvas id="probChart"></canvas></div>
  </div>

  <h2>전체 히스토리</h2>
  <div class="t-scroll">
  <table>
    <thead><tr><th>Date</th><th>Price</th><th>Top Scenario</th><th>Darkpool</th><th>Short Case</th><th>Options</th><th>Link</th></tr></thead>
    <tbody>{''.join(rows) if rows else '<tr><td colspan="7">아직 리포트 없음</td></tr>'}</tbody>
  </table>
  </div>

  <h2>주간 AI 분석 리포트 히스토리</h2>
  {_render_narrative_archive(ticker)}

  <div id="narrative-root"></div>

  <div style="margin-top:24px;font-size:10px;color:{COLOR['muted']};text-align:center;">
    Data: <a href="../../data/{ticker}.json">{ticker}.json</a> ·
    <a href="../../">Back to dashboard</a>
  </div>
</div>
<script src="../../narrative.js"></script>
<script>
  document.addEventListener('DOMContentLoaded', () => {{
    if (window.SMA_Narrative) {{
      window.SMA_Narrative.mount(document.getElementById('narrative-root'), {json.dumps(ticker)});
    }}
  }});
</script>
<script>
Chart.defaults.font.family = {json.dumps(FONT)};
Chart.defaults.color = "{COLOR['text']}";
// 글로벌 legend defaults는 per-chart 설정으로 덮어씀 (lineChart 안에서 boxWidth=40, usePointStyle=true)
Chart.defaults.plugins.legend.labels.usePointStyle = true;
const DATES = {json.dumps(dates)};
function lineChart(id, datasets) {{
  new Chart(document.getElementById(id), {{
    type:'line',
    data:{{ labels:DATES, datasets:datasets }},
    options:{{ responsive:true, maintainAspectRatio:false,
      plugins:{{
        legend:{{
          display:datasets.length>1, position:'bottom',
          labels:{{
            usePointStyle:true,         // ← 이게 핵심
            boxWidth:40, boxHeight:10, padding:14,
            generateLabels(chart) {{
              return chart.data.datasets.map((ds, i) => ({{
                text: ds.label,
                fillStyle: ds.borderColor,      // 선 색으로 채움
                strokeStyle: ds.borderColor,
                lineWidth: ds.borderWidth || 2,
                lineDash: ds.borderDash || [],  // dash 패턴 반영
                pointStyle: 'line',
                hidden: !chart.isDatasetVisible(i),
                datasetIndex: i
              }}));
            }}
          }},
          onClick(e, item, legend) {{
            const ci = legend.chart;
            ci.setDatasetVisibility(item.datasetIndex, !ci.isDatasetVisible(item.datasetIndex));
            ci.update();
          }}
        }},
        tooltip:{{
          usePointStyle:true,
          callbacks:{{
            labelPointStyle(ctx) {{
              // 툴팁의 마커도 선 형태로
              return {{ pointStyle:'line', rotation:0 }};
            }}
          }}
        }}
      }},
      scales:{{y:{{grid:{{color:'{COLOR['panel']}'}}}},x:{{grid:{{display:false}}}}}},
      elements:{{point:{{radius:2}},line:{{tension:0.3,borderWidth:2}}}},
    }}
  }});
}}
// Price · Max Pain · GEX Flip 통합 — 세 선 모두 같은 USD 축이라 한 차트에 넣기 자연스러움
lineChart('priceChart', [
  {{label:'Price',    data:{json.dumps(prices)},   borderColor:'{COLOR["info"]}', backgroundColor:'transparent', borderWidth:2.5}},
  {{label:'Max Pain', data:{json.dumps(max_pain)}, borderColor:'{COLOR["warn"]}', backgroundColor:'transparent', borderDash:[4,3]}},
  {{label:'GEX Flip', data:{json.dumps(flip)},     borderColor:'{COLOR["bull"]}', backgroundColor:'transparent', borderDash:[2,2]}}
]);
lineChart('probChart', [
  {{label:'Bullish', data:{json.dumps(bull_probs)}, borderColor:'{COLOR["bull"]}', backgroundColor:'transparent'}},
  {{label:'Bearish', data:{json.dumps(bear_probs)}, borderColor:'{COLOR["bear"]}', backgroundColor:'transparent'}}
]);
lineChart('shortChart', [
  {{label:'Short %', data:{json.dumps(short_pct)}, borderColor:'{COLOR["bear"]}', yAxisID:'y', backgroundColor:'transparent'}},
  {{label:'CTB %', data:{json.dumps(ctb)}, borderColor:'{COLOR["warn"]}', yAxisID:'y', backgroundColor:'transparent'}}
]);
lineChart('obvChart', [
  {{label:'Total OBV (5d)', data:{json.dumps(total_obv)}, borderColor:'{COLOR["bull"]}', backgroundColor:'transparent'}},
  {{label:'Dark Pool %',   data:{json.dumps(dp_pct_series)}, borderColor:'{COLOR["info"]}', backgroundColor:'transparent', yAxisID:'y1'}}
]);
</script>
</body>
</html>
"""


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


def render_dashboard(db: dict, watchlist: set = None) -> tuple[str, str]:
    """HTML + app.js 생성. Vanilla JS로 검색/필터 구현. watchlist에 속한
    티커는 'Active', 그 외 과거 데이터는 'Archive'로 분리 표시."""
    watchlist = watchlist or set()
    # 티커별 요약 행
    summary = []
    for t, series in db.items():
        if not series: continue
        latest = series[-1]
        sc = latest.get("scenarios") or {}
        top = "A" if sc.get("A_bullish",0) >= max(sc.get("B_neutral",0), sc.get("C_bearish",0)) \
              else "B" if sc.get("B_neutral",0) >= sc.get("C_bearish",0) else "C"
        top_val = sc.get({"A":"A_bullish","B":"B_neutral","C":"C_bearish"}[top], 0)
        summary.append({
            "ticker": t,
            "is_active": t in watchlist,
            "latest_date": latest.get("date"),
            "price": latest.get("price"),
            "scenario": top, "scenario_pct": top_val,
            "raw_score": sc.get("raw_score"),
            "patterns": latest.get("patterns") or [],
            "l1_scenario": (latest.get("l1") or {}).get("scenario"),
            "l2_case":     (latest.get("l2") or {}).get("case"),
            "l3_scenario": (latest.get("l3") or {}).get("scenario"),
            "macro_env":   latest.get("macro_env"),
            "dp_pct":      (latest.get("l1") or {}).get("dp_pct"),
            "ctb_fee":     (latest.get("l2") or {}).get("ctb_fee"),
            "short_pct":   (latest.get("l2") or {}).get("short_pct_latest"),
            "max_pain":    (latest.get("l3") or {}).get("max_pain"),
            "report_count": len(series),
        })

    from datetime import timedelta as _td
    generated = (datetime.now(timezone.utc) + _td(hours=9)).strftime("%Y-%m-%d %H:%M KST")

    html = f"""<!DOCTYPE html>
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
         -webkit-user-select:none; -moz-user-select:none; -ms-user-select:none; user-select:none;
         -webkit-touch-callout:none; -webkit-tap-highlight-color:transparent; }}
  .wrap {{ max-width:1400px; margin:0 auto; }}
  header {{ display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid {COLOR['border']}; padding-bottom:12px; margin-bottom:16px; flex-wrap:wrap; gap:12px; }}
  h1 {{ font-size:20px; margin:0; }}
  .meta {{ color:{COLOR['muted']}; font-size:11px; }}
  .controls {{ display:flex; gap:8px; align-items:center; margin-bottom:12px; flex-wrap:wrap; }}
  input, select {{ padding:6px 10px; border:0.5px solid {COLOR['border']}; border-radius:4px; font-family:{FONT}; font-size:13px; background:{COLOR['bg']}; color:{COLOR['text']}; }}
  input {{ min-width:220px; }}
  .stat-bar {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:12px; }}
  .stat {{ background:{COLOR['card']}; border:0.5px solid {COLOR['border']}; border-radius:6px; padding:8px 14px; min-width:120px; }}
  .stat-label {{ font-size:10px; color:{COLOR['muted']}; text-transform:uppercase; letter-spacing:0.05em; }}
  .stat-value {{ font-size:18px; font-weight:600; }}
  table {{ border-collapse:collapse; width:100%; font-size:12px; }}
  th,td {{ border:0.5px solid {COLOR['border']}; padding:6px 8px; text-align:left; white-space:nowrap; }}
  th {{ background:{COLOR['panel']}; color:{COLOR['muted']}; font-weight:600; cursor:pointer; user-select:none; position:sticky; top:0; }}
  th:hover {{ background:#DDE3E8; }}
  tr:nth-child(even) {{ background:{COLOR['card']}; }}
  tr:hover {{ background:{COLOR['alert_blue']}; }}
  a {{ color:{COLOR['info']}; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .pill {{ padding:1px 8px; border-radius:10px; font-size:10px; font-weight:600; }}
  .pill-A {{ background:{COLOR['alert_green']}; color:{COLOR['bull']}; }}
  .pill-B {{ background:{COLOR['alert_amber']}; color:{COLOR['warn']}; }}
  .pill-C {{ background:{COLOR['alert_red']}; color:{COLOR['bear']}; }}
  .pattern {{ font-size:9px; background:{COLOR['alert_amber']}; color:{COLOR['warn']}; padding:1px 4px; border-radius:6px; margin-right:2px; }}
  .tbl-wrap {{ max-height:70vh; overflow:auto; border:0.5px solid {COLOR['border']}; border-radius:6px; -webkit-overflow-scrolling:touch; }}
  .empty {{ padding:32px; text-align:center; color:{COLOR['muted']}; }}

  /* ── 반응형 ────────────────────────────────────────── */
  @media (max-width:1024px) {{
    .wrap {{ max-width:100% !important; }}
    header {{ flex-direction:column; align-items:flex-start; gap:6px; }}
  }}
  @media (max-width:720px) {{
    body {{ padding:14px !important; font-size:12.5px; }}
    h1 {{ font-size:18px !important; }}
    h2 {{ font-size:13px; }}
    #mgmt {{ padding:10px 12px !important; }}
    #mgmt input[type="text"], #mgmt input[type="password"] {{ width:100% !important; max-width:320px; }}
    #mgmt button {{ white-space:nowrap; }}
    .tbl-wrap {{ max-height:none; }}
    th, td {{ padding:4px 6px; font-size:11px; }}
    /* 긴 영문 라벨 대응 */
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
    <div class="meta">Generated {generated}</div>
  </div>
  <div>
    <a href="https://github.com/z66g/sma" target="_blank" aria-label="GitHub" title="GitHub" style="color:{COLOR['muted']};display:inline-flex;align-items:center;">
      <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M12 .5C5.73.5.75 5.48.75 11.75c0 4.98 3.23 9.2 7.71 10.7.56.1.77-.24.77-.54 0-.27-.01-1.16-.02-2.1-3.14.68-3.8-1.34-3.8-1.34-.51-1.3-1.25-1.65-1.25-1.65-1.03-.7.08-.69.08-.69 1.13.08 1.73 1.16 1.73 1.16 1.01 1.73 2.65 1.23 3.3.94.1-.73.4-1.23.72-1.51-2.5-.28-5.14-1.25-5.14-5.57 0-1.23.44-2.23 1.16-3.02-.12-.29-.5-1.43.11-2.98 0 0 .94-.3 3.09 1.15.9-.25 1.86-.37 2.82-.38.96.01 1.92.13 2.82.38 2.15-1.45 3.09-1.15 3.09-1.15.61 1.55.23 2.69.11 2.98.72.79 1.16 1.79 1.16 3.02 0 4.33-2.64 5.29-5.15 5.56.4.35.77 1.04.77 2.1 0 1.52-.01 2.75-.01 3.13 0 .3.2.65.78.54 4.47-1.5 7.7-5.72 7.7-10.7C23.25 5.48 18.27.5 12 .5z"/>
      </svg>
    </a>
  </div>
</header>

<div id="mgmt" style="margin-bottom:16px;background:{COLOR['card']};border:0.5px solid {COLOR['border']};border-radius:6px;padding:14px 16px;">

  <!-- PAT 미설정 시 안내 -->
  <div id="mgmt-need-token" style="display:none;">
    <div style="margin-bottom:8px;font-size:12px;color:{COLOR['muted']};">
      GitHub Personal Access Token이 필요합니다 (브라우저 localStorage만 저장, repo 커밋 X).
      <a href="https://github.com/settings/tokens/new" target="_blank">Classic PAT</a> 발급 →
      <b>repo</b> + <b>workflow</b> 권한.
    </div>
    <input id="pat" type="password" placeholder="ghp_… 또는 github_pat_…" style="width:360px;">
    <button id="pat-save" style="padding:6px 14px;background:{COLOR['info']};color:#fff;border:0;border-radius:4px;cursor:pointer;">저장</button>
    <button id="pat-clear" style="padding:6px 10px;background:none;color:{COLOR['muted']};border:0.5px solid {COLOR['border']};border-radius:4px;cursor:pointer;">지우기</button>
  </div>

  <!-- PAT 설정 후 컨트롤 패널 -->
  <div id="mgmt-panel" style="display:none;">

    <!-- 1) 단일 종목 즉시 분석 -->
    <div style="margin-bottom:14px;">
      <div style="font-size:11px;color:{COLOR['muted']};text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:6px;">
        단일 종목 즉시 분석
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <input id="adhoc-ticker" type="text" placeholder="티커 (예: PLTR)" style="text-transform:uppercase;width:160px;">
        <button id="adhoc-btn" style="padding:4px 10px;background:{COLOR['panel']};color:{COLOR['text']};border:0.5px solid {COLOR['border']};border-radius:4px;cursor:pointer;font-size:12px;">개별분석</button>
      </div>
    </div>

    <!-- 2) 워치리스트 관리 -->
    <div style="border-top:0.5px solid {COLOR['border']};padding-top:14px;">
      <div style="font-size:11px;color:{COLOR['muted']};text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:6px;">
        워치리스트 — 매일 오전 11:00 KST 자동분석
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
  <thead><tr>
    <th>Ticker</th>
    <th>Latest</th>
    <th>Price</th>
    <th>Top Scenario</th>
    <th>Score</th>
    <th>Macro</th>
    <th>Darkpool</th>
    <th>Short Case</th>
    <th>Options</th>
    <th>DP %</th>
    <th>Short %</th>
    <th>CTB %</th>
    <th>Max Pain</th>
    <th>Patterns</th>
    <th>📄</th>
    <th></th>
  </tr></thead>
  <tbody id="tbody-active"></tbody>
</table>
</div>

<details id="archive-wrap" style="margin-top:24px;">
<summary style="cursor:pointer;font-size:14px;font-weight:700;color:{COLOR['muted']};border-bottom:0.5px solid {COLOR['border']};padding-bottom:4px;">히스토리 (Archive) <span id="archive-count" style="font-size:11px;font-weight:400;"></span></summary>
<div class="tbl-wrap" style="margin-top:8px;">
<table id="tbl-archive">
  <thead><tr>
    <th>Ticker</th>
    <th>Latest</th>
    <th>Price</th>
    <th>Top Scenario</th>
    <th>Score</th>
    <th>Macro</th>
    <th>Darkpool</th>
    <th>Short Case</th>
    <th>Options</th>
    <th>DP %</th>
    <th>Short %</th>
    <th>CTB %</th>
    <th>Max Pain</th>
    <th>Patterns</th>
    <th>📄</th>
    <th></th>
  </tr></thead>
  <tbody id="tbody-archive"></tbody>
</table>
</div>
</details>
</div>
</div>
<script>
window.__DB__ = {json.dumps(summary, default=str)};
</script>
<script src="app.js"></script>
</body>
</html>
"""

    app_js = r"""
// Dashboard logic — operates on window.__DB__ (array of ticker summaries)
const DB = window.__DB__ || [];
const bodyActive  = document.getElementById('tbody-active');
const bodyArchive = document.getElementById('tbody-archive');
const archiveWrap = document.getElementById('archive-wrap');
const activeCnt   = document.getElementById('active-count');
const archiveCnt  = document.getElementById('archive-count');

function fmtPct(v, dec){ return v==null ? '-' : (dec==null ? v.toFixed(1) : v.toFixed(dec)) + '%'; }
function fmtDol(v){ return v==null ? '-' : '$' + (+v).toFixed(2); }
function scenarioPill(s, v){
  const cls = 'pill pill-' + s;
  const lbl = { A: '[A] Bull', B: '[B] Neut', C: '[C] Bear' }[s] || s;
  return `<span class="${cls}">${lbl} ${v==null?'':v.toFixed(0)+'%'}</span>`;
}
// L2 case 표기에서 CASE_1_/CASE_2_ 같은 접두어 완전히 제거
function casePretty(c){
  if (!c) return '-';
  return c.replace(/^CASE_\d+_/, '').replace(/^CASE_/, '');
}
function rowHTML(r, mode) {
  // mode: 'active' → × (remove) 버튼, 'archive' → ＋ (add) 버튼, undefined → 버튼 없음
  const patBadges = (r.patterns||[]).map(p => `<span class="pattern">${p}</span>`).join('') || '-';
  const tickerLink = `<a href="tickers/${r.ticker}/"><b>${r.ticker}</b></a>`;
  const dateLink = `<a href="${r.latest_date}/${r.ticker}_3Layer_Forensic_${r.latest_date}.html">${r.latest_date}</a>`;
  let actionBtn = '';
  if (mode === 'archive') {
    actionBtn = `<button class="add-wl-btn" data-t="${r.ticker}" title="워치리스트에 추가" style="padding:1px 7px;background:#1A7F5A;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:12px;font-weight:700;margin-right:4px;">＋</button>`;
  } else if (mode === 'active') {
    actionBtn = `<button class="rm-wl-btn" data-t="${r.ticker}" title="워치리스트에서 제거 (아카이브로 이동)" style="padding:1px 7px;background:#CF222B;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:12px;font-weight:700;margin-right:4px;">×</button>`;
  }
  return `<tr>
    <td>${tickerLink}</td>
    <td>${dateLink}</td>
    <td>${fmtDol(r.price)}</td>
    <td>${scenarioPill(r.scenario, r.scenario_pct)}</td>
    <td>${r.raw_score==null?'-':r.raw_score.toFixed(2)}</td>
    <td>${r.macro_env||'-'}</td>
    <td>${r.l1_scenario||'-'}</td>
    <td>${casePretty(r.l2_case)}</td>
    <td>${r.l3_scenario||'-'}</td>
    <td>${fmtPct(r.dp_pct)}</td>
    <td>${fmtPct(r.short_pct)}</td>
    <td>${fmtPct(r.ctb_fee, 2)}</td>
    <td>${fmtDol(r.max_pain)}</td>
    <td>${patBadges}</td>
    <td><a href="tickers/${r.ticker}/" style="font-weight:600;">${r.report_count}</a></td>
    <td style="white-space:nowrap;">${actionBtn}</td>
  </tr>`;
}

function render() {
  // 티커 알파벳 오름차순 기본 정렬
  const rows = DB.slice().sort((a,b) => a.ticker.localeCompare(b.ticker));

  // Active vs Archive 분리
  const active  = rows.filter(r => r.is_active);
  const archive = rows.filter(r => !r.is_active);

  activeCnt.textContent  = `· ${active.length}개`;
  archiveCnt.textContent = `· ${archive.length}개`;

  bodyActive.innerHTML = active.length
    ? active.map(r => rowHTML(r, 'active')).join('')
    : '<tr><td colspan="16" class="empty">감시 중 종목이 없습니다. 위 ⚙ 패널에서 추가하세요.</td></tr>';

  bodyArchive.innerHTML = archive.length
    ? archive.map(r => rowHTML(r, 'archive')).join('')
    : '<tr><td colspan="16" class="empty">아카이브 비어있음.</td></tr>';

  // 아카이브 비어있으면 details 자체 숨김
  archiveWrap.style.display = archive.length ? '' : 'none';
}

render();

// ───── Watchlist management via GitHub API ─────
const REPO = 'z66g/sma';
const API  = 'https://api.github.com';
const LS_KEY = 'sma_github_pat';

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

let ticksCache = [];     // current tickers.txt lines (non-comment)
let txtSha = null;        // current file SHA for PUT

function getPAT() { return localStorage.getItem(LS_KEY) || ''; }
function setPAT(v) { localStorage.setItem(LS_KEY, v); }
function clearPAT() { localStorage.removeItem(LS_KEY); }

function showPanel(hasToken) {
  mgmtNeedToken.style.display = hasToken ? 'none' : 'block';
  mgmtPanel.style.display     = hasToken ? 'block' : 'none';
}
function setStatus(msg, isError) {
  // innerHTML 사용: 우리가 직접 조합한 <a>/<button> 조각을 렌더 가능하게
  // (외부 사용자 입력을 직접 넣는 경로 없으므로 안전)
  statusEl.innerHTML = msg || '';
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
    const err = await res.json().catch(()=>({}));
    throw new Error(`GitHub ${res.status}: ${err.message || res.statusText}`);
  }
  return res.status === 204 ? null : res.json();
}

function parseTickers(text) {
  return text.split('\n')
    .map(l => l.trim())
    .filter(l => l && !l.startsWith('#'));
}

async function loadTickers() {
  setStatus('tickers.txt 로드 중...');
  // 캐시 우회: 같은 페이지 세션 내에서 직전 수정이 즉시 반영되도록 타임스탬프 쿼리
  const j = await gh(`/repos/${REPO}/contents/tickers.txt?ref=main&_=${Date.now()}`);
  txtSha = j.sha;
  const text = atob(j.content.replace(/\n/g, ''));
  ticksCache = parseTickers(text);

  // ── 핵심: 로컬 DB의 is_active 를 **실시간 tickers.txt** 기준으로 재동기화 ──
  // (대시보드 정적 빌드 시 박힌 is_active 가 stale 상태일 수 있으므로,
  //  pill 목록과 Active/Archive 테이블 사이의 불일치를 방지)
  const active = new Set(ticksCache);
  let flipped = 0;
  for (const r of DB) {
    const shouldBeActive = active.has(r.ticker);
    if (r.is_active !== shouldBeActive) { r.is_active = shouldBeActive; flipped++; }
  }

  renderWL();
  if (flipped > 0) render();   // 테이블 재렌더해서 동기화 즉시 반영
  setStatus(`${ticksCache.length}개 감시 중 · last commit ${j.sha.slice(0,7)}`
            + (flipped ? ` · ${flipped}개 행 동기화` : ""));
}

function renderWL() {
  if (!ticksCache.length) {
    wlList.innerHTML = '<span style="color:#656D76;font-size:11px;">감시 종목 없음</span>';
    return;
  }
  wlList.innerHTML = ticksCache.map(t =>
    `<span style="background:#DDF4FF;color:#0969DA;padding:3px 4px 3px 10px;border-radius:12px;font-size:12px;font-weight:600;display:inline-flex;align-items:center;gap:6px;">
       ${t}
       <button data-t="${t}" class="rm-btn" style="background:none;border:0;color:#CF222B;cursor:pointer;font-size:14px;padding:0 6px;">×</button>
     </span>`
  ).join('');
  wlList.querySelectorAll('.rm-btn').forEach(b =>
    b.addEventListener('click', () => removeTicker(b.dataset.t))
  );
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
  setStatus(`✅ 커밋 완료: ${message} (${txtSha.slice(0,7)})`);
}

async function addTicker() {
  const t = (newTickerEl.value || '').trim().toUpperCase();
  if (!t) return;
  if (!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(t)) {
    setStatus(`"${t}"는 유효한 티커 형식이 아닙니다`, true); return;
  }
  if (ticksCache.includes(t)) {
    setStatus(`${t}는 이미 등록됨`, true); return;
  }
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
    // 로컬 DB에 반영 → 테이블 재렌더 → Active에서 Archive로 이동
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
  // GitHub은 dispatch 후 run이 나타나기까지 몇 초 걸림.
  // 1차: workflow runs 목록에서 dispatchTime 이후의 event=workflow_dispatch run을 찾기.
  const deadline = Date.now() + (timeoutMs || 15 * 60 * 1000);
  let runId = null, runUrl = null;
  const pre = Date.now();
  setStatus(`${label}: 워크플로우 등록 대기 중...`);
  while (!runId && Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 4000));
    try {
      const j = await gh(`/repos/${REPO}/actions/workflows/daily-analysis.yml/runs?event=workflow_dispatch&per_page=5`);
      const found = (j.workflow_runs || []).find(r =>
        new Date(r.created_at).getTime() >= dispatchTime - 3000);
      if (found) { runId = found.id; runUrl = found.html_url; }
    } catch (_) {}
  }
  if (!runId) throw new Error('워크플로우 run ID 조회 실패');
  // 2차: 상태 폴링
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 12000));
    try {
      const r = await gh(`/repos/${REPO}/actions/runs/${runId}`);
      const elapsed = Math.round((Date.now() - dispatchTime) / 1000);
      const statusStr = r.status === 'completed'
        ? (r.conclusion === 'success' ? '✅ 완료' : `❌ ${r.conclusion}`)
        : r.status;
      setStatus(
        `${label}: ${statusStr} · ${elapsed}s 경과 · <a href="${runUrl}" target="_blank" style="color:#0969DA;">Actions 보기</a>`
        + (r.status === 'completed' && r.conclusion === 'success'
           ? ` · <button onclick="location.reload()" style="padding:2px 10px;background:#1A7F5A;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:11px;">새로고침</button>`
           : '')
      );
      if (r.status === 'completed') return r;
    } catch (_) {}
  }
  throw new Error('타임아웃 (15분 초과)');
}

async function runWorkflow(singleTicker) {
  const inputs = {};
  if (singleTicker) inputs.ticker = singleTicker;
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
  const t = (document.getElementById('adhoc-ticker').value || '').trim().toUpperCase();
  if (!t) { setStatus('티커를 입력하세요', true); return; }
  if (!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(t)) { setStatus(`"${t}"는 유효한 티커 형식이 아닙니다`, true); return; }
  document.getElementById('adhoc-ticker').value = '';
  await runWorkflow(t);
}

// ── Archive ＋ / Active × 버튼 클릭 위임 ─────────────────────
document.addEventListener('click', async (e) => {
  // Archive → Watchlist 승격
  const addBtn = e.target.closest('.add-wl-btn');
  if (addBtn) {
    e.preventDefault();
    const t = addBtn.dataset.t;
    if (!t || !getPAT()) { setStatus('PAT 필요', true); return; }
    if (ticksCache.includes(t)) { setStatus(`${t}는 이미 워치리스트에 있음`); return; }
    addBtn.disabled = true;
    addBtn.textContent = '…';
    try {
      ticksCache.push(t); ticksCache.sort();
      await commitTickers(`watchlist: promote ${t} from archive`);
      const row = DB.find(x => x.ticker === t);
      if (row) row.is_active = true;
      renderWL();
      render();
      setStatus(`✅ ${t} 워치리스트로 이동됨 (매일 11:00 KST 자동분석 포함)`);
    } catch (err) {
      ticksCache = ticksCache.filter(x => x !== t);
      addBtn.disabled = false;
      addBtn.textContent = '＋';
      setStatus('실패: ' + err.message, true);
    }
    return;
  }
  // Active 테이블의 × 버튼 → removeTicker 호출 (확인창 포함)
  const rmBtn = e.target.closest('.rm-wl-btn');
  if (rmBtn) {
    e.preventDefault();
    const t = rmBtn.dataset.t;
    if (!t || !getPAT()) { setStatus('PAT 필요', true); return; }
    await removeTicker(t);
  }
});

patSaveBtn.addEventListener('click', () => {
  const v = patInput.value.trim();
  if (!v.startsWith('ghp_') && !v.startsWith('github_pat_')) {
    setStatus('유효한 PAT 형식이 아님 (ghp_ 또는 github_pat_ 시작)', true);
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

// 초기화
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
    return html, app_js


def main():
    db = scan_reports()
    tickers = sorted(db.keys())

    # 1) 종목별 JSON 덤프
    for t, series in db.items():
        (DATA_DIR / f"{t}.json").write_text(
            json.dumps(series, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8"
        )
    # 전체 덤프
    (DATA_DIR / "all.json").write_text(
        json.dumps(db, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8"
    )

    # 2) 종목 전용 페이지
    for t, series in db.items():
        tdir = TICKERS_DIR / t
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "index.html").write_text(render_ticker_page(t, series), encoding="utf-8")

    # 3) 메인 대시보드
    watchlist = load_watchlist()
    html, js = render_dashboard(db, watchlist)
    (REPORTS / "index.html").write_text(html, encoding="utf-8")
    (REPORTS / "app.js").write_text(js, encoding="utf-8")

    total = sum(len(v) for v in db.values())
    print(f"[index] {len(tickers)} tickers · {total} reports")
    print(f"[index] wrote reports/index.html, reports/app.js")
    print(f"[index] wrote reports/data/{{{','.join(tickers[:5])}{',…' if len(tickers)>5 else ''}}}.json + all.json")
    print(f"[index] wrote reports/tickers/{{TICKER}}/index.html for {len(tickers)} tickers")

if __name__ == "__main__":
    main()
