/* ─────────────────────────────────────────────────────────────────────────
   Smart Money Analyzer — AI Narrative
   Browser-side Claude API client (narrative.py 와 같은 프롬프트/데이터 형식).
   - API 키는 이 브라우저 localStorage 에만 저장 (repo/다른 사이트로 전송 X)
   - 모델 출력은 allowlist sanitizer(DOMParser) 를 거친 DOM 노드로만 삽입
   ───────────────────────────────────────────────────────────────────────── */
(function(){

const LS_ANTH_KEY = 'sma_anthropic_key';
const MODEL = 'claude-opus-5-5';
const MODEL_LABEL = 'Claude Opus 5.5';
const EFFORT = 'high';
const API = 'https://api.anthropic.com/v1/messages';
const MAX_TOKENS = 16000;
const MAX_CONTINUATIONS = 6;     // pause_turn 재개 상한
const MAX_RETRIES = 6;
const WEB_SEARCH_MAX_USES = 5;
const MAX_SESSIONS = 30;
const RETRY_STATUS = new Set([408, 409, 429, 500, 502, 503, 504, 529]);
const FALLBACK_BETA = 'server-side-fallback-2026-07-01';
// $/1M tokens: [input, output, cache_read]. cache write = 1.25×input, web search $10/1k
const PRICING = {
  'claude-opus-5-5': [4, 20, 0.20], 'claude-opus-5': [5, 25, 0.5], 'claude-opus-4-8': [5, 25, 0.5],
  'claude-fable-5-2': [8, 40, 0.20], 'claude-sonnet-5-5': [2, 10, 0.10], 'claude-sonnet-5': [2, 10, 0.2]
};

// 시스템 프롬프트 — narrative.py SYSTEM_PROMPT 와 동일 (tools/test_sanitizer.py 가 동기화 검사)
const SYSTEM_PROMPT_CORE = `You are a Smart Money forensic analyst following the CLAUDE.md framework.

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

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1F2328;">

<div style="margin-bottom:16px;">
  <h1 style="font-size:20px;font-weight:700;margin:0 0 4px 0;color:#1F2328;">{{TICKER}} Smart Money Forensic · 데이터 기준 {{DATE}}</h1>
  <div style="font-size:11px;color:#656D76;">{{price_quality_and_patterns_oneliner}}</div>
</div>

<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:0.5px solid #9A6700;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#9A6700;font-weight:700;font-size:14px;">▶ 핵심 발견</span>
  <span style="background:{{kf_badge_bg}};color:{{kf_badge_fg}};padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;">{{KF_BADGE_TEXT}}</span>
</div>
<p style="font-size:13px;line-height:1.7;color:#1F2328;margin:0;">{{핵심 1-2문장, 패러독스/충돌 우선}}</p>

<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:0.5px solid #9A6700;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#9A6700;font-weight:700;font-size:14px;">▶ 4-LAYER SYNTHESIS</span>
</div>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;">
  <div style="background:#F6F8FA;border:0.5px solid #D0D7DE;border-radius:6px;padding:12px;">
    <div style="font-size:10px;color:#656D76;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">L1 DARK POOL</div>
    <div style="font-size:15px;font-weight:600;color:{{l1_color}};margin-bottom:6px;">{{l1_scenario_label}}</div>
    <div style="font-size:12px;line-height:1.6;color:#1F2328;">{{L1: Total OBV(5d) 방향+규모, dp_pct 수준, dp_ratio, Divergence. 코호트 필드가 없으면 기관/리테일 분리 언급 금지}}</div>
  </div>
  <div style="background:#F6F8FA;border:0.5px solid #D0D7DE;border-radius:6px;padding:12px;">
    <div style="font-size:10px;color:#656D76;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">L2 SHORT · CTB</div>
    <div style="font-size:15px;font-weight:600;color:{{l2_color}};margin-bottom:6px;">{{l2_scenario_label}}</div>
    <div style="font-size:12px;line-height:1.6;color:#1F2328;">{{L2: short% slope + CTB 방향(Δ%) + short interest/DTC. 절대 %를 bearish로 해석 금지}}</div>
  </div>
  <div style="background:#F6F8FA;border:0.5px solid #D0D7DE;border-radius:6px;padding:12px;">
    <div style="font-size:10px;color:#656D76;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">L3 OPTIONS</div>
    <div style="font-size:15px;font-weight:600;color:{{l3_color}};margin-bottom:6px;">{{l3_scenario_label}}</div>
    <div style="font-size:12px;line-height:1.6;color:#1F2328;">{{L3: Max Pain 거리, Net GEX 부호, Flip Zone regime, Call/Put Wall, Expected Move}}</div>
  </div>
  <div style="background:#F6F8FA;border:0.5px solid #D0D7DE;border-radius:6px;padding:12px;">
    <div style="font-size:10px;color:#656D76;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">L4 CHART</div>
    <div style="font-size:15px;font-weight:600;color:{{l4_color}};margin-bottom:6px;">{{l4_scenario_label}}</div>
    <div style="font-size:12px;line-height:1.6;color:#1F2328;">{{L4: MA 배열, BB 폭/위치, RSI, S/R 거리}}</div>
  </div>
</div>

<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:0.5px solid #9A6700;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#9A6700;font-weight:700;font-size:14px;">▶ ARCHITECT PHASE TIMELINE</span>
  <span style="background:#DDF4FF;color:#0969DA;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;">{{N_days}}개 세션 관찰</span>
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
    <tr style="background:#FFFFFF;"><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">Phase 3 Resolution</td><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">{{p3_dates}}</td><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">{{p3_desc: 조건부 시나리오 + 입력 데이터에 있는 관찰 레벨(구간)만}}</td></tr>
  </tbody>
</table>

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
    <tr style="background:#FFFFFF;"><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">{{trigger_name}}</td><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">{{direction_pill}}</td><td style="padding:6px 8px;border:0.5px solid #D0D7DE;font-family:monospace;">{{threshold}}</td><td style="padding:6px 8px;border:0.5px solid #D0D7DE;">{{implication}}</td></tr>
  </tbody>
</table>

<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:0.5px solid #9A6700;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#9A6700;font-weight:700;font-size:14px;">▶ CONFIDENCE & LIMITATIONS</span>
  <span style="background:{{conf_bg}};color:{{conf_fg}};padding:2px 10px;border-radius:12px;font-size:11px;font-weight:700;">{{MAX_CONFIDENCE}}</span>
</div>
<p style="font-size:12px;line-height:1.7;color:#1F2328;margin:0;">{{데이터 기준 세션 날짜 · N_days개 세션 · 데이터 품질 등급 · 누락 레이어/지표 · 웹 검색 출처와 날짜}}</p>

</div>

==== TEMPLATE END ====

TRIGGER ROWS: 3-5 rows, alternating row background #FFFFFF / #F6F8FA. Direction pills:
- bullish: <span style="background:#DAFBE1;color:#1A7F5A;padding:1px 8px;border-radius:10px;font-size:10px;font-weight:600;">BULLISH</span>
- bearish: <span style="background:#FFEBE9;color:#CF222B;padding:1px 8px;border-radius:10px;font-size:10px;font-weight:600;">BEARISH</span>
- caution: <span style="background:#FFF8C5;color:#9A6700;padding:1px 8px;border-radius:10px;font-size:10px;font-weight:600;">WATCH</span>

ARCHITECT PHASE when N_days < 7: replace the table with
<p style="font-size:12px;color:#9A6700;background:#FFF8C5;padding:8px 12px;border-radius:4px;">시계열 N개 세션으로 Phase 1(과거 setup)을 구조적으로 해석하기엔 부족합니다. 최소 7개 세션 필요.</p>

COLOR MAPPING:
- signal=BULLISH -> #1A7F5A, signal=BEARISH -> #CF222B, signal=NEUTRAL -> #9A6700
- confidence HIGH -> #DAFBE1/#1A7F5A, MEDIUM or MEDIUM_LOW -> #FFF8C5/#9A6700, LOW -> #FFEBE9/#CF222B

PROHIBITIONS:
- Markdown syntax 금지 (# ## - ** fenced code). 순수 HTML만.
- script, style, iframe, img, svg 태그·이벤트 핸들러·javascript:/data: URI 금지 (렌더 전에 모두 제거됨).
- 점(point) 가격 예측·단일 목표가 금지. 가격은 입력 데이터에 있는 레벨(max_pain, flip_zone, call_wall, put_wall, S/R, levels, expected_move)만 "관찰 레벨/구간"으로 인용. 확률은 입력의 scenarios 값만 사용.
- 시나리오 확률 80% 초과 금지.
- 주말/미국 휴장일 날짜 금지 — 미래 날짜는 UPCOMING_TRADING_DAYS 목록에서만.
- 원본 JSON/CSV에 없는 숫자 언급 금지 (웹 검색으로 얻은 사실은 출처·날짜와 함께만).
- 템플릿의 div/table/tr 구조를 생략·축약 금지. 2x2 카드와 Probability Bar 필수.

응답은 반드시 템플릿의 첫 div 로 시작하고 마지막 닫는 div 로 끝낸다. 앞뒤 설명 문장 금지.
`;

// ─── Utility ───
function esc(v) {
  return v == null ? '' : String(v).replace(/[&<>"']/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function lsGet(k) { try { return localStorage.getItem(k) || ''; } catch (_) { return ''; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (_) {} }
function lsDel(k) { try { localStorage.removeItem(k); } catch (_) {} }
function getKey() { return lsGet(LS_ANTH_KEY); }
function isNum(v) { return typeof v === 'number' && isFinite(v); }
function f(v, n) {
  if (v == null || typeof v === 'boolean') return '';
  if (typeof v === 'number') return isFinite(v) ? v.toFixed(n == null ? 2 : n) : '';
  return String(v).replace(/,/g, ';');
}

// ─── NYSE 거래일 캘린더 (smartmoney/calendar.py 포팅) ───
function ymd(t) { return new Date(t).toISOString().slice(0, 10); }
function utc(y, m, d) { return Date.UTC(y, m, d); }
function easter(y) {
  const a = y % 19, b = Math.floor(y / 100), c = y % 100, d = Math.floor(b / 4), e = b % 4;
  const f2 = Math.floor((b + 8) / 25), g = Math.floor((b - f2 + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30, i = Math.floor(c / 4), k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31), day = ((h + l - 7 * m + 114) % 31) + 1;
  return utc(y, month - 1, day);
}
function nthWeekday(y, m, wd, n) { let t = utc(y, m, 1); while (new Date(t).getUTCDay() !== wd) t += 864e5; return t + (n - 1) * 7 * 864e5; }
function lastWeekday(y, m, wd) { let t = utc(y, m + 1, 0); while (new Date(t).getUTCDay() !== wd) t -= 864e5; return t; }
function observed(t) { const w = new Date(t).getUTCDay(); return w === 6 ? t - 864e5 : (w === 0 ? t + 864e5 : t); }
const _hol = {};
function nyseHolidays(y) {
  if (_hol[y]) return _hol[y];
  const s = new Set();
  const ny = utc(y, 0, 1), nw = new Date(ny).getUTCDay();
  if (nw === 0) s.add(ymd(ny + 864e5)); else if (nw !== 6) s.add(ymd(ny));
  s.add(ymd(nthWeekday(y, 0, 1, 3)));            // MLK
  s.add(ymd(nthWeekday(y, 1, 1, 3)));            // Presidents
  s.add(ymd(easter(y) - 2 * 864e5));             // Good Friday
  s.add(ymd(lastWeekday(y, 4, 1)));              // Memorial
  if (y >= 2022) s.add(ymd(observed(utc(y, 5, 19))));  // Juneteenth
  s.add(ymd(observed(utc(y, 6, 4))));            // Independence
  s.add(ymd(nthWeekday(y, 8, 1, 1)));            // Labor
  s.add(ymd(nthWeekday(y, 10, 4, 4)));           // Thanksgiving
  s.add(ymd(observed(utc(y, 11, 25))));          // Christmas
  return (_hol[y] = s);
}
function isTradingDay(t) { const d = new Date(t), w = d.getUTCDay(); return w !== 0 && w !== 6 && !nyseHolidays(d.getUTCFullYear()).has(ymd(t)); }
function upcomingTradingDays(dateStr, n) {
  const out = []; let t = Date.parse(dateStr + 'T00:00:00Z');
  while (out.length < n) { t += 864e5; if (isTradingDay(t)) out.push(ymd(t)); }
  return out;
}

// ─── 데이터 → 프롬프트 (narrative.py 와 동일 형식) ───
const L1_KEYS = ['scenario','signal','confidence','dp_pct','dp_avg20','dp_ratio','dp_class','delta_total','divergence','obv_source','window_days','delta_inst','delta_pro','delta_retail','iar','dp_vwap','dp_vwap_dist_pct','ats'];
const L2_KEYS = ['scenario','signal','case','short_pct_latest','short_avg_14d','short_slope','slope_dir','anomaly_z','short_shares','ctb_fee','ctb_delta_pct','shares_available','avail_delta_pct','borrow_source','short_interest'];
const L3_KEYS = ['scenario','signal','expiry','dte','max_pain','max_pain_dist_pct','pc_oi','pc_vol','pc_vol_adj','skew','skew_25d','net_gex','flip_zone','call_wall','put_wall','atm_iv','iv30','term_structure','expected_move','expected_move_pct','oi_total','binary_event','monthly_expiry','monthly_max_pain'];
const L4_KEYS = ['scenario','signal','ma_alignment','sma20','sma50','sma200','ema9','ema21','rsi14','atr14','atr_pct','bb_width_pct','bb_upper','bb_lower','bb_position','bb_width_pctile','immediate_resistance','immediate_support','key_resistance','key_support','vol_ratio30','high_52w','low_52w'];

function prune(o) {
  if (typeof o === 'number') return isFinite(o) ? o : null;
  if (Array.isArray(o)) return o.map(prune).filter(x => x != null);
  if (o && typeof o === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(o)) {
      const p = prune(v);
      if (p == null || p === '' || (Array.isArray(p) && !p.length) || (typeof p === 'object' && !Array.isArray(p) && !Object.keys(p).length)) continue;
      out[k] = p;
    }
    return out;
  }
  return o;
}
function pick(d, keys) { const out = {}; d = d || {}; for (const k of keys) if (k in d) out[k] = d[k]; return out; }

function snapshot(row) {
  const q = row.quality || {};
  const layers = {};
  for (const [k, v] of Object.entries(q.layers || {})) if (v && typeof v === 'object') layers[k] = pick(v, ['status', 'missing']);
  const l1 = pick(row.l1, L1_KEYS);
  const cum = (row.l1 || {}).cumulative_daily || [];
  if (cum.length) l1.cumulative_daily = cum.slice(-5);
  return prune({
    ticker: row.ticker, data_date: row.date, schema: row.schema, price: row.price,
    prev_close: row.prev_close, change_pct: row.change_pct, headline: row.headline,
    macro_env: row.macro_env, patterns: row.patterns, scenarios: row.scenarios,
    phase: (row.schema || 2) >= 3 ? row.phase : null,   // 레거시 v2 phase 는 실행일 기준(주말 섞임)
    levels: (row.levels || []).slice(0, 12),
    quality: { grade: q.grade, score: q.score, layers },
    warnings: (row.warnings || []).slice(0, 10),
    l1, l2: pick(row.l2, L2_KEYS), l3: pick(row.l3, L3_KEYS), l4: pick(row.l4, L4_KEYS)
  });
}

const CSV_COLS = ['date','price','scen_A','scen_B','scen_C','raw_score','macro','dp_pct','dp_ratio','delta_total','divergence',
  'short_pct','short_shares','short_slope','ctb_fee','ctb_delta_pct','si_days_to_cover','max_pain','max_pain_dist_pct','pc_oi','pc_vol',
  'net_gex_m','flip_zone','call_wall','put_wall','exp_move_pct','ma_align','bb_width','rsi14','quality','patterns'];

function toCSV(series) {
  if (!series.length) return '';
  const rows = [CSV_COLS.join(',')];
  for (const s of series) {
    const l1 = s.l1 || {}, l2 = s.l2 || {}, l3 = s.l3 || {}, l4 = s.l4 || {}, sc = s.scenarios || {};
    const si = l2.short_interest || {}, q = s.quality || {};
    rows.push([
      s.date, f(s.price), f(sc.A_bullish, 0), f(sc.B_neutral, 0), f(sc.C_bearish, 0), f(sc.raw_score), f(s.macro_env),
      f(l1.dp_pct, 1), f(l1.dp_ratio), f(l1.delta_total, 0), f(l1.divergence),
      f(l2.short_pct_latest, 1), f(l2.short_shares, 0), f(l2.short_slope), f(l2.ctb_fee), f(l2.ctb_delta_pct, 1), f(si.days_to_cover, 1),
      f(l3.max_pain), f(l3.max_pain_dist_pct, 1), f(l3.pc_oi), f(l3.pc_vol),
      isNum(l3.net_gex) ? (l3.net_gex / 1e6).toFixed(1) : '',
      f(l3.flip_zone), f(l3.call_wall), f(l3.put_wall), f(l3.expected_move_pct, 1),
      f(l4.ma_alignment), f(l4.bb_width_pct, 1), f(l4.rsi14, 1),
      (q.grade || '') + (isNum(q.score) ? q.score.toFixed(0) : ''),
      (s.patterns || []).join('|')
    ].join(','));
  }
  return rows.join('\n');
}

function classifyWindow(n) {
  if (n <= 1) return { cap: 'LOW', note: '⚠ 첫 분석 — 시계열 없음. Phase 1/2/3 미구성. 오늘 스냅샷의 구조적 해석만.' };
  if (n <= 6) return { cap: 'MEDIUM_LOW', note: `📊 초기 관찰 — ${n}개 세션. Phase 1은 유보. Final Absorption 조건부.` };
  if (n <= 14) return { cap: 'MEDIUM', note: `📊 단기 트렌드 — ${n}개 세션. Phase 1 setup 가능.` };
  return { cap: 'HIGH', note: `📊 표준 심화 — ${n}개 세션. 전체 프레임워크 적용.` };
}

function buildUserText(ticker, series) {
  const today = series[series.length - 1];
  const n = series.length, cls = classifyWindow(n), d = today.date;
  const snap = snapshot(today);
  const l1 = snap.l1 || {};
  const hasCohort = ['delta_inst', 'delta_pro', 'delta_retail', 'iar'].some(k => k in l1);
  const parts = [
    `TICKER: ${ticker}`,
    `DATA_DATE: ${d} (미국 정규장 세션, ET)`,
    `MAX_CONFIDENCE: ${cls.cap}`,
    `N_days: ${n}`,
    `UPCOMING_TRADING_DAYS: ${upcomingTradingDays(d, 15).join(', ')}`,
    `COHORT_SPLIT_AVAILABLE: ${hasCohort ? 'yes' : 'no — 기관/프로/리테일 분리·IAR 언급 금지'}`,
    '', cls.note, '사용자 온디맨드 요청. 최근 흐름 기반 표준 분석.', '',
    `## 최신 세션(${d}) 스냅샷 (JSON, 없는 필드 = 데이터 없음)`, '```json', JSON.stringify(snap, null, 1), '```'
  ];
  if (n > 1) parts.push('', `## 시계열 (${n}개 세션, CSV — 빈 칸 = 데이터 없음, net_gex_m = 백만 단위)`, '```csv', toCSV(series), '```');
  parts.push('', `요청: Smart Money 분석 프레임워크에 따라 ${ticker} 내러티브 리포트를 지정된 HTML 템플릿으로 생성. ` +
    `필요 시 web_search 로 ${d} 이전 1주의 뉴스·이벤트·SEC 공시를 확인하고 출처·날짜를 명시. 신뢰도 상한은 ${cls.cap}.`);
  return { text: parts.join('\n'), cap: cls.cap, date: d, n };
}

// ─── Claude API (web_search 서버 툴 + pause_turn 재개 + 재시도) ───
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function postWithRetry(apiKey, body, statusCb) {
  let last = '';
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    let res = null;
    try {
      res = await fetch(API, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': apiKey,
          'anthropic-version': '2023-06-01',
          'anthropic-beta': FALLBACK_BETA,
          'anthropic-dangerous-direct-browser-access': 'true'
        },
        body: JSON.stringify(body)
      });
    } catch (e) { last = '네트워크 오류: ' + e.message; }
    if (res) {
      if (res.ok) return res.json();
      const err = await res.json().catch(() => ({}));
      const msg = (err && err.error && err.error.message) || res.statusText;
      if (res.status === 401) { lsDel(LS_ANTH_KEY); throw new Error('Anthropic API Key 무효 — 다시 저장해주세요.'); }
      const overloaded = err && err.error && err.error.type === 'overloaded_error';
      if (!RETRY_STATUS.has(res.status) && !overloaded) throw new Error(`API ${res.status}: ${msg}`);
      last = `API ${res.status}: ${msg}`;
    }
    if (attempt === MAX_RETRIES - 1) break;
    const ra = res && parseFloat(res.headers.get('retry-after') || '');
    const delay = Math.min(Math.max(isNum(ra) ? ra * 1000 : 0, 2000 * 2 ** attempt + Math.random() * 1500), 90000);
    statusCb(`재시도 대기 ${Math.round(delay / 1000)}s (${last.slice(0, 80)})`);
    await sleep(delay);
  }
  throw new Error(`요청 실패 (${MAX_RETRIES}회 시도): ${last}`);
}

function responseCost(data) {
  const u = data.usage || {};
  const p = PRICING[data.model] || PRICING[MODEL];
  const [inp, out, cr] = p;
  const searches = (u.server_tool_use || {}).web_search_requests || 0;
  return ((u.input_tokens || 0) * inp + (u.cache_creation_input_tokens || 0) * inp * 1.25 +
          (u.cache_read_input_tokens || 0) * cr + (u.output_tokens || 0) * out) / 1e6 + searches * 0.01;
}

async function callClaude(apiKey, systemPrompt, userText, statusCb) {
  let messages = [{ role: 'user', content: [{ type: 'text', text: userText }] }];
  const texts = [];
  const tot = { in: 0, out: 0, cacheRead: 0, searches: 0, cost: 0, turns: 0, model: MODEL };
  for (let i = 0; i <= MAX_CONTINUATIONS; i++) {
    statusCb(`${MODEL_LABEL} 호출 중 (turn ${i + 1})...`);
    const data = await postWithRetry(apiKey, {
      model: MODEL,
      max_tokens: MAX_TOKENS,
      system: [{ type: 'text', text: systemPrompt, cache_control: { type: 'ephemeral' } }],
      tools: [{ type: 'web_search_20260209', name: 'web_search', max_uses: WEB_SEARCH_MAX_USES }],
      output_config: { effort: EFFORT },
      fallbacks: 'default',
      messages
    }, statusCb);
    const u = data.usage || {};
    tot.turns++; tot.out += u.output_tokens || 0; tot.cacheRead += u.cache_read_input_tokens || 0;
    tot.in += (u.input_tokens || 0) + (u.cache_creation_input_tokens || 0) + (u.cache_read_input_tokens || 0);
    tot.searches += (u.server_tool_use || {}).web_search_requests || 0;
    tot.cost += responseCost(data);
    tot.model = data.model || tot.model;
    const content = data.content || [];
    for (const b of content) if (b.type === 'text') texts.push(b.text || '');
    if (data.stop_reason === 'pause_turn') {
      // 서버 샘플링 루프 일시정지 → assistant 내용을 그대로 덧붙여 재요청 (추가 user 메시지 없음)
      messages = messages.concat([{ role: 'assistant', content }]);
      statusCb(`웹 검색 계속 (turn ${i + 1})...`);
      continue;
    }
    if (data.stop_reason === 'refusal') {
      const det = data.stop_details || {};
      throw new Error(`모델이 요청을 거절함 (${det.category || 'unknown'})`);
    }
    if (data.stop_reason === 'max_tokens') throw new Error('출력 토큰 한도 도달 — 응답이 잘림');
    return { text: texts.join(''), usage: tot };
  }
  throw new Error(`pause_turn 재개 한도(${MAX_CONTINUATIONS}) 초과`);
}

// ─── HTML Sanitizer (allowlist, DOMParser → 새 DOM 재구성) ───
const ALLOWED_TAGS = new Set(['p','h1','h2','h3','h4','ul','ol','li','b','strong','em','i','table','thead','tbody','tr','th','td',
  'span','div','br','hr','a','code','blockquote','small','sup','sub']);
const VOID_TAGS = new Set(['br', 'hr']);
const DROP_CONTENT_TAGS = new Set(['script','style','iframe','object','embed','svg','math','noscript','template','textarea','title',
  'head','select','option','frameset','frame','noembed','noframes','xmp','plaintext','applet','canvas','video','audio','picture','form']);
const STYLE_BAD = /url\s*\(|expression|javascript|vbscript|@import|behavior|-moz-binding|image-set|position\s*:\s*(fixed|sticky)|[\\<>]|\/\*/i;
const CTRL_WS = /[\u0000-\u0020\u007f-\u009f\u200b-\u200f\u2028\u2029\ufeff]+/g;
const HTML_NS = 'http://www.w3.org/1999/xhtml';

function safeHref(v) {
  const p = v.replace(CTRL_WS, '').toLowerCase();
  return (p.startsWith('http://') || p.startsWith('https://')) ? v.trim() : null;
}
function safeStyle(v) {
  return (STYLE_BAD.test(v) || STYLE_BAD.test(v.replace(CTRL_WS, ''))) ? null : v.trim();
}
function cleanAttr(tag, name, value) {
  if (name === 'class') return value.replace(/[^\w\- ]/g, '').slice(0, 200);
  if (name === 'style') return safeStyle(value);
  if (name === 'href' && tag === 'a') return safeHref(value);
  if ((name === 'colspan' || name === 'rowspan') && (tag === 'td' || tag === 'th'))
    return /^\d{1,2}$/.test(value.trim()) && +value > 0 && +value <= 50 ? value.trim() : null;
  return null;   // on*, src, id, srcdoc, formaction … 전부 제거
}
function cleanInto(src, dst, doc) {
  for (const child of Array.from(src.childNodes)) {
    if (child.nodeType === 3) { dst.appendChild(doc.createTextNode(child.nodeValue)); continue; }
    if (child.nodeType !== 1) continue;                     // 주석 등 버림
    const tag = (child.localName || '').toLowerCase();
    if (DROP_CONTENT_TAGS.has(tag)) continue;
    if (!ALLOWED_TAGS.has(tag) || child.namespaceURI !== HTML_NS) { cleanInto(child, dst, doc); continue; }
    const el = doc.createElement(tag);
    for (const a of Array.from(child.attributes)) {
      const name = a.name.toLowerCase();
      if (el.hasAttribute(name)) continue;
      const v = cleanAttr(tag, name, a.value);
      if (v) el.setAttribute(name, v);
    }
    if (tag === 'a' && el.hasAttribute('href')) { el.setAttribute('rel', 'noopener noreferrer nofollow'); el.setAttribute('target', '_blank'); }
    if (!VOID_TAGS.has(tag)) cleanInto(child, el, doc);
    dst.appendChild(el);
  }
}
function extractHtmlBlock(text) {
  const s = text.indexOf('<div'), e = text.lastIndexOf('</div>');
  return (s === -1 || e === -1 || e < s) ? text : text.slice(s, e + 6);
}
// 반환: 현재 document 소유의 DocumentFragment (innerHTML 재파싱 없이 append 가능)
function sanitizeToFragment(raw) {
  const s = String(raw || '').trim().replace(/^```(?:html)?\s*/i, '').replace(/\s*```$/i, '');
  const parsed = new DOMParser().parseFromString(s, 'text/html');   // inert 문서: 스크립트/이미지 로드 없음
  const frag = document.createDocumentFragment();
  cleanInto(parsed.body, frag, document);
  return frag;
}
function sanitizeHTML(raw) {
  const box = document.createElement('div');
  box.appendChild(sanitizeToFragment(raw));
  return box.innerHTML;
}

// ─── UI Hookup ───
window.SMA_Narrative = {
  sanitizeHTML,
  mount(rootEl, ticker) {
    if (!/^[A-Z0-9.\-]{1,10}$/.test(ticker || '')) return;
    rootEl.innerHTML = `
      <details open style="margin-top:24px;background:#F6F8FA;border:0.5px solid #D0D7DE;border-radius:6px;">
        <summary style="cursor:pointer;padding:10px 14px;font-weight:600;font-size:14px;color:#0969DA;">AI 분석 레포트 요청하기 (${esc(MODEL_LABEL)})</summary>
        <div style="padding:14px;border-top:0.5px solid #D0D7DE;">
          <div data-role="need-key" style="display:none;margin-bottom:10px;">
            <div style="font-size:12px;color:#656D76;margin-bottom:6px;">
              Anthropic API Key 필요 (이 브라우저 localStorage 에만 저장, 커밋/제3자 전송 X — 요청은 api.anthropic.com 으로 직접).
              <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener">키 발급</a>
            </div>
            <input data-role="key" type="password" autocomplete="off" placeholder="sk-ant-..." style="width:360px;max-width:100%;padding:6px 10px;border:0.5px solid #D0D7DE;border-radius:4px;">
            <button data-role="save" style="padding:6px 14px;background:#0969DA;color:#fff;border:0;border-radius:4px;cursor:pointer;">저장</button>
          </div>
          <div data-role="panel" style="display:none;">
            <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center;">
              <button data-role="gen" style="padding:5px 12px;background:#EAEEF2;color:#1F2328;border:0.5px solid #D0D7DE;border-radius:4px;cursor:pointer;font-size:12px;">분석 요청</button>
              <button data-role="key-edit" style="padding:6px 10px;background:none;color:#656D76;border:0.5px solid #D0D7DE;border-radius:4px;cursor:pointer;font-size:11px;">API Key 변경</button>
              <span data-role="status" style="font-size:11px;color:#656D76;"></span>
            </div>
            <div data-role="output" style="background:#FFFFFF;border:0.5px solid #D0D7DE;border-radius:4px;padding:20px 24px;min-height:80px;font-size:13px;line-height:1.7;color:#1F2328;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"></div>
            <div data-role="save-area" style="display:none;margin-top:8px;">
              <button data-role="commit" style="padding:6px 14px;background:#9A6700;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:12px;">📝 리포트 저장 (repo 커밋)</button>
              <span data-role="commit-status" style="font-size:11px;color:#656D76;margin-left:8px;"></span>
            </div>
          </div>
        </div>
      </details>
    `;
    const $ = role => rootEl.querySelector(`[data-role="${role}"]`);
    const needKey = $('need-key'), panel = $('panel'), keyIn = $('key'), saveBtn = $('save');
    const editBtn = $('key-edit'), genBtn = $('gen'), status = $('status'), output = $('output');
    const saveArea = $('save-area'), commitBtn = $('commit'), commitSt = $('commit-status');

    function show(hasKey) {
      needKey.style.display = hasKey ? 'none' : 'block';
      panel.style.display   = hasKey ? 'block' : 'none';
    }
    show(!!getKey());

    saveBtn.addEventListener('click', () => {
      const v = keyIn.value.trim();
      if (!v.startsWith('sk-ant-')) { alert('유효한 Anthropic key 형식이 아닙니다 (sk-ant- 시작)'); return; }
      lsSet(LS_ANTH_KEY, v); keyIn.value = ''; show(true);
    });
    editBtn.addEventListener('click', () => show(false));

    let lastHTML = '', lastMeta = null;

    function showError(msg) {
      output.replaceChildren();
      const d = document.createElement('div');
      d.style.color = '#CF222B';
      d.textContent = '실패: ' + msg;
      output.appendChild(d);
    }

    genBtn.addEventListener('click', async () => {
      genBtn.disabled = true;
      output.textContent = '데이터 로드 중...';
      saveArea.style.display = 'none';
      try {
        const res = await fetch(`../../data/${encodeURIComponent(ticker)}.json`, { cache: 'no-cache' });
        if (!res.ok) throw new Error(`data/${ticker}.json 로드 실패`);
        const all = await res.json();
        if (!Array.isArray(all) || !all.length) throw new Error('시계열 비어있음');
        const series = all.slice(-MAX_SESSIONS);      // 세션 날짜 기준 (build_index 가 레거시 v2 매핑)
        const u = buildUserText(ticker, series);
        const { text, usage } = await callClaude(getKey(), SYSTEM_PROMPT_CORE, u.text, s => status.textContent = s);

        const frag = sanitizeToFragment(extractHtmlBlock(text));
        const holder = document.createElement('div');
        holder.appendChild(frag.cloneNode(true));
        lastHTML = holder.innerHTML;
        lastMeta = { date: u.date, usage };
        output.replaceChildren(frag);
        status.textContent = `완료 · 데이터 기준 ${u.date} · ${usage.turns} turn · 입력 ${usage.in} (캐시 ${usage.cacheRead}) · 출력 ${usage.out} · 검색 ${usage.searches} · ~$${usage.cost.toFixed(4)}`;
        saveArea.style.display = 'block';

        if (lsGet('sma_github_pat')) {
          autoCommit().catch(e => { commitSt.textContent = '자동 저장 실패 (수동 저장 버튼 사용): ' + e.message; });
        } else {
          commitSt.textContent = '※ GitHub PAT 저장하면 자동 아카이브됨 (대시보드에서 설정)';
        }
      } catch (e) {
        showError(e.message);
        status.textContent = '';
      } finally {
        genBtn.disabled = false;
      }
    });

    async function autoCommit() {
      const ghPat = lsGet('sma_github_pat');
      if (!ghPat || !lastHTML || !lastMeta) return;
      commitSt.textContent = '📝 repo에 자동 저장 중...';
      const now = new Date();
      const hh = String(now.getUTCHours()).padStart(2, '0');
      const mm = String(now.getUTCMinutes()).padStart(2, '0');
      const d = lastMeta.date, us = lastMeta.usage;
      const filename = `${d}-adhoc-${hh}${mm}.html`;     // 세션 날짜 + 생성 시각(UTC)
      const path = `reports/tickers/${ticker}/narratives/${filename}`;
      const meta = `Generated by ${us.model} (adhoc) · 데이터 기준 ${d} 미국 정규장 세션 · ${now.toISOString().slice(0, 16)} UTC · ~$${us.cost.toFixed(4)}`;
      const page = `<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(ticker)} AI Narrative · ${esc(d)} (adhoc)</title></head><body style="background:#FFFFFF;color:#1F2328;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;"><div style="max-width:1000px;margin:0 auto;"><div style="font-size:11px;color:#656D76;margin-bottom:12px;"><a href="../../../tickers/${esc(ticker)}/" style="color:#0969DA;text-decoration:none;">← ${esc(ticker)} page</a> · ${esc(meta)}</div>${lastHTML}</div></body></html>`;
      const content = btoa(unescape(encodeURIComponent(page)));
      const res = await fetch(`https://api.github.com/repos/z66g/sma/contents/${path}`, {
        method: 'PUT',
        headers: { 'Authorization': 'Bearer ' + ghPat, 'Accept': 'application/vnd.github+json' },
        body: JSON.stringify({ message: `narrative: ${ticker} ${d} adhoc`, content })
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.message || res.statusText);
      }
      commitSt.replaceChildren();
      commitSt.append('✅ 저장됨 → ');
      const a = document.createElement('a');
      a.href = 'narratives/' + encodeURIComponent(filename);
      a.textContent = filename;
      commitSt.append(a, ' · 1-2분 후 위 아카이브 목록에 자동 반영');
    }

    commitBtn.addEventListener('click', async () => {
      if (!lsGet('sma_github_pat')) { commitSt.textContent = 'GitHub PAT 없음 — 대시보드에서 먼저 설정'; return; }
      if (!lastHTML) { commitSt.textContent = '저장할 내러티브 없음'; return; }
      try { await autoCommit(); } catch (e) { commitSt.textContent = '실패: ' + e.message; }
    });
  }
};

})();
