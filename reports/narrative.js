/* ─────────────────────────────────────────────────────────────────────────
   Smart Money Analyzer — AI Narrative (P3)
   Browser-side Claude API client.
   데이터 양(N=확보된 거래일 수)에 따라 프롬프트·신뢰도 상한을 자동 조정.
   ───────────────────────────────────────────────────────────────────────── */
(function(){

const LS_ANTH_KEY = 'sma_anthropic_key';
const MODEL = 'claude-sonnet-4-5';
const API = 'https://api.anthropic.com/v1/messages';
const MAX_TURNS = 5;

// 시스템 프롬프트 — CLAUDE.md §1/§8/§9/§10/§11 핵심 + 스타일드 HTML 출력 지시
const SYSTEM_PROMPT_CORE = `You are a Smart Money forensic analyst following the CLAUDE.md framework.

CORE STANCE:
- Zero bullish/bearish bias. Data and capital flows ONLY.
- Architect perspective — interpret unusual signals as intentional market design first.
- Never fabricate values not in the JSON/CSV input.
- Never exceed the MAX_CONFIDENCE cap.
- Prefer "insufficient data" to speculation.

LAYERS:
- L1 Dark Pool: delta_institutional = FINRA CNMS signed off-exchange volume (~100% institutional). IAR = |inst|/(|pro|+|retail|). High IAR = dark pool dominates.
- L2 Short Volume: FINRA Reg SHO short volume ≠ short interest. 40-55% is structurally normal. Judge only by slope + CTB + anomaly_z. NEVER read "short% > 50" as bearish.
- L3 Options: Max Pain, P/C, Net GEX, Flip Zone, IV Skew. Pinning meaningful only DTE ≤ 5. Positive Net GEX = long gamma (pin regime). Flip crossing = vol regime change.
- L4 Chart: MA alignment, BB width, immediate/key S&R.

WEIGHTS: L1=0.35, L2=0.20, L3=0.30, L4=0.15.

PATTERNS:
- FINAL_ABSORPTION: DP% > 40, short slope < 0, CTB delta ∈ [-5,+5]%, inst OBV > 0 (3+ of 5)
- THETA_BURN: 3+ days low vol + tight range + near-zero inst OBV
- LOW_CTB_PARADOX: CTB < 1% + shares_available ↓ > 5% = quiet institutional shorting
- GAMMA_SQUEEZE_SETUP: P/C OI < 0.7 + P/C Vol < 0.7 + rising call OI
- SHORT_SQUEEZE_RISK: CTB rising > 5% + short slope rising + HTB

ARCHITECT PHASES (N_days ≥ 7 only): Phase 1 past / Phase 2 present / Phase 3 future (trading days only).

══════════════════════════════════════════════════════════════
OUTPUT: STYLED HTML FRAGMENT (Korean text, English technical terms OK)
══════════════════════════════════════════════════════════════

Return ONLY the HTML fragment body (no <!DOCTYPE>, no <html>, no <body>, no <script>, no <style> tag — use INLINE styles only).

STRICT DESIGN SYSTEM (hardcoded HEX, never CSS variables):
- bull:      #1A7F5A  (bullish/buy/long)
- bear:      #CF222B  (bearish/sell/short)
- warn:      #9A6700  (section headers, caution)
- info:      #0969DA  (info, data source)
- text:      #1F2328  (primary)
- muted:     #656D76  (secondary)
- bg-card:   #F6F8FA  (cards)
- bg-panel:  #EAEEF2  (table headers)
- border:    #D0D7DE  (borders)
- alert-green: #DAFBE1 ; alert-red: #FFEBE9 ; alert-amber: #FFF8C5 ; alert-blue: #DDF4FF

FONT: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif  (apply to all inline styles)

MANDATORY SECTION PATTERN:
Each section starts with a header row like this:
<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:0.5px solid #9A6700;margin:24px 0 12px 0;padding-bottom:4px;">
  <span style="color:#9A6700;font-weight:700;font-size:14px;">▶ SECTION NAME</span>
  <div style="display:flex;gap:6px;flex-wrap:wrap;">
    <span style="background:#DAFBE1;color:#1A7F5A;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;">Key finding</span>
  </div>
</div>

BADGE TYPES (pill shape):
- bullish: background:#DAFBE1 color:#1A7F5A
- bearish: background:#FFEBE9 color:#CF222B
- warn:    background:#FFF8C5 color:#9A6700
- info:    background:#DDF4FF color:#0969DA

TABLES:
<table style="border-collapse:collapse;width:100%;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:12px;">
  <thead><tr><th style="background:#EAEEF2;color:#656D76;padding:4px 6px;border:0.5px solid #D0D7DE;text-align:left;">H</th>...</tr></thead>
  <tbody><tr style="background:#FFFFFF;"><td style="padding:4px 6px;border:0.5px solid #D0D7DE;">V</td>...</tr></tbody>
</table>

PROBABILITY BAR (CRITICAL — use this exact pattern):
<div style="display:flex;flex-direction:column;gap:4px;font-size:12px;">
  <div style="display:flex;align-items:center;gap:8px;">
    <span style="width:90px;color:#1A7F5A;font-weight:600;">[A] Bullish</span>
    <div style="flex:1;background:#EAEEF2;border-radius:4px;overflow:hidden;"><div style="width:XX%;background:#1A7F5A;height:14px;"></div></div>
    <span style="width:40px;text-align:right;font-weight:600;">XX%</span>
  </div>
  (similarly for B Neutral with #9A6700 and C Bearish with #CF222B)
</div>

REQUIRED SECTIONS (use this order, each with the header pattern):

1. ▶ 핵심 발견 (KEY FINDING)
   1-2 문장으로 가장 중요한 구조적 신호. 패러독스나 충돌이 있으면 우선.
   Use <p style="font-size:13px;line-height:1.7;color:#1F2328;">.

2. ▶ 4-LAYER SYNTHESIS
   4개 카드 (grid 2×2): L1 Dark Pool / L2 Short-CTB / L3 Options / L4 Chart.
   Each card:
   <div style="background:#F6F8FA;border:0.5px solid #D0D7DE;border-radius:6px;padding:12px;">
     <div style="font-size:11px;color:#656D76;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">L1 DARK POOL</div>
     <div style="font-size:15px;font-weight:600;color:#1A7F5A;">ACCUMULATION</div>
     <div style="font-size:12px;color:#1F2328;line-height:1.6;margin-top:6px;">...</div>
   </div>
   Wrap 4 cards in grid:
   <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;">...</div>

3. ▶ ARCHITECT PHASE TIMELINE  (skip entirely if N_days < 7, instead show one-line notice)
   3열 테이블: Phase | Dates | Description

4. ▶ PROBABILITY MATRIX
   위의 probability bar 패턴 3줄. 확률은 scenarios 값 그대로.
   바 아래에 small: Raw Score · Macro · Confidence.

5. ▶ DECISIVE TRIGGERS
   테이블: Trigger | Direction | Threshold | Implication
   3-5 rows with specific numeric thresholds.

6. ▶ CONFIDENCE & LIMITATIONS
   배지 하나: 신뢰도 {MAX_CONFIDENCE} (colored accordingly)
   + 짧은 문단으로 data gap / PARTIAL 섹션 / 확보 일수 명시.

STRICT PROHIBITIONS:
- 절대 <script>, <style>, <iframe>, event handlers(onclick= etc), external URLs(javascript:, data:) 포함 금지.
- 주가 예측 금지. 시나리오 확률만.
- 어떤 시나리오도 80% 초과 금지.
- 주말/휴장일 날짜 금지.
- 원본 JSON/CSV에 없는 숫자 언급 금지.
- Markdown 문법(# ## - **) 사용 금지. 순수 HTML만.
`;

// ─── Utility ───
function getKey() { return localStorage.getItem(LS_ANTH_KEY) || ''; }
function setKey(v) { localStorage.setItem(LS_ANTH_KEY, v); }
function clearKey() { localStorage.removeItem(LS_ANTH_KEY); }

function fmt(v, n=2) { return v==null ? '' : (typeof v==='number' ? v.toFixed(n) : v); }

function toCSV(series) {
  // series: 시계열 배열 (oldest→newest)
  if (!series.length) return '';
  const cols = [
    'date','price',
    'scen_A','scen_C','raw_score','macro',
    'dp_pct','inst_delta','pro_delta','retail_delta','iar','divergence',
    'short_pct','short_slope','slope_dir','ctb_fee',
    'max_pain','max_pain_dist','pc_oi','net_gex_bn','flip_zone',
    'ma_align','bb_width','patterns'
  ];
  const rows = [cols.join(',')];
  for (const s of series) {
    const l1=s.l1||{}, l2=s.l2||{}, l3=s.l3||{}, l4=s.l4||{}, sc=s.scenarios||{};
    rows.push([
      s.date,
      fmt(s.price,2),
      fmt(sc.A_bullish,0), fmt(sc.C_bearish,0), fmt(sc.raw_score,2),
      s.macro_env||'',
      fmt(l1.dp_pct,1),
      l1.delta_institutional==null ? '' : Math.round(l1.delta_institutional),
      l1.delta_professional==null  ? '' : Math.round(l1.delta_professional),
      l1.delta_retail==null        ? '' : Math.round(l1.delta_retail),
      fmt(l1.iar,2), l1.divergence||'',
      fmt(l2.short_pct_latest,1), fmt(l2.short_slope,2), l2.slope_dir||'', fmt(l2.ctb_fee,2),
      fmt(l3.max_pain,2), fmt(l3.max_pain_dist_pct,1), fmt(l3.pc_oi,2),
      l3.net_gex==null ? '' : (l3.net_gex/1e9).toFixed(2),
      fmt(l3.flip_zone,2),
      l4.ma_alignment||'', fmt(l4.bb_width_pct,1),
      (s.patterns||[]).join('|')
    ].join(','));
  }
  return rows.join('\n');
}

function classifyWindow(nDays) {
  if (nDays <= 1) return {
    cap: 'LOW',
    tier: 'SNAPSHOT',
    note: '⚠ 첫 분석 — 시계열 없음. Phase 1/2/3는 구성하지 말고 "시계열 부족"으로 유보. Divergence/trend 단정 금지. 오늘 스냅샷의 **구조적 해석**과 **다가오는 트리거 후보**만 제시.'
  };
  if (nDays <= 6) return {
    cap: 'MEDIUM_LOW',
    tier: 'EARLY',
    note: `📊 초기 관찰 — ${nDays}일 시계열. Theta Burn·Phase 1은 판단 불가 (최소 7일+ 필요). Final Absorption은 조건 충족 시 조건부 가능. **단기 관찰** 수준으로만 결론.`
  };
  if (nDays <= 14) return {
    cap: 'MEDIUM',
    tier: 'SHORT',
    note: `📊 단기 트렌드 — ${nDays}일 시계열. Phase 1 setup은 해석 가능하나 월간 반복 패턴(어닝 주기 등)은 추론 금지.`
  };
  return {
    cap: 'HIGH',
    tier: 'STANDARD',
    note: `📊 표준 심화 — ${nDays}일 시계열. 전체 프레임워크 적용.`
  };
}

async function callClaudeMultiTurn(apiKey, systemPrompt, userContent, statusCb) {
  const messages = [{ role: 'user', content: userContent }];
  let finalText = '';

  for (let turn = 0; turn < MAX_TURNS; turn++) {
    statusCb(`Claude 호출 중 (turn ${turn+1}/${MAX_TURNS})...`);
    const res = await fetch(API, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
        'anthropic-dangerous-direct-browser-access': 'true'
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: 8192,
        temperature: 0,
        system: systemPrompt,
        tools: [{ type: 'web_search_20250305', name: 'web_search' }],
        messages
      })
    });
    if (!res.ok) {
      const err = await res.json().catch(()=>({}));
      if (res.status === 401) {
        localStorage.removeItem(LS_ANTH_KEY);
        throw new Error('Anthropic API Key 무효 — 다시 저장해주세요.');
      }
      throw new Error(`API ${res.status}: ${err?.error?.message || res.statusText}`);
    }
    const data = await res.json();
    if (data.stop_reason === 'tool_use') {
      messages.push({ role:'assistant', content: data.content });
      const toolResults = data.content
        .filter(b => b.type === 'tool_use')
        .map(b => ({ type:'tool_result', tool_use_id: b.id, content:'' }));
      messages.push({ role:'user', content: toolResults });
      statusCb(`웹 검색 처리 중 (turn ${turn+1})...`);
      continue;
    }
    finalText = data.content.filter(b => b.type === 'text').map(b => b.text).join('');
    const usage = data.usage || {};
    statusCb(`완료 · 입력 ${usage.input_tokens||'?'} · 출력 ${usage.output_tokens||'?'} 토큰`);
    return { text: finalText, usage };
  }
  throw new Error('MAX_TURNS 초과 — 모델이 도구 호출 루프에서 빠져나오지 못함');
}

