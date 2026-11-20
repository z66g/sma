# Report JSON Schema v3 (engine → site contract)

`sma.py` 가 종목·세션마다 `reports/{SESSION_DATE}/{T}_{SESSION_DATE}.json` 을 쓴다.
v2(레거시) 키는 **모두 유지**되고, v3 는 키를 추가만 한다. 소비자(build_index.py,
narrative.py, narrative.js)는 새 키가 없을 수도 있다고 가정하고 `null`/누락을
"N/A" 로 표시해야 한다 (절대 0 으로 표시하지 말 것).

## 날짜 의미 (v3 변경점)
- `date` = **데이터가 속한 미국 정규장 세션 날짜** (ET 기준, 거래일만). 실행일(UTC/KST) 아님.
- `python3 sma.py --print-session-date` → 최근 완료된 세션 날짜(YYYY-MM-DD)를 stdout 에 출력.
  ET 16:30 이전 실행이면 직전 거래일. 주말·NYSE 휴장일 자동 제외.
- 같은 세션을 다시 돌리면 같은 파일을 덮어쓴다 (휴장일 중복 행 생성 금지).

## 최상위
| key | type | 비고 |
|---|---|---|
| `schema` | int | `3` (레거시 파일엔 없음 → v2 로 간주) |
| `ticker`, `date`, `price`, `is_index` | | 기존 |
| `generated_at` | str | UTC ISO8601 |
| `prev_close`, `change_pct` | float | |
| `quality` | obj | `{score:0-100, grade:"A".."F", layers:{l1..l4,macro,news:{status:"OK"/"PARTIAL"/"MISSING", missing:[str], sources:[str]}}}` |
| `warnings` | [str] | 기존. 이제 실제 수집 실패가 모두 기록됨 |
| `patterns`, `macro_env` | | 기존 |
| `scenarios` | obj | 기존 + `contributions:{L1,L2,L3,L4,macro,patterns}` (점수 기여도) |
| `phase` | obj | 기존 키 유지. 날짜는 모두 거래일 |
| `levels` | [obj] | `{type, price, dist_pct, significance}` 핵심 가격 레벨 지도 |
| `headline` | str | 한 줄 핵심 결론 (한국어) |

## l1 (Dark Pool)
기존: `scenario, signal, confidence, dp_pct, delta_total, divergence, cumulative_daily, obv_source, window_days, inst_abs_volume, lit_abs_volume`
추가: `dp_avg20, dp_ratio` (당일/20일평균), `dp_class`, `dp_series:[{date,dp_pct,off_exchange_volume,market_volume}]`,
`delta_inst, delta_pro, delta_retail, iar` (trade-level 소스 있을 때만, 없으면 null),
`dp_vwap, dp_vwap_dist_pct` (trade-level 소스 있을 때만),
`ats:{week_start, ats_shares, otc_non_ats_shares, ats_share_pct, ats_share_prev_pct, ats_trades_avg_size}` (FINRA OTC 주간, 2~4주 지연),
`flow:{days, delta_inst, delta_pro, delta_retail, delta_total, iar, iar_class, dp_vwap, dp_vwap_dist_pct, sessions:[{session, volume, dark, dark_pct, avg20_dark, ratio, flag}], blocks, dark_blocks, last30_block_share, bjzz_imbalance, cohort_vol_share}` (Alpaca 체결 단위, 키 있을 때만)

## l2 (Short / Borrow)
기존: `scenario, signal, case, short_pct_latest, short_avg_14d, short_slope, slope_dir, anomaly_z, ctb_fee, ctb_delta_pct, shares_available`
추가: `short_shares` (실물 재계산), `short_series:[{date,short_pct,short_vol,total_vol}]`,
`avail_delta_pct`, `borrow_source` (`ibkr_ftp`/`iborrowdesk`/null), `borrow_series:[{date,fee,available}]`,
`short_interest:{settlement_date, shares, prev_shares, change_pct, days_to_cover, pct_float}`

