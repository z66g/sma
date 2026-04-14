# Smart Money Flow Analyzer (sma_rebuild)

CLAUDE.md §13.3 `SmartMoneyAnalyzer` 클래스 구조 기반 Python CLI.
**스크린샷 업로드 없이** 티커만으로 L1–L4 + macro/news를 자동 수집해
Section 0~7 HTML 리포트 + Markdown 아카이브를 생성합니다.

## 설치

```bash
pip install -r requirements.txt
```

## 실행

```bash
# 무료 티어만 (Polygon 없음): OBV 4-way는 일봉 근사
python3 sma.py NVDA

# Polygon.io API 키 설정 시: OBV 4-way를 1분봉 기반으로 실제 계산
export POLYGON_API_KEY="여기에_키_붙여넣기"
python3 sma.py NVDA

# 영속화하려면 ~/.zshrc 혹은 ~/.bash_profile 에 추가:
echo 'export POLYGON_API_KEY="..."' >> ~/.zshrc && source ~/.zshrc
```

**주의**: `POLYGON_API_KEY`는 **절대 repo에 커밋하지 마세요**. 환경변수로만 읽습니다.
Polygon 무료 티어는 **분당 5회 호출 제한**이 있어 티커 한 번에 1회만 씁니다.

출력: `./output/`
- `{TICKER}_3Layer_Forensic_{DATE}.html` — Chart.js 내장 인터랙티브 리포트 (§10 섹션 0~7, MD 아카이브 블록 포함)
- `SmartMoney_{TICKER}_{DATE}.md` — 누적 히스토리 테이블 포함 MD

## 데이터 소스 (전부 무료)

| Layer | Source | 비고 |
|-------|--------|------|
| L1 Dark Pool | FINRA RegSHO `FNSQshvol` (ADF off-exchange) vs `CNMSshvol` (consolidated) | 세션별(Pre/Reg/AH) 분해는 유료 전용 — 근사치로 대체 |
| L1 OBV 4-way | **Polygon.io** 1분봉 (키 있을 때) / yfinance daily 근사 (없을 때) | 분봉 분위 기반: top20%=inst / mid60%=pro / bot20%=retail |
| L2 Short % | FINRA RegSHO `CNMSshvol` 일일 파일 | T+1 지연 |
| L2 CTB Fee | iborrowdesk.com JSON API | 공개 대차 데이터 |
| L3 Options Chain | yfinance `option_chain` | Greeks는 BS 근사 (yfinance 미제공) |
| L4 OHLCV | yfinance `history` | SMA/BB/S&R 로컬 계산 |
| Macro | FRED `fredgraph.csv`(키 없이) + yfinance DXY/WTI | |
| News/Filings | yfinance `news` + SEC EDGAR atom feed | |

수집 실패 섹션은 `PARTIAL` 플래그와 함께 HTML 상단 경고 배너에 표시.

## 아키텍처 (§13.3)

```
SmartMoneyAnalyzer
├── fetch_all_data()      # L1/L2/L3/L4 + macro + news 병렬 수집
├── run_analysis(data)    # §3~§9 분석 파이프라인 (패턴 탐지, 시나리오 확률)
└── generate_outputs()    # HTML + MD 렌더
```

## 제약 (무료 티어)

- 세션별 다크풀 volume은 유료(Cboe DataShop / Quiver) 전용 — ADF 합산치로 근사.
- OBV tick-level 분류 불가 → 일봉 volume 분위로 근사 (추세 방향 일치 시 유효).
- 옵션 Greeks는 Black-Scholes 근사(yfinance는 delta/gamma 미제공).
- FINRA 파일은 T+1 업데이트이므로 장중 실시간 분석 불가.

이들은 §2.2 data validation rule에 따라 `_partial: true`로 플래그되고
Section 상단에 "데이터 부재" 문구가 들어갑니다.

## GitHub Actions 자동화

