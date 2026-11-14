# Smart Money Flow Analyzer v3

티커만으로 다크풀(L1) · 공매도/대차(L2) · 옵션(L3) · 차트(L4) · 거시 유동성 · 뉴스/공시를 자동 수집해
**SECTION 0~7 인터랙티브 HTML 리포트 + 누적 히스토리 Markdown + 사이트용 JSON(v3)** 을 생성합니다.
분석 명세는 `CLAUDE.md`, 엔진→사이트 데이터 계약은 `docs/SCHEMA_V3.md`.

## 설치

```bash
pip install -r requirements.txt
```

## 실행

```bash
python3 sma.py TSLA                      # 최근 완료된 미국 정규장 세션 기준
python3 sma.py TSLA --date 2026-11-13    # 특정 세션
python3 sma.py ^SPX                      # 지수 (L3/L4 중심)
python3 sma.py --print-session-date      # 최근 완료 세션 날짜 (워크플로용)
```

출력: `./output/{SESSION_DATE}/` (워크플로에서는 `SMA_OUT_DIR=reports/{DATE}`)
- `{T}_3Layer_Forensic_{DATE}.html` — 독립 실행형 리포트 (Chart.js 4.4 + annotation)
- `SmartMoney_{T}_{DATE}.md` — 핵심 수치 + **전체 누적 히스토리 테이블** (과거 리포트 JSON 에서 재구성)
- `{T}_{DATE}.json` — 사이트/내러티브용 v3 스키마 (레거시 키 유지)

종료 코드: `0` 성공 · `2` 유효하지 않은 티커 · `1` 기타 실패. 모든 수집 실패는 stderr 와 리포트의
**데이터 품질 패널**(등급 A–F, 레이어별 커버리지·누락 필드·출처)에 기록됩니다 — 조용한 실패 없음.

## 날짜 규칙 (중요)

리포트 날짜 = **데이터가 속한 미국 정규장 세션 날짜**(ET, NYSE 거래일). 실행일(KST/UTC)이 아닙니다.
`smartmoney/calendar.py` 가 NYSE 휴장일·조기폐장을 규칙 기반으로 계산하므로 연도 제한이 없습니다.
v2(2026-11 이전) 리포트는 파일명이 KST 실행일이라 실제 세션은 하루 전 거래일입니다 —
`build_index.py`·엔진 히스토리 로더가 자동 보정합니다.

## 데이터 소스 (전부 무료 · 2026-11 실측)

| Layer | 1차 소스 | 폴백 / 비고 |
|-------|---------|-----------|
| L1 다크풀 비중 | FINRA RegSHO `CNMSshvol` (장외 체결) ÷ 총거래량 | 20일 평균 대비 배수로 이상 탐지 |
| L1 Net OBV | 1분봉 CLV 가중 순체결 (Polygon 무료 키 → Yahoo 1m) | 일봉 CLV |
| L1 체결 주체 구성 | FINRA OTC Transparency API (ATS vs 홀세일러, 주간) | 2~4주 지연 공시 |
| L2 Short % | FINRA RegSHO (장외 보고분) + 총거래량 환산 | |
| L2 CTB / 가용잔고 | **IBKR shortstock FTP** (`ftp2.interactivebrokers.com`) | iborrowdesk (2026-07 이후 응답 없음) |
| L2 공매도 잔고 | Nasdaq short-interest API | Polygon short-interest |
| L3 옵션 | CBOE delayed quotes (OI·IV·Greeks·IV30) | Yahoo option_chain (BS Greeks) |
| L4 차트 | Yahoo 일봉 2년 (미완성 당일 봉 자동 제거) | |
| 거시 | FRED (DFF·RRP·SOFR·IORB·WALCL·TGA·HY OAS·10Y) + Yahoo (DXY·WTI·VIX) | |
| 뉴스/공시 | Yahoo news · SEC `data.sec.gov` submissions + Form 4 XML | |

같은 실행 안의 중복 다운로드(FINRA 일일 파일 등)는 `SMA_CACHE_DIR`(기본 `/tmp/sma_cache`)에 캐시됩니다.

