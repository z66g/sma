---
name: valuation-desk
description: 한 종목의 펀더멘털 적정가를 리서치해 발행하고 채팅으로 보고한다 (Valuation Desk). 소유자가 "{티커} 분석해줘", "{티커} 적정가", "적정가 분석 {티커}" 처럼 데이터 없이 종목만 말할 때 쓴다. 다크풀·공매도·옵션 데이터(L1~L4)를 함께 준 수급 3-Layer 분석 요청에는 쓰지 않는다.
---

# "{티커} 분석해줘" — 적정가 산출 (대화 세션)

관심종목에 넣기 전에 그 종목의 적정가부터 보는 용도다. 관심종목이 아니면 **검토 종목**(`review.txt`)에 넣어 대시보드에 표시하고,
소유자가 대시보드에서 편입할지 정한다. 저장소 소유자가 이 절차의 `main` 직접 커밋·푸시를 허용했다.

## 요청 구분
- 종목만 말했다("TSLA 분석해줘") → 이 절차.
- 다크풀·공매도·옵션·차트 데이터(스크린샷·표)를 함께 줬다 → 수급 3-Layer 분석(사용자 지침). 이 절차가 아니다.
- 애매하면 "적정가 산출로 진행합니다"라고 한 줄 밝히고 이 절차를 따른다.

## 절차
규칙은 `docs/VALUATION_ROUTINE.md` §0·§3·§4 와 `docs/VALUATION_AUDIT.md` 를 그대로 따른다(이 문서는 차이점만 적는다). 처음에 둘 다 읽는다.

1. **준비** — 작업 트리에 커밋하지 않은 변경이 있으면 멈추고 소유자에게 묻는다. 없으면 §0 의 명령(`main` 최신화, 의존성, 테스트)을 그대로.
   작업 폴더 `DESK` 는 세션 스크래치 디렉터리 아래에 만든다.
2. **대상 확인** — 티커 형식(영문 대문자 1~10자)과 상장 여부(`python3 valuation.py prepare {T} --out DESK/{T}` 가 재무를 받는지)를 확인한다.
   - 이미 발행된 리서치가 있고 7일 이내이며 그 뒤 실적 발표가 없으면, **조사하지 않고** 발행된 결과를 아래 형식으로 보고한 뒤
     "다시 조사할까요?"라고 묻는다(비용 절약). 그 밖에는 바로 진행한다.
   - 관심종목(`tickers.txt`)에 있으면 갱신(UPDATE), 없으면 신규(리서치가 있으면 UPDATE).
3. **조사** — §3 의 2단계대로 분석가 서브에이전트 1개(general-purpose, **모델 opus**)에 맡긴다. `analyst` 표기는 "Claude Valuation Desk (대화 세션)".
4. **검증** — §3 의 3단계를 이 세션이 직접 한다. dry-run 이 `[감사 필요]` 를 출력하면 `docs/VALUATION_AUDIT.md` 대로 독립 감사(opus)를 돌리고,
   지적을 반영시킨 뒤(재검토 최대 2회) `audit` 기록을 넣는다. 통과하지 못하면 발행하지 않고 이유를 보고한다.
5. **발행·반영** —
   ```bash
   python3 valuation.py publish {T} --research DESK/{T}/{T}_research.json
   python3 valuation.py review add {T}          # 관심종목이 아닐 때만 (관심종목이면 넣지 않는다)
   python3 tools/test_valuation.py
   python3 build_index.py
   git status --short
   git add review.txt reports/valuation state/valuation state/fundamentals reports/tickers reports/index.html reports/app.js reports/data
   git commit -m "valuation: {T} 적정가 분석 (대화 세션)"
   git push origin HEAD:main
   ```
   푸시 거부 시 §4 의 재시도 규칙. **화~토 10:50~11:40 KST**(일일 분석)와 **토 08:30~10:40 KST**(주간 실행)에는 푸시하지 말고 그 뒤에 한다.
   `review.txt` 커밋이 수시 실행 워크플로를 깨우지만, 방금 발행했으므로 대기 종목이 없어 아무것도 호출되지 않는다.
6. **보고** — 아래 형식으로 채팅에 쓴다.

## 채팅 보고 형식 (한국어)
- 제목 줄: `{T} 적정가 {fair} — {판정} (현재가 {price} 대비 {±%}, 불확실성 {등급}, 확신도 {conf})`
- 시나리오 표: 시나리오 · 확률 · 주당 가치 · 실현 조건 한 줄 · 핵심 가정(성장·성숙 마진·도달 연차)
- 범위(약세~강세), 할인율 ±1%p 범위, 판정이 그 범위에서 바뀌는지
- 값을 가장 크게 움직이는 가정 2~3개와 그 민감도 (분석가 보고의 민감도 표에서)
- "현재가가 맞으려면" 한 줄 (리서치 `valuation_notes` 에서)
- 주요 위험 3개, 확인하지 못한 것
- 독립 감사를 했으면 판정과 반영한 지적 요약
- 상세 페이지 `https://analyzer.zbbg.kr/reports/tickers/{T}/valuation.html`, 대시보드 '검토 종목'에서 편입·재분석·삭제 가능하다는 한 줄
- 이번 분석에 든 서브에이전트 수(분석가·감사)

주가나 애널리스트 목표가에 맞춰 가정을 바꾸지 않는다. 투자 권유 문구를 쓰지 않는다 — 수치와 근거만.