// HTML Sanitizer — 허용 태그/속성만 통과시키고 나머지 제거
function sanitizeHTML(raw) {
  // 1) Claude가 실수로 <!DOCTYPE>나 fenced code block을 섞어도 제거
  let s = raw.replace(/^```html\s*/i, '').replace(/\s*```$/i, '').trim();
  s = s.replace(/<\!DOCTYPE[^>]*>/gi, '');
  s = s.replace(/<html[^>]*>|<\/html>|<body[^>]*>|<\/body>|<head[^>]*>[\s\S]*?<\/head>/gi, '');

  // 2) DOMParser로 파싱 후 위험 노드/속성 제거
  const doc = new DOMParser().parseFromString(`<div>${s}</div>`, 'text/html');
  const root = doc.body.firstChild;
  const DANGEROUS_TAGS = new Set(['SCRIPT','STYLE','IFRAME','OBJECT','EMBED','LINK','META','BASE','FORM','INPUT','BUTTON','TEXTAREA']);

  function walk(node) {
    // 자식을 먼저 순회 (remove 중 index shift 방지하려고 array copy)
    for (const child of Array.from(node.childNodes)) walk(child);
    if (node.nodeType !== 1) return;
    if (DANGEROUS_TAGS.has(node.tagName)) {
      node.remove();
      return;
    }
    // 속성 필터링
    for (const attr of Array.from(node.attributes)) {
      const n = attr.name.toLowerCase();
      const v = attr.value;
      if (n.startsWith('on')) { node.removeAttribute(attr.name); continue; }
      if (n === 'href' || n === 'src') {
        if (/^\s*javascript:/i.test(v) || /^\s*data:/i.test(v)) {
          node.removeAttribute(attr.name);
        }
        continue;
      }
      if (n === 'style') {
        // style 값에서 url(...), expression(), javascript: 제거
        const cleaned = v.replace(/url\s*\([^)]*\)/gi,'').replace(/expression\s*\([^)]*\)/gi,'').replace(/javascript:/gi,'');
        node.setAttribute('style', cleaned);
        continue;
      }
      // class, id, width, height 등은 허용
    }
  }
  walk(root);
  return root.innerHTML;
}

