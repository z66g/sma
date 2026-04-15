/**
 * Cloudflare Worker — SMA cron trigger
 *
 * 스케줄된 시각마다 GitHub Actions workflow_dispatch API를 호출해
 * daily.yml / weekly.yml 워크플로우를 강제로 실행시킨다.
 *
 * GitHub-side cron 은 러너 큐 혼잡으로 15분 ~ 1시간 지연이 흔해
 * 이 워커로 정시 트리거를 보장한다.
 *
 * Secrets (wrangler secret put):
 *   GH_PAT   — repo + workflow scope Classic PAT
 *
 * Vars (wrangler.toml [vars]):
 *   OWNER    — GitHub owner (z66g)
 *   REPO     — Repo name (sma)
 *   REF      — Branch to run against (main)
 */

const UA = "sma-cron-worker/1.0";

async function dispatch(workflow, env) {
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
    body: JSON.stringify({ ref: env.REF || "main" }),
  });
  const body = res.ok ? "" : await res.text();
  return { workflow, status: res.status, ok: res.ok, body };
}

// 단일 cron "0,30 2 * * 2-6" 이 발사한 scheduled 이벤트를 분 / 요일로 분기.
//   event.scheduledTime 은 ms epoch (UTC)
function routeByTime(scheduledTime) {
  const d = new Date(scheduledTime);
  const minute = d.getUTCMinutes();
  const dow = d.getUTCDay(); // 0=Sun … 6=Sat
  if (minute === 0) return "daily.yml";
  if (minute === 30 && dow === 6) return "weekly.yml";
  return null; // 토요일이 아닌 날의 02:30 은 skip
}

export default {
  async scheduled(event, env, ctx) {
    const wf = routeByTime(event.scheduledTime);
    if (!wf) {
      console.log(JSON.stringify({ event: "skip", scheduledTime: event.scheduledTime, cron: event.cron }));
      return;
    }
    const r = await dispatch(wf, env);
    console.log(JSON.stringify({ event: "dispatched", ...r, cron: event.cron, scheduledTime: event.scheduledTime }));
  },

  // 수동 테스트용: https://<worker>.workers.dev/daily 로 POST 하면 즉시 디스패치
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    if (req.method !== "POST") {
      return new Response(
        "POST /daily or /weekly to manually dispatch.\nRequires ?key=<MANUAL_KEY>",
        { status: 200 }
      );
    }
    if (env.MANUAL_KEY && url.searchParams.get("key") !== env.MANUAL_KEY) {
      return new Response("forbidden", { status: 403 });
    }
    let wf;
    if (url.pathname === "/daily") wf = "daily.yml";
    else if (url.pathname === "/weekly") wf = "weekly.yml";
    else return new Response("not found", { status: 404 });

    const r = await dispatch(wf, env);
    return new Response(JSON.stringify(r, null, 2), {
      status: r.ok ? 200 : 502,
      headers: { "Content-Type": "application/json" },
    });
  },
};
