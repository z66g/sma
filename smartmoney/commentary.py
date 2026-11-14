"""규칙 기반 해석 문장 생성 (한국어 · 데이터 중심 · 내부 규칙명 노출 금지)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# ─── 포맷 ────────────────────────────────────────────────────────────────────
def n(x: Optional[float], d: int = 2) -> str:
    return "N/A" if x is None else f"{x:,.{d}f}"


def usd(x: Optional[float], d: int = 2) -> str:
    return "N/A" if x is None else f"${x:,.{d}f}"


def sh(x: Optional[float]) -> str:
    if x is None:
        return "N/A"
    a = abs(x)
    s = "-" if x < 0 else ""
    if a >= 1e9:
        return f"{s}{a/1e9:.2f}B"
    if a >= 1e6:
        return f"{s}{a/1e6:.2f}M"
    if a >= 1e3:
        return f"{s}{a/1e3:.1f}K"
    return f"{s}{a:.0f}"


def ssh(x: Optional[float]) -> str:
    return "N/A" if x is None else ("+" if x >= 0 else "") + sh(x)


def pc(x: Optional[float], d: int = 1, sign: bool = True) -> str:
    if x is None:
        return "N/A"
    return f"{x:+.{d}f}%" if sign else f"{x:.{d}f}%"


def money_big(x: Optional[float]) -> str:
    if x is None:
        return "N/A"
    a, s = abs(x), ("-" if x < 0 else "+")
    if a >= 1e9:
        return f"{s}${a/1e9:.2f}B"
    if a >= 1e6:
        return f"{s}${a/1e6:.1f}M"
    return f"{s}${a/1e3:.0f}K"


KO_SCEN = {
    "ACCUMULATION": "매집", "DISTRIBUTION": "분배", "NEUTRAL": "중립", "AMBIGUOUS": "혼재",
    "SHORT_SQUEEZE_RISK": "숏스퀴즈 위험", "DIRECTIONAL_SHORT": "방향성 공매도", "MM_HEDGE": "MM 델타헤지",
    "SYNTHETIC_HEDGE": "합성 헤지", "SHORT_COVERING": "숏커버링",
    "PINNING": "핀닝", "GAMMA_SQUEEZE": "감마 스퀴즈", "VOLATILITY_EXPANSION": "변동성 확장", "HEDGING": "헤지 수요",
    "UPTREND": "상승 추세", "DOWNTREND": "하락 추세", "RANGE_BOUND": "박스권",
    "BREAKOUT_PENDING": "상방 돌파 대기", "BREAKDOWN_PENDING": "하방 이탈 대기", "NO_DATA": "데이터 없음", "N/A": "해당 없음",
}
KO_CASE = {"CASE_1_MM_DELTA_HEDGE": "① MM 델타헤지", "CASE_2_DIRECTIONAL_SHORT": "② 투기적 방향성 숏",
           "CASE_3_SYNTHETIC_HEDGE": "③ 합성 헤지", "SHORT_COVERING": "숏커버링", "MIXED": "혼재(판별 보류)"}
KO_PAT = {"THETA_BURN": "세타 번 (L자 횡보)", "THETA_BURN_BREAKOUT": "세타 번 이탈 개시", "FINAL_ABSORPTION": "최종 흡수",
          "LOW_CTB_PARADOX": "저CTB 역설", "SHORT_SQUEEZE_RISK": "숏스퀴즈 위험", "GAMMA_SQUEEZE_SETUP": "감마 스퀴즈 셋업",
          "OPTION_PINNING": "옵션 핀닝", "BINARY_EVENT_PRICED": "이진 이벤트 프라이싱", "DARK_POOL_VOLUME_SPIKE": "다크풀 볼륨 급증"}
KO_ENV = {"FAVORABLE": "우호", "NEUTRAL": "중립", "RESTRICTED": "제한"}


def ko(s: Optional[str]) -> str:
    return KO_SCEN.get(s or "", s or "N/A")


# ─── 섹션 해석 ───────────────────────────────────────────────────────────────
def l1_text(l1: Dict, l2: Dict, l3: Dict) -> Tuple[List[str], str]:
    if l1.get("scenario") == "NO_DATA":
        return ["FINRA off-exchange 데이터를 확보하지 못해 다크풀 레이어 판정을 보류한다."], "교차검증 불가."
    out = []
    dp, avg, ratio = l1.get("dp_pct"), l1.get("dp_avg20"), l1.get("dp_ratio")
    if dp is not None:
        lvl = ("기관 스텔스 모드(50%+)" if dp > 50 else "장외 활동 고조(40–50%)" if dp > 40 else
               "정상 기관 범위(30–40%)" if dp > 30 else "리테일 우위(<30%)")
        rel = ("평균 상회 — 장외 흡수 강화" if (ratio or 1) > 1.1 else "평균 하회 — 장외 흡수 둔화" if (ratio or 1) < 0.9
               else "평균 수준")
        out.append(f"다크풀(Off-exchange) 비중 <b>{pc(dp, sign=False)}</b>로 {lvl}. 20일 평균 {pc(avg, sign=False)} 대비 "
                   f"<b>{n(ratio)}배</b>, {rel}. 장외 거래량은 20일 평균의 {n(l1.get('off_vol_ratio'))}배.")
    st = l1.get("obv_strength")
    basis = "체결 단위(SIP tick rule) 순체결" if l1.get("flow") else "CLV 가중 순체결"
    out.append(f"5거래일 {basis}(Net OBV) <b>{ssh(l1.get('delta_total'))}주</b>, ADV 대비 <b>{pc((st or 0)*100)}</b> — "
               + ("유의미한 순매수 축적." if (st or 0) > 0.08 else "유의미한 순매도 누적." if (st or 0) < -0.08
                  else "방향성 미약(노이즈 범위)."))
    div = l1.get("divergence")
    ps, os_ = l1.get("price_slope_pct"), l1.get("obv_slope_pct")
    if div == "BEARISH_DIVERGENCE":
        out.append(f"가격 기울기 일 {pc(ps,2)} 상승 vs 순체결 기울기 일 {pc(os_)}(ADV) 하락 → <b>약세 다이버전스</b>. "
                   "상승 구간에서 매도 체결이 우위 — 강세 속 물량 출회(분배) 패턴.")
    elif div == "BULLISH_DIVERGENCE":
        out.append(f"가격 기울기 일 {pc(ps,2)} 하락 vs 순체결 기울기 일 {pc(os_)}(ADV) 상승 → <b>강세 다이버전스</b>. "
                   "하락 구간에서 매수 체결이 우위 — 공포 속 흡수(매집) 패턴.")
    elif div == "CONVERGENCE":
        out.append("가격과 순체결이 같은 방향 — 현 추세를 수급이 확인(Convergence).")
    else:
        out.append("가격·순체결 모두 유의미한 기울기 없음 — 방향성 대기.")
    v5, vd = l1.get("vwap5"), l1.get("vwap_dist_pct")
    if v5:
        out.append(f"VWAP 앵커: 5일 VWAP {usd(v5)}, 종가는 그 대비 <b>{pc(vd,2)}</b> — "
                   + ("최근 매수자 평균단가 위(수익 구간)에서 거래." if (vd or 0) > 0 else "최근 매수자 평균단가 아래(손실 구간) — 매물 압력 잠재."))
    ats = l1.get("ats")
    if ats:
        prev = ats.get("ats_share_prev_pct")
        chg = f", 전주 대비 {ats['ats_share_pct'] - prev:+.1f}pp" if prev is not None else ""
        out.append(f"FINRA 주간 체결 구성({ats['week_start']}주, 2~4주 지연 공시): ATS(다크풀) <b>{pc(ats['ats_share_pct'], sign=False)}</b>"
                   f"{chg} · 홀세일러 내부화(리테일 주문) <b>{pc(ats['otc_share_pct'], sign=False)}</b> · 거래소(Lit) "
                   f"{pc(ats['lit_share_pct'], sign=False)}. ATS 평균 체결 {n(ats.get('ats_trades_avg_size'),0)}주 — 대량 주문 분할 체결 흔적.")
    fl = l1.get("flow")
    if fl:
        iar = fl.get("iar")
        cls = {"INSTITUTIONAL_LEAD": "기관 주도 — 방향성 의도 유력", "MIXED": "혼재 — 기관 방향성 불명확",
               "RETAIL_PRO_LEAD": "리테일·프로 주도 — 신호 신뢰도 낮음"}.get(fl.get("iar_class"), "")
        out.append(f"체결 단위 4-Way({len(fl['days'])}세션, SIP 전 체결 tick rule): 기관 <b>{ssh(fl['delta_inst'])}</b> · 프로 "
                   f"{ssh(fl['delta_pro'])} · 리테일 {ssh(fl['delta_retail'])} → 전체 {ssh(fl['delta_total'])}주. "
                   f"기관 흡수율 IAR <b>{n(iar)}</b> ({cls}).")
        dv, dd = fl.get("dp_vwap"), fl.get("dp_vwap_dist_pct")
        if dv:
            out.append(f"다크풀 전용 VWAP {usd(dv)} — 종가 대비 <b>{pc(dd, 2)}</b>: "
                       + ("기관이 종가보다 높은 가격에서 장외 매수 → 상방 압력." if (dd or 0) > 0.5 else
                          "기관이 종가보다 낮은 가격에서 장외 처분 → 하방 압력." if (dd or 0) < -0.5 else
                          "종가와 근접 — 장외 체결 가격 중립."))
        sh_ = [x for x in fl.get("sessions") or [] if x.get("flag")]
        if sh_:
            out.append("세션 이상: " + ", ".join(f"{x['session']} 장외 {n(x['ratio'])}배({x['flag']})" for x in sh_) + ".")
        bj = fl.get("bjzz_imbalance")
        if bj is not None:
            out.append(f"리테일 교차검증(BJZZ 서브페니): 리테일 순매수 불균형 {bj:+.2f} — "
                       + ("리테일 매수 우위." if bj > 0.1 else "리테일 매도 우위." if bj < -0.1 else "균형."))
    else:
        out.append("기관 흡수율(IAR)·다크풀 전용 VWAP은 체결(Trade-level) 데이터가 필요하며, 현재 데이터 등급에서는 산출하지 않는다(추정치 미표기).")
    # 교차검증
    l2s, l3s = l2.get("signal"), l3.get("signal")
    s = l1.get("signal")
    agree = [x for x in (l2s, l3s) if x == s and s != "NEUTRAL"]
    if s == "NEUTRAL":
        xv = "L1 중립 — 교차검증상 방향 기여 없음. L2/L3 신호가 상대적 우선권을 가진다."
    elif agree:
        xv = f"L1 {ko(l1.get('scenario'))} 신호를 {'L2' if l2s == s else ''}{'·' if len(agree) == 2 else ''}{'L3' if l3s == s else ''}가 지지 — 수급 정합."
    else:
        xv = f"L1 {ko(l1.get('scenario'))} 신호를 L2({ko(l2.get('scenario'))})·L3({ko(l3.get('scenario'))})가 지지하지 않음 — 신호 충돌, 신뢰도 하향."
    return out, xv


def l2_text(l2: Dict, l1: Dict, l3: Dict) -> Tuple[List[str], str]:
    out = []
    sp = l2.get("short_pct_latest")
    if sp is None:
        return ["FINRA 공매도 데이터 매칭 실패 — 레이어 판정 보류."], "교차검증 불가."
    z = l2.get("anomaly_z") or 0
    out.append(f"공매도 비중 <b>{pc(sp, sign=False)}</b> (FINRA TRF 보고분 기준, 14일 평균 {pc(l2.get('short_avg_14d'), sign=False)}, "
               f"z={z:+.2f}, 기울기 일 {l2.get('short_slope', 0):+.2f}pp → {l2.get('slope_dir')}). "
               "대형 유동주는 MM 헤지 구조상 40–55%가 기본값이므로 절대값이 아닌 자체 기준선 대비 이탈로 판독한다.")
    out.append(f"실물 재계산: 장외 공매도 {sh(l2.get('short_shares'))}주, 총거래량 환산 추정 {sh(l2.get('est_short_total'))}주.")
    fee = l2.get("ctb_fee")
    if fee is not None:
        fd, ad = l2.get("ctb_delta_pct"), l2.get("avail_delta_pct")
        avail = "10M+ (상한 표기)" if l2.get("available_capped") else sh(l2.get("shares_available"))
        out.append(f"대차: 수수료 <b>{n(fee)}%</b>({l2.get('htb_class')}) · 전일 대비 <b>{pc(fd)}</b> · 가용잔고 {avail} "
                   f"(Δ {pc(ad) if ad is not None else '산출불가'}). 출처 {l2.get('borrow_source')}.")
    else:
        out.append("대차 수수료·가용잔고 미수집 — 공매도 비용 방향성(가장 중요한 판별 변수) 공백. 케이스 판별 신뢰도 하향.")
    si = l2.get("short_interest")
    if si:
        out.append(f"공매도 잔고(SI, {si['settlement_date']} 결제 기준) {sh(si.get('shares'))}주 · 직전 대비 {pc(si.get('change_pct'))} · "
                   f"유통주식 대비 {pc(si.get('pct_float'), 2, sign=False)} · Days-to-Cover {n(si.get('days_to_cover'))}일.")
    case = l2.get("case")
    out.append(f"케이스 판별: <b>{KO_CASE.get(case, case)}</b> — 근거: {', '.join(l2.get('case_basis') or ['판별 근거 부족'])}.")
    if l2.get("low_ctb_paradox"):
        out.append("<b>저CTB 역설</b>: 수수료 1% 미만인데 가용잔고 감소 — 저비용으로 가격 억제 포지션을 선점하는 구조. "
                   "기술적 반등 신호만으로의 진입은 무한 핀 리스크.")
    l1s = l1.get("signal")
    if case == "CASE_2_DIRECTIONAL_SHORT" and l1s == "BULLISH":
        xv = "L1 매집 vs L2 방향성 숏 — 서로 다른 주체(롱 매집 vs 숏 공격)의 충돌. 상충 신호로 분류."
    elif case == "CASE_1_MM_DELTA_HEDGE":
        xv = "Short% 상승은 옵션 헤지 성격(CTB 불변) — 방향성 약세로 해석하지 않으며 L3 옵션 포지션이 우선."
    elif case == "CASE_3_SYNTHETIC_HEDGE":
        xv = "L1 기관 순매수와 동반된 공매도 — 롱 보유분 헤지(합성 헤지)로 순 약세 아님."
    else:
        xv = f"L1 {ko(l1.get('scenario'))} / L2 {ko(l2.get('scenario'))} — " + \
             ("방향 일치." if l1s == l2.get("signal") and l1s != "NEUTRAL" else "독립적 신호, 가중 반영.")
    return out, xv


def l3_text(l3: Dict, l1: Dict, l4: Dict, price: float) -> Tuple[List[str], str]:
    if l3.get("scenario") == "NO_DATA":
        return ["옵션 체인 미수집 — 옵션 레이어 판정 보류."], "교차검증 불가."
    out = []
    mp, dte = l3.get("max_pain"), l3.get("dte")
    out.append(f"주 만기 {l3.get('expiry')} (DTE {dte}) Max Pain <b>{usd(mp)}</b> — 현재가 대비 {pc(-(l3.get('max_pain_dist_pct') or 0), 2)} "
               + ("(만기 임박: MM 헤지비용 최소점으로의 견인력 유효)." if (dte or 99) <= 5 else "(만기 여유: 방향성 참고치).")
               + (f" 월물 {l3.get('monthly_expiry')} Max Pain {usd(l3.get('monthly_max_pain'))}." if l3.get("monthly_max_pain") else ""))
    flip, reg = l3.get("flip_zone"), l3.get("gamma_regime")
    ng = l3.get("net_gex")
    if flip:
        side = "위" if price > flip else "아래"
        out.append(f"Net GEX {money_big(ng)}/1% · 감마 플립 <b>{usd(flip)}</b>, 현재가는 플립 {side}({pc((price-flip)/flip*100,2)}) → "
                   + ("<b>양(+)감마 레짐</b>: 딜러가 변동성을 흡수(되돌림 매매) — 급등락 억제." if price > flip else
                      "<b>음(−)감마 레짐</b>: 딜러 헤지가 추세를 증폭 — 변동성 확대 구간."))
    elif reg:
        out.append(f"Net GEX {money_big(ng)}/1% · ±20% 범위 내 플립 부재 — 전 구간 {'양(+)' if reg == 'POSITIVE' else '음(−)'}감마.")
    cw, pw = l3.get("call_wall"), l3.get("put_wall")
    if cw or pw:
        out.append(f"감마 벽: 콜월 <b>{usd(cw)}</b>({pc((cw-price)/price*100) if cw else 'N/A'}) 저항 · 풋월 <b>{usd(pw)}</b>"
                   f"({pc((pw-price)/price*100) if pw else 'N/A'}) 지지. 두 벽 사이가 딜러 헤지가 만드는 가격 회랑.")
    pvo, pvv, adj = l3.get("pc_oi"), l3.get("pc_vol"), l3.get("pc_vol_adj")
    adj_s = f" (만기물 제외 보정 {n(adj)})" if adj is not None and pvv is not None and abs(adj - pvv) > 0.01 else ""
    out.append(f"P/C OI {n(pvo)} · P/C 거래량 {n(pvv)}{adj_s} — "
               + ("거래량이 OI 대비 콜 쏠림: 신규 콜 매수 가속." if (pvv or 1) < (pvo or 1) * 0.8 else
                  "거래량이 OI 대비 풋 쏠림: 신규 하방 베팅/헤지 가속." if (pvv or 1) > (pvo or 1) * 1.2 else
                  "포지션 구성과 신규 흐름이 유사."))
    sk = l3.get("skew_25d")
    if sk is not None:
        out.append(f"25Δ 스큐 {sk:+.1f}vol pt (풋−콜) · ATM IV {pc((l3.get('atm_iv') or 0)*100, sign=False)} · CBOE IV30 "
                   f"{pc((l3.get('iv30') or 0)*100, sign=False) if l3.get('iv30') else 'N/A'} · 기간구조 {l3.get('term_structure') or 'N/A'}. "
                   + ("콜 IV 우위(역스큐) — 상방 수요 이례적." if sk < 0 else "하방 보호 수요 과열." if sk > 8 else "통상적 하방 프리미엄."))
    em = l3.get("expected_move")
    if em:
        out.append(f"ATM 스트래들 기준 주 만기 예상 변동폭 <b>±{usd(em)} (±{n(l3.get('expected_move_pct'))}%)</b> → "
                   f"{usd(price-em)} ~ {usd(price+em)}.")
    if l3.get("binary_event"):
        out.append(f"<b>이진 이벤트 프라이싱 감지</b>: {'; '.join(l3.get('binary_basis') or [])} — 시장이 불연속 갭을 가격에 반영 중.")
    pin = l3.get("pinning")
    if pin:
        out.append(f"직전 만기({pin['expiry']}) 핀닝 사후검증: 종가와 전일 산출 Max Pain {usd(pin['max_pain'])} 괴리 {n(pin['dist_pct'])}% → "
                   + {"PINNING_SUCCESS": "MM 핀닝 성공(헤지비용 최적화 지점 도달).",
                      "PINNING_ATTEMPT": "핀닝 시도 흔적(±0.5% 이내).",
                      "BREAKAWAY": "핀닝 이탈 — 방향성 자금 또는 신규 정보가 감마를 압도."}[pin["status"]])
    if l3.get("oi_trend_pct") is not None:
        out.append(f"60일 내 만기 총 OI 5거래일 변화 {pc(l3['oi_trend_pct'])} (풋 OI {pc(l3.get('put_oi_trend_pct'))}).")
    l1s = l1.get("signal")
    if l1s == l3.get("signal") and l1s != "NEUTRAL":
        xv = f"L3 옵션 구조가 L1 {ko(l1.get('scenario'))} 방향과 일치 — 다크풀·옵션 정합."
    elif flip and price > flip and l1s == "BEARISH":
        xv = "L1 분배 신호에도 양감마 레짐이 하방을 완충 — 플립 하향 이탈 전까지 하락 속도 제한."
    elif flip and price < flip and l1s == "BULLISH":
        xv = "L1 매집 신호와 음감마 레짐 공존 — 플립 상향 회복 시 변동성 축소와 함께 상방 안정화 가능."
    else:
        xv = f"L1 {ko(l1.get('scenario'))} / L3 {ko(l3.get('scenario'))} — 옵션 구조는 가격 회랑을 규정할 뿐 독립적 방향성은 제한적."
    return out, xv


def l4_text(l4: Dict, l1: Dict, l3: Dict) -> Tuple[List[str], str]:
    if l4.get("scenario") == "NO_DATA":
        return ["일봉 데이터 부족."], "교차검증 불가."
    p = l4["current_price"]
    out = [f"이동평균 배열 <b>{l4.get('ma_alignment')}</b> — 종가 {usd(p)} / SMA20 {usd(l4.get('sma20'))} / SMA50 {usd(l4.get('sma50'))} / "
           f"SMA200 {usd(l4.get('sma200'))}. EMA9 {usd(l4.get('ema9'))} {'>' if (l4.get('ema9') or 0) > (l4.get('ema21') or 0) else '<'} EMA21 {usd(l4.get('ema21'))}.",
           f"볼린저 폭 {n(l4.get('bb_width_pct'))}% (1년 백분위 {n(l4.get('bb_width_pctile'),0)}) · {l4.get('bb_width_trend')} · "
           f"밴드 내 위치 {n((l4.get('bb_position') or 0)*100,0)}% ({l4.get('band_status')}). "
           + ("밴드 수축 하위 20% — 변동성 압축, 방향 이탈 임박." if (l4.get("bb_width_pctile") or 100) <= 20 else
              "밴드 확장 상위 20% — 추세 진행/과열 구간." if (l4.get("bb_width_pctile") or 0) >= 80 else ""),
           f"RSI(14) {n(l4.get('rsi14'),1)} · ATR(14) {usd(l4.get('atr14'))} ({n(l4.get('atr_pct'))}%) · 거래량 30일 평균 대비 {n(l4.get('vol_ratio30'))}배.",
           f"지지 {usd(l4.get('immediate_support'))} / {usd(l4.get('key_support'))} · 저항 {usd(l4.get('immediate_resistance'))} / "
           f"{usd(l4.get('key_resistance'))} · 60일 거래량 POC {usd(l4.get('poc60'))}."]
    if l1.get("signal") == "BULLISH" and l4.get("signal") == "BEARISH":
        xv = "차트 약세 vs 다크풀 매집 — 후행 지표인 차트가 아직 수급 변화를 반영하지 못한 축적 국면 가능성."
    elif l1.get("signal") == "BEARISH" and l4.get("signal") == "BULLISH":
        xv = "차트 강세 vs 다크풀 분배 — 상승 추세 속 물량 출회. 추세 신뢰도 하향."
    else:
        xv = f"차트 {ko(l4.get('scenario'))}는 L1 {ko(l1.get('scenario'))}·L3 {ko(l3.get('scenario'))}와 " + \
             ("정합." if l4.get("signal") == l1.get("signal") else "부분 상충 — 차트 가중치(15%)는 최저.")
    return out, xv


# ─── 트리거 · 리스크 ─────────────────────────────────────────────────────────
def triggers(a: Dict) -> List[Dict[str, str]]:
    l1, l2, l3, l4 = a["l1"], a["l2"], a["l3"], a["l4"]
    p = a["meta"]["price"]
    t: List[Dict[str, str]] = []

    def add(name, kind, met, impl):
        t.append({"trigger": name, "type": kind, "status": "N/A" if met is None else ("MET" if met else "NOT MET"),
                  "implication": impl})
    vr = l4.get("vol_ratio30")
    add("거래량 > 30일 평균 150%", "BREAKOUT", None if vr is None else vr > 1.5, f"이동 개시 신호 (현재 {n(vr)}배)")
    add("BB 폭 1년 하위 20% (압축)", "SETUP", None if l4.get("bb_width_pctile") is None else l4["bb_width_pctile"] <= 20,
        f"변동성 폭발 전조 (백분위 {n(l4.get('bb_width_pctile'),0)})")
    if l3.get("flip_zone"):
        add(f"종가 > GEX 플립 {usd(l3['flip_zone'])}", "STABILIZER", p > l3["flip_zone"], "양감마: 변동성 억제 / 이탈 시 증폭")
    if l3.get("call_wall"):
        add(f"종가 > 콜월 {usd(l3['call_wall'])}", "BULLISH", p > l3["call_wall"], "감마 스퀴즈 연료 점화")
    if l3.get("put_wall"):
        add(f"종가 < 풋월 {usd(l3['put_wall'])}", "BEARISH", p < l3["put_wall"], "딜러 숏감마 가속 하락")
    if l3.get("max_pain"):
        add(f"종가 < Max Pain {usd(l3['max_pain'])}", "BULLISH" if (l3.get("dte") or 99) <= 5 else "INFO",
            p < l3["max_pain"], f"충족 시 만기 견인 상방 중력 (현재 {'상방' if p < l3['max_pain'] else '하방'} 중력)")
    add("Net OBV (5d) > 0", "BULLISH", None if l1.get("delta_total") is None else l1["delta_total"] > 0, "순매수 체결 우위")
    add("다크풀 비중 2일+ 40% 초과", "ABSORPTION", None if l1.get("dp_days_over_40") is None else l1["dp_days_over_40"] >= 2,
        f"장외 흡수 지속 (최근 5일 중 {l1.get('dp_days_over_40', 'N/A')}일)")
    add("Short% 기울기 하락", "BULLISH", None if l2.get("slope_dir") is None else l2["slope_dir"] == "FALLING", "숏 커버링 진행")
    add("CTB 수수료 > 5%", "SQUEEZE", None if l2.get("ctb_fee") is None else l2["ctb_fee"] > 5, "대차 압박 — 스퀴즈 셋업")
    add("CTB 전일 대비 +5%↑", "BEARISH", None if l2.get("ctb_delta_pct") is None else l2["ctb_delta_pct"] > 5, "공매도 수요 증가")
    if l4.get("sma50"):
        add(f"종가 > SMA50 {usd(l4['sma50'])}", "BULLISH", p > l4["sma50"], "중기 추세 지지")
    return t


def risks(a: Dict) -> List[str]:
    l1, l2, l3, q = a["l1"], a["l2"], a["l3"], a["quality"]
    pats, scen = a["patterns"], a["scenarios"]
    r = []
    if "LOW_CTB_PARADOX" in pats:
        r.append("[저CTB 역설] 저비용 공매도 포지션 선점 구조 — 기술적 반등 신호 단독 진입 시 무한 핀 리스크.")
    if l3.get("binary_event"):
        r.append("[이진 이벤트] 옵션시장이 불연속 갭을 프라이싱 — 방향 무관 갭 리스크, 사이즈 축소.")
    if l3.get("flip_zone") and abs(a["meta"]["price"] - l3["flip_zone"]) / l3["flip_zone"] < 0.015:
        r.append(f"[레짐 전환] 현재가가 감마 플립 {usd(l3['flip_zone'])}의 ±1.5% 이내 — 소폭 이동으로 변동성 레짐 반전 가능.")
    if scen.get("macro") == "RESTRICTED":
        r.append("[거시 역풍] 유동성 제한 환경 — 상방 시나리오는 모델 대비 포지션 축소 필요.")
    if (scen.get("dispersion") or 0) > 0.35:
        r.append("[신호 분산] 레이어 간 점수 분산이 커 단일 방향 확신 불가 — 중립 시나리오 가중.")
    earn = [e for e in a["events"] if e["kind"] == "earnings"]
    if earn:
        r.append(f"[실적 이벤트] {earn[0]['date']} 실적 발표 (D-{earn[0]['days_until']}) — 가이던스 변화 시 수급 가정 전면 재설정.")
    for k, L in q["layers"].items():
        if L["status"] != "OK" and L["missing"]:
            r.append(f"[데이터 공백 · {k.upper()}] {', '.join(L['missing'][:4])} 미확보 — 해당 레이어 신뢰도 하향 반영.")
    if not r:
        r.append("[촉매 리스크] 미공개 공시·헤드라인 발생 시 구조적 가정 붕괴 가능.")
    return r[:7]


# ─── 헤드라인 · 3줄 요약 · 핵심 결론 ─────────────────────────────────────────
def paradoxes(a: Dict) -> List[str]:
    l1, l2, l3, l4 = a["l1"], a["l2"], a["l3"], a["l4"]
    p = a["meta"]["price"]
    out = []
    if l1.get("divergence") == "BEARISH_DIVERGENCE":
        out.append("가격은 오르는데 순체결은 매도 우위 — 상승이 매수 수요가 아닌 유동성 공급(물량 출회) 위에서 진행")
    if l1.get("divergence") == "BULLISH_DIVERGENCE":
        out.append("가격은 내리는데 순체결은 매수 우위 — 하락을 이용한 조용한 흡수")
    if l2.get("case") == "CASE_1_MM_DELTA_HEDGE" and (l2.get("anomaly_z") or 0) > 1:
        out.append("공매도 비중 급등에도 대차 비용 불변 — 약세 베팅이 아닌 옵션 매도분의 기계적 델타헤지")
    if l2.get("low_ctb_paradox"):
        out.append("대차 비용은 싼데 빌릴 주식은 줄어드는 저CTB 역설 — 저비용 억제 포지션 선점")
    if (l1.get("dp_ratio") or 1) > 1.15 and (l1.get("delta_total") or 0) < 0:
        out.append("장외 비중 확대와 순매도 동반 — 다크풀을 통한 분배")
    if (l1.get("dp_ratio") or 1) > 1.15 and (l1.get("delta_total") or 0) > 0:
        out.append("장외 비중 확대와 순매수 동반 — 다크풀을 통한 매집")
    if l3.get("flip_zone") and p > l3["flip_zone"] and l3.get("max_pain") and abs(p - l3["max_pain"]) / p < 0.01:
        out.append("양감마 레짐과 Max Pain 근접이 겹친 핀닝 구조 — 딜러가 가격을 붙잡는 구간")
    if l3.get("flip_zone") and p < l3["flip_zone"]:
        out.append("음감마 레짐 — 딜러 헤지가 방향을 증폭하는 구간")
    if (l3.get("pc_oi") or 0) > 1.2 and l1.get("signal") == "BULLISH":
        out.append("풋 OI 우위 속 다크풀 매집 — 현물 매집과 하방 보험의 동시 구축")
    if l4.get("signal") == "BULLISH" and l1.get("signal") == "BEARISH":
        out.append("차트는 강세지만 체결은 분배 — 추세 추종 매수가 공급을 받아내는 구조")
    return out


def headline(a: Dict) -> str:
    s = a["scenarios"]
    top = max((("A 상승", s["A_bullish"]), ("B 중립", s["B_neutral"]), ("C 하락", s["C_bearish"])), key=lambda x: x[1])
    px = paradoxes(a)
    l3 = a["l3"]
    parts = [f"[{top[0]} {top[1]:.0f}%]"]
    if px:
        parts.append(px[0].split(" — ")[0])
    if l3.get("flip_zone"):
        parts.append(f"감마 플립 {usd(l3['flip_zone'])} {'상단' if a['meta']['price'] > l3['flip_zone'] else '하단'}")
    if l3.get("call_wall") and l3.get("put_wall"):
        parts.append(f"회랑 {usd(l3['put_wall'],0)}–{usd(l3['call_wall'],0)}")
    return " · ".join(parts)


def three_lines(a: Dict) -> List[str]:
    l1, l2, l3 = a["l1"], a["l2"], a["l3"]
    p = a["meta"]["price"]
    one = (f"다크풀 {pc(l1.get('dp_pct'), sign=False)}(20일 평균 대비 {n(l1.get('dp_ratio'))}배) · Net OBV {ssh(l1.get('delta_total'))}주 · "
           f"{l1.get('divergence')} → {ko(l1.get('scenario'))}.")
    fee = l2.get("ctb_fee")
    two = (f"Short% {pc(l2.get('short_pct_latest'), sign=False)} (z {(l2.get('anomaly_z') or 0):+.1f}, {l2.get('slope_dir')}) · "
           + (f"CTB {n(fee)}% (Δ {pc(l2.get('ctb_delta_pct'))})" if fee is not None else "CTB 미수집")
           + f" → {KO_CASE.get(l2.get('case'), l2.get('case'))}.")
    if a["meta"].get("is_index"):
        one = "지수 — 다크풀 레이어 해당 없음."
        two = "지수 — 공매도 레이어 해당 없음."
    flip = l3.get("flip_zone")
    three = (f"Max Pain {usd(l3.get('max_pain'))}(DTE {l3.get('dte')}) · 플립 {usd(flip)} "
             f"({'양감마' if flip and p > flip else '음감마' if flip else l3.get('gamma_regime') or 'N/A'}) · 콜월 {usd(l3.get('call_wall'))} / "
             f"풋월 {usd(l3.get('put_wall'))} → {ko(l3.get('scenario'))}.")
    return [one, two, three]


def conclusions(a: Dict) -> List[str]:
    l1, l2, l3, l4 = a["l1"], a["l2"], a["l3"], a["l4"]
    s, ph = a["scenarios"], a["phase"]
    p = a["meta"]["price"]
    T = a["meta"]["ticker"]
    px = paradoxes(a)
    top = max((("A", s["A_bullish"]), ("B", s["B_neutral"]), ("C", s["C_bearish"])), key=lambda x: x[1])
    intent = {"A": "축적 완료 후 상방 이동 준비", "B": "가격 고정·시간가치 소멸을 통한 포지션 정리",
              "C": "분배 진행 및 하방 재가격"}[top[0]]
    contrib = s.get("contributions") or {}
    lead = max(("L1", "L2", "L3", "L4"), key=lambda k: abs(contrib.get(k, 0)))
    p1 = (f"{T}의 가장 중요한 발견은 " + (f"<u>{px[0]}</u>이다. " if px else "레이어 간 신호의 뚜렷한 역설이 없다는 점이다. ")
          + (f"보조 신호로 {px[1]}도 관측된다. " if len(px) > 1 else "")
          + f"4개 레이어 가중 점수는 {s['raw_score']:+.3f}(분산 {s.get('dispersion', 0):.2f})로, 확률은 [A] {s['A_bullish']}% / "
          f"[B] {s['B_neutral']}% / [C] {s['C_bearish']}%에 수렴한다. 점수를 가장 크게 움직인 레이어는 {lead}"
          f"({contrib.get(lead, 0):+.3f})이며, 거시 유동성은 {KO_ENV.get(s.get('macro'), s.get('macro'))} 환경으로 "
          f"{contrib.get('macro', 0)*100:+.1f}%p 보정되었다. 이 조합에서 설계자의 의도는 <u>{intent}</u> 단계로 해석된다.")
    flip = l3.get("flip_zone")
    p2 = (f"구조적으로 보면, 장외 비중 {pc(l1.get('dp_pct'), sign=False)}와 순체결 {ssh(l1.get('delta_total'))}주는 "
          f"{ko(l1.get('scenario'))} 흐름을, 공매도 측은 {KO_CASE.get(l2.get('case'), l2.get('case'))} 성격을 보인다. "
          f"옵션 측에서는 " + (f"감마 플립 {usd(flip)}을 기준으로 현재가가 {'양감마(변동성 억제)' if p > flip else '음감마(변동성 증폭)'} 구간에 있고, "
                            if flip else "플립이 관측 범위 밖이어서 감마 레짐이 단일하며, ")
          + f"콜월 {usd(l3.get('call_wall'))}과 풋월 {usd(l3.get('put_wall'))}이 딜러 헤지가 만드는 가격 회랑을 규정한다. "
          f"차트는 {ko(l4.get('scenario'))}({l4.get('ma_alignment')})로 후행 확인 역할에 그친다. "
          f"이 해석이 틀리는 경우는 순체결 부호가 반전되거나, 대차 비용이 급등해 공매도 성격이 방향성으로 전환될 때이다.")
    trig = []
    if flip:
        trig.append(f"종가 기준 감마 플립 {usd(flip)} {'하향 이탈(음감마 진입·하방 가속)' if p > flip else '상향 회복(양감마 진입·안정화)'}")
    if l3.get("call_wall"):
        trig.append(f"콜월 {usd(l3['call_wall'])} 돌파(딜러 숏콜 헤지 매수 유발)")
    if l3.get("put_wall"):
        trig.append(f"풋월 {usd(l3['put_wall'])} 붕괴(딜러 숏풋 헤지 매도 유발)")
    trig.append("거래량 30일 평균 150% 이상 동반 볼린저 확장(이동 개시)")
    if l2.get("ctb_fee") is not None:
        trig.append(f"CTB {n(l2['ctb_fee'])}%에서 전일 대비 +5% 이상 상승 지속(공매도 공격 전환)")
    cats = ph.get("catalysts") or []
    cat_s = f" 일정상 {cats[0]['date']} {cats[0]['event']}이 Phase 2→3 전환의 시간적 기준점이다." if cats else ""
    p3 = ("결정적 트리거는 다음 중 먼저 발생하는 것이다: " + "; ".join(trig) + "." + cat_s +
          f" Phase 3({ph['p3_date']})의 기준 방향은 {ph['p3_direction']}이며, 1차 목표 상단 {usd(ph.get('p3_target_hi'))} / "
          f"하단 {usd(ph.get('p3_target_lo'))}이다. 위 트리거가 현 가설과 반대로 발동하면 시나리오 전체를 재산정한다.")
    return [p1, p2, p3]
