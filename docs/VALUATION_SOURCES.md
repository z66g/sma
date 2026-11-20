# Valuation Desk — 리서치용 네트워크 허용 목록

리서치 세션은 클라우드 환경의 네트워크 허용 목록(Allowed domains) 안에서만 원문을 열 수 있다. 목록은 **호스트 단위 정확 일치**라
`example.com` 과 `www.example.com` 은 별개다(예: `finbox.com` 은 열리지만 `www.finbox.com` 은 막힘) — 두 형태를 함께 넣는다.
설정 위치: 세션 제목 표시줄의 클라우드 환경 메뉴 → Edit → Network access → Allowed domains.
회사 IR 처럼 서브도메인이 제각각인 곳은 설정 화면에서 와일드카드(`*.tesla.com`)로 넣는 편이 낫다.

원문을 열지 못한 사실은 리서치에 `[검색 요약]`으로 표시되고, 핵심 가정이 거기에 걸리면 확신도가 LOW 로 내려간다.

## 허용 현황 (2026-11-19 기준)

§1~§4 의 호스트는 `sma` 환경에 추가됐다. 예외와 주의:
- 추가 실패(서버 거절): `www.reuters.com` · `www.cnbc.com` · `apnews.com` · `www.marketwatch.com` — 설정 화면에서 직접 추가 시도 필요.
- 허용됐지만 단순 호출에 403(사이트의 봇 차단): `www.tesla.com` · `ir.tesla.com` · `www.coinbase.com` · `www.hims.com` · `www.vertiv.com` ·
  `www.businesswire.com` · `www.globenewswire.com` · `www.fcc.gov` · `www.nhtsa.gov` · `www.miningweekly.com` · `www.datacenterdynamics.com` ·
  `www.energy-storage.news` · `www.fiercehealthcare.com` · `www.lme.com` · `www.theblock.co` — 다른 조회 도구나 다른 출처로 대체.
- 불안정: `www.eetimes.com` · `www.coindesk.com` · `www.xylem.com`.
- **원문은 `curl` 로 받는다** (브라우저형 User-Agent, 압축 응답은 `--compressed`). 세션 밖에서 도는 웹 조회 도구(WebFetch)는 이 환경의
  허용 목록을 따르지 않아 새로 허용한 호스트에서도 실패한다 (`quartr.com`, 회사 IR 등에서 확인).

## 1. 2026-11-19 파일럿에서 실제로 막혔던 곳 (우선)

| 호스트 | 용도 | 막혀서 못 한 것 |
|---|---|---|
| `www.fool.com`, `quartr.com`, `www.zacks.com` | 실적 콘퍼런스콜 녹취록·요약 | 4종목 모두 콜 원문 미확인 (CAPEX 전망, 신사업 일정 등) |
| `investor.marvell.com`, `ir.tesla.com` 등 회사 IR | 인베스터데이 자료, 주주서한, 장기 목표 | MRVL FY31 목표 마진, TSLA 1·2분기 주주서한 |
| `electrek.co`, `cleantechnica.com`, `www.notateslaapp.com`, `driveteslacanada.ca`, `evwire.com` | EV·로보택시·FSD 뉴스 | TSLA 로보택시 도시·차량 수, FSD 가격·부착률 |
| `www.satellitetoday.com`, `satnews.com`, `www.advanced-television.com` | 위성통신 산업 뉴스 | ASTS 경쟁(SpaceX 허가, 통신 3사 합작), Ligado 승인 상태 |
| `www.miningweekly.com`, `news.metal.com` | 구리·광산 뉴스 | FCX 구리 현물, 인도네시아 수출세 |
| `futurumgroup.com`, `www.trendforce.com`, `www.tikr.com` | 반도체 산업 분석 | MRVL 커스텀 실리콘 프로그램, 합병 평가 |
| `pages.stern.nyu.edu` | Damodaran 데이터(내재 ERP · 업종별 마진·자본효율) | 엔진의 위험프리미엄·섹터 기본값 검증 |

## 2. 워치리스트 회사 사이트 (1차 자료 — Yahoo 프로필의 공식 도메인)

`ast-science.com` · `www.coinbase.com` · `fcx.com` / `www.fcx.com` · `fluenceenergy.com` · `www.hims.com` · `www.ice.com` · `www.ionq.com` ·
`iren.com` · `www.kbr.com` / `investors.kbr.com` · `www.keelinfra.com` · `www.centrusenergy.com` · `www.lumentum.com` · `www.medtronic.com` ·
`www.marvell.com` / `investor.marvell.com` · `www.tesla.com` / `ir.tesla.com` · `www.vertiv.com` · `www.xylem.com`

IR 서브도메인(`investor.` / `investors.` / `ir.`)은 회사마다 달라 위 3곳 외에는 확인하지 못했다 → 도메인 와일드카드 권장.

## 3. 공통 뉴스·보도자료

| 구분 | 호스트 |
|---|---|
| 보도자료 원문 | `www.businesswire.com` · `www.prnewswire.com` · `www.globenewswire.com` |
| 종합 뉴스 | `www.reuters.com` · `www.cnbc.com` · `apnews.com` · `www.marketwatch.com` |
| 규제·통계 | `www.fcc.gov` · `www.nhtsa.gov` · `www.fda.gov` · `www.eia.gov` · `www.nrc.gov` · `www.usgs.gov` |

Bloomberg · WSJ · FT 는 유료 장벽이라 허용해도 본문을 못 읽는 경우가 많다.

## 4. 업종별 전문 매체 (워치리스트 기준 후보)

| 업종 | 종목 | 호스트 |
|---|---|---|
| 반도체·광통신·데이터센터 | MRVL · LITE · VRT | `www.eetimes.com` · `www.digitimes.com` · `www.lightreading.com` · `www.datacenterdynamics.com` |
| EV·자율주행 | TSLA | `insideevs.com` · `www.teslarati.com` · `cnevpost.com` |
| 위성·우주 | ASTS | `spacenews.com` · `payloadspace.com` |
| 광업·원자재 | FCX · LEU | `www.mining.com` · `www.kitco.com` · `icsg.org` · `www.lme.com` · `www.world-nuclear-news.org` |
| 에너지저장·전력 | FLNC · VRT | `www.energy-storage.news` · `www.utilitydive.com` |
| 암호화폐·채굴 | COIN · IREN · KEEL | `www.coindesk.com` · `www.theblock.co` |
| 헬스케어 | HIMS · MDT | `www.fiercehealthcare.com` · `www.medtechdive.com` · `www.statnews.com` |
| 양자컴퓨팅 | IONQ | `thequantuminsider.com` |
| 엔지니어링·물 인프라 | KBR · XYL | `www.enr.com` |

§4 는 호스트명을 이 환경에서 직접 열어 확인하지 못한 후보 목록이다(차단 상태라 확인 불가). 추가 후 열리지 않으면 `www.` 유무를 바꿔 본다.
