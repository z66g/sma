#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weekly AI narrative — 기본 경로는 **API 과금 없음**.

예약 작업(Claude Code 클라우드 세션, Claude Opus 5.5)이 매주 토요일 다음 순서로 실행한다:
  1) python3 narrative.py TICKER --prepare WORK_DIR   → 프롬프트 패키지(규칙+데이터) 생성, API 호출 없음
  2) 세션의 Claude 가 패키지를 읽고(필요 시 웹 검색) 본문 HTML 을 WORK_DIR/TICKER_body.html 로 작성
  3) python3 narrative.py TICKER --publish WORK_DIR/TICKER_body.html
       → 허용목록 정화기 통과 후 reports/tickers/{T}/narratives/{SESSION_DATE}-weekly.html 게시
  4) python3 build_index.py → main 커밋·푸시 → weekly-narrative.yml(push 트리거)이 텔레그램 알림

기타:
  python3 narrative.py --resanitize-all [--dry-run]   게시된 narrative 전체 재정화
  python3 narrative.py TICKER --api                   (비권장·과금) Anthropic API 직접 호출, ANTHROPIC_API_KEY 필요

reads  : reports/*/{TICKER}_*.json (build_index.scan_ticker — 세션 날짜 정규화)
exit   : 0 = 성공/skip, 1 = 실패, 2 = 설정 오류
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import random
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
TICKERS_DIR = ROOT / "reports" / "tickers"

API = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL = os.environ.get("SMA_CLAUDE_MODEL", "").strip() or "claude-opus-5-5"
EFFORT = os.environ.get("SMA_CLAUDE_EFFORT", "").strip() or "high"
WEB_SEARCH_MAX_USES = int(os.environ.get("SMA_WEB_SEARCH_MAX_USES", "5") or 5)
MAX_TOKENS = 16000          # 비스트리밍 요청 — SDK/HTTP 타임아웃 안쪽
MAX_CONTINUATIONS = 6       # pause_turn 재개 상한
MAX_RETRIES = 6
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
HTTP_TIMEOUT = 600

# 모델 기능 (claude-api 스킬 기준, 2026-11)
_MODERN = ("claude-fable-", "claude-mythos-", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
           "claude-opus-4-6", "claude-sonnet-5", "claude-sonnet-4-6", "claude-haiku-5")
FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# $/1M tokens: (input, output, cache_read). cache write(5m) = 1.25 × input. web search = $10 / 1,000.
PRICING = {
    "claude-opus-5-5":   (4.00, 20.00, 0.20),
    "claude-opus-5":     (5.00, 25.00, None),
    "claude-opus-4-8":   (5.00, 25.00, None),
    "claude-opus-4-7":   (5.00, 25.00, None),
    "claude-opus-4-6":   (5.00, 25.00, None),
    "claude-fable-5-2":  (8.00, 40.00, 0.20),
    "claude-fable-5-1":  (10.00, 50.00, None),
    "claude-fable-5":    (10.00, 50.00, None),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-sonnet-5":   (2.00, 10.00, None),
    "claude-sonnet-4-6": (3.00, 15.00, None),
    "claude-haiku-5-5":  (0.10, 0.50, None),
    "claude-haiku-4-5":  (1.00, 5.00, None),
}
WEB_SEARCH_PER_REQ = 10.0 / 1000


def is_modern(model: str) -> bool:
    return model.startswith(_MODERN)


# ── 시스템 프롬프트 (narrative.js 의 SYSTEM_PROMPT_CORE 와 동일 텍스트 유지) ──
# 주의: JS 템플릿 리터럴로 복사되므로 백틱·달러-중괄호·백슬래시를 쓰지 말 것.
SYSTEM_PROMPT = r"""You are a Smart Money forensic analyst following the CLAUDE.md framework.

CORE STANCE:
- Zero bullish/bearish bias. Data and capital flows ONLY.
- Architect perspective — interpret unusual signals as intentional market design first.
- Never fabricate values not in the JSON/CSV input. A field that is absent or null is UNAVAILABLE — say "N/A" / "데이터 없음", never treat it as 0.
- Never exceed the MAX_CONFIDENCE cap given in the user message.
- Prefer "insufficient data" to speculation.
- DATA DATE: every number refers to the US regular-session date given as DATA_DATE (ET). Cite it explicitly ("데이터 기준 YYYY-MM-DD 세션").

LAYERS:
- L1 Dark Pool: delta_total = 5-session sum of CLV-weighted signed volume (all participants combined, net flow direction). dp_pct = FINRA off-exchange share of volume (>45% heavy, 30-45% normal, <30% lit/retail-dominant); dp_ratio = today vs 20-day average. divergence (BULLISH_DIVERGENCE / BEARISH_DIVERGENCE / CONVERGENCE / NEUTRAL) = price slope vs OBV slope, the strongest Architect signal. Cohort fields delta_inst / delta_pro / delta_retail / iar and dp_vwap exist ONLY when a trade-level source was available. If they are absent, do NOT discuss institutional vs professional vs retail split or IAR at all.
- L2 Short Volume: FINRA Reg SHO short volume is NOT short interest. 40-55% is structurally normal. Judge only by slope + CTB fee direction (ctb_delta_pct) + anomaly_z + case. NEVER read "short% > 50" as bearish. short_interest (settlement-date based, 2-week lag) gives days_to_cover and pct_float — cite its settlement_date when used. Report CTB as direction / rate of change, not as a standalone absolute signal.
- L3 Options: Max Pain, P/C (OI and volume), Net GEX, Flip Zone, call_wall / put_wall, expected_move_pct (ATM straddle), IV skew, term_structure. Pinning meaningful only when DTE <= 5. Positive Net GEX = long gamma (pin regime). Flip crossing = vol regime change.
- L4 Chart: MA alignment, BB width / position, RSI, ATR, immediate/key S&R.
- quality: data-quality grade/score and per-layer missing fields. Lower confidence when grade is C or worse, and name the missing layers in the limitations section.

WEIGHTS: L1=0.35, L2=0.20, L3=0.30, L4=0.15.

PATTERNS:
- FINAL_ABSORPTION: DP% > 40, short slope < 0, CTB delta in [-5,+5]%, net OBV > 0 (3+ of 5)
- THETA_BURN: 3+ sessions low volume + tight range + near-zero OBV change
- LOW_CTB_PARADOX: CTB < 1% + shares_available down > 5% = quiet shorting
- GAMMA_SQUEEZE_SETUP: P/C OI < 0.7 + P/C Vol < 0.7 + rising call OI
- SHORT_SQUEEZE_RISK: CTB rising > 5% + short slope rising + HTB

ARCHITECT PHASES (N_days >= 7 only): Phase 1 past / Phase 2 present / Phase 3 future.
- Past/present dates must come from the CSV session dates. Future dates must be chosen ONLY from the UPCOMING_TRADING_DAYS list in the user message (US market holidays and weekends are already excluded). Never invent a date.

====================================================================
OUTPUT: FILL THIS EXACT HTML TEMPLATE. NO MARKDOWN. INLINE STYLES ONLY.
====================================================================

CRITICAL: Copy the template below verbatim. Replace ONLY text inside {{...}} or the content between tags.
{{TICKER}}, {{DATE}} and {{MAX_CONFIDENCE}} take the TICKER, DATA_DATE and MAX_CONFIDENCE values from the user message.
DO NOT omit any section. DO NOT change the style attributes or structure.
The 2x2 CARD GRID and the PROBABILITY BAR are MANDATORY visual elements — not optional.

==== TEMPLATE START (copy this entire block, fill content) ====

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1C1B19;">

<div style="margin-bottom:16px;">
  <h1 style="font-size:20px;font-weight:700;margin:0 0 4px 0;color:#1C1B19;">{{TICKER}} Smart Money Forensic · 데이터 기준 {{DATE}}</h1>
  <div style="font-size:11px;color:#6E6A63;">{{price_quality_and_patterns_oneliner}}</div>
</div>

<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #CFCCC5;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#1C1B19;font-weight:700;font-size:15px;">핵심 발견</span>
  <span style="background:{{kf_badge_bg}};color:{{kf_badge_fg}};padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600;">{{KF_BADGE_TEXT}}</span>
</div>
<p style="font-size:13px;line-height:1.7;color:#1C1B19;margin:0;">{{핵심 1-2문장, 패러독스/충돌 우선}}</p>

<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #CFCCC5;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#1C1B19;font-weight:700;font-size:15px;">4-LAYER SYNTHESIS</span>
</div>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;">
  <div style="background:#FAFAF8;border:0.5px solid #E2E0DB;border-radius:6px;padding:12px;">
    <div style="font-size:10px;color:#6E6A63;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">L1 DARK POOL</div>
    <div style="font-size:15px;font-weight:600;color:{{l1_color}};margin-bottom:6px;">{{l1_scenario_label}}</div>
    <div style="font-size:12px;line-height:1.6;color:#1C1B19;">{{L1: Total OBV(5d) 방향+규모, dp_pct 수준, dp_ratio, Divergence. 코호트 필드가 없으면 기관/리테일 분리 언급 금지}}</div>
  </div>
  <div style="background:#FAFAF8;border:0.5px solid #E2E0DB;border-radius:6px;padding:12px;">
    <div style="font-size:10px;color:#6E6A63;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">L2 SHORT · CTB</div>
    <div style="font-size:15px;font-weight:600;color:{{l2_color}};margin-bottom:6px;">{{l2_scenario_label}}</div>
    <div style="font-size:12px;line-height:1.6;color:#1C1B19;">{{L2: short% slope + CTB 방향(Δ%) + short interest/DTC. 절대 %를 bearish로 해석 금지}}</div>
  </div>
  <div style="background:#FAFAF8;border:0.5px solid #E2E0DB;border-radius:6px;padding:12px;">
    <div style="font-size:10px;color:#6E6A63;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">L3 OPTIONS</div>
    <div style="font-size:15px;font-weight:600;color:{{l3_color}};margin-bottom:6px;">{{l3_scenario_label}}</div>
    <div style="font-size:12px;line-height:1.6;color:#1C1B19;">{{L3: Max Pain 거리, Net GEX 부호, Flip Zone regime, Call/Put Wall, Expected Move}}</div>
  </div>
  <div style="background:#FAFAF8;border:0.5px solid #E2E0DB;border-radius:6px;padding:12px;">
    <div style="font-size:10px;color:#6E6A63;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">L4 CHART</div>
    <div style="font-size:15px;font-weight:600;color:{{l4_color}};margin-bottom:6px;">{{l4_scenario_label}}</div>
    <div style="font-size:12px;line-height:1.6;color:#1C1B19;">{{L4: MA 배열, BB 폭/위치, RSI, S/R 거리}}</div>
  </div>
</div>

<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #CFCCC5;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#1C1B19;font-weight:700;font-size:15px;">ARCHITECT PHASE TIMELINE</span>
  <span style="background:#EDF1F8;color:#2C4C9C;padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600;">{{N_days}}개 세션 관찰</span>
</div>
<table style="border-collapse:collapse;width:100%;font-size:12px;margin-top:4px;">
  <thead>
    <tr>
      <th style="background:#F2F1EE;color:#6E6A63;padding:6px 8px;border:0.5px solid #E2E0DB;text-align:left;">Phase</th>
      <th style="background:#F2F1EE;color:#6E6A63;padding:6px 8px;border:0.5px solid #E2E0DB;text-align:left;">Dates (trading days)</th>
      <th style="background:#F2F1EE;color:#6E6A63;padding:6px 8px;border:0.5px solid #E2E0DB;text-align:left;">Description</th>
    </tr>
  </thead>
  <tbody>
    <tr style="background:#FFFFFF;"><td style="padding:6px 8px;border:0.5px solid #E2E0DB;">Phase 1 Setup</td><td style="padding:6px 8px;border:0.5px solid #E2E0DB;">{{p1_dates}}</td><td style="padding:6px 8px;border:0.5px solid #E2E0DB;">{{p1_desc}}</td></tr>
    <tr style="background:#FAFAF8;"><td style="padding:6px 8px;border:0.5px solid #E2E0DB;">Phase 2 Transition</td><td style="padding:6px 8px;border:0.5px solid #E2E0DB;">{{p2_dates}}</td><td style="padding:6px 8px;border:0.5px solid #E2E0DB;">{{p2_desc}}</td></tr>
    <tr style="background:#FFFFFF;"><td style="padding:6px 8px;border:0.5px solid #E2E0DB;">Phase 3 Resolution</td><td style="padding:6px 8px;border:0.5px solid #E2E0DB;">{{p3_dates}}</td><td style="padding:6px 8px;border:0.5px solid #E2E0DB;">{{p3_desc: 조건부 시나리오 + 입력 데이터에 있는 관찰 레벨(구간)만}}</td></tr>
  </tbody>
</table>

<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #CFCCC5;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#1C1B19;font-weight:700;font-size:15px;">PROBABILITY MATRIX</span>
  <span style="background:#EDF1F8;color:#2C4C9C;padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600;">Raw Score {{raw_score}}</span>
</div>
<div style="display:flex;flex-direction:column;gap:6px;font-size:12px;">
  <div style="display:flex;align-items:center;gap:10px;">
    <span style="width:100px;color:#2F7254;font-weight:600;">[A] Bullish</span>
    <div style="flex:1;background:#F2F1EE;border-radius:4px;overflow:hidden;height:16px;"><div style="width:{{A_pct}}%;background:#2F7254;height:16px;"></div></div>
    <span style="width:48px;text-align:right;font-weight:600;color:#2F7254;">{{A_pct}}%</span>
  </div>
  <div style="display:flex;align-items:center;gap:10px;">
    <span style="width:100px;color:#94630F;font-weight:600;">[B] Neutral</span>
    <div style="flex:1;background:#F2F1EE;border-radius:4px;overflow:hidden;height:16px;"><div style="width:{{B_pct}}%;background:#94630F;height:16px;"></div></div>
    <span style="width:48px;text-align:right;font-weight:600;color:#94630F;">{{B_pct}}%</span>
  </div>
  <div style="display:flex;align-items:center;gap:10px;">
    <span style="width:100px;color:#B23B34;font-weight:600;">[C] Bearish</span>
    <div style="flex:1;background:#F2F1EE;border-radius:4px;overflow:hidden;height:16px;"><div style="width:{{C_pct}}%;background:#B23B34;height:16px;"></div></div>
    <span style="width:48px;text-align:right;font-weight:600;color:#B23B34;">{{C_pct}}%</span>
  </div>
</div>
<div style="font-size:11px;color:#6E6A63;margin-top:6px;">Macro: {{macro_env}} · Patterns: {{patterns_or_none}}</div>

<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #CFCCC5;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#1C1B19;font-weight:700;font-size:15px;">DECISIVE TRIGGERS</span>
</div>
<table style="border-collapse:collapse;width:100%;font-size:12px;">
  <thead>
    <tr>
      <th style="background:#F2F1EE;color:#6E6A63;padding:6px 8px;border:0.5px solid #E2E0DB;text-align:left;">Trigger</th>
      <th style="background:#F2F1EE;color:#6E6A63;padding:6px 8px;border:0.5px solid #E2E0DB;text-align:left;">Direction</th>
      <th style="background:#F2F1EE;color:#6E6A63;padding:6px 8px;border:0.5px solid #E2E0DB;text-align:left;">Threshold</th>
      <th style="background:#F2F1EE;color:#6E6A63;padding:6px 8px;border:0.5px solid #E2E0DB;text-align:left;">Implication</th>
    </tr>
  </thead>
  <tbody>
    <tr style="background:#FFFFFF;"><td style="padding:6px 8px;border:0.5px solid #E2E0DB;">{{trigger_name}}</td><td style="padding:6px 8px;border:0.5px solid #E2E0DB;">{{direction_pill}}</td><td style="padding:6px 8px;border:0.5px solid #E2E0DB;font-family:monospace;">{{threshold}}</td><td style="padding:6px 8px;border:0.5px solid #E2E0DB;">{{implication}}</td></tr>
  </tbody>
</table>

<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #CFCCC5;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#1C1B19;font-weight:700;font-size:15px;">CONFIDENCE & LIMITATIONS</span>
  <span style="background:{{conf_bg}};color:{{conf_fg}};padding:2px 10px;border-radius:3px;font-size:11px;font-weight:700;">{{MAX_CONFIDENCE}}</span>
</div>
<p style="font-size:12px;line-height:1.7;color:#1C1B19;margin:0;">{{데이터 기준 세션 날짜 · N_days개 세션 · 데이터 품질 등급 · 누락 레이어/지표 · 웹 검색 출처와 날짜}}</p>

</div>

==== TEMPLATE END ====

TRIGGER ROWS: 3-5 rows, alternating row background #FFFFFF / #FAFAF8. Direction pills:
- bullish: <span style="background:#EDF4EF;color:#2F7254;padding:1px 8px;border-radius:10px;font-size:10px;font-weight:600;">BULLISH</span>
- bearish: <span style="background:#F8EDEB;color:#B23B34;padding:1px 8px;border-radius:10px;font-size:10px;font-weight:600;">BEARISH</span>
- caution: <span style="background:#F6F0E2;color:#94630F;padding:1px 8px;border-radius:10px;font-size:10px;font-weight:600;">WATCH</span>

ARCHITECT PHASE when N_days < 7: replace the table with
<p style="font-size:12px;color:#94630F;background:#F6F0E2;padding:8px 12px;border-radius:4px;">시계열 N개 세션으로 Phase 1(과거 setup)을 구조적으로 해석하기엔 부족합니다. 최소 7개 세션 필요.</p>

COLOR MAPPING:
- signal=BULLISH -> #2F7254, signal=BEARISH -> #B23B34, signal=NEUTRAL -> #94630F
- confidence HIGH -> #EDF4EF/#2F7254, MEDIUM or MEDIUM_LOW -> #F6F0E2/#94630F, LOW -> #F8EDEB/#B23B34

PROHIBITIONS:
- Markdown syntax 금지 (# ## - ** fenced code). 순수 HTML만.
- script, style, iframe, img, svg 태그·이벤트 핸들러·javascript:/data: URI 금지 (렌더 전에 모두 제거됨).
- 점(point) 가격 예측·단일 목표가 금지. 가격은 입력 데이터에 있는 레벨(max_pain, flip_zone, call_wall, put_wall, S/R, levels, expected_move)만 "관찰 레벨/구간"으로 인용. 확률은 입력의 scenarios 값만 사용.
- 시나리오 확률 80% 초과 금지.
- 주말/미국 휴장일 날짜 금지 — 미래 날짜는 UPCOMING_TRADING_DAYS 목록에서만.
- 원본 JSON/CSV에 없는 숫자 언급 금지 (웹 검색으로 얻은 사실은 출처·날짜와 함께만).
- 템플릿의 div/table/tr 구조를 생략·축약 금지. 2x2 카드와 Probability Bar 필수.

응답은 반드시 템플릿의 첫 div 로 시작하고 마지막 닫는 div 로 끝낸다. 앞뒤 설명 문장 금지.
"""


# ───────────────────────── HTML sanitizer (allowlist, html.parser 기반) ─────────────────────────
ALLOWED_TAGS = {
    "p", "h1", "h2", "h3", "h4", "ul", "ol", "li", "b", "strong", "em", "i",
    "table", "thead", "tbody", "tr", "th", "td", "span", "div", "br", "hr", "a",
    "code", "blockquote", "small", "sup", "sub",
}
VOID_TAGS = {"br", "hr"}
# 내용까지 통째로 버리는 태그
DROP_CONTENT_TAGS = {
    "script", "style", "iframe", "object", "embed", "svg", "math", "noscript", "template",
    "textarea", "title", "head", "select", "option", "frameset", "frame", "noembed",
    "noframes", "xmp", "plaintext", "applet", "canvas", "video", "audio", "picture", "form",
}
_STYLE_BAD = re.compile(
    r"url\s*\(|expression|javascript|vbscript|@import|behavior|-moz-binding|image-set|"
    r"position\s*:\s*(fixed|sticky)|[\\<>]|/\*",
    re.I,
)
_CTRL_WS = re.compile("[\x00-\x20\x7f-\x9f\u200b-\u200f\u2028\u2029\ufeff]+")


def _fully_unescape(v: str) -> str:
    prev = None
    while prev != v:
        prev, v = v, html.unescape(v)
    return v


def _safe_href(v: str) -> Optional[str]:
    probe = _CTRL_WS.sub("", _fully_unescape(v)).lower()
    if probe.startswith("http://") or probe.startswith("https://"):
        return v.strip()
    return None


def _safe_style(v: str) -> Optional[str]:
    probe = _fully_unescape(v)
    if _STYLE_BAD.search(probe) or _STYLE_BAD.search(_CTRL_WS.sub("", probe)):
        return None
    return probe.strip()


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self.stack: List[str] = []         # 열린 허용 태그
        self.drop_tag: Optional[str] = None
        self.drop_depth = 0

    # 속성 필터
    def _attrs(self, tag: str, attrs) -> str:
        parts = []
        seen = set()
        for name, value in attrs:
            name = (name or "").lower()
            if name in seen or value is None:
                continue
            seen.add(name)
            if name == "class":
                v = re.sub(r"[^\w\- ]", "", value)[:200]
            elif name == "style":
                v = _safe_style(value)
            elif name == "href" and tag == "a":
                v = _safe_href(value)
            elif name in ("colspan", "rowspan") and tag in ("td", "th"):
                v = value.strip() if value.strip().isdigit() and 0 < int(value.strip()) <= 50 else None
            else:
                v = None       # on*, src, id, srcset, formaction … 전부 제거
            if v:
                parts.append(f' {name}="{html.escape(v, quote=True)}"')
        if tag == "a" and any(p.startswith(" href=") for p in parts):
            parts.append(' rel="noopener noreferrer nofollow" target="_blank"')
        return "".join(parts)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.drop_tag:
            if tag == self.drop_tag:
                self.drop_depth += 1
            return
        if tag in DROP_CONTENT_TAGS:
            self.drop_tag, self.drop_depth = tag, 1
            return
        if tag not in ALLOWED_TAGS:
            return      # 태그만 버리고 내용(텍스트)은 유지
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.drop_tag or tag in DROP_CONTENT_TAGS or tag not in ALLOWED_TAGS:
            return
        if tag in VOID_TAGS:
            self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")
        else:
            self.out.append(f"<{tag}{self._attrs(tag, attrs)}></{tag}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.drop_tag:
            if tag == self.drop_tag:
                self.drop_depth -= 1
                if self.drop_depth <= 0:
                    self.drop_tag = None
            return
        if tag not in self.stack:
            return
        while self.stack:
            t = self.stack.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        if not self.drop_tag:
            self.out.append(html.escape(data, quote=False))

    # 주석·선언·PI 는 모두 버림
    def handle_comment(self, data): pass
    def handle_decl(self, decl): pass
    def handle_pi(self, data): pass
    def unknown_decl(self, data): pass

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_html(raw: str) -> str:
    """allowlist 기반 sanitizer. 허용 태그/속성 외 전부 제거."""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:html)?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*```$", "", s)
    p = _Sanitizer()
    p.feed(s)
    p.close()
    return p.result().strip()


def extract_html_block(text: str) -> str:
    """모델 응답에서 첫 <div ... 부터 마지막 </div> 까지만 (웹 검색 전후 잡담 제거)."""
    start = text.find("<div")
    end = text.rfind("</div>")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start:end + len("</div>")]


# ───────────────────────── data → prompt ─────────────────────────
def _prune(o: Any) -> Any:
    """None/NaN/빈 컨테이너 제거 — 모델에 '없는 값'을 0 처럼 보여주지 않기 위해."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        out = {k: _prune(v) for k, v in o.items()}
        return {k: v for k, v in out.items() if v is not None and v != [] and v != {} and v != ""}
    if isinstance(o, list):
        return [x for x in (_prune(v) for v in o) if x is not None]
    return o


def _pick(d: dict, keys) -> dict:
    d = d or {}
    return {k: d.get(k) for k in keys if k in d}


L1_KEYS = ("scenario", "signal", "confidence", "dp_pct", "dp_avg20", "dp_ratio", "dp_class", "delta_total",
           "divergence", "obv_source", "window_days", "delta_inst", "delta_pro", "delta_retail", "iar",
           "dp_vwap", "dp_vwap_dist_pct", "ats")
L2_KEYS = ("scenario", "signal", "case", "short_pct_latest", "short_avg_14d", "short_slope", "slope_dir",
           "anomaly_z", "short_shares", "ctb_fee", "ctb_delta_pct", "shares_available", "avail_delta_pct",
           "borrow_source", "short_interest")
L3_KEYS = ("scenario", "signal", "expiry", "dte", "max_pain", "max_pain_dist_pct", "pc_oi", "pc_vol",
           "pc_vol_adj", "skew", "skew_25d", "net_gex", "flip_zone", "call_wall", "put_wall", "atm_iv", "iv30",
           "term_structure", "expected_move", "expected_move_pct", "oi_total", "binary_event",
           "monthly_expiry", "monthly_max_pain")
L4_KEYS = ("scenario", "signal", "ma_alignment", "sma20", "sma50", "sma200", "ema9", "ema21", "rsi14", "atr14",
           "atr_pct", "bb_width_pct", "bb_upper", "bb_lower", "bb_position", "bb_width_pctile",
           "immediate_resistance", "immediate_support", "key_resistance", "key_support", "vol_ratio30",
           "high_52w", "low_52w")


def snapshot(row: dict) -> dict:
    q = row.get("quality") or {}
    layers = {}
    for name, layer in (q.get("layers") or {}).items():
        if isinstance(layer, dict):
            layers[name] = _pick(layer, ("status", "missing"))
    l1 = _pick(row.get("l1"), L1_KEYS)
    fl = (row.get("l1") or {}).get("flow")
    if isinstance(fl, dict):        # 체결 단위(Alpaca SIP) 세부 — 세션별 다크풀·블록·종가 집중·리테일 교차검증
        l1["trade_flow"] = _pick(fl, ("days", "iar_class", "sessions", "blocks", "dark_blocks",
                                      "last30_block_share", "last30_move_pct", "bjzz_imbalance",
                                      "cohort_vol_share", "dark_pct_trades", "trades"))
    cum = (row.get("l1") or {}).get("cumulative_daily") or []
    if cum:
        l1["cumulative_daily"] = cum[-5:]
    snap = {
        "ticker": row.get("ticker"),
        "data_date": row.get("date"),
        "schema": row.get("schema"),
        "price": row.get("price"),
        "prev_close": row.get("prev_close"),
        "change_pct": row.get("change_pct"),
        "headline": row.get("headline"),
        "macro_env": row.get("macro_env"),
        "patterns": row.get("patterns"),
        "pattern_evidence": row.get("pattern_detail"),
        "scenarios": row.get("scenarios"),
        # 레거시 v2 phase 는 KST 실행일 기준이라 주말 날짜가 섞여 있음 → v3 만 전달
        "phase": row.get("phase") if (row.get("schema") or 2) >= 3 else None,
        "levels": (row.get("levels") or [])[:12],
        "quality": {"grade": q.get("grade"), "score": q.get("score"), "layers": layers},
        "warnings": (row.get("warnings") or [])[:10],
        "l1": l1,
        "l2": _pick(row.get("l2"), L2_KEYS),
        "l3": _pick(row.get("l3"), L3_KEYS),
        "l4": _pick(row.get("l4"), L4_KEYS),
    }
    return _prune(snap)


def _f(v, n=2) -> str:
    if isinstance(v, bool) or v is None:
        return ""
    if isinstance(v, (int, float)):
        return f"{v:.{n}f}" if math.isfinite(v) else ""
    return str(v).replace(",", ";")


CSV_COLS = ["date", "price", "scen_A", "scen_B", "scen_C", "raw_score", "macro",
            "dp_pct", "dp_ratio", "delta_total", "obv_inst", "obv_pro", "obv_retail", "iar", "dp_vwap_dist_pct",
            "divergence",
            "short_pct", "short_shares", "short_slope", "ctb_fee", "ctb_delta_pct", "si_days_to_cover",
            "max_pain", "max_pain_dist_pct", "pc_oi", "pc_vol", "net_gex_m", "flip_zone",
            "call_wall", "put_wall", "exp_move_pct", "ma_align", "bb_width", "rsi14", "quality", "patterns"]


def to_csv(series: List[Dict]) -> str:
    """빈 칸 = 데이터 없음 (0 아님). net_gex 는 백만(M) 단위."""
    if not series:
        return ""
    out = [",".join(CSV_COLS)]
    for s in series:
        l1, l2, l3, l4 = (s.get(k) or {} for k in ("l1", "l2", "l3", "l4"))
        sc = s.get("scenarios") or {}
        si = l2.get("short_interest") or {}
        q = s.get("quality") or {}
        ng = l3.get("net_gex")
        ng_m = _f(ng / 1e6, 1) if isinstance(ng, (int, float)) and not isinstance(ng, bool) and math.isfinite(ng) else ""
        dt = l1.get("delta_total")
        row = [
            s.get("date", ""), _f(s.get("price")),
            _f(sc.get("A_bullish"), 0), _f(sc.get("B_neutral"), 0), _f(sc.get("C_bearish"), 0),
            _f(sc.get("raw_score")), _f(s.get("macro_env")),
            _f(l1.get("dp_pct"), 1), _f(l1.get("dp_ratio")), _f(dt, 0),
            _f(l1.get("delta_inst"), 0), _f(l1.get("delta_pro"), 0), _f(l1.get("delta_retail"), 0),
            _f(l1.get("iar")), _f(l1.get("dp_vwap_dist_pct")), _f(l1.get("divergence")),
            _f(l2.get("short_pct_latest"), 1), _f(l2.get("short_shares"), 0), _f(l2.get("short_slope")),
            _f(l2.get("ctb_fee")), _f(l2.get("ctb_delta_pct"), 1), _f(si.get("days_to_cover"), 1),
            _f(l3.get("max_pain")), _f(l3.get("max_pain_dist_pct"), 1), _f(l3.get("pc_oi")), _f(l3.get("pc_vol")),
            ng_m, _f(l3.get("flip_zone")), _f(l3.get("call_wall")), _f(l3.get("put_wall")),
            _f(l3.get("expected_move_pct"), 1), _f(l4.get("ma_alignment")), _f(l4.get("bb_width_pct"), 1),
            _f(l4.get("rsi14"), 1),
            (str(q.get("grade") or "") + (f"{q['score']:.0f}" if isinstance(q.get("score"), (int, float)) else "")),
            "|".join(map(str, s.get("patterns") or [])),
        ]
        out.append(",".join(row))
    return "\n".join(out)


def classify_window(n_days: int):
    if n_days <= 1:
        return ("LOW", "첫 분석 — 시계열 없음. Phase 1/2/3 미구성. 오늘 스냅샷의 구조적 해석만.")
    if n_days <= 6:
        return ("MEDIUM_LOW", f"📊 초기 관찰 — {n_days}개 세션. Phase 1은 유보. Final Absorption 조건부.")
    if n_days <= 14:
        return ("MEDIUM", f"📊 단기 트렌드 — {n_days}개 세션. Phase 1 setup 가능.")
    return ("HIGH", f"📊 표준 심화 — {n_days}개 세션. 전체 프레임워크 적용.")


def upcoming_trading_days(session_date: str, n: int = 15) -> List[str]:
    from smartmoney.calendar import next_trading_day, parse_date
    d = parse_date(session_date)
    return [next_trading_day(d, k).isoformat() for k in range(1, n + 1)]


def valuation_block(ticker: str, today: dict) -> List[str]:
    """Valuation Desk 가 발행한 펀더멘털 적정가 요약 (없으면 빈 목록). 수급 분석과 독립된 축으로만 전달한다."""
    try:
        from smartmoney import valuation_desk as VD
        v = VD.read_published(ticker, today.get("price"), today.get("date"))
    except Exception:  # noqa: BLE001
        v = None
    if not v or v.get("fair_value") is None:
        return ["", "FAIR_VALUE: no — 발행된 펀더멘털 적정가 없음. 적정가·저평가·고평가 언급 금지"]
    R = v.get("research") or {}
    sc = R.get("scenarios") or {}
    rs = R.get("rate_sensitivity") or {}
    digest = {
        "fair_value": v.get("fair_value"), "range_bear_bull": [v.get("fair_low"), v.get("fair_high")],
        "price_at_session_close": v.get("price"), "gap_pct": v.get("upside_pct"), "verdict": v.get("verdict_ko"),
        "uncertainty": (v.get("uncertainty") or {}).get("label"),
        "scenarios": {k: {"prob": s.get("prob"), "value": s.get("value")} for k, s in sc.items()},
        "discount_rate_pm_1pt": [rs.get("plus_1pt"), rs.get("minus_1pt")] if rs else None,
        "verdict_changes_within_pm_1pt": (not R.get("verdict_robust", True)) if rs else None,
        "research_date": R.get("as_of"), "research_confidence": R.get("confidence"),
        "needs_refresh": bool(v.get("stale")), "thesis": (str(R.get("thesis") or "")[:320] or None),
    }
    return ["", "## 펀더멘털 적정가 (Valuation Desk 발행 — 수급 분석과 독립된 축)",
            ("FAIR_VALUE: yes — 아래는 사업·재무 리서치로 산출한 적정가다. 규칙: (1) 수급 시나리오 확률과 Phase 판단에는 쓰지 않는다 — "
             "다크풀·공매도·옵션 신호와 섞지 말 것. (2) 가격 레벨 맥락과 핵심 결론에서 '현재가가 적정가 범위의 어디에 있는가'를 한두 문장으로만 언급한다. "
             "(3) 적정가는 점이 아니라 범위로 쓴다: 약세~강세 범위와 할인율 ±1%p 범위를 함께 적고, verdict_changes_within_pm_1pt 가 true 면 "
             "'저평가/고평가'로 단정하지 않는다. (4) 불확실성 등급과 리서치 날짜를 밝히고, needs_refresh 가 true 면 '갱신 필요'라고 적는다. "
             "(5) 목표가·매수/매도 권유로 쓰지 않는다."),
            "```json", json.dumps(_prune(digest), ensure_ascii=False, indent=1), "```"]


def build_user_text(ticker: str, mode: str, series: List[dict]) -> Tuple[str, str]:
    today = series[-1]
    n_days = len(series)
    cap, note = classify_window(n_days)
    data_date = today["date"]
    snap = snapshot(today)
    l1 = snap.get("l1") or {}
    has_cohort = any(k in l1 for k in ("delta_inst", "delta_pro", "delta_retail", "iar"))
    mode_hint = {
        "weekly": f"이번 주(데이터 기준 {data_date} 포함 최근 5개 세션) 흐름 중심으로 서술하되, 전체 {n_days}개 세션 컨텍스트 참고.",
        "daily": "최신 세션 스냅샷 중심, 최근 5개 세션 변화 맥락 언급.",
        "adhoc": "사용자 온디맨드 요청. 최근 흐름 기반 표준 분석.",
    }[mode]
    parts = [
        f"TICKER: {ticker}",
        f"DATA_DATE: {data_date} (미국 정규장 세션, ET)",
        f"MAX_CONFIDENCE: {cap}",
        f"N_days: {n_days}",
        f"UPCOMING_TRADING_DAYS: {', '.join(upcoming_trading_days(data_date))}",
        f"COHORT_SPLIT_AVAILABLE: {'yes' if has_cohort else 'no — 기관/프로/리테일 분리·IAR 언급 금지'}",
        ("TRADE_FLOW: yes — l1.trade_flow 는 SIP 전 체결(Alpaca) 기반 실측. 세션별(프리/정규/애프터) 다크풀 배수, "
         "블록·장외 블록 수, 마지막 30분 블록 집중도(Final Absorption ①②), BJZZ 리테일 불균형, 코호트 거래량 비중을 "
         "다크풀 섹션의 1차 근거로 사용하고, pattern_evidence 의 조건별 충족 여부를 그대로 인용할 것."
         if "trade_flow" in l1 else "TRADE_FLOW: no — 체결 단위 세부(세션별 다크풀·블록) 언급 금지"),
        "",
        note,
        mode_hint,
        "",
        f"## 최신 세션({data_date}) 스냅샷 (JSON, 없는 필드 = 데이터 없음)",
        "```json",
        json.dumps(snap, ensure_ascii=False, indent=1),
        "```",
    ]
    parts += valuation_block(ticker, today)
    if n_days > 1:
        parts += ["", f"## 시계열 ({n_days}개 세션, CSV — 빈 칸 = 데이터 없음, net_gex_m = 백만 단위)",
                  "```csv", to_csv(series), "```"]
    parts += ["", (f"요청: Smart Money 분석 프레임워크에 따라 {ticker} 내러티브 리포트를 지정된 HTML 템플릿으로 생성. "
                   f"필요 시 web_search 로 {data_date} 이전 1주의 뉴스·이벤트·SEC 공시를 확인하고 출처·날짜를 명시. "
                   f"신뢰도 상한은 {cap}.")]
    return "\n".join(parts), cap


# ───────────────────────── Claude API ─────────────────────────
def build_payload(model: str, messages: list) -> Tuple[dict, dict]:
    headers = {
        "Content-Type": "application/json",
        "x-api-key": os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        "anthropic-version": ANTHROPIC_VERSION,
    }
    modern = is_modern(model)
    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        # 정적 시스템 프롬프트 → 종목 간·pause_turn 재개 간 캐시 재사용
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "tools": [{
            "type": "web_search_20260209" if modern else "web_search_20250305",
            "name": "web_search",
            "max_uses": WEB_SEARCH_MAX_USES,
        }],
        "messages": messages,
    }
    # temperature/top_p/top_k 는 보내지 않음 (Opus 4.7+ 계열에서 400).
    # thinking 생략 = Opus 5.5 에선 adaptive. 깊이는 effort 로 조절.
    if modern:
        payload["output_config"] = {"effort": EFFORT}
    if model in FALLBACK_MODELS:
        headers["anthropic-beta"] = FALLBACK_BETA
        payload["fallbacks"] = "default"
    return headers, payload


def post_with_retry(headers: dict, payload: dict) -> dict:
    import requests

    last = ""
    for attempt in range(MAX_RETRIES):
        try:
            res = requests.post(API, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as e:
            last = f"network: {e}"
            res = None
        if res is not None:
            if res.status_code == 200:
                return res.json()
            body = res.text[:500]
            overloaded = "overloaded" in body
            if res.status_code not in RETRY_STATUS and not overloaded:
                raise RuntimeError(f"Claude HTTP {res.status_code}: {body}")
            last = f"HTTP {res.status_code}: {body}"
            retry_after = res.headers.get("retry-after")
        else:
            retry_after = None
        if attempt == MAX_RETRIES - 1:
            break
        try:
            delay = float(retry_after) if retry_after else 0.0
        except ValueError:
            delay = 0.0
        delay = min(max(delay, 2 * (2 ** attempt) + random.uniform(0, 1.5)), 90)
        print(f"[narr] retry {attempt + 1}/{MAX_RETRIES - 1} in {delay:.0f}s ({last[:120]})", file=sys.stderr)
        time.sleep(delay)
    raise RuntimeError(f"Claude request failed after {MAX_RETRIES} attempts: {last}")


def response_cost(data: dict, requested_model: str) -> float:
    u = data.get("usage") or {}
    model = data.get("model") or requested_model
    price = PRICING.get(model) or PRICING.get(requested_model) or PRICING["claude-opus-5-5"]
    inp, outp, cread = price
    cread = cread if cread is not None else inp * 0.1
    cost = (
        (u.get("input_tokens") or 0) * inp
        + (u.get("cache_creation_input_tokens") or 0) * inp * 1.25
        + (u.get("cache_read_input_tokens") or 0) * cread
        + (u.get("output_tokens") or 0) * outp
    ) / 1e6
    searches = ((u.get("server_tool_use") or {}).get("web_search_requests") or 0)
    return cost + searches * WEB_SEARCH_PER_REQ


def call_claude(user_text: str, model: str = MODEL) -> Dict[str, Any]:
    """web_search 서버 툴 + pause_turn 재개. 모든 턴의 토큰/비용 합산."""
    messages: List[dict] = [{"role": "user", "content": [{"type": "text", "text": user_text}]}]
    texts: List[str] = []
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
              "cache_read_input_tokens": 0, "web_search_requests": 0, "cost": 0.0, "turns": 0}
    served_model = model
    for _ in range(MAX_CONTINUATIONS + 1):
        headers, payload = build_payload(model, messages)
        data = post_with_retry(headers, payload)
        totals["turns"] += 1
        u = data.get("usage") or {}
        for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            totals[k] += u.get(k) or 0
        totals["web_search_requests"] += ((u.get("server_tool_use") or {}).get("web_search_requests") or 0)
        totals["cost"] += response_cost(data, model)
        served_model = data.get("model") or served_model
        content = data.get("content") or []
        texts.extend(b.get("text", "") for b in content if b.get("type") == "text")
        stop = data.get("stop_reason")
        if stop == "pause_turn":
            # 서버측 샘플링 루프 일시정지 → assistant 내용을 그대로 덧붙여 재요청 (추가 user 메시지 없음)
            messages = messages + [{"role": "assistant", "content": content}]
            continue
        if stop == "refusal":
            det = data.get("stop_details") or {}
            raise RuntimeError(f"model refusal (category={det.get('category')}): {det.get('explanation')}")
        if stop == "max_tokens":
            raise RuntimeError("max_tokens reached — output truncated")
        if stop not in ("end_turn", "stop_sequence", None):
            raise RuntimeError(f"unexpected stop_reason: {stop}")
        return {"text": "".join(texts), "usage": totals, "model": served_model}
    raise RuntimeError(f"pause_turn continuation limit ({MAX_CONTINUATIONS}) exceeded")


# ───────────────────────── Main ─────────────────────────
def existing_covers(out_dir: Path, mode: str, date_str: str) -> Optional[Path]:
    """같은 모드의 narrative 가 이 세션 날짜 이후 날짜로 이미 있으면 그 경로 (레거시 실행일 명명 포함)."""
    if not out_dir.exists():
        return None
    for p in sorted(out_dir.glob(f"*-{mode}*.html"), reverse=True):
        m = re.match(r"(\d{4}-\d{2}-\d{2})-" + re.escape(mode) + r"(?:-[\w\-]+)?\.html$", p.name)
        if m and m.group(1) >= date_str:
            return p
    return None


def _page(ticker: str, date_str: str, mode: str, meta: str, html_body: str) -> str:
    t = html.escape(ticker)
    return (
        f"<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"UTF-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{t} AI Narrative · {html.escape(date_str)} ({html.escape(mode)})</title></head>"
        f"<body style=\"background:#FFFFFF;color:#1C1B19;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;\">"
        f"<div style=\"max-width:1000px;margin:0 auto;\">"
        f"<div style=\"font-size:11px;color:#6E6A63;margin-bottom:12px;\">"
        f"<a href=\"../../../tickers/{t}/\" style=\"color:#2C4C9C;text-decoration:none;\">← {t} page</a> · "
        f"{html.escape(meta)}</div>{html_body}</div></body></html>"
    )


def _load_series(ticker: str, days: int) -> List[dict]:
    from build_index import scan_ticker
    # v3 리포트만 — v2 는 방법론(OBV·대차 출처·판정 규칙)이 달라 추세 해석에 섞지 않는다
    return [r for r in scan_ticker(ticker) if not r.get("legacy")][-max(1, days):]


def cmd_prepare(ticker: str, mode: str, days: int, force: bool, out_dir_s: str) -> int:
    """API 호출 없이 프롬프트 패키지만 생성 — 예약된 Claude Code 세션이 직접 작성한다."""
    series = _load_series(ticker, days)
    if not series:
        print(f"[skip] {ticker}: 리포트 JSON 없음", file=sys.stderr)
        return 0
    date_str = series[-1]["date"]
    out_dir = TICKERS_DIR / ticker / "narratives"
    target = out_dir / f"{date_str}-{mode}.html"
    if not force:
        covered = target if target.exists() else (existing_covers(out_dir, mode, date_str) if mode != "adhoc" else None)
        if covered:
            print(json.dumps({"ticker": ticker, "status": "skip", "reason": f"{covered.name} already covers {date_str}"}))
            return 0
    user_text, cap = build_user_text(ticker, mode, series)
    work = Path(out_dir_s)
    work.mkdir(parents=True, exist_ok=True)
    prompt_path = work / f"{ticker}_prompt.md"
    body_path = work / f"{ticker}_body.html"
    prompt_path.write_text(
        "# 역할·작성 규칙 (SYSTEM)\n\n" + SYSTEM_PROMPT +
        "\n\n---\n\n# 데이터 (USER)\n\n" + user_text +
        "\n\n---\n\n# 출력 방법\n"
        f"- 위 규칙의 HTML 템플릿대로 본문 HTML 만 작성해 `{body_path}` 파일로 저장한다 (```html 코드펜스 없이도 됨).\n"
        "- <script>/<style>/이벤트 핸들러/외부 이미지 금지 — 발행 단계에서 허용목록 정화기로 제거된다.\n"
        "- 뉴스·이벤트·공시는 웹 검색으로 확인하고 날짜·출처를 명시한다. 주말·휴장일 날짜 금지 (UPCOMING_TRADING_DAYS 사용).\n",
        encoding="utf-8")
    print(json.dumps({"ticker": ticker, "status": "ready", "session": date_str, "mode": mode, "confidence_cap": cap,
                      "prompt": str(prompt_path), "body": str(body_path), "target": str(target)}, ensure_ascii=False))
    return 0


def cmd_publish(ticker: str, mode: str, days: int, body_file: str, model_label: str) -> int:
    series = _load_series(ticker, days)
    if not series:
        print(f"[FATAL] {ticker}: 리포트 JSON 없음", file=sys.stderr)
        return 1
    date_str = series[-1]["date"]
    raw = Path(body_file).read_text(encoding="utf-8")
    html_body = sanitize_html(extract_html_block(raw))
    if "<div" not in html_body or len(html_body) < 1500:
        print(f"[FATAL] {ticker}: 본문 HTML 이 비었거나 너무 짧음 ({len(html_body)} chars)", file=sys.stderr)
        return 1
    meta = (f"Generated by {model_label} ({mode}) · 데이터 기준 {date_str} 미국 정규장 세션 · "
            f"예약 작업(Claude Code) 생성 — API 과금 없음")
    out_dir = TICKERS_DIR / ticker / "narratives"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date_str}-{mode}.html"
    out_path.write_text(_page(ticker, date_str, mode, meta, html_body), encoding="utf-8")
    print(f"[narr] {ticker} published → {out_path}")
    return 0


_BODY_RE = re.compile(r"(?is)^(.*?<head>.*?</head>)\s*<body([^>]*)>(.*)</body>\s*</html>\s*$")


def cmd_resanitize_all(dry: bool) -> int:
    """이미 게시된 narrative 페이지를 현재 허용목록 정화기로 재정화."""
    changed = scanned = 0
    stripped: Dict[str, int] = {}
    for p in sorted(TICKERS_DIR.glob("*/narratives/*.html")):
        scanned += 1
        src = p.read_text(encoding="utf-8")
        m = _BODY_RE.match(src)
        if not m:
            print(f"[resan] skip (unrecognized layout): {p}", file=sys.stderr)
            continue
        head, battrs, inner = m.groups()
        head = re.sub(r"(?is)<script.*?</script>", "", head)
        battrs = re.sub(r"(?i)\s+on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", battrs)
        battrs = re.sub(r"-webkit-user-select:none;|-moz-user-select:none;|-ms-user-select:none;|user-select:none;|"
                        r"-webkit-touch-callout:none;", "", battrs)
        clean = sanitize_html(inner)
        for tag in re.findall(r"(?i)<(script|iframe|svg|object|embed|img|style)\b", inner):
            stripped[tag.lower()] = stripped.get(tag.lower(), 0) + 1
        for attr in re.findall(r"(?i)\s(on[a-z]+)\s*=", inner):
            stripped[attr.lower()] = stripped.get(attr.lower(), 0) + 1
        out = f"<!DOCTYPE html><html lang=\"ko\">{head[head.lower().find('<head'):]}<body{battrs}>{clean}</body></html>"
        if out != src:
            changed += 1
            if not dry:
                p.write_text(out, encoding="utf-8")
    print(f"[resan] scanned {scanned} · rewritten {changed} · unsafe markup found: {stripped or 'none'}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Weekly AI narrative — 기본은 API 미사용 (예약 Claude 세션이 작성)")
    ap.add_argument("ticker", nargs="?")
    ap.add_argument("--mode", default="weekly", choices=["weekly", "daily", "adhoc"])
    ap.add_argument("--days", type=int, default=30, help="시계열 최대 세션 수 (기본 30)")
    ap.add_argument("--force", action="store_true", help="동일 세션 narrative 가 이미 있어도 다시 생성")
    ap.add_argument("--prepare", metavar="WORK_DIR", help="프롬프트 패키지 생성 (API 호출 없음)")
    ap.add_argument("--publish", metavar="BODY_HTML", help="작성된 본문 HTML 을 정화해 게시")
    ap.add_argument("--model-label", default="Claude Opus 5.5", help="게시 페이지 메타에 표기할 모델명")
    ap.add_argument("--resanitize-all", action="store_true", help="게시된 narrative 전체 재정화")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--api", action="store_true", help="(비권장·과금) Anthropic API 로 직접 생성 — ANTHROPIC_API_KEY 필요")
    args = ap.parse_args()

    if args.resanitize_all:
        sys.exit(cmd_resanitize_all(args.dry_run))
    if not args.ticker:
        ap.error("ticker 필요")
    ticker = args.ticker.upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,10}", ticker):
        print(f"[FATAL] invalid ticker: {ticker!r}", file=sys.stderr)
        sys.exit(2)
    if args.prepare:
        sys.exit(cmd_prepare(ticker, args.mode, args.days, args.force, args.prepare))
    if args.publish:
        sys.exit(cmd_publish(ticker, args.mode, args.days, args.publish, args.model_label))
    if not args.api:
        ap.error("--prepare / --publish / --resanitize-all 중 하나를 지정 (API 직접 호출은 --api)")
    sys.exit(cmd_api(ticker, args.mode, args.days, args.force))


def cmd_api(ticker: str, mode: str, days: int, force: bool) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("[FATAL] ANTHROPIC_API_KEY env var missing", file=sys.stderr)
        return 2
    series = _load_series(ticker, days)
    if not series:
        print(f"[skip] {ticker}: 리포트 JSON 없음 (먼저 sma.py 실행 필요)", file=sys.stderr)
        return 0
    date_str = series[-1]["date"]
    out_dir = TICKERS_DIR / ticker / "narratives"
    out_path = out_dir / f"{date_str}-{mode}.html"
    if not force:
        covered = out_path if out_path.exists() else (existing_covers(out_dir, mode, date_str) if mode != "adhoc" else None)
        if covered:
            print(f"[narr] {ticker} skip: {covered.name} already covers session {date_str} (use --force)", file=sys.stderr)
            return 0
    user_text, cap = build_user_text(ticker, mode, series)
    t0 = time.time()
    try:
        result = call_claude(user_text)
    except Exception as e:  # noqa: BLE001
        print(f"[narr] {ticker} FAILED: {e}", file=sys.stderr)
        return 1
    html_body = sanitize_html(extract_html_block(result["text"]))
    if "<div" not in html_body:
        print(f"[narr] {ticker} FAILED: response contained no HTML body", file=sys.stderr)
        return 1
    u = result["usage"]
    meta = (f"Generated by {result['model']} ({mode}) via API · 데이터 기준 {date_str} 미국 정규장 세션 · "
            f"web search {u['web_search_requests']} · ~${u['cost']:.4f}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_page(ticker, date_str, mode, meta, html_body), encoding="utf-8")
    print(f"[narr] {ticker} OK ({time.time() - t0:.1f}s) → {out_path}")
    return 0


if __name__ == "__main__":
    main()
