
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
