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
python3 sma.py NVDA
python3 sma.py TSLA --date 2026-04-14
```

출력: `./output/`
- `{TICKER}_3Layer_Forensic_{DATE}.html` — Chart.js 내장 인터랙티브 리포트 (§10 섹션 0~7, MD 아카이브 블록 포함)
- `SmartMoney_{TICKER}_{DATE}.md` — 누적 히스토리 테이블 포함 MD

## 데이터 소스 (전부 무료)

| Layer | Source | 비고 |
|-------|--------|------|
| L1 Dark Pool | FINRA RegSHO `FNSQshvol` (ADF off-exchange) vs `CNMSshvol` (consolidated) | 세션별(Pre/Reg/AH) 분해는 유료 전용 — 근사치로 대체 |
| L1 OBV 4-way | yfinance daily bars 기반 근사 | tick 데이터 부재로 거래규모 기반 분류는 PARTIAL |
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

## 구조 참조

- `CLAUDE.md` §1–§12 스펙 전체 구현
- §13.3 클래스 골격: `SmartMoneyAnalyzer.fetch_*`/`analyze_*`/`detect_patterns`/`calculate_scenarios`/`build_section*`
- §11 Design System: hardcoded HEX, Chart.js 4.4, `-apple-system` 폰트
- §12 File Output Rules: 두 파일 모두 생성, MD 아카이브 블록(`id="md-archive-text"`) HTML 하단 삽입
