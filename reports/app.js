
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
