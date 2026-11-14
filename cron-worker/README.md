# sma-cron (Cloudflare Worker)

GitHub Actions의 기본 cron 스케줄은 러너 큐 혼잡으로 지연이 흔해, 이 워커가
외부에서 `workflow_dispatch` API 를 호출해 정시 실행을 보장한다.
(cron-job.org 에서 같은 API 를 호출하는 방식도 동일하게 동작한다 — 둘 중 하나만 켜 둘 것.)

| 시각 (UTC) | 동작 |
|---|---|
| 화~토 02:00 (= KST 11:00) | `daily-analysis.yml` dispatch — 직전 미국 정규장 세션(ET 기준 전날) 분석 |
| 토 02:30 (= KST 11:30) | `weekly-narrative.yml` dispatch — 주간 AI 내러티브 |

## 휴장일 처리

- 워커: 02:00 UTC 실행이 분석할 세션(= UTC 날짜 − 1일, ET 기준 전날)이 주말/NYSE 휴장일이면
  daily dispatch 를 **건너뛴다** (`smartmoney/calendar.py` 규칙을 JS 로 포팅: New Year, MLK,
  Presidents, Good Friday, Memorial, Juneteenth, Independence, Labor, Thanksgiving, Christmas + 대체휴일).
- 워크플로: `python3 sma.py --print-session-date` 로 세션 날짜를 정하고, 그 세션의 v3 리포트가
  워치리스트 전 종목에 이미 있으면 분석·커밋·텔레그램을 생략한다 (`force=true` 입력으로 재실행).
  → 워커 규칙이 어긋나도 중복 리포트는 생기지 않는다.
- weekly 는 휴장일과 무관하게 토요일마다 실행 (이미 같은 세션 narrative 가 있으면 종목별 skip).

## 구조

```
cron-worker/
├── worker.js       # scheduled + fetch 핸들러, NYSE 휴장일 계산
├── wrangler.toml   # cron + vars
└── README.md
```

## 최초 배포

1. Node 18+ 설치 확인 후 `npm install -g wrangler`
2. `wrangler login`
3. `cd cron-worker`
4. GitHub 토큰을 secret 으로 등록 — **Fine-grained PAT 권장**
   (Repository access: Only select repositories → `z66g/sma`, Permissions: **Actions: Read and write**)
   ```bash
   wrangler secret put GH_PAT
   ```
5. 수동 트리거용 키 (설정하지 않으면 수동 POST 는 503 으로 거부)
   ```bash
   wrangler secret put MANUAL_KEY   # 임의 난수 문자열 (예: openssl rand -hex 32)
   ```
6. `wrangler deploy`

배포 후 CF 대시보드 → Workers & Pages → `sma-cron` → Triggers 탭에서 cron 확인.

## 수동 테스트

키는 **헤더**로만 전달한다 (쿼리스트링은 로그·히스토리에 남으므로 거부).

```bash
curl -X POST -H "X-Manual-Key: $MANUAL_KEY" "https://sma-cron.<account>.workers.dev/daily"
curl -X POST -H "X-Manual-Key: $MANUAL_KEY" "https://sma-cron.<account>.workers.dev/daily?force=1"
curl -X POST -H "X-Manual-Key: $MANUAL_KEY" "https://sma-cron.<account>.workers.dev/daily?ticker=NVDA"
curl -X POST -H "X-Manual-Key: $MANUAL_KEY" "https://sma-cron.<account>.workers.dev/weekly"
```

수동 dispatch 는 휴장일 검사를 하지 않는다 (워크플로 skip 로직만 적용, `force=1` 이면 그것도 무시).
성공 시 GitHub → Actions 탭에 새 run 이 나타난다.

## 비용

CF Workers 무료 tier: 100k 요청/일, cron trigger 무제한. 이 워커는 주당 ~6회 실행.
