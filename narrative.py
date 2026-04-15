#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Server-side Claude narrative generator.

CLI:
  python3 narrative.py TICKER [--mode weekly|daily|adhoc] [--days 30]

reads  : reports/data/{TICKER}.json       (시계열 DB, build_index.py 가 관리)
writes : reports/tickers/{TICKER}/narratives/{YYYY-MM-DD}-{MODE}.html
env    : ANTHROPIC_API_KEY  (필수)

같은 내용을 브라우저 narrative.js 가 on-demand 로 생성함. 본 스크립트는
GitHub Actions 스케줄에서 돌아 weekly 자동 생성 또는 수동 dispatch 용.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "reports" / "data"
TICKERS_DIR = ROOT / "reports" / "tickers"

ANTH_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
if not ANTH_KEY:
    print("[FATAL] ANTHROPIC_API_KEY env var missing", file=sys.stderr)
    sys.exit(2)

API  = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("SMA_CLAUDE_MODEL", "claude-sonnet-4-5")
MAX_TURNS = 5

# ── 시스템 프롬프트 (narrative.js 와 동일한 버전) ──
SYSTEM_PROMPT = r"""You are a Smart Money forensic analyst following the CLAUDE.md framework.

CORE STANCE:
- Zero bullish/bearish bias. Data and capital flows ONLY.
- Architect perspective — interpret unusual signals as intentional market design first.
- Never fabricate values not in the JSON/CSV input.
- Never exceed the MAX_CONFIDENCE cap.
- Prefer "insufficient data" to speculation.

LAYERS:
- L1 Dark Pool: delta_institutional = FINRA CNMS signed off-exchange volume (~100% institutional). IAR = |inst|/(|pro|+|retail|). High IAR = dark pool dominates.
- L2 Short Volume: FINRA Reg SHO short volume != short interest. 40-55% is structurally normal. Judge only by slope + CTB + anomaly_z. NEVER read "short% > 50" as bearish.
- L3 Options: Max Pain, P/C, Net GEX, Flip Zone, IV Skew. Pinning meaningful only DTE <= 5. Positive Net GEX = long gamma (pin regime). Flip crossing = vol regime change.
- L4 Chart: MA alignment, BB width, immediate/key S&R.

WEIGHTS: L1=0.35, L2=0.20, L3=0.30, L4=0.15.

PATTERNS:
- FINAL_ABSORPTION: DP% > 40, short slope < 0, CTB delta in [-5,+5]%, inst OBV > 0 (3+ of 5)
- THETA_BURN: 3+ days low vol + tight range + near-zero inst OBV
- LOW_CTB_PARADOX: CTB < 1% + shares_available down > 5% = quiet institutional shorting
- GAMMA_SQUEEZE_SETUP: P/C OI < 0.7 + P/C Vol < 0.7 + rising call OI
- SHORT_SQUEEZE_RISK: CTB rising > 5% + short slope rising + HTB

ARCHITECT PHASES (N_days >= 7 only): Phase 1 past / Phase 2 present / Phase 3 future (trading days only).

====================================================================
OUTPUT: FILL THIS EXACT HTML TEMPLATE. NO MARKDOWN. INLINE STYLES ONLY.
====================================================================

CRITICAL: Copy the template below verbatim. Replace ONLY text inside {{...}} or the content between tags.
DO NOT omit any section. DO NOT change the class names, style attributes, or structure.
The 2x2 CARD GRID and the PROBABILITY BAR are MANDATORY visual elements — not optional.

==== TEMPLATE START (copy this entire block, fill content) ====

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1F2328;">

<!-- title -->
<div style="margin-bottom:16px;">
  <h1 style="font-size:20px;font-weight:700;margin:0 0 4px 0;color:#1F2328;">{{TICKER}} Smart Money Forensic · {{DATE}}</h1>
  <div style="font-size:11px;color:#656D76;">{{price_and_patterns_oneliner}}</div>
</div>

<!-- 1. KEY FINDING -->
<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:0.5px solid #9A6700;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#9A6700;font-weight:700;font-size:14px;">▶ 핵심 발견</span>
  <span style="background:{{kf_badge_bg}};color:{{kf_badge_fg}};padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;">{{KF_BADGE_TEXT}}</span>
</div>
<p style="font-size:13px;line-height:1.7;color:#1F2328;margin:0;">{{핵심 1-2문장, 패러독스/충돌 우선}}</p>

<!-- 2. 4-LAYER SYNTHESIS (2x2 GRID) -->
<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:0.5px solid #9A6700;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#9A6700;font-weight:700;font-size:14px;">▶ 4-LAYER SYNTHESIS</span>
</div>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;">
  <div style="background:#F6F8FA;border:0.5px solid #D0D7DE;border-radius:6px;padding:12px;">
    <div style="font-size:10px;color:#656D76;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">L1 DARK POOL</div>
    <div style="font-size:15px;font-weight:600;color:{{l1_color}};margin-bottom:6px;">{{l1_scenario_label}}</div>
    <div style="font-size:12px;line-height:1.6;color:#1F2328;">{{L1 설명: 기관 OBV 방향·규모·IAR·Divergence 해석, 실제 수치 인용}}</div>
  </div>
  <div style="background:#F6F8FA;border:0.5px solid #D0D7DE;border-radius:6px;padding:12px;">
    <div style="font-size:10px;color:#656D76;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">L2 SHORT · CTB</div>
    <div style="font-size:15px;font-weight:600;color:{{l2_color}};margin-bottom:6px;">{{l2_scenario_label}}</div>
    <div style="font-size:12px;line-height:1.6;color:#1F2328;">{{L2 설명: slope + CTB 조합. 절대 %를 bearish로 해석 금지}}</div>
  </div>
  <div style="background:#F6F8FA;border:0.5px solid #D0D7DE;border-radius:6px;padding:12px;">
    <div style="font-size:10px;color:#656D76;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">L3 OPTIONS</div>
    <div style="font-size:15px;font-weight:600;color:{{l3_color}};margin-bottom:6px;">{{l3_scenario_label}}</div>
    <div style="font-size:12px;line-height:1.6;color:#1F2328;">{{L3 설명: Max Pain 거리, Net GEX 부호, Flip Zone 위치 regime}}</div>
  </div>
  <div style="background:#F6F8FA;border:0.5px solid #D0D7DE;border-radius:6px;padding:12px;">
    <div style="font-size:10px;color:#656D76;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">L4 CHART</div>
    <div style="font-size:15px;font-weight:600;color:{{l4_color}};margin-bottom:6px;">{{l4_scenario_label}}</div>
    <div style="font-size:12px;line-height:1.6;color:#1F2328;">{{L4 설명: MA 배열, BB 폭, S/R 거리}}</div>
  </div>
</div>

<!-- 3. ARCHITECT PHASE (skip structure if N_days < 7; replace with amber notice) -->
<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:0.5px solid #9A6700;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#9A6700;font-weight:700;font-size:14px;">▶ ARCHITECT PHASE TIMELINE</span>
  <span style="background:#DDF4FF;color:#0969DA;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;">{{N_days}}일 관찰</span>
</div>
<table style="border-collapse:collapse;width:100%;font-size:12px;margin-top:4px;">
  <thead>
    <tr>
      <th style="background:#EAEEF2;color:#656D76;padding:6px 8px;border:0.5px solid #D0D7DE;text-align:left;">Phase</th>
      <th style="background:#EAEEF2;color:#656D76;padding:6px 8px;border:0.5px solid #D0D7DE;text-align:left;">Dates (trading days)</th>
      <th style="background:#EAEEF2;color:#656D76;padding:6px 8px;border:0.5px solid #D0D7DE;text-align:left;">Description</th>
    </tr>
  </thead>
  <tbody>
    <tr style="background:#FFFFFF;"><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">Phase 1 Setup</td><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">{{p1_dates}}</td><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">{{p1_desc}}</td></tr>
    <tr style="background:#F6F8FA;"><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">Phase 2 Transition</td><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">{{p2_dates}}</td><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">{{p2_desc}}</td></tr>
    <tr style="background:#FFFFFF;"><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">Phase 3 Resolution</td><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">{{p3_dates}}</td><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">{{p3_desc_with_targets}}</td></tr>
  </tbody>
</table>

<!-- 4. PROBABILITY MATRIX (MANDATORY HORIZONTAL BARS) -->
<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:0.5px solid #9A6700;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#9A6700;font-weight:700;font-size:14px;">▶ PROBABILITY MATRIX</span>
  <span style="background:#DDF4FF;color:#0969DA;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;">Raw Score {{raw_score}}</span>
</div>
<div style="display:flex;flex-direction:column;gap:6px;font-size:12px;">
  <div style="display:flex;align-items:center;gap:10px;">
    <span style="width:100px;color:#1A7F5A;font-weight:600;">[A] Bullish</span>
    <div style="flex:1;background:#EAEEF2;border-radius:4px;overflow:hidden;height:16px;"><div style="width:{{A_pct}}%;background:#1A7F5A;height:16px;"></div></div>
    <span style="width:48px;text-align:right;font-weight:600;color:#1A7F5A;">{{A_pct}}%</span>
  </div>
  <div style="display:flex;align-items:center;gap:10px;">
    <span style="width:100px;color:#9A6700;font-weight:600;">[B] Neutral</span>
    <div style="flex:1;background:#EAEEF2;border-radius:4px;overflow:hidden;height:16px;"><div style="width:{{B_pct}}%;background:#9A6700;height:16px;"></div></div>
    <span style="width:48px;text-align:right;font-weight:600;color:#9A6700;">{{B_pct}}%</span>
  </div>
  <div style="display:flex;align-items:center;gap:10px;">
    <span style="width:100px;color:#CF222B;font-weight:600;">[C] Bearish</span>
    <div style="flex:1;background:#EAEEF2;border-radius:4px;overflow:hidden;height:16px;"><div style="width:{{C_pct}}%;background:#CF222B;height:16px;"></div></div>
    <span style="width:48px;text-align:right;font-weight:600;color:#CF222B;">{{C_pct}}%</span>
  </div>
</div>
<div style="font-size:11px;color:#656D76;margin-top:6px;">Macro: {{macro_env}} · Patterns: {{patterns_or_none}}</div>

<!-- 5. DECISIVE TRIGGERS -->
<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:0.5px solid #9A6700;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#9A6700;font-weight:700;font-size:14px;">▶ DECISIVE TRIGGERS</span>
</div>
<table style="border-collapse:collapse;width:100%;font-size:12px;">
  <thead>
    <tr>
      <th style="background:#EAEEF2;color:#656D76;padding:6px 8px;border:0.5px solid #D0D7DE;text-align:left;">Trigger</th>
      <th style="background:#EAEEF2;color:#656D76;padding:6px 8px;border:0.5px solid #D0D7DE;text-align:left;">Direction</th>
      <th style="background:#EAEEF2;color:#656D76;padding:6px 8px;border:0.5px solid #D0D7DE;text-align:left;">Threshold</th>
      <th style="background:#EAEEF2;color:#656D76;padding:6px 8px;border:0.5px solid #D0D7DE;text-align:left;">Implication</th>
    </tr>
  </thead>
  <tbody>
    <!-- 3~5 rows with direction pills: bullish=#DAFBE1/#1A7F5A, bearish=#FFEBE9/#CF222B, warn=#FFF8C5/#9A6700 -->
    <tr style="background:#FFFFFF;"><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">{{trigger1_name}}</td><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">{{pill}}</td><td style="padding:6px 8px;border:0.5px solid #D0D7DE;font-family:monospace;">{{threshold1}}</td><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">{{implication1}}</td></tr>
  </tbody>
</table>

<!-- 6. CONFIDENCE & LIMITATIONS -->
<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:0.5px solid #9A6700;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#9A6700;font-weight:700;font-size:14px;">▶ CONFIDENCE & LIMITATIONS</span>
  <span style="background:{{conf_bg}};color:{{conf_fg}};padding:2px 10px;border-radius:12px;font-size:11px;font-weight:700;">{{MAX_CONFIDENCE}}</span>
</div>
<p style="font-size:12px;line-height:1.7;color:#1F2328;margin:0;">{{N_days일 시계열 · 누락 지표 · PARTIAL 섹션 등 data gap 명시}}</p>

</div>

==== TEMPLATE END ====

COLOR MAPPING:
- signal=BULLISH -> #1A7F5A
- signal=BEARISH -> #CF222B
- signal=NEUTRAL -> #9A6700
- confidence HIGH       -> #DAFBE1/#1A7F5A
- confidence MEDIUM     -> #FFF8C5/#9A6700
- confidence MEDIUM_LOW -> #FFF8C5/#9A6700
- confidence LOW        -> #FFEBE9/#CF222B

PROHIBITIONS:
- Markdown syntax 금지 (# ## - ** fenced code).
- <script>, <style>, <iframe>, event handlers, javascript:/data: URI 금지.
- 주가 예측 금지, 확률만 제시.
- 시나리오 확률 80% 초과 금지.
- 주말/휴장일 날짜 금지.
- 원본 JSON/CSV에 없는 숫자 언급 금지.
- 템플릿의 <div>/<table>/<tr> 구조를 생략·축약 금지. 2x2 카드와 Probability Bar 필수.

응답 시작은 반드시 <div style="font-family:... 로, 끝은 </div> 로 마무리.
"""