## 아키텍처

```
sma.py                      CLI
smartmoney/
  calendar.py   NYSE 거래일·세션 날짜     net.py       HTTP 재시도·디스크 캐시
  sources.py    소스별 수집기             history.py   과거 리포트·state/ 누적
  analytics.py  L1–L4·패턴·거시·확률·Phase  quality.py   데이터 품질 등급
  events.py     30일 이벤트 캘린더         commentary.py 한국어 해석·교차검증·결론
  engine.py     수집→분석 오케스트레이션    render_html.py / render_data.py  출력
state/borrow/{T}.json   IBKR 대차 스냅샷 누적 (FTP 는 히스토리 미제공 → CTB Δ% 계산용)
state/options/{T}.json  OI·Max Pain·플립 누적 (OI 추세 · 핀닝 사후검증)
```

확률 엔진: 레이어별 `score∈[-1,1] × confidence × weight(L1 .35 · L2 .20 · L3 .30 · L4 .15)` →
연속 확률 매핑 + 레이어 분산(불일치)만큼 중립 가중 + 거시(±5~10%p)·패턴 보정 → 80% 상한.

## 알려진 한계

- **기관/프로/리테일 OBV 4-Way · IAR · 다크풀 전용 VWAP** 은 체결(Trade-level) 데이터가 필요합니다.
  현재 Polygon 키는 무료 등급(분봉만)이라 `v3/trades` 가 403 → 해당 값은 **N/A 로 정직하게 표기**합니다
  (추정치로 채우지 않음). Polygon/Massive 의 trades 포함 플랜 키를 `POLYGON_API_KEY` 에 넣으면 확장 가능.
- 다크풀 세션별(Pre/Regular/AH) 분해는 유료 데이터 전용 — 일별 장외 볼륨 이상 탐지로 대체.
- FINRA Short % 는 장외(TRF) 체결 기준이며 포지션 잔고가 아닙니다.
- `smartmoney/events.py` 의 FOMC 일정은 공식 발표분만 기재 — 다음 해 일정 공개 시 추가 필요.

## GitHub Actions 자동화

### 트리거 & 스케줄
GitHub 네이티브 cron 은 지연이 심해 쓰지 않는다. 외부 스케줄러(**cron-job.org** 또는 `cron-worker/` 의
Cloudflare Worker)가 `workflow_dispatch` API 를 호출한다.

| 워크플로 | 트리거 시각 | 동작 |
|---|---|---|
| **daily-analysis.yml** | 화~토 02:00 UTC (= KST 11:00) | 직전 미국 정규장 세션 분석 → `reports/{SESSION_DATE}/` 커밋 → 텔레그램 |
| **weekly-narrative.yml** | 토 02:30 UTC (= KST 11:30) | 종목별 AI 내러티브(`narrative.py`, 기본 `claude-opus-5-5`) → `reports/tickers/{T}/narratives/{SESSION_DATE}-weekly.html` |

두 워크플로는 같은 concurrency 그룹(`sma-repo-write`)을 써서 저장소 쓰기가 겹치지 않는다.
Actions 탭 → Run workflow 로 수동 실행 가능 (`ticker`, `force` 입력; weekly 는 `mode` 도).

### 세션 날짜 규칙
- 폴더/파일명 날짜 = **데이터가 속한 미국 정규장 세션 날짜**(ET, 거래일만). 실행일(KST) 아님.
  워크플로는 `DATE=$(python3 sma.py --print-session-date)` 로 정하고 `SMA_SESSION_DATE` 로 엔진에 넘긴다.
- 그 세션의 v3 리포트가 워치리스트 전 종목에 이미 있으면 분석·커밋·알림을 생략 → 휴장일/중복 dispatch 에도
  중복 리포트가 생기지 않는다. 다시 돌리려면 `force=true`. 단일 종목 dispatch 는 항상 실행.
