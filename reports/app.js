
// Dashboard logic — operates on window.__DB__ (array of ticker summaries)
const DB = window.__DB__ || [];
const tbody = document.getElementById('tbody');
const q = document.getElementById('q');
const fScenario = document.getElementById('filter-scenario');
const fPattern = document.getElementById('filter-pattern');
const sortSel = document.getElementById('sort');
const countEl = document.getElementById('count');
const statBar = document.getElementById('statBar');

function fmtPct(v, dec){ return v==null ? '-' : (dec==null ? v.toFixed(1) : v.toFixed(dec)) + '%'; }
function fmtDol(v){ return v==null ? '-' : '$' + (+v).toFixed(2); }
function scenarioPill(s, v){
  const cls = 'pill pill-' + s;
  const lbl = { A: '[A] Bull', B: '[B] Neut', C: '[C] Bear' }[s] || s;
  return `<span class="${cls}">${lbl} ${v==null?'':v.toFixed(0)+'%'}</span>`;
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
  countEl.textContent = `${rows.length} / ${DB.length} tickers`;
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="16" class="empty">조건에 맞는 종목이 없습니다. <code>tickers.txt</code>를 편집하거나 워크플로우를 실행하세요.</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(r => {
    const patBadges = (r.patterns||[]).map(p => `<span class="pattern">${p}</span>`).join('') || '-';
    const tickerLink = `<a href="tickers/${r.ticker}/"><b>${r.ticker}</b></a>`;
    const dateLink = `<a href="${r.latest_date}/${r.ticker}_3Layer_Forensic_${r.latest_date}.html">${r.latest_date}</a>`;
    return `<tr>
      <td>${tickerLink}</td>
      <td>${dateLink}</td>
      <td>${fmtDol(r.price)}</td>
      <td>${scenarioPill(r.scenario, r.scenario_pct)}</td>
      <td>${r.raw_score==null?'-':r.raw_score.toFixed(2)}</td>
      <td>${r.macro_env||'-'}</td>
      <td>${r.l1_scenario||'-'}</td>
      <td>${(r.l2_case||'').replace('CASE_','C')||'-'}</td>
      <td>${r.l3_scenario||'-'}</td>
      <td>${fmtPct(r.dp_pct)}</td>
      <td>${fmtPct(r.short_pct)}</td>
      <td>${fmtPct(r.ctb_fee, 2)}</td>
      <td>${fmtDol(r.max_pain)}</td>
      <td>${patBadges}</td>
      <td>${r.report_count}</td>
      <td><a href="tickers/${r.ticker}/">›</a></td>
    </tr>`;
  }).join('');
}

function renderStats() {
  const total = DB.length;
  const bulls = DB.filter(r=>r.scenario==='A').length;
  const bears = DB.filter(r=>r.scenario==='C').length;
  const neuts = DB.filter(r=>r.scenario==='B').length;
  const patterns = DB.filter(r=>(r.patterns||[]).length>0).length;
  statBar.innerHTML = `
    <div class="stat"><div class="stat-label">Tickers</div><div class="stat-value">${total}</div></div>
    <div class="stat"><div class="stat-label">[A] Bullish</div><div class="stat-value" style="color:#1A7F5A;">${bulls}</div></div>
    <div class="stat"><div class="stat-label">[B] Neutral</div><div class="stat-value" style="color:#9A6700;">${neuts}</div></div>
    <div class="stat"><div class="stat-label">[C] Bearish</div><div class="stat-value" style="color:#CF222B;">${bears}</div></div>
    <div class="stat"><div class="stat-label">With Patterns</div><div class="stat-value">${patterns}</div></div>
  `;
}

[q, fScenario, fPattern, sortSel].forEach(el => el.addEventListener('input', render));
renderStats();
render();