# ── Utility ──
def classify_window(n_days: int):
    if n_days <= 1:
        return ("LOW", "SNAPSHOT", "⚠ 첫 분석 — 시계열 없음. Phase 1/2/3 미구성. 오늘 스냅샷의 구조적 해석만.")
    if n_days <= 6:
        return ("MEDIUM_LOW", "EARLY",
                f"📊 초기 관찰 — {n_days}일 시계열. Phase 1은 유보. Final Absorption 조건부.")
    if n_days <= 14:
        return ("MEDIUM", "SHORT", f"📊 단기 트렌드 — {n_days}일. Phase 1 setup 가능.")
    return ("HIGH", "STANDARD", f"📊 표준 심화 — {n_days}일. 전체 프레임워크 적용.")

def to_csv(series: List[Dict]) -> str:
    if not series:
        return ""
    cols = ["date","price","scen_A","scen_C","raw_score","macro",
            "dp_pct","inst_delta","pro_delta","retail_delta","iar","divergence",
            "short_pct","short_slope","slope_dir","ctb_fee",
            "max_pain","max_pain_dist","pc_oi","net_gex_bn","flip_zone",
            "ma_align","bb_width","patterns"]
    out = [",".join(cols)]
    def f(v, n=2):
        if v is None: return ""
        if isinstance(v, (int,float)): return f"{v:.{n}f}"
        return str(v)
    def i(v):
        return "" if v is None else f"{round(v):d}"
    for s in series:
        l1 = s.get("l1") or {}; l2 = s.get("l2") or {}
        l3 = s.get("l3") or {}; l4 = s.get("l4") or {}
        sc = s.get("scenarios") or {}
        net_gex = l3.get("net_gex")
        row = [
            s.get("date",""),
            f(s.get("price"), 2),
            f(sc.get("A_bullish"), 0), f(sc.get("C_bearish"), 0), f(sc.get("raw_score"), 2),
            s.get("macro_env","") or "",
            f(l1.get("dp_pct"), 1),
            i(l1.get("delta_institutional")), i(l1.get("delta_professional")), i(l1.get("delta_retail")),
            f(l1.get("iar"), 2), l1.get("divergence","") or "",
            f(l2.get("short_pct_latest"), 1), f(l2.get("short_slope"), 2),
            l2.get("slope_dir","") or "",
            f(l2.get("ctb_fee"), 2),
            f(l3.get("max_pain"), 2), f(l3.get("max_pain_dist_pct"), 1),
            f(l3.get("pc_oi"), 2),
            "" if net_gex is None else f"{net_gex/1e9:.2f}",
            f(l3.get("flip_zone"), 2),
            l4.get("ma_alignment","") or "", f(l4.get("bb_width_pct"), 1),
            "|".join(s.get("patterns") or [])
        ]
        out.append(",".join(row))
    return "\n".join(out)

