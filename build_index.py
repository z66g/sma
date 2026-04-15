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
    inst_obv = [(s.get("l1") or {}).get("delta_institutional") for s in series]
    retail_obv = [(s.get("l1") or {}).get("delta_retail") for s in series]

    # 히스토리 테이블
    rows = []
    for s in reversed(series):
        sc = s.get("scenarios") or {}
        sl1 = s.get("l1") or {}; sl2 = s.get("l2") or {}; sl3 = s.get("l3") or {}
        d = s["date"]
        scen_label = "[A]Bull" if sc.get("A_bullish",0) >= max(sc.get("B_neutral",0), sc.get("C_bearish",0)) \
                     else "[B]Neut" if sc.get("B_neutral",0) >= sc.get("C_bearish",0) \
                     else "[C]Bear"
        color = COLOR["bull"] if "Bull" in scen_label else COLOR["bear"] if "Bear" in scen_label else COLOR["warn"]
        rows.append(
            f'<tr>'
            f'<td>{d}</td>'
            f'<td>${s.get("price",0):.2f}</td>'
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
<title>{ticker} · Smart Money History</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body {{ font-family:{FONT}; background:{COLOR['bg']}; color:{COLOR['text']}; margin:0; padding:24px; font-size:13px; }}
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
  @media (max-width:720px) {{ .chart-grid {{ grid-template-columns:1fr; }} }}
  .nav {{ margin-bottom:16px; font-size:12px; }}
  .nav a {{ color:{COLOR['muted']}; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="nav"><a href="../../">← 전체 대시보드</a></div>
  <h1>{ticker}</h1>
  <div class="meta">Latest: {latest.get('date','N/A')} · ${latest.get('price',0):.2f} · {len(series)}개 리포트 · {pattern_badges}</div>

  <h2>핵심 지표 (최신)</h2>
  <div class="grid">
    <div class="card"><div class="card-label">Scenario [A] Bullish</div>
      <div class="card-value" style="color:{COLOR['bull']};">{scen.get('A_bullish',0):.1f}%</div>
      <div class="card-sub">Score {scen.get('raw_score',0):+.2f} · Macro {latest.get('macro_env','-')}</div>
    </div>
    <div class="card"><div class="card-label">Dark Pool % (L1)</div>
      <div class="card-value">{(f'{l1.get("dp_pct"):.1f}%' if l1.get("dp_pct") is not None else "N/A")}</div>
      <div class="card-sub">IAR {l1.get('iar') or 0:.2f} · {l1.get('divergence','-')}</div>
    </div>
    <div class="card"><div class="card-label">Short % · CTB (L2)</div>
      <div class="card-value">{(f'{l2.get("short_pct_latest"):.1f}%' if l2.get("short_pct_latest") is not None else "N/A")} · {(f'{l2.get("ctb_fee"):.2f}%' if l2.get("ctb_fee") is not None else "N/A")}</div>
      <div class="card-sub">{l2.get('case','-')}</div>
    </div>
    <div class="card"><div class="card-label">Max Pain · GEX Flip (L3)</div>
      <div class="card-value">{('$'+format(l3.get('max_pain'),'.2f')) if l3.get('max_pain') else 'N/A'} · {('$'+format(l3.get('flip_zone'),'.2f')) if l3.get('flip_zone') else 'N/A'}</div>
      <div class="card-sub">DTE {l3.get('dte','-')} · {l3.get('scenario','-')}</div>
    </div>
  </div>

  <h2>시계열 추이</h2>
  <div class="chart-grid">
    <div class="card"><div class="card-label">Price</div><canvas id="priceChart"></canvas></div>
    <div class="card"><div class="card-label">Scenario Probabilities (%)</div><canvas id="probChart"></canvas></div>
    <div class="card"><div class="card-label">Short % · CTB Fee</div><canvas id="shortChart"></canvas></div>
    <div class="card"><div class="card-label">Max Pain · GEX Flip</div><canvas id="optChart"></canvas></div>
    <div class="card" style="grid-column:1/-1;"><div class="card-label">OBV Δ (Institutional vs Retail)</div><canvas id="obvChart"></canvas></div>
  </div>

  <h2>전체 히스토리</h2>
  <table>
    <thead><tr><th>Date</th><th>Price</th><th>Top Scenario</th><th>Darkpool</th><th>Short Case</th><th>Options</th><th>Link</th></tr></thead>
    <tbody>{''.join(rows) if rows else '<tr><td colspan="7">아직 리포트 없음</td></tr>'}</tbody>
  </table>

  <h2>🤖 AI 내러티브 아카이브</h2>
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
Chart.defaults.plugins.legend.labels.boxWidth = 12;
const DATES = {json.dumps(dates)};
function lineChart(id, datasets) {{
  new Chart(document.getElementById(id), {{
    type:'line',
    data:{{ labels:DATES, datasets:datasets }},
    options:{{ responsive:true, maintainAspectRatio:false,
      plugins:{{legend:{{display:datasets.length>1,position:'bottom'}}}},
      scales:{{y:{{grid:{{color:'{COLOR['panel']}'}}}},x:{{grid:{{display:false}}}}}},
      elements:{{point:{{radius:2}},line:{{tension:0.3,borderWidth:2}}}},
    }}
  }});
}}
lineChart('priceChart', [{{label:'Price', data:{json.dumps(prices)}, borderColor:'{COLOR["info"]}', backgroundColor:'{COLOR["alert_blue"]}'}}]);
lineChart('probChart', [
  {{label:'[A] Bullish', data:{json.dumps(bull_probs)}, borderColor:'{COLOR["bull"]}', backgroundColor:'transparent'}},
  {{label:'[C] Bearish', data:{json.dumps(bear_probs)}, borderColor:'{COLOR["bear"]}', backgroundColor:'transparent'}}
]);
lineChart('shortChart', [
  {{label:'Short %', data:{json.dumps(short_pct)}, borderColor:'{COLOR["bear"]}', yAxisID:'y', backgroundColor:'transparent'}},
  {{label:'CTB %', data:{json.dumps(ctb)}, borderColor:'{COLOR["warn"]}', yAxisID:'y', backgroundColor:'transparent'}}
]);
lineChart('optChart', [
  {{label:'Max Pain', data:{json.dumps(max_pain)}, borderColor:'{COLOR["warn"]}', backgroundColor:'transparent'}},
  {{label:'GEX Flip', data:{json.dumps(flip)}, borderColor:'{COLOR["info"]}', backgroundColor:'transparent'}}
]);
lineChart('obvChart', [
  {{label:'Institutional Δ', data:{json.dumps(inst_obv)}, borderColor:'{COLOR["bull"]}', backgroundColor:'transparent'}},
  {{label:'Retail Δ', data:{json.dumps(retail_obv)}, borderColor:'{COLOR["bear"]}', backgroundColor:'transparent'}}
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

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Smart Money Analyzer — Dashboard</title>
<style>
  body {{ font-family:{FONT}; background:{COLOR['bg']}; color:{COLOR['text']}; margin:0; padding:24px; font-size:13px; }}
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
  .tbl-wrap {{ max-height:70vh; overflow:auto; border:0.5px solid {COLOR['border']}; border-radius:6px; }}
  .empty {{ padding:32px; text-align:center; color:{COLOR['muted']}; }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1>Smart Money Analyzer</h1>
    <div class="meta">Generated {generated}</div>
  </div>
  <div>
    <a href="https://github.com/z66g/sma" style="font-size:11px;">GitHub</a>
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
        단일 종목 즉시 분석 — 워치리스트 변경 없이 1회만 수행
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <input id="adhoc-ticker" type="text" placeholder="티커 (예: PLTR)" style="text-transform:uppercase;width:160px;">
        <button id="adhoc-btn" style="padding:6px 14px;background:{COLOR['info']};color:#fff;border:0;border-radius:4px;cursor:pointer;">▶ 분석 실행</button>
        <span style="font-size:11px;color:{COLOR['muted']};">→ 결과는 ‘히스토리(Archive)’에 저장</span>
      </div>
    </div>

    <!-- 2) 워치리스트 관리 -->
    <div style="border-top:0.5px solid {COLOR['border']};padding-top:14px;">
      <div style="font-size:11px;color:{COLOR['muted']};text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:6px;">
        워치리스트 — 매일 오전 10:30 KST 자동 분석되는 종목
      </div>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap;">
        <input id="new-ticker" type="text" placeholder="티커 추가 (예: PLTR)" style="text-transform:uppercase;width:160px;">
        <button id="add-btn" style="padding:6px 14px;background:{COLOR['bull']};color:#fff;border:0;border-radius:4px;cursor:pointer;">＋ 워치리스트에 추가</button>
        <button id="run-btn" style="padding:6px 14px;background:{COLOR['warn']};color:#fff;border:0;border-radius:4px;cursor:pointer;">▶ 워치리스트 전체 즉시 분석</button>
        <button id="pat-edit" style="padding:6px 10px;background:none;color:{COLOR['muted']};border:0.5px solid {COLOR['border']};border-radius:4px;cursor:pointer;font-size:11px;margin-left:auto;">PAT 변경</button>
      </div>
      <div id="wl-list" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px;"></div>
    </div>

    <div id="mgmt-status" style="font-size:11px;color:{COLOR['muted']};min-height:16px;margin-top:8px;"></div>
  </div>
</div>

<div class="controls">
  <input id="q" type="text" placeholder="티커 검색 (예: NVDA)...">
  <select id="filter-scenario">
    <option value="">시나리오 전체</option>
    <option value="A">[A] Bullish 우세</option>
    <option value="B">[B] Neutral 우세</option>
    <option value="C">[C] Bearish 우세</option>
  </select>
  <select id="filter-pattern">
    <option value="">패턴 전체</option>
    <option value="FINAL_ABSORPTION">FINAL_ABSORPTION</option>
    <option value="THETA_BURN">THETA_BURN</option>
    <option value="GAMMA_SQUEEZE_SETUP">GAMMA_SQUEEZE_SETUP</option>
    <option value="SHORT_SQUEEZE_RISK">SHORT_SQUEEZE_RISK</option>
    <option value="LOW_CTB_PARADOX">LOW_CTB_PARADOX</option>
  </select>
  <select id="sort">
    <option value="ticker">Ticker ↓</option>
    <option value="scenario_pct-desc">확률 높은순</option>
    <option value="raw_score-desc">Score 높은순</option>
    <option value="raw_score-asc">Score 낮은순</option>
    <option value="ctb_fee-desc">CTB 높은순</option>
    <option value="short_pct-desc">Short% 높은순</option>
  </select>
  <span id="count" style="color:{COLOR['muted']};font-size:11px;"></span>
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
    <th>Reports</th>
    <th></th>
  </tr></thead>
  <tbody id="tbody-active"></tbody>
</table>
</div>

<details id="archive-wrap" style="margin-top:24px;">
<summary style="cursor:pointer;font-size:14px;font-weight:700;color:{COLOR['muted']};border-bottom:0.5px solid {COLOR['border']};padding-bottom:4px;">히스토리 (Archive — 과거에 분석했지만 현재 감시 목록에 없는 종목) <span id="archive-count" style="font-size:11px;font-weight:400;"></span></summary>
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
    <th>Reports</th>
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
const q = document.getElementById('q');
const fScenario = document.getElementById('filter-scenario');
const fPattern = document.getElementById('filter-pattern');
const sortSel = document.getElementById('sort');
const countEl = document.getElementById('count');

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
function rowHTML(r, showAdd) {
  const patBadges = (r.patterns||[]).map(p => `<span class="pattern">${p}</span>`).join('') || '-';
  const tickerLink = `<a href="tickers/${r.ticker}/"><b>${r.ticker}</b></a>`;
  const dateLink = `<a href="${r.latest_date}/${r.ticker}_3Layer_Forensic_${r.latest_date}.html">${r.latest_date}</a>`;
  const addBtn = showAdd
    ? `<button class="add-wl-btn" data-t="${r.ticker}" title="워치리스트에 추가" style="padding:1px 7px;background:#1A7F5A;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:12px;font-weight:700;margin-right:4px;">＋</button>`
    : '';
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
    <td>${r.report_count}</td>
    <td style="white-space:nowrap;">${addBtn}<a href="tickers/${r.ticker}/">›</a></td>
  </tr>`;
}

function render() {
  const qv = (q.value||'').toUpperCase().trim();
  const fs = fScenario.value;
  const fp = fPattern.value;
  let rows = DB.filter(r => {
    if (qv && !r.ticker.includes(qv)) return false;
    if (fs && r.scenario !== fs) return false;
    if (fp && !(r.patterns||[]).includes(fp)) return false;
    return true;
  });
  const [key, dir] = (sortSel.value.split('-'));
  rows.sort((a,b)=>{
    const av = a[key]; const bv = b[key];
    if (av==null && bv==null) return 0;
    if (av==null) return 1;
    if (bv==null) return -1;
    if (typeof av === 'string') return dir==='desc' ? bv.localeCompare(av) : av.localeCompare(bv);
    return dir==='desc' ? bv-av : av-bv;
  });

  // Active vs Archive 분리
  const active  = rows.filter(r => r.is_active);
  const archive = rows.filter(r => !r.is_active);

  countEl.textContent = `${rows.length} / ${DB.length} tickers (active ${active.length} / archive ${archive.length})`;
  activeCnt.textContent  = `· ${active.length}개`;
  archiveCnt.textContent = `· ${archive.length}개`;

  bodyActive.innerHTML = active.length
    ? active.map(r => rowHTML(r, false)).join('')
    : '<tr><td colspan="16" class="empty">감시 중 종목이 없습니다. 위 ⚙ 패널에서 추가하세요.</td></tr>';

  bodyArchive.innerHTML = archive.length
    ? archive.map(r => rowHTML(r, true)).join('')
    : '<tr><td colspan="16" class="empty">아카이브 비어있음.</td></tr>';

  // 아카이브 비어있으면 details 자체 숨김
  archiveWrap.style.display = archive.length ? '' : 'none';
}

[q, fScenario, fPattern, sortSel].forEach(el => el.addEventListener('input', render));
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
  statusEl.textContent = msg || '';
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
  const j = await gh(`/repos/${REPO}/contents/tickers.txt`);
  txtSha = j.sha;
  const text = atob(j.content.replace(/\n/g, ''));
  ticksCache = parseTickers(text);
  renderWL();
  setStatus(`${ticksCache.length}개 감시 중 · last commit ${j.sha.slice(0,7)}`);
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
  if (!confirm(`${t} 제거?`)) return;
  const prev = ticksCache.slice();
  ticksCache = ticksCache.filter(x => x !== t);
  try {
    await commitTickers(`watchlist: remove ${t}`);
    renderWL();
  } catch (e) {
    ticksCache = prev;
    setStatus('실패: ' + e.message, true);
  }
}

// ── 워크플로우 dispatch + status polling ─────────────────────────
async function dispatchWorkflow(inputs) {
  const dispatchTime = Date.now();
  await gh(`/repos/${REPO}/actions/workflows/daily.yml/dispatches`, {
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
      const j = await gh(`/repos/${REPO}/actions/workflows/daily.yml/runs?event=workflow_dispatch&per_page=5`);
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

// ── Archive ＋ 버튼: 해당 티커를 워치리스트로 이동 ─────────
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.add-wl-btn');
  if (!btn) return;
  e.preventDefault();
  const t = btn.dataset.t;
  if (!t || !getPAT()) { setStatus('PAT 필요', true); return; }
  if (ticksCache.includes(t)) { setStatus(`${t}는 이미 워치리스트에 있음`); return; }
  btn.disabled = true;
  btn.textContent = '…';
  try {
    ticksCache.push(t); ticksCache.sort();
    await commitTickers(`watchlist: promote ${t} from archive`);
    // 로컬 DB에 is_active 반영 + 즉시 리렌더
    const row = DB.find(x => x.ticker === t);
    if (row) row.is_active = true;
    renderWL();
    render();
    setStatus(`✅ ${t} 워치리스트로 이동됨 (매일 10:30 KST 자동 분석 포함)`);
  } catch (err) {
    ticksCache = ticksCache.filter(x => x !== t);
    btn.disabled = false;
    btn.textContent = '＋';
    setStatus('실패: ' + err.message, true);
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