// ─── UI Hookup ───
window.SMA_Narrative = {
  mount(rootEl, ticker) {
    rootEl.innerHTML = `
      <details open style="margin-top:24px;background:#F6F8FA;border:0.5px solid #D0D7DE;border-radius:6px;">
        <summary style="cursor:pointer;padding:10px 14px;font-weight:600;font-size:14px;color:#0969DA;">🤖 AI 심화 분석 (Claude Sonnet 4.5)</summary>
        <div style="padding:14px;border-top:0.5px solid #D0D7DE;">
          <div id="narr-need-key-${ticker}" style="display:none;margin-bottom:10px;">
            <div style="font-size:12px;color:#656D76;margin-bottom:6px;">
              Anthropic API Key 필요 (브라우저 localStorage만 저장, 커밋/전송 X).
              <a href="https://console.anthropic.com/settings/keys" target="_blank">키 발급</a>
            </div>
            <input id="narr-key-${ticker}" type="password" placeholder="sk-ant-..." style="width:360px;padding:6px 10px;border:0.5px solid #D0D7DE;border-radius:4px;">
            <button id="narr-save-${ticker}" style="padding:6px 14px;background:#0969DA;color:#fff;border:0;border-radius:4px;cursor:pointer;">저장</button>
          </div>
          <div id="narr-panel-${ticker}" style="display:none;">
            <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center;">
              <button id="narr-gen-${ticker}" style="padding:8px 20px;background:#1A7F5A;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:13px;font-weight:600;">▶ 내러티브 생성</button>
              <button id="narr-key-edit-${ticker}" style="padding:6px 10px;background:none;color:#656D76;border:0.5px solid #D0D7DE;border-radius:4px;cursor:pointer;font-size:11px;">API Key 변경</button>
              <span id="narr-status-${ticker}" style="font-size:11px;color:#656D76;"></span>
            </div>
            <div id="narr-output-${ticker}" style="background:#FFFFFF;border:0.5px solid #D0D7DE;border-radius:4px;padding:20px 24px;min-height:80px;font-size:13px;line-height:1.7;color:#1F2328;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"></div>
            <div id="narr-save-area-${ticker}" style="display:none;margin-top:8px;">
              <button id="narr-commit-${ticker}" style="padding:6px 14px;background:#9A6700;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:12px;">📝 리포트 저장 (repo 커밋)</button>
              <span id="narr-commit-status-${ticker}" style="font-size:11px;color:#656D76;margin-left:8px;"></span>
            </div>
          </div>
        </div>
      </details>
    `;

    const needKey = rootEl.querySelector(`#narr-need-key-${ticker}`);
    const panel   = rootEl.querySelector(`#narr-panel-${ticker}`);
    const keyIn   = rootEl.querySelector(`#narr-key-${ticker}`);
    const saveBtn = rootEl.querySelector(`#narr-save-${ticker}`);
    const editBtn = rootEl.querySelector(`#narr-key-edit-${ticker}`);
    const genBtn  = rootEl.querySelector(`#narr-gen-${ticker}`);
    const status  = rootEl.querySelector(`#narr-status-${ticker}`);
    const output  = rootEl.querySelector(`#narr-output-${ticker}`);
    const saveArea= rootEl.querySelector(`#narr-save-area-${ticker}`);
    const commitBtn=rootEl.querySelector(`#narr-commit-${ticker}`);
    const commitSt= rootEl.querySelector(`#narr-commit-status-${ticker}`);

    function show(hasKey) {
      needKey.style.display = hasKey ? 'none' : 'block';
      panel.style.display   = hasKey ? 'block' : 'none';
    }
    show(!!getKey());

    saveBtn.addEventListener('click', () => {
      const v = keyIn.value.trim();
      if (!v.startsWith('sk-ant-')) { alert('유효한 Anthropic key 형식이 아닙니다 (sk-ant- 시작)'); return; }
      setKey(v); keyIn.value = ''; show(true);
    });
    editBtn.addEventListener('click', () => show(false));

    let lastMarkdown = '';

    genBtn.addEventListener('click', async () => {
      genBtn.disabled = true;
      output.innerHTML = '<em style="color:#656D76;">데이터 로드 중...</em>';
      saveArea.style.display = 'none';
      try {
        // 시계열 JSON 로드
        const res = await fetch(`../../data/${ticker}.json`);
        if (!res.ok) throw new Error(`data/${ticker}.json 로드 실패`);
        const series = await res.json();
        const nDays = series.length;
        const today = series[series.length - 1];
        const csv = toCSV(series);
        const cls = classifyWindow(nDays);

        const systemPrompt = SYSTEM_PROMPT_CORE
          .replace('{TICKER}', ticker)
          .replace('{DATE}', today.date)
          .replace('{MAX_CONFIDENCE}', cls.cap);

        const userText = [
          cls.note,
          '',
          `## 오늘(${today.date}) 스냅샷 (full JSON)`,
          '```json',
          JSON.stringify(today, null, 2),
          '```',
          '',
          nDays > 1 ? `## 시계열 (${nDays}일, CSV)` : '',
          nDays > 1 ? '```csv' : '',
          nDays > 1 ? csv : '',
          nDays > 1 ? '```' : '',
          '',
          `요청: CLAUDE.md Smart Money 분석 프레임워크에 따라 ${ticker} 내러티브 리포트를 위 지정된 출력 포맷으로 생성. 필요 시 web_search로 최근 1주 뉴스·이벤트·SEC 공시 확인. 신뢰도 상한은 ${cls.cap}.`
        ].filter(Boolean).join('\n');

        status.textContent = 'Claude Sonnet 4.5 호출 중...';
        const { text, usage } = await callClaudeMultiTurn(
          getKey(), systemPrompt, [{ type: 'text', text: userText }],
          s => status.textContent = s
        );

        lastMarkdown = text;   // 저장용 원본
        output.innerHTML = sanitizeHTML(text);
        const cost = usage ? ((usage.input_tokens*3 + usage.output_tokens*15)/1e6).toFixed(4) : '?';
        status.textContent = `완료 · 입력 ${usage?.input_tokens||'?'} · 출력 ${usage?.output_tokens||'?'} · ~$${cost}`;
        saveArea.style.display = 'block';
      } catch (e) {
        output.innerHTML = `<div style="color:#CF222B;">실패: ${e.message}</div>`;
        status.textContent = '';
      } finally {
        genBtn.disabled = false;
      }
    });

    // Save-to-repo via GitHub PAT (reuses existing sma_github_pat from dashboard)
    commitBtn.addEventListener('click', async () => {
      const ghPat = localStorage.getItem('sma_github_pat');
      if (!ghPat) { commitSt.textContent = 'GitHub PAT 없음 — 대시보드에서 먼저 설정'; return; }
      if (!lastMarkdown) { commitSt.textContent = '저장할 내러티브 없음'; return; }
      commitSt.textContent = '커밋 중...';
      try {
        const today = new Date().toISOString().slice(0,10);
        const path = `reports/tickers/${ticker}/narratives/${today}.html`;
        // Get file SHA if exists
        let sha = undefined;
        const getRes = await fetch(`https://api.github.com/repos/z66g/sma/contents/${path}`, {
          headers: { 'Authorization': 'Bearer ' + ghPat, 'Accept': 'application/vnd.github+json' }
        });
        if (getRes.ok) { sha = (await getRes.json()).sha; }
        const page = `<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${ticker} AI Narrative · ${today}</title></head><body style="background:#FFFFFF;color:#1F2328;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;"><div style="max-width:1000px;margin:0 auto;"><div style="font-size:11px;color:#656D76;margin-bottom:12px;"><a href="../../" style="color:#0969DA;text-decoration:none;">← ${ticker} page</a> · Generated by Claude Sonnet 4.5 · ${today}</div>${sanitizeHTML(lastMarkdown)}</div></body></html>`;
        const content = btoa(unescape(encodeURIComponent(page)));
        const putRes = await fetch(`https://api.github.com/repos/z66g/sma/contents/${path}`, {
          method: 'PUT',
          headers: { 'Authorization': 'Bearer ' + ghPat, 'Accept': 'application/vnd.github+json' },
          body: JSON.stringify({ message: `narrative: ${ticker} ${today}`, content, sha })
        });
        if (!putRes.ok) {
          const err = await putRes.json().catch(()=>({}));
          throw new Error(err.message || putRes.statusText);
        }
        commitSt.textContent = `✅ 저장됨 → ${path}`;
      } catch (e) {
        commitSt.textContent = '실패: ' + e.message;
      }
    });
  }
};

})();