- 레거시(v2, `schema` 키 없음) 리포트는 파일명이 KST 실행일이고 데이터는 직전 거래일 세션이다.
  `build_index.py` 가 `legacy_session_for()` 로 세션 날짜에 매핑하고, (종목, 세션) 중복은 최신 생성본만 남긴다.
  새 세션 폴더에 같은 이름의 레거시 파일이 있으면 덮어쓰기 전에 `reports/{DATE}/_v2/` 로 옮겨 보존한다
  (`tools/session_files.py preserve-legacy`).

### 산출물 / 상태
- `reports/{SESSION_DATE}/` — 종목별 HTML·MD·JSON(v3 스키마: `docs/SCHEMA_V3.md`)
- `reports/data/{T}.json` — 종목별 시계열 (세션 날짜 기준, minified) · `reports/tickers/{T}/` — 종목 페이지
- `state/` — 엔진 누적 상태(예: 일별 대차 스냅샷 `state/borrow/{T}.json`). 커밋 단계에서 `reports/` 와 함께 add.
- 출력이 결정적(실행 시각 미기록)이라 rebase 충돌이 적고, 충돌 시 원격 위로 rebase 후 인덱스를 재생성한다.

### 데이터 품질 요약 & 알림
- 분석 후 `tools/quality_summary.py reports/$DATE` 가 종목별 품질 등급/점수·누락 레이어/필드·경고 수를
  **Actions Step Summary** 표로 남긴다.
- 텔레그램: 실제 HTML 이 생성된 종목만 링크 + 한 줄 품질 경고(예: `⚠ 데이터 경고: CTB 누락 17/17`) +
  무효/실패 종목. 생성된 리포트가 0개면 잡이 실패하고 실패 알림이 간다.
- exit code 규약(`sma.py`): 0 성공, 2 무효 티커(skip 목록), 그 외 = 실패.
- weekly: 종목별 실패를 모아 Step Summary·텔레그램에 표시, 전 종목 실패 시 잡 실패.

### 최초 1회 설정

1. **Secrets** (Settings → Secrets and variables → Actions)
   - `POLYGON_API_KEY` — Polygon.io
   - `ANTHROPIC_API_KEY` — 주간 AI 내러티브 (없으면 weekly 실패)
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — 알림 (선택)
   - (선택) Variables 에 `SMA_CLAUDE_MODEL` 로 내러티브 모델 override
2. **Workflow permissions**: Settings → Actions → General → **Read and write permissions**
3. **스케줄러**: cron-job.org 에서 위 시각에 `POST https://api.github.com/repos/z66g/sma/actions/workflows/{daily-analysis.yml|weekly-narrative.yml}/dispatches`
   (body `{"ref":"main"}`, Fine-grained PAT — `z66g/sma` 전용, Actions: Read and write) 를 호출하거나,
   `cron-worker/README.md` 대로 Worker 를 배포한다 (Worker 는 NYSE 휴장일 daily 를 자체 skip).
4. **GitHub Pages**: Settings → Pages → main / (root) — `https://analyzer.zbbg.kr/reports/`

### 감시 종목 수정 (웹 UI)

대시보드(`reports/index.html`) 상단 패널에서 추가/삭제/즉시 실행이 가능하다.

**최초 1회**: Fine-grained Personal Access Token 생성 (Classic PAT 비권장)
1. https://github.com/settings/personal-access-tokens/new
2. **Repository access**: Only select repositories → `z66g/sma`
3. **Permissions**: Contents **Read and write** (tickers.txt 편집) · Actions **Read and write** (워크플로 실행)
4. 만료일 설정 → Generate → 대시보드 PAT 입력창에 저장

**보안**: PAT 는 브라우저 `localStorage` 에만 저장된다. repo 에 커밋되지 않고 GitHub API 외 다른 곳으로 전송되지 않는다.

### 감시 종목 수정 (파일 직접 편집)

`tickers.txt` 편집 후 커밋하면 다음 실행부터 반영 (한 줄에 티커 하나, `#` 주석).

### 로컬에서 수동 인덱스 재생성

```bash
python3 build_index.py        # reports/index.html, app.js, data/*.json, tickers/*/index.html
python3 tools/test_sanitizer.py   # 내러티브 HTML sanitizer 회귀 테스트
```

---