## l3 (Options)
기존: `scenario, signal, expiry, dte, max_pain, max_pain_dist_pct, pc_oi, pc_vol, skew, net_gex, flip_zone`
추가: `call_wall, put_wall, atm_iv, iv30, skew_25d, term_structure` (`CONTANGO`/`BACKWARDATION`/`FLAT`),
`expected_move, expected_move_pct` (ATM 스트래들), `pc_vol_adj`, `oi_total`, `binary_event` (bool),
`monthly_expiry, monthly_max_pain`, `gex_profile_source`

## l4 (Chart)
기존: `scenario, signal, ma_alignment, sma20, sma50, sma200, bb_width_pct, immediate_resistance, immediate_support`
추가: `ema9, ema21, rsi14, atr14, atr_pct, bb_upper, bb_lower, bb_position, bb_width_pctile` (1y 백분위),
`key_resistance, key_support, vol_ratio30, high_52w, low_52w`

## valuation (펀더멘털 적정가치 · 선택)
Valuation Desk(별도 기능)가 발행한 `reports/valuation/{T}.json` 을 엔진이 **읽기만** 해서 붙인다 (세션 종가로 `upside_pct`·`verdict` 만 재산정).
리서치가 발행되지 않은 종목·과거 세션 재실행(7일 초과)·`SMA_VALUATION=0` 이면 키 자체가 없다. 소비자는 누락을 N/A 로 표시한다.
구조: `docs/VALUATION.md` · 리서치 지침: `docs/VALUATION_RESEARCH.md`.

| key | type | 비고 |
|---|---|---|
| `method` | str | `RESEARCH` (시나리오 확률가중) / `MECHANICAL` (재무제표만 — 기본 설정에서는 SMA 에 전달되지 않음) |
| `status` | str | `OK` / `NO_MODEL` |
| `published`, `fundamentals_asof`, `priced_at` | str | 발행일 / 재무 스냅샷 수집일 / 괴리율을 매긴 세션 |
| `price`, `fair_value`, `fair_low`, `fair_high` | float | 주당. RESEARCH 는 범위 = 약세~강세 시나리오 가치 |
| `upside_pct`, `verdict`, `verdict_ko`, `mos_band_pct` | | `DEEP_UNDERVALUED` / `UNDERVALUED` / `FAIR` / `OVERVALUED` / `DEEP_OVERVALUED`, 밴드 10/15/25/35 |
| `research` | obj | `{as_of, analyst, thesis, confidence, stale, option_value, n_sources, scenarios:{bear,base,bull:{prob, value, cagr5, target_margin}}}` |
| `age_days`, `stale` | int, bool | 리서치 경과일 · 100일 초과 여부 |
| `mechanical_fair` | float\|null | 기계적 기준선 (교차검증용) |
| `archetype` | obj | `{code, label, traits:[str]}` |
| `uncertainty` | obj | `{level:"LOW"/"MEDIUM"/"HIGH"/"VERY_HIGH", label, score:0-100}` |
| `n_models`, `models` | int, [obj] | 기계적 모델 `{id, name, low, mid, high, weight}` |
| `wacc`, `ke` | float | 소수 (0.09 = 9%) |
| `implied_cagr_5y`, `base_cagr_5y` | float\|null | 역DCF 내재 5년 매출 CAGR / 기계적 가정 |
| `mc` | obj\|null | 몬테카를로 `{p10, p50, p90, prob_above_price}` (기계적 가정 기준) |
| `analyst_target` | float\|null | 참고용 (입력으로 쓰지 않음) |
| `summary` | str | 한 문장 요약 (한국어) |

## 상태 저장소 (`state/`)
- `state/flow/{T}.json` — 일별 체결 단위 집계 (Alpaca)
- `state/borrow/{T}.json` — 일별 대차 스냅샷 `[{date, fee, available}]` (IBKR FTP 는 히스토리가 없어 직접 누적)
- `state/fundamentals/{T}.json` — 재무 스냅샷 (적정가치 입력) · `state/fundamentals/_peers.json` — peer 프로필 공유 캐시
- `state/valuation/research/{T}.json` — 발행된 리서치 원본 (시나리오 가정·출처; Valuation Desk 전용)
- 워크플로 커밋 단계는 `reports/` 와 함께 `state/` 도 add 해야 한다.
