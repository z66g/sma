# sma-cron (Cloudflare Worker)

GitHub Actions의 기본 cron 스케줄은 러너 큐 혼잡으로 지연이 흔해, 이 워커가
외부에서 `workflow_dispatch` API 를 호출해 정시 실행을 보장한다.

## 구조

```
cron-worker/
├── worker.js       # 핵심 로직 (scheduled + fetch)
├── wrangler.toml   # cron + vars
└── README.md
```

## 최초 배포

1. Node 18+ 설치 확인 후
   ```bash
   npm install -g wrangler
   ```
2. CF 계정 로그인
   ```bash
   wrangler login
   ```
3. 이 디렉토리로 이동
   ```bash
   cd cron-worker
   ```
4. GitHub PAT 을 secret 으로 등록 (대시보드 PAT 재사용 가능, `repo` + `workflow` scope 필요)
   ```bash
   wrangler secret put GH_PAT
   # (프롬프트에 PAT 붙여넣기)
   ```
5. (선택) 수동 트리거 보호용 키
   ```bash
   wrangler secret put MANUAL_KEY
   # 임의 난수 문자열
   ```
6. 배포
   ```bash
   wrangler deploy
   ```

배포 후 CF 대시보드 → Workers & Pages → `sma-cron` → Triggers 탭에
두 개 cron 표시 확인.

## 수동 테스트

```bash
curl -X POST "https://sma-cron.<account>.workers.dev/daily?key=<MANUAL_KEY>"
curl -X POST "https://sma-cron.<account>.workers.dev/weekly?key=<MANUAL_KEY>"
```

성공 시 GitHub → Actions 탭에 새 run 이 나타난다.

## GitHub-side cron 비활성화

CF 워커가 안정적으로 뜨는 것을 며칠 확인한 뒤, 중복 실행을 막기 위해
`.github/workflows/daily.yml` 과 `weekly.yml` 의 `schedule:` 블록을
주석 처리하는 것을 권장 (workflow_dispatch 는 남겨둠).

## 비용

CF Workers 무료 tier: 100k 요청/일, cron trigger 무제한.
이 워커는 주당 ~6회 실행이라 무료 범위 압도적으로 충분.