### 스케줄
| 워크플로 | 시간 | 동작 |
|---|---|---|
| **daily.yml** | 01:30 UTC 화~토 (= KST 10:30 토~일) | `tickers.txt`의 모든 종목 raw 데이터 수집 → `reports/YYYY-MM-DD/` 커밋. 미 애프터장 마감(8PM ET) 이후이므로 전일 FINRA 데이터가 완성된 상태에서 돌아감. |
| **weekly.yml** | 02:00 UTC 토 (= KST 11:00 토) | 금요일 daily 완료 30분 뒤에 각 종목에 대해 Claude Sonnet 4.5로 주간 내러티브 생성 → `reports/tickers/{T}/narratives/{DATE}-weekly.html` 커밋. |

**각 워크플로는 Actions 탭에서 수동 트리거(Run workflow)** 도 가능.
weekly.yml은 `ticker`, `mode` 입력을 받을 수 있어 단일 종목만 재생성하거나 `adhoc`로 명명할 수 있음.

### 최초 1회 설정

1. **GitHub 레포에 Secrets 등록** (Settings → Secrets and variables → Actions → New repository secret)
   - `POLYGON_API_KEY` — Polygon.io (OBV 4-way 정밀 계산용)
   - `ANTHROPIC_API_KEY` — Anthropic (주간 자동 AI 내러티브용, 없으면 weekly.yml 실패)
   - Secrets는 Actions 런너에만 주입되며 커밋/로그에 노출되지 않음.
2. **Actions 권한 확인**
   - Settings → Actions → General → Workflow permissions
   - **Read and write permissions** 체크 (봇이 reports/ 커밋할 수 있도록)
3. **GitHub Pages 활성화** (선택)
   - Settings → Pages → Source: Deploy from a branch → **main** / **/ (root)** 또는 **/reports**
   - 활성화하면 `https://z66g.github.io/sma/reports/index.html`에서 브라우저로 모든 리포트 열람

### 감시 종목 수정 (웹 UI)

대시보드(`reports/index.html`) 상단의 **⚙ Manage Watchlist** 패널에서 직접 추가/삭제/즉시 실행이 가능합니다.

**최초 1회**: GitHub Personal Access Token 생성
1. https://github.com/settings/personal-access-tokens/new (Fine-grained PAT 추천)
2. **Repository access**: Only select repositories → `z66g/sma`
3. **Permissions**: 
   - Contents: **Read and write** (tickers.txt 편집)
   - Actions:  **Read and write** (워크플로우 수동 실행)
4. Generate → 복사 → 대시보드 PAT 입력창에 붙여넣고 저장

**보안**: PAT는 브라우저 `localStorage`에만 저장됩니다. repo에 커밋되지 않고, 다른 사이트로 전송되지 않습니다. 만료시 "PAT 변경" 버튼으로 교체.

### 감시 종목 수정 (파일 직접 편집)

`tickers.txt` 편집 후 커밋하면 다음 실행부터 반영됩니다:

```
NVDA
IONQ
TSLA
# 주석은 무시됨
SMR
```

### 즉시 실행 (스케줄 대기 없이)

GitHub → Actions 탭 → **Daily Smart Money Analysis** → Run workflow → main 선택 → 실행.
5~10분 뒤 `reports/{오늘날짜}/`에 결과 커밋됨.

### 로컬에서 수동 인덱스 재생성

```bash
python3 build_index.py    # reports/index.html 갱신
```

---

## 구조 참조

- `CLAUDE.md` §1–§12 스펙 전체 구현
- §13.3 클래스 골격: `SmartMoneyAnalyzer.fetch_*`/`analyze_*`/`detect_patterns`/`calculate_scenarios`/`build_section*`
- §11 Design System: hardcoded HEX, Chart.js 4.4, `-apple-system` 폰트
- §12 File Output Rules: 두 파일 모두 생성, MD 아카이브 블록(`id="md-archive-text"`) HTML 하단 삽입