def call_claude_multi(system: str, user_text: str) -> Dict[str, Any]:
    messages = [{"role":"user","content":[{"type":"text","text": user_text}]}]
    for turn in range(MAX_TURNS):
        res = requests.post(
            API,
            headers={
                "Content-Type":"application/json",
                "x-api-key": ANTH_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": MODEL,
                "max_tokens": 8192,
                "temperature": 0,
                "system": system,
                "tools": [{"type":"web_search_20250305","name":"web_search"}],
                "messages": messages,
            },
            timeout=180,
        )
        if res.status_code != 200:
            raise RuntimeError(f"Claude HTTP {res.status_code}: {res.text[:500]}")
        data = res.json()
        if data.get("stop_reason") == "tool_use":
            messages.append({"role":"assistant","content": data["content"]})
            tool_results = [{"type":"tool_result","tool_use_id": b["id"],"content":""}
                            for b in data["content"] if b["type"] == "tool_use"]
            messages.append({"role":"user","content": tool_results})
            continue
        text = "".join(b.get("text","") for b in data.get("content", []) if b.get("type") == "text")
        return {"text": text, "usage": data.get("usage", {})}
    raise RuntimeError("MAX_TURNS exceeded")

def sanitize_html(raw: str) -> str:
    s = re.sub(r"^```html\s*", "", raw.strip())
    s = re.sub(r"\s*```$", "", s)
    s = re.sub(r"<\!DOCTYPE[^>]*>", "", s, flags=re.I)
    s = re.sub(r"<html[^>]*>|</html>|<body[^>]*>|</body>|<head[^>]*>.*?</head>", "", s, flags=re.I|re.S)
    # strip scripts / styles / event handlers
    s = re.sub(r"<(script|style|iframe|object|embed|form|input|button|textarea)[^>]*>.*?</\1>", "", s, flags=re.I|re.S)
    s = re.sub(r"<(script|style|iframe|object|embed|link|meta|base)[^>]*/?>", "", s, flags=re.I)
    s = re.sub(r'\s+on\w+="[^"]*"', "", s)
    s = re.sub(r"\s+on\w+='[^']*'", "", s)
    s = re.sub(r'(href|src)\s*=\s*"(?:javascript|data):[^"]*"', "", s, flags=re.I)
    s = re.sub(r"(href|src)\s*=\s*'(?:javascript|data):[^']*'", "", s, flags=re.I)
    return s.strip()

