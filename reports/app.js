
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
