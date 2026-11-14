/**
 * Cloudflare Worker — SMA cron trigger
 *
 * 스케줄된 시각마다 GitHub Actions workflow_dispatch API 를 호출해
 *   .github/workflows/daily-analysis.yml   (일일 분석)
 *   .github/workflows/weekly-narrative.yml (주간 AI 내러티브)
 * 를 정시에 실행시킨다. (GitHub-side cron 은 러너 혼잡으로 15분~1시간 지연이 흔함)
 *
 * 휴장일 처리: 02:00 UTC(화~토) 실행은 "직전 미국 정규장 세션"(= ET 기준 전날)을 분석한다.
 * 그 날이 주말/NYSE 휴장일이면 daily dispatch 를 건너뛴다. 워크플로 자체도
 * "이미 완료된 세션이면 skip" 로직이 있어 이중 안전장치다.
 *
 * Secrets (wrangler secret put):
 *   GH_PAT      — Fine-grained PAT (z66g/sma 전용, Actions: Read and write) 권장
 *   MANUAL_KEY  — 수동 POST /daily, /weekly 용 공유 비밀 (없으면 수동 dispatch 비활성)
 *
 * Vars (wrangler.toml [vars]): OWNER, REPO, REF
 */

const UA = "sma-cron-worker/2.0";
const WORKFLOWS = { daily: "daily-analysis.yml", weekly: "weekly-narrative.yml" };
const TICKER_RE = /^[A-Z^.\-]{1,10}$/;

// ─── NYSE 휴장일 (smartmoney/calendar.py 포팅) ───
const DAY = 864e5;
const ymd = (t) => new Date(t).toISOString().slice(0, 10);
function easter(y) {
  const a = y % 19, b = Math.floor(y / 100), c = y % 100, d = Math.floor(b / 4), e = b % 4;
  const f = Math.floor((b + 8) / 25), g = Math.floor((b - f + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30, i = Math.floor(c / 4), k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31), day = ((h + l - 7 * m + 114) % 31) + 1;
  return Date.UTC(y, month - 1, day);
}
function nthWeekday(y, m, wd, n) { let t = Date.UTC(y, m, 1); while (new Date(t).getUTCDay() !== wd) t += DAY; return t + (n - 1) * 7 * DAY; }
function lastWeekday(y, m, wd) { let t = Date.UTC(y, m + 1, 0); while (new Date(t).getUTCDay() !== wd) t -= DAY; return t; }
function observed(t) { const w = new Date(t).getUTCDay(); return w === 6 ? t - DAY : w === 0 ? t + DAY : t; }
function nyseHolidays(y) {
  const s = new Set();
  const ny = Date.UTC(y, 0, 1), nw = new Date(ny).getUTCDay();
  if (nw === 0) s.add(ymd(ny + DAY)); else if (nw !== 6) s.add(ymd(ny));   // 토요일 New Year 은 대체휴일 없음
  s.add(ymd(nthWeekday(y, 0, 1, 3)));            // MLK
  s.add(ymd(nthWeekday(y, 1, 1, 3)));            // Presidents
  s.add(ymd(easter(y) - 2 * DAY));               // Good Friday
  s.add(ymd(lastWeekday(y, 4, 1)));              // Memorial
  if (y >= 2022) s.add(ymd(observed(Date.UTC(y, 5, 19))));  // Juneteenth
  s.add(ymd(observed(Date.UTC(y, 6, 4))));       // Independence
  s.add(ymd(nthWeekday(y, 8, 1, 1)));            // Labor
  s.add(ymd(nthWeekday(y, 10, 4, 4)));           // Thanksgiving
  s.add(ymd(observed(Date.UTC(y, 11, 25))));     // Christmas
  return s;
}
function isTradingDay(t) {
  const d = new Date(t), w = d.getUTCDay();
  return w !== 0 && w !== 6 && !nyseHolidays(d.getUTCFullYear()).has(ymd(t));
}

async function dispatch(workflow, env, inputs = {}) {
  const url = `https://api.github.com/repos/${env.OWNER}/${env.REPO}/actions/workflows/${workflow}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_PAT}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": UA,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: env.REF || "main", inputs }),
  });
  const body = res.ok ? "" : (await res.text()).slice(0, 500);
  return { workflow, status: res.status, ok: res.ok, body };
}

// 단일 cron "0,30 2 * * 2-6" (UTC) 을 분/요일로 분기.
//   minute 0            → daily  (단, 분석 대상 세션 = ET 기준 전날이 휴장일이면 skip)
//   minute 30 & 토요일  → weekly
function routeByTime(scheduledTime) {
  const d = new Date(scheduledTime);
  const minute = d.getUTCMinutes();
  const dow = d.getUTCDay(); // 0=Sun … 6=Sat
  if (minute === 0) {
    // 02:00 UTC = 전날 21:00/22:00 ET → 분석 세션 = UTC 날짜 - 1일
    const session = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - DAY;
    if (!isTradingDay(session)) return { wf: null, reason: `session ${ymd(session)} is not an NYSE trading day` };
    return { wf: WORKFLOWS.daily, session: ymd(session) };
  }
  if (minute === 30 && dow === 6) return { wf: WORKFLOWS.weekly };
  return { wf: null, reason: "no route for this minute/day" };
}

function timingSafeEqual(a, b) {
  const ea = new TextEncoder().encode(a), eb = new TextEncoder().encode(b);
  let diff = ea.length ^ eb.length;
  for (let i = 0; i < Math.max(ea.length, eb.length); i++) diff |= (ea[i] || 0) ^ (eb[i] || 0);
  return diff === 0;
}

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj, null, 2), { status, headers: { "Content-Type": "application/json" } });

export default {
  async scheduled(event, env, ctx) {
    const r = routeByTime(event.scheduledTime);
    if (!r.wf) {
      console.log(JSON.stringify({ event: "skip", reason: r.reason, scheduledTime: event.scheduledTime, cron: event.cron }));
      return;
    }
    const res = await dispatch(r.wf, env);
    console.log(JSON.stringify({ event: "dispatched", session: r.session, ...res, cron: event.cron, scheduledTime: event.scheduledTime }));
  },

  // 수동 트리거: POST /daily 또는 /weekly, 헤더 X-Manual-Key: <MANUAL_KEY>
  //   선택 쿼리: ?force=1 (daily/weekly 재실행), ?ticker=NVDA (단일 종목)
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    if (req.method !== "POST") {
      return new Response("POST /daily or /weekly with header X-Manual-Key to manually dispatch.\n", { status: 200 });
    }
    if (!env.MANUAL_KEY) return new Response("manual dispatch disabled (MANUAL_KEY not set)", { status: 503 });
    const key = req.headers.get("X-Manual-Key") || "";
    if (!timingSafeEqual(key, env.MANUAL_KEY)) return new Response("forbidden", { status: 403 });

    const name = url.pathname.replace(/^\/+|\/+$/g, "");
    const wf = WORKFLOWS[name];
    if (!wf) return new Response("not found", { status: 404 });

    const inputs = {};
    if (url.searchParams.get("force") === "1") inputs.force = "true";
    const ticker = (url.searchParams.get("ticker") || "").toUpperCase();
    if (ticker) {
      if (!TICKER_RE.test(ticker)) return new Response("invalid ticker", { status: 400 });
      inputs.ticker = ticker;
    }
    const r = await dispatch(wf, env, inputs);
    return json(r, r.ok ? 200 : 502);
  },
};