# ── Main ──
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--mode", default="weekly", choices=["weekly","daily","adhoc"],
                    help="파일명 접미어 + 프롬프트 맥락")
    ap.add_argument("--days", type=int, default=30, help="시계열 최대 일수")
    ap.add_argument("--force", action="store_true",
                    help="동일 경로 narrative 파일이 이미 있어도 덮어쓰기 (재호출 = 토큰 재지출)")
    args = ap.parse_args()

    ticker = args.ticker.upper()
    data_path = DATA_DIR / f"{ticker}.json"
    if not data_path.exists():
        print(f"[skip] {ticker}: {data_path} 없음 (먼저 sma.py 실행 필요)", file=sys.stderr)
        sys.exit(0)

    series = json.loads(data_path.read_text(encoding="utf-8"))
    # 최근 N일만
    series = series[-args.days:]
    if not series:
        print(f"[skip] {ticker}: 시계열 비어있음", file=sys.stderr)
        sys.exit(0)
    today = series[-1]
    n_days = len(series)
    cap, tier, note = classify_window(n_days)

    # ── 이미 생성된 narrative 가 있으면 API 호출 전에 skip (토큰 절약) ──
    date_str = today["date"]
    out_dir = TICKERS_DIR / ticker / "narratives"
    filename = f"{date_str}-{args.mode}.html"
    out_path = out_dir / filename
    if out_path.exists() and not args.force:
        print(f"[narr] {ticker} skip: {out_path} already exists (use --force to overwrite)",
              file=sys.stderr)
        sys.exit(0)

    mode_hint = {
        "weekly": f"이번 주({today['date']} 포함 최근 5거래일) 흐름 중심으로 서술하되, 전체 {n_days}일 컨텍스트 참고.",
        "daily":  "오늘 단일 스냅샷 중심, 최근 5일 변화 맥락 언급.",
        "adhoc":  "사용자 온디맨드 요청. 최근 흐름 기반 표준 분석.",
    }[args.mode]

    user_text = (
        f"{note}\n\n{mode_hint}\n\n"
        f"## 오늘({today['date']}) 스냅샷 (full JSON)\n"
        f"```json\n{json.dumps(today, ensure_ascii=False, indent=2)}\n```\n\n"
    )
    if n_days > 1:
        user_text += f"## 시계열 ({n_days}일, CSV)\n```csv\n{to_csv(series)}\n```\n\n"
    user_text += (f"요청: CLAUDE.md Smart Money 분석 프레임워크에 따라 {ticker} 내러티브 리포트를 "
                  f"위 지정된 HTML 템플릿으로 생성. 필요 시 web_search로 최근 1주 뉴스·이벤트·SEC 공시 확인. "
                  f"신뢰도 상한은 {cap}.")

    print(f"[narr] {ticker} · mode={args.mode} · days={n_days} · cap={cap}", file=sys.stderr)
    t0 = time.time()
    try:
        result = call_claude_multi(SYSTEM_PROMPT, user_text)
    except Exception as e:
        print(f"[narr] {ticker} FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    html_body = sanitize_html(result["text"])
    usage = result.get("usage", {})
    in_tok = usage.get("input_tokens", 0) or 0
    out_tok = usage.get("output_tokens", 0) or 0
    cost = (in_tok * 3 + out_tok * 15) / 1e6

    out_dir.mkdir(parents=True, exist_ok=True)

    page = (
        f"<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"UTF-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{ticker} AI Narrative · {date_str} ({args.mode})</title></head>"
        f"<body style=\"background:#FFFFFF;color:#1F2328;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;\">"
        f"<div style=\"max-width:1000px;margin:0 auto;\">"
        f"<div style=\"font-size:11px;color:#656D76;margin-bottom:12px;\">"
        f"<a href=\"../../../tickers/{ticker}/\" style=\"color:#0969DA;text-decoration:none;\">← {ticker} page</a> · "
        f"Generated by {MODEL} ({args.mode}) · {date_str} · "
        f"in {in_tok} out {out_tok} tokens ~${cost:.4f}"
        f"</div>{html_body}</div></body></html>"
    )
    out_path.write_text(page, encoding="utf-8")
    dt = time.time() - t0
    print(f"[narr] {ticker} OK ({dt:.1f}s) → {out_path}  [{in_tok}+{out_tok} ~${cost:.3f}]")

if __name__ == "__main__":
    main()
