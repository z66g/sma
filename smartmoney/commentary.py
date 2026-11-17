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




_DIGIT_FINAL = {"0": 21, "1": 8, "2": 0, "3": 16, "4": 0, "5": 0, "6": 1, "7": 8, "8": 8, "9": 0}


def _final(w: str) -> int:
    """마지막 음절의 받침 코드 (0=없음, 8=ㄹ). 괄호·따옴표는 건너뛴다."""
    w = w.rstrip(" ’'\")")
    if w.endswith(")") or "(" in w and w.rfind("(") > w.rfind(")"):
        w = w[: w.rfind("(")].rstrip()
    if not w:
        return 0
    c = w[-1]
    if "가" <= c <= "힣":
        return (ord(c) - 0xAC00) % 28
    return _DIGIT_FINAL.get(c, 0)


def jo(w: str, kind: str) -> str:
    """조사 자동 선택: kind ∈ {'으로','이','은','과','을'}."""
    f = _final(w)
    if kind == "으로":
        return w + ("로" if f in (0, 8) else "으로")
    pair = {"이": ("가", "이"), "은": ("는", "은"), "과": ("와", "과"), "을": ("를", "을")}[kind]
    return w + (pair[1] if f else pair[0])


def ab(x: Optional[float], d: int = 1) -> str:
    """부호 없는 퍼센트 (방향은 문장에서 '위/아래'로 표현할 때)."""
    return "N/A" if x is None else f"{abs(x):.{d}f}%"


# ─── 용어 사전 (내부 코드 → 화면 표기) ─────────────────────────────────────────
KO_SCEN = {
    "ACCUMULATION": "매집", "DISTRIBUTION": "분산", "NEUTRAL": "중립", "AMBIGUOUS": "혼조",
    "SHORT_SQUEEZE_RISK": "숏스퀴즈 위험", "DIRECTIONAL_SHORT": "방향성 공매도", "MM_HEDGE": "마켓메이커 헤지",
    "SYNTHETIC_HEDGE": "헤지성 공매도", "SHORT_COVERING": "숏커버링",
    "PINNING": "가격 고정(핀닝)", "GAMMA_SQUEEZE": "감마 스퀴즈", "VOLATILITY_EXPANSION": "변동성 확대", "HEDGING": "헤지 수요",
    "UPTREND": "상승 추세", "DOWNTREND": "하락 추세", "RANGE_BOUND": "박스권",
    "BREAKOUT_PENDING": "상단 돌파 시도", "BREAKDOWN_PENDING": "하단 이탈 위험", "NO_DATA": "데이터 없음", "N/A": "해당 없음",
}
KO_CASE = {"CASE_1_MM_DELTA_HEDGE": "① 마켓메이커 헤지", "CASE_2_DIRECTIONAL_SHORT": "② 방향성 공매도",
           "CASE_3_SYNTHETIC_HEDGE": "③ 보유 물량 헤지", "SHORT_COVERING": "숏커버링", "MIXED": "혼조(판단 보류)"}
KO_PAT = {"THETA_BURN": "세타 소멸형 횡보", "THETA_BURN_BREAKOUT": "횡보 이탈 시작", "FINAL_ABSORPTION": "바닥권 물량 흡수",
          "LOW_CTB_PARADOX": "저CTB 역설", "SHORT_SQUEEZE_RISK": "숏스퀴즈 위험", "GAMMA_SQUEEZE_SETUP": "감마 스퀴즈 조건",
          "OPTION_PINNING": "옵션 핀닝", "BINARY_EVENT_PRICED": "이벤트 급변동 반영", "DARK_POOL_VOLUME_SPIKE": "다크풀 거래량 급증"}
KO_ENV = {"FAVORABLE": "우호", "NEUTRAL": "중립", "RESTRICTED": "제한"}
KO_DIV = {"BEARISH_DIVERGENCE": "약세 다이버전스", "BULLISH_DIVERGENCE": "강세 다이버전스",
          "CONVERGENCE": "가격·수급 동행", "NEUTRAL": "방향 없음"}
KO_IAR = {"INSTITUTIONAL_LEAD": "기관 주도", "MIXED": "혼조", "RETAIL_PRO_LEAD": "리테일·프로 주도"}
KO_SLOPE = {"RISING": "상승", "FALLING": "하락", "FLAT": "횡보"}
KO_HTB = {"ETB": "대차 쉬움", "MODERATE": "보통", "HTB": "대차 어려움", "EXTREME": "대차 매우 어려움", "CRITICAL": "대차 극히 어려움"}
KO_TERM = {"BACKWARDATION": "역전(단기 IV > 차월)", "CONTANGO": "정상(차월 IV > 단기)", "FLAT": "평탄"}
KO_REGIME = {"POSITIVE": "양감마", "NEGATIVE": "음감마"}
KO_PIN = {"PINNING_SUCCESS": "핀닝 성공", "PINNING_ATTEMPT": "핀닝 근접", "BREAKAWAY": "핀닝 이탈"}
KO_FLIP = {"NO_FLIP_ALL_POSITIVE": "플립 없음 (±50% 전체 양감마)", "NO_FLIP_ALL_NEGATIVE": "플립 없음 (±50% 전체 음감마)",
           "NO_FLIP_IN_RANGE": "플립 없음 (탐색 범위 내)"}
KO_MA = {"FULL_BULL": "정배열", "FULL_BEAR": "역배열", "RECOVERING": "회복 중", "WEAKENING": "약화 중",
         "MIXED": "혼조", "INSUFFICIENT_DATA": "데이터 부족"}
KO_BAND = {"UPPER_TOUCH": "상단 터치", "LOWER_TOUCH": "하단 터치", "UPPER_ZONE": "상단 부근", "LOWER_ZONE": "하단 부근",
           "MID_ZONE": "중간"}
KO_WIDTH = {"EXPANDING": "확장", "CONTRACTING": "수축", "NEUTRAL": "보합"}
KO_DIR = {"UPWARD": "상방", "DOWNWARD": "하방", "RANGE": "박스권"}
KO_FLAG = {"EXTREME": "매우 큼", "HIGH": "큼", "MODERATE": "다소 큼"}
KO_SIGF = {"HIGH": "높음", "MED": "보통", "LOW": "낮음", "—": "—"}
KO_STATUS = {"MET": "충족", "NOT MET": "미충족", "N/A": "판단 불가"}
KO_TTYPE = {"BREAKOUT": "돌파", "SETUP": "준비", "STABILIZER": "안정", "BULLISH": "상승", "BEARISH": "하락",
            "INFO": "참고", "ABSORPTION": "흡수", "SQUEEZE": "스퀴즈"}
KO_QSTAT = {"OK": "정상", "PARTIAL": "일부 누락", "MISSING": "누락"}
KO_FILING = {"INSIDER": "내부자", "DILUTION": "희석", "MATERIAL": "주요 공시", "OWNERSHIP": "지분"}
KO_LEVEL = {"Current Price": "현재가", "Max Pain": "Max Pain", "Monthly Max Pain": "월물 Max Pain", "GEX Flip": "감마 플립",
            "Call Wall": "콜월", "Put Wall": "풋월", "Expected Move ↑": "예상 변동 상단", "Expected Move ↓": "예상 변동 하단",
            "Immediate Resistance": "1차 저항", "Key Resistance": "2차 저항", "Immediate Support": "1차 지지",
            "Key Support": "2차 지지", "SMA 50": "50일선", "SMA 200": "200일선", "Volume POC (60d)": "60일 거래량 밀집대",
            "BB Upper": "볼린저 상단", "BB Lower": "볼린저 하단", "52W High": "52주 고점", "52W Low": "52주 저점"}
KO_SRC = {"90d 스윙": "90일 스윙", "1y 스윙": "1년 스윙", "거래량 POC(60d)": "60일 거래량 밀집대", "SMA50": "50일선",
          "SMA200": "200일선"}
KO_SESSION = {"pre": "프리마켓", "reg": "정규장", "ah": "애프터마켓"}
LAYER_KO = {"L1": "L1 다크풀", "L2": "L2 공매도", "L3": "L3 옵션", "L4": "L4 차트"}


def ko(s: Optional[str]) -> str:
    return KO_SCEN.get(s or "", s or "N/A")


def kt(d: Dict[str, str], v: Optional[str], default: str = "N/A") -> str:
    """코드값을 사전으로 번역 (없으면 원문)."""
    return d.get(v, v) if v else default


def srcs(lst: Optional[List[str]]) -> str:
    return " · ".join(KO_SRC.get(x, x) for x in (lst or [])) or "—"


def lv_pair(a: Optional[float], b: Optional[float], src: Optional[List[str]]) -> str:
    """'$1 / $2(출처)' — 없는 값과 중복 출처는 생략."""
    vals = [usd(x) for x in (a, b) if x]
    if not vals:
        return "확인되지 않음"
    seen: List[str] = []
    for x in (src or [])[:len(vals)]:
        x = KO_SRC.get(x, x)
        if x not in seen:
            seen.append(x)
    return " / ".join(vals) + (f"({' · '.join(seen)})" if seen else "")


def q(s: str) -> str:
    return f"‘{s}’"


# ─── 섹션 해석 ───────────────────────────────────────────────────────────────
def l1_text(l1: Dict, l2: Dict, l3: Dict) -> Tuple[List[str], str]:
    if l1.get("scenario") == "NO_DATA":
        return ["FINRA 장외 거래 데이터를 받지 못해 다크풀 레이어는 판단을 보류합니다."], "교차검증할 수 없습니다."
    out = []
    dp, avg, ratio = l1.get("dp_pct"), l1.get("dp_avg20"), l1.get("dp_ratio")
    if dp is not None:
        lvl = ("기관 거래가 매우 많은 수준(50% 초과)" if dp > 50 else "평소보다 높은 수준(40~50%)" if dp > 40 else
               "일반적인 수준(30~40%)" if dp > 30 else "낮은 수준(30% 미만)")
        rel = ("장외 거래 비중이 늘었습니다" if (ratio or 1) > 1.1 else "장외 거래 비중이 줄었습니다" if (ratio or 1) < 0.9
               else "평소와 비슷합니다")
        out.append(f"다크풀(장외) 비중은 <b>{pc(dp, sign=False)}</b>로 {lvl}입니다. 20일 평균({pc(avg, sign=False)})의 "
                   f"<b>{n(ratio)}배</b>로 {rel}. 장외 거래량은 20일 평균의 {n(l1.get('off_vol_ratio'))}배입니다.")
    st = l1.get("obv_strength") or 0
    basis = "체결 단위 집계" if l1.get("flow") else "CLV 가중 추정"
    out.append(f"최근 5거래일 순매수 체결량(Net OBV, {basis})은 <b>{ssh(l1.get('delta_total'))}주</b>로, 일평균 거래량(ADV)의 "
               f"<b>{pc(st * 100)}</b>입니다. "
               + ("뚜렷한 순매수 흐름입니다." if st > 0.08 else "뚜렷한 순매도 흐름입니다." if st < -0.08
                  else "방향성은 뚜렷하지 않습니다."))
    div = l1.get("divergence")
    ps, os_ = l1.get("price_slope_pct"), l1.get("obv_slope_pct")
    if div == "BEARISH_DIVERGENCE":
        out.append(f"가격은 하루 평균 {pc(ps, 2)}씩 오르는데 순체결은 하루 {pc(os_)}(ADV 대비)씩 줄어드는 <b>약세 다이버전스</b>입니다. "
                   "오르는 동안 매도 체결이 더 많았다는 뜻으로, 상승을 이용해 물량이 나오고 있을 가능성이 있습니다.")
    elif div == "BULLISH_DIVERGENCE":
        out.append(f"가격은 하루 평균 {pc(ps, 2)}씩 내리는데 순체결은 하루 {pc(os_)}(ADV 대비)씩 늘어나는 <b>강세 다이버전스</b>입니다. "
                   "내리는 동안 매수 체결이 더 많았다는 뜻으로, 하락을 이용한 저가 매수 가능성이 있습니다.")
    elif div == "CONVERGENCE":
        out.append("가격과 순체결이 같은 방향으로 움직여, 현재 추세를 수급이 뒷받침하고 있습니다.")
    else:
        out.append("가격과 순체결 모두 뚜렷한 추세가 없어, 방향을 정하지 못한 구간입니다.")
    v5, vd = l1.get("vwap5"), l1.get("vwap_dist_pct")
    if v5:
        out.append(f"최근 5일 VWAP은 {usd(v5)}이고 종가는 이보다 <b>{ab(vd, 2)} {'높습니다' if (vd or 0) >= 0 else '낮습니다'}</b>. "
                   + ("최근 매수자 대부분이 수익 구간에 있습니다." if (vd or 0) > 0
                      else "최근 매수자 대부분이 손실 구간에 있어, 반등 때 매도 물량이 나올 수 있습니다."))
    ats = l1.get("ats")
    if ats:
        prev = ats.get("ats_share_prev_pct")
        chg = f"(전주 대비 {ats['ats_share_pct'] - prev:+.1f}%p)" if prev is not None else ""
        out.append(f"FINRA 주간 체결 구성({ats['week_start']} 주간, 2~4주 늦게 공개)은 ATS(다크풀) <b>{pc(ats['ats_share_pct'], sign=False)}</b>"
                   f"{chg}, 홀세일러 내부화(리테일 주문) <b>{pc(ats['otc_share_pct'], sign=False)}</b>, 거래소 "
                   f"{pc(ats['lit_share_pct'], sign=False)}입니다. ATS 건당 평균 체결 규모는 {n(ats.get('ats_trades_avg_size'), 0)}주입니다.")
    fl = l1.get("flow")
    if fl:
        iar = fl.get("iar")
        cls = {"INSTITUTIONAL_LEAD": "기관이 흐름을 주도하고 있습니다.",
               "MIXED": "기관의 방향성은 뚜렷하지 않습니다.",
               "RETAIL_PRO_LEAD": "리테일·프로 거래가 주도하고 있어 신호의 신뢰도는 낮습니다."}.get(fl.get("iar_class"), "")
        out.append(f"체결 주체별로 나누면 최근 {len(fl['days'])}세션 동안 기관 <b>{ssh(fl['delta_inst'])}</b>, 프로 "
                   f"{ssh(fl['delta_pro'])}, 리테일 {ssh(fl['delta_retail'])}주로 합계 {ssh(fl['delta_total'])}주입니다. "
                   f"기관 흡수율(IAR)은 <b>{n(iar)}</b>입니다. {cls}")
        dv, dd = fl.get("dp_vwap"), fl.get("dp_vwap_dist_pct")
        if dv:
            out.append(f"다크풀 체결만 따로 계산한 VWAP은 {usd(dv)}이며, 종가보다 <b>{ab(dd, 2)} {'높습니다' if (dd or 0) >= 0 else '낮습니다'}</b>. "
                       + ("기관이 종가보다 비싼 가격에도 장외에서 사들였다는 뜻으로 상승 요인입니다." if (dd or 0) > 0.5 else
                          "기관이 종가보다 싼 가격에 장외에서 처분했다는 뜻으로 하락 요인입니다." if (dd or 0) < -0.5 else
                          "종가와 거의 같아 장외 체결 가격에서는 방향성이 보이지 않습니다."))
        sh_ = [x for x in fl.get("sessions") or [] if x.get("flag")]
        if sh_:
            out.append("세션별로는 " + ", ".join(f"{x['session']} 장외 거래량이 평소의 {n(x['ratio'])}배" for x in sh_)
                       + "로 평소보다 많았습니다.")
        bj = fl.get("bjzz_imbalance")
        if bj is not None:
            out.append(f"서브페니 체결로 추정한 리테일 매수·매도 불균형은 {bj:+.2f}이며, "
                       + ("리테일 매수가 우세합니다." if bj > 0.1 else "리테일 매도가 우세합니다." if bj < -0.1 else "매수와 매도가 비슷합니다."))
    else:
        out.append("기관 흡수율(IAR)과 다크풀 VWAP은 체결 단위 데이터가 있어야 계산할 수 있어, 이번 리포트에서는 표시하지 않습니다.")
    # 교차검증
    l2s, l3s = l2.get("signal"), l3.get("signal")
    s = l1.get("signal")
    agree = [nm for nm, x in (("L2", l2s), ("L3", l3s)) if x == s]
    if s == "NEUTRAL":
        xv = "L1은 중립이라 방향 판단에는 기여하지 않으며, L2·L3 신호를 더 중요하게 봅니다."
    elif agree:
        xv = f"L1의 {q(ko(l1.get('scenario')))} 신호를 {'·'.join(agree)}도 뒷받침합니다."
    else:
        xv = (f"L1의 {q(ko(l1.get('scenario')))} 신호를 L2({ko(l2.get('scenario'))})와 L3({ko(l3.get('scenario'))})가 "
              "뒷받침하지 않아, 신뢰도를 낮춰 반영합니다.")
    return out, xv


def l2_text(l2: Dict, l1: Dict, l3: Dict) -> Tuple[List[str], str]:
    out = []
    sp = l2.get("short_pct_latest")
    if sp is None:
        return ["FINRA 공매도 데이터를 찾지 못해 이 레이어는 판단을 보류합니다."], "교차검증할 수 없습니다."
    z = l2.get("anomaly_z") or 0
    out.append(f"장외 공매도 비중은 <b>{pc(sp, sign=False)}</b>입니다(FINRA TRF 보고 기준). 14일 평균은 "
               f"{pc(l2.get('short_avg_14d'), sign=False)}, 평균 대비 편차(z)는 {z:+.2f}, 추세는 하루 {l2.get('short_slope', 0):+.2f}%p로 "
               f"{kt(KO_SLOPE, l2.get('slope_dir'))}입니다. 거래가 많은 종목은 마켓메이커 헤지 때문에 40~55%가 흔하므로, "
               "수치 자체보다 평소 수준에서 얼마나 벗어났는지가 중요합니다.")
    out.append(f"공매도 물량은 장외 {sh(l2.get('short_shares'))}주이며, 전체 거래량 기준으로는 약 {sh(l2.get('est_short_total'))}주로 추정됩니다.")
    fee = l2.get("ctb_fee")
    if fee is not None:
        fd, ad = l2.get("ctb_delta_pct"), l2.get("avail_delta_pct")
        avail = "1,000만 주 이상" if l2.get("available_capped") else f"{sh(l2.get('shares_available'))}주"
        fds = f"전일 대비 <b>{pc(fd)}</b> 변했고" if fd is not None else "전일 대비 변화는 이력이 쌓이면 계산되며"
        ads = f"(전일 대비 {pc(ad)})" if ad is not None else ""
        out.append(f"대차 수수료는 <b>{n(fee)}%</b>({kt(KO_HTB, l2.get('htb_class'))})이며 {fds}, 대여 가능 물량은 {avail}{ads}입니다. "
                   f"출처: {l2.get('borrow_source')}.")
    else:
        out.append("대차 수수료와 대여 가능 물량을 받지 못했습니다. 공매도 성격을 가르는 핵심 변수가 빠져 있어 판단의 신뢰도를 낮춰 반영합니다.")
    si = l2.get("short_interest")
    if si:
        out.append(f"공매도 잔고(SI, {si['settlement_date']} 결제 기준)는 {sh(si.get('shares'))}주로 직전 대비 {pc(si.get('change_pct'))}, "
                   f"유통주식의 {pc(si.get('pct_float'), 2, sign=False)}이며, 전량 상환에는 약 {n(si.get('days_to_cover'))}일(Days to Cover)이 걸립니다.")
    case = l2.get("case")
    out.append(f"공매도 성격은 <b>{jo(KO_CASE.get(case, case or 'N/A'), '으로')}</b> 판단합니다. 근거: {', '.join(l2.get('case_basis') or ['근거 부족'])}.")
    if l2.get("low_ctb_paradox"):
        out.append("<b>저CTB 역설</b>: 대차 수수료는 1% 미만으로 낮은데 대여 가능 물량은 줄고 있습니다. 적은 비용으로 가격을 누르는 "
                   "공매도 포지션이 미리 쌓이고 있을 수 있어, 기술적 반등 신호만 보고 진입하면 가격이 오래 묶일 위험이 있습니다.")
    l1s = l1.get("signal")
    if case == "CASE_2_DIRECTIONAL_SHORT" and l1s == "BULLISH":
        xv = "L1은 매집, L2는 방향성 공매도로, 매수 측과 공매도 측이 맞서고 있는 상충 신호입니다."
    elif case == "CASE_1_MM_DELTA_HEDGE":
        why = "대차 비용 변화 없이" if l2.get("ctb_delta_pct") is not None else "옵션 거래량 증가와 함께"
        xv = f"공매도 증가는 {why} 나타난 옵션 헤지 성격이므로 약세 신호로 보지 않고, L3 옵션 포지션을 우선합니다."
    elif case == "CASE_3_SYNTHETIC_HEDGE":
        xv = "기관 순매수와 함께 나타난 공매도로, 보유 물량을 헤지하는 성격이 강해 순수한 약세 신호로 보지 않습니다."
    else:
        xv = (f"L1({ko(l1.get('scenario'))})과 L2({ko(l2.get('scenario'))})는 "
              + ("같은 방향을 가리킵니다." if l1s == l2.get("signal") and l1s != "NEUTRAL"
                 else "서로 독립적인 신호로, 각각의 가중치대로 반영합니다."))
    return out, xv


def l3_text(l3: Dict, l1: Dict, l4: Dict, price: float) -> Tuple[List[str], str]:
    if l3.get("scenario") == "NO_DATA":
        return ["옵션 체인을 받지 못해 옵션 레이어는 판단을 보류합니다."], "교차검증할 수 없습니다."
    out = []
    mp, dte = l3.get("max_pain"), l3.get("dte")
    out.append(f"이번 주 만기({l3.get('expiry')}, {dte}일 남음)의 Max Pain은 <b>{usd(mp)}</b>(현재가 대비 "
               f"{pc(-(l3.get('max_pain_dist_pct') or 0), 2)})입니다. "
               + (("만기가 가까워 가격이 이 수준으로 끌려갈 가능성이 있습니다." if abs(l3.get("max_pain_dist_pct") or 0) <= 10
                   else "만기가 가깝지만 현재가와 차이가 커서 실제로 끌어당기는 힘은 제한적입니다.") if (dte or 99) <= 5
                  else "만기까지 여유가 있어 방향을 가늠하는 참고치로 봅니다.")
               + (f" 월물({l3.get('monthly_expiry')}) Max Pain은 {usd(l3.get('monthly_max_pain'))}입니다."
                  if l3.get("monthly_max_pain") and l3.get("monthly_expiry") != l3.get("expiry") else ""))
    flip, reg = l3.get("flip_zone"), l3.get("gamma_regime")
    ng = l3.get("net_gex")
    if flip:
        above = price > flip
        out.append(f"Net GEX는 1% 움직임당 {money_big(ng)}이고 감마 플립은 <b>{usd(flip)}</b>입니다. 현재가는 플립보다 "
                   f"{ab((price - flip) / flip * 100, 2)} {'위' if above else '아래'}에 있어 "
                   + ("<b>양(+)감마 구간</b>입니다. 딜러가 오르면 팔고 내리면 사는 방식으로 헤지하므로 급등락이 억제되는 경향이 있습니다."
                      if above else
                      "<b>음(−)감마 구간</b>입니다. 딜러 헤지가 가격이 움직이는 방향으로 나오기 때문에 변동성이 커지기 쉽습니다."))
    elif reg:
        why = {"NO_FLIP_ALL_POSITIVE": "현재가 ±50% 범위 전체가 양(+)감마여서, 급락하더라도 딜러의 받쳐 주는 매수가 나오는 구조입니다.",
               "NO_FLIP_ALL_NEGATIVE": "현재가 ±50% 범위 전체가 음(−)감마여서, 어느 방향으로 움직이든 딜러 헤지가 움직임을 키우는 구조입니다.",
               "NO_FLIP_IN_RANGE": "탐색 범위 안에서 감마가 0을 지나는 지점이 없습니다."}.get(l3.get("flip_status") or "",
                                                                                 "플립 지점이 확인되지 않았습니다.")
        out.append(f"Net GEX는 1% 움직임당 {money_big(ng)}이며 감마 플립은 없습니다. {why}")
    cw, pw = l3.get("call_wall"), l3.get("put_wall")
    if cw and pw:
        out.append(f"콜월(<b>{usd(cw)}</b>, 현재가 대비 {pc((cw - price) / price * 100)})은 저항, 풋월(<b>{usd(pw)}</b>, "
                   f"{pc((pw - price) / price * 100)})은 지지 역할을 합니다. 두 가격 사이에서는 딜러 헤지가 가격을 붙잡아 두는 경향이 있습니다.")
    elif cw or pw:
        out.append(f"콜월 {usd(cw)} · 풋월 {usd(pw)}입니다.")
    pvo, pvv, adj = l3.get("pc_oi"), l3.get("pc_vol"), l3.get("pc_vol_adj")
    adj_s = f"(당일 만기물 제외 시 {n(adj)})" if adj is not None and pvv is not None and abs(adj - pvv) > 0.01 else ""
    out.append(f"풋/콜 비율은 미결제약정(OI) 기준 {n(pvo)}, 거래량 기준 {n(pvv)}{adj_s}입니다. "
               + ("OI보다 거래량에서 콜 비중이 높아 신규 콜 매수가 늘고 있습니다." if (pvv or 1) < (pvo or 1) * 0.8 else
                  "OI보다 거래량에서 풋 비중이 높아 신규 하락 베팅이나 헤지가 늘고 있습니다." if (pvv or 1) > (pvo or 1) * 1.2 else
                  "기존 포지션과 신규 거래의 구성이 비슷합니다."))
    sk = l3.get("skew_25d")
    if sk is not None:
        out.append(f"25델타 스큐(풋 IV − 콜 IV)는 {sk:+.1f}%p, ATM IV는 {pc((l3.get('atm_iv') or 0) * 100, sign=False)}, CBOE IV30은 "
                   f"{pc((l3.get('iv30') or 0) * 100, sign=False) if l3.get('iv30') else 'N/A'}, 기간구조는 {kt(KO_TERM, l3.get('term_structure'))}입니다. "
                   + ("콜 IV가 풋보다 높은 드문 상황으로, 상승에 베팅하는 수요가 강합니다." if sk < 0 else
                      "하락에 대비한 보험 수요가 과열된 수준입니다." if sk > 8 else "하락 보험료는 일반적인 수준입니다."))
    em = l3.get("expected_move")
    if em:
        out.append(f"ATM 스트래들 가격으로 본 이번 주 예상 변동폭은 <b>±{usd(em)} (±{n(l3.get('expected_move_pct'))}%)</b>로, "
                   f"{usd(price - em)}~{usd(price + em)} 범위입니다.")
    if l3.get("binary_event"):
        out.append(f"<b>이벤트 급변동 반영</b>: {'; '.join(l3.get('binary_basis') or [])}. 옵션 시장이 큰 폭의 갭 가능성을 가격에 반영하고 있습니다.")
    pin = l3.get("pinning")
    if pin:
        out.append(f"직전 만기({pin['expiry']}) 종가는 전일 계산한 Max Pain({usd(pin['max_pain'])})과 {n(pin['dist_pct'])}% 차이가 나, "
                   + {"PINNING_SUCCESS": "핀닝이 성공했습니다. 마켓메이커의 헤지 비용이 가장 낮은 지점에서 마감했습니다.",
                      "PINNING_ATTEMPT": "핀닝에 근접했습니다(±0.5% 이내).",
                      "BREAKAWAY": "핀닝에서 벗어났습니다. 방향성 자금이나 새로운 정보가 옵션 헤지보다 강하게 작용한 것으로 봅니다."}[pin["status"]])
    if l3.get("oi_trend_pct") is not None:
        out.append(f"60일 안에 만기가 돌아오는 옵션의 전체 OI는 최근 5거래일 동안 {pc(l3['oi_trend_pct'])} 변했습니다"
                   f"(풋 OI {pc(l3.get('put_oi_trend_pct'))}).")
    l1s = l1.get("signal")
    if l1s == l3.get("signal") and l1s != "NEUTRAL":
        xv = f"옵션 구조가 L1의 {q(ko(l1.get('scenario')))} 신호와 같은 방향을 가리켜, 다크풀과 옵션이 일치합니다."
    elif flip and price > flip and l1s == "BEARISH":
        xv = "L1은 분산 신호지만 양감마 구간이 하락을 완충하고 있어, 플립을 하향 이탈하기 전까지는 하락 속도가 제한될 수 있습니다."
    elif flip and price < flip and l1s == "BULLISH":
        xv = "L1 매집 신호와 음감마 구간이 함께 나타납니다. 플립을 회복하면 변동성이 줄면서 상승이 안정될 수 있습니다."
    else:
        xv = (f"L1은 {q(ko(l1.get('scenario')))}, L3는 {q(ko(l3.get('scenario')))}입니다. 옵션 구조는 주로 가격이 움직일 범위를 "
              "알려 줄 뿐, 그 자체로 방향을 정하는 힘은 크지 않습니다.")
    return out, xv


def l4_text(l4: Dict, l1: Dict, l3: Dict) -> Tuple[List[str], str]:
    if l4.get("scenario") == "NO_DATA":
        return ["일봉 데이터가 부족합니다."], "교차검증할 수 없습니다."
    p = l4["current_price"]
    ema_up = (l4.get("ema9") or 0) > (l4.get("ema21") or 0)
    wt = {"EXPANDING": "밴드는 넓어지는 중이고,", "CONTRACTING": "밴드는 좁아지는 중이고,"}.get(l4.get("bb_width_trend"), "밴드 폭 변화는 크지 않고,")
    pct = l4.get("bb_width_pctile")
    bp = (l4.get("bb_position") or 0) * 100
    where = ("밴드 하단 아래로 벗어나 있습니다" if bp < 0 else "밴드 상단 위로 벗어나 있습니다" if bp > 100
             else f"밴드 안 {bp:.0f}% 지점({kt(KO_BAND, l4.get('band_status'))})에 있습니다")
    out = [f"이동평균은 <b>{kt(KO_MA, l4.get('ma_alignment'))}</b>입니다. 종가 {usd(p)}, 20일선 {usd(l4.get('sma20'))}, "
           f"50일선 {usd(l4.get('sma50'))}, 200일선 {usd(l4.get('sma200'))}이며, 단기선 EMA9({usd(l4.get('ema9'))})가 "
           f"EMA21({usd(l4.get('ema21'))})보다 {'위' if ema_up else '아래'}에 있습니다.",
           f"볼린저밴드 폭은 {n(l4.get('bb_width_pct'))}%(최근 1년 백분위 {n(pct, 0)})입니다. {wt} 가격은 {where}."
           + (" 밴드 폭이 최근 1년 중 가장 좁은 20% 구간이라 변동성이 눌려 있고, 곧 한쪽으로 크게 움직일 가능성이 있습니다."
              if (pct if pct is not None else 100) <= 20 else
              " 밴드 폭이 최근 1년 중 가장 넓은 20% 구간이라 추세가 강하게 진행 중이거나 과열된 상태입니다."
              if (pct or 0) >= 80 else ""),
           f"RSI(14)는 {n(l4.get('rsi14'), 1)}, ATR(14)은 {usd(l4.get('atr14'))}(가격의 {n(l4.get('atr_pct'))}%)이며, "
           f"거래량은 30일 평균의 {n(l4.get('vol_ratio30'))}배입니다.",
           f"지지선은 {lv_pair(l4.get('immediate_support'), l4.get('key_support'), l4.get('support_source'))}, "
           f"저항선은 {lv_pair(l4.get('immediate_resistance'), l4.get('key_resistance'), l4.get('resistance_source'))}이며, "
           f"최근 60일 동안 거래가 가장 많았던 가격대는 {usd(l4.get('poc60'))}입니다."
           + (" <b>현재가가 최근 1년 스윙 저점을 모두 밑돌고 있어</b> 차트상 뚜렷한 지지가 없는 구간입니다. "
              "이동평균, 거래량 밀집대, 52주 저점을 지지선으로 대신 사용했습니다." if l4.get("below_all_swing_lows") else "")]
    if l1.get("signal") == "BULLISH" and l4.get("signal") == "BEARISH":
        xv = "차트는 약세지만 다크풀에서는 매집이 보입니다. 차트가 수급 변화를 늦게 반영하는 축적 국면일 수 있습니다."
    elif l1.get("signal") == "BEARISH" and l4.get("signal") == "BULLISH":
        xv = "차트는 강세지만 다크풀에서는 분산이 보입니다. 상승 중에 물량이 나오고 있어 추세의 신뢰도를 낮춰 봅니다."
    else:
        xv = (f"차트({ko(l4.get('scenario'))})는 L1({ko(l1.get('scenario'))}), L3({ko(l3.get('scenario'))})와 "
              + ("같은 방향입니다." if l4.get("signal") == l1.get("signal")
                 else "일부 엇갈리지만, 차트 가중치(15%)가 가장 낮아 영향은 제한적입니다."))
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
    add("거래량이 30일 평균의 150% 초과", "BREAKOUT", None if vr is None else vr > 1.5, f"본격적인 움직임의 시작 신호 (현재 {n(vr)}배)")
    add("볼린저밴드 폭이 1년 중 하위 20%", "SETUP", None if l4.get("bb_width_pctile") is None else l4["bb_width_pctile"] <= 20,
        f"변동성 확대 전조 (백분위 {n(l4.get('bb_width_pctile'), 0)})")
    if l3.get("flip_zone"):
        add(f"종가가 감마 플립 {usd(l3['flip_zone'])} 위", "STABILIZER", p > l3["flip_zone"], "위에서는 변동성 억제, 이탈하면 변동성 확대")
    if l3.get("call_wall"):
        add(f"종가가 콜월 {usd(l3['call_wall'])} 돌파", "BULLISH", p > l3["call_wall"], "딜러 헤지 매수로 상승이 빨라질 수 있음")
    if l3.get("put_wall"):
        add(f"종가가 풋월 {usd(l3['put_wall'])} 아래", "BEARISH", p < l3["put_wall"], "딜러 헤지 매도로 하락이 빨라질 수 있음")
    if l3.get("max_pain"):
        add(f"종가가 Max Pain {usd(l3['max_pain'])} 아래", "BULLISH" if (l3.get("dte") or 99) <= 5 else "INFO",
            p < l3["max_pain"], f"만기가 가까울수록 Max Pain 쪽으로 끌리는 힘 (현재 {'위' if p < l3['max_pain'] else '아래'} 방향)")
    add("5일 순체결(Net OBV)이 플러스", "BULLISH", None if l1.get("delta_total") is None else l1["delta_total"] > 0, "매수 체결 우위")
    add("다크풀 비중 40% 초과가 2일 이상", "ABSORPTION", None if l1.get("dp_days_over_40") is None else l1["dp_days_over_40"] >= 2,
        f"장외 매수 흡수 지속 (최근 5일 중 {l1.get('dp_days_over_40', 'N/A')}일)")
    add("공매도 비중 추세 하락", "BULLISH", None if l2.get("slope_dir") is None else l2["slope_dir"] == "FALLING", "숏커버링 진행")
    add("대차 수수료 5% 초과", "SQUEEZE", None if l2.get("ctb_fee") is None else l2["ctb_fee"] > 5, "대차 부담 증가로 숏스퀴즈 조건 형성")
    add("대차 수수료가 전일 대비 5% 이상 상승", "BEARISH", None if l2.get("ctb_delta_pct") is None else l2["ctb_delta_pct"] > 5, "공매도 수요 증가")
    if l4.get("sma50"):
        add(f"종가가 50일선 {usd(l4['sma50'])} 위", "BULLISH", p > l4["sma50"], "중기 추세 유지")
    return t


def risks(a: Dict) -> List[str]:
    l3, q_ = a["l3"], a["quality"]
    pats, scen = a["patterns"], a["scenarios"]
    r = []
    if "LOW_CTB_PARADOX" in pats:
        r.append("[저CTB 역설] 낮은 대차 비용으로 공매도 포지션이 쌓이는 구조입니다. 기술적 반등 신호만 보고 진입하면 가격이 오래 묶일 수 있습니다.")
    if l3.get("binary_event"):
        r.append("[이벤트 급변] 옵션 시장이 큰 폭의 갭 가능성을 반영하고 있습니다. 방향과 관계없이 갭 위험이 있어 포지션 규모 관리가 필요합니다.")
    if l3.get("flip_zone") and abs(a["meta"]["price"] - l3["flip_zone"]) / l3["flip_zone"] < 0.015:
        r.append(f"[구간 전환] 현재가가 감마 플립({usd(l3['flip_zone'])})과 1.5% 이내로 가까워, 작은 움직임에도 변동성 성격이 바뀔 수 있습니다.")
    if scen.get("macro") == "RESTRICTED":
        r.append("[거시 역풍] 유동성이 제한된 환경이라, 상방 시나리오는 모델 결과보다 보수적으로 볼 필요가 있습니다.")
    if (scen.get("dispersion") or 0) > 0.35:
        r.append("[신호 엇갈림] 레이어 간 점수 차이가 커서 한 방향으로 확신하기 어렵습니다. 중립 시나리오의 비중을 높여 봅니다.")
    earn = [e for e in a["events"] if e["kind"] == "earnings"]
    if earn:
        r.append(f"[실적 발표] {earn[0]['date']}(D-{earn[0]['days_until']})에 실적 발표가 예정되어 있습니다. "
                 "가이던스가 바뀌면 수급 가정을 처음부터 다시 점검해야 합니다.")
    for k, L in q_["layers"].items():
        if L["status"] != "OK" and L["missing"]:
            r.append(f"[데이터 공백 · {k.upper()}] {', '.join(L['missing'][:4])} 데이터가 없어 해당 레이어의 신뢰도를 낮춰 반영했습니다.")
    if not r:
        r.append("[돌발 변수] 예상하지 못한 공시나 뉴스가 나오면 이 분석의 전제가 달라질 수 있습니다.")
    return r[:7]


# ─── 헤드라인 · 3줄 요약 · 핵심 결론 ─────────────────────────────────────────
def paradoxes(a: Dict) -> List[str]:
    """'짧은 요약 — 설명' 형식. 요약은 헤드라인에, 전체는 결론에 쓴다."""
    l1, l2, l3, l4 = a["l1"], a["l2"], a["l3"], a["l4"]
    p = a["meta"]["price"]
    out = []
    inst, ret_ = l1.get("delta_inst"), l1.get("delta_retail")
    adv = l1.get("avg_vol20") or 0
    if inst is not None and ret_ is not None and adv and inst * ret_ < 0 and abs(inst) / adv >= 0.03 and abs(ret_) / adv >= 0.01:
        out.append("기관 매수 · 리테일 매도 — 개인이 내놓는 물량을 기관이 받아 가는 손바뀜입니다" if inst > 0 else
                   "기관 매도 · 리테일 매수 — 기관이 개인에게 물량을 넘기는 손바뀜입니다")
    if l1.get("divergence") == "BEARISH_DIVERGENCE":
        out.append("가격은 오르는데 순체결은 매도 우위 — 상승 과정에서 매도 물량이 꾸준히 나오고 있습니다")
    if l1.get("divergence") == "BULLISH_DIVERGENCE":
        out.append("가격은 내리는데 순체결은 매수 우위 — 하락을 이용해 조용히 사들이는 흐름으로 보입니다")
    if l2.get("case") == "CASE_1_MM_DELTA_HEDGE" and (l2.get("anomaly_z") or 0) > 1 and l2.get("ctb_delta_pct") is not None:
        out.append("공매도 비중 급등에도 대차 비용은 그대로 — 약세 베팅보다는 옵션 매도에 따른 기계적인 헤지로 보입니다")
    if l2.get("low_ctb_paradox"):
        out.append("대차 비용은 낮은데 빌릴 수 있는 주식은 감소 — 적은 비용으로 가격을 누르는 공매도 포지션이 쌓이고 있을 수 있습니다")
    if (l1.get("dp_ratio") or 1) > 1.15 and (l1.get("delta_total") or 0) < 0:
        out.append("장외 비중 확대와 순매도가 동시에 — 다크풀을 통해 물량을 내보내는 흐름으로 보입니다")
    if (l1.get("dp_ratio") or 1) > 1.15 and (l1.get("delta_total") or 0) > 0:
        out.append("장외 비중 확대와 순매수가 동시에 — 다크풀을 통해 물량을 모으는 흐름으로 보입니다")
    if l3.get("flip_zone") and p > l3["flip_zone"] and l3.get("max_pain") and abs(p - l3["max_pain"]) / p < 0.01:
        out.append("양감마 구간에서 Max Pain에 근접 — 딜러 헤지가 가격을 현재 수준에 붙잡아 두기 쉬운 구조입니다")
    if l3.get("flip_zone") and p < l3["flip_zone"]:
        out.append("음감마 구간 진입 — 딜러 헤지가 가격 움직임을 키우기 쉬운 구간입니다")
    if (l3.get("pc_oi") or 0) > 1.2 and l1.get("signal") == "BULLISH":
        out.append("풋 OI가 많은 가운데 다크풀 매집 — 현물을 사 모으면서 하락에 대비한 보험도 함께 사는 모습입니다")
    if l4.get("signal") == "BULLISH" and l1.get("signal") == "BEARISH":
        out.append("차트는 강세인데 체결은 분산 — 추세를 따라 들어온 매수가 기존 보유 물량을 받아내고 있습니다")
    return out


def _split(px: str) -> Tuple[str, str]:
    a, _, b = px.partition(" — ")
    return a, b


def _top(s: Dict) -> Tuple[str, str, float]:
    return max((("A", "상승", s["A_bullish"]), ("B", "중립", s["B_neutral"]), ("C", "하락", s["C_bearish"])), key=lambda x: x[2])


def headline(a: Dict) -> str:
    code, name, prob = _top(a["scenarios"])
    px = paradoxes(a)
    l3 = a["l3"]
    parts = [f"[{code} {name} {prob:.0f}%]"]
    if px:
        parts.append(_split(px[0])[0])
    if l3.get("flip_zone"):
        parts.append(f"감마 플립 {usd(l3['flip_zone'])} {'위' if a['meta']['price'] > l3['flip_zone'] else '아래'}")
    if l3.get("call_wall") and l3.get("put_wall"):
        d = 0 if a["meta"]["price"] >= 20 else 2
        parts.append(f"풋월~콜월 {usd(l3['put_wall'], d)}–{usd(l3['call_wall'], d)}")
    return " · ".join(parts)


def three_lines(a: Dict) -> List[str]:
    l1, l2, l3 = a["l1"], a["l2"], a["l3"]
    p = a["meta"]["price"]
    one = (f"다크풀 비중 {pc(l1.get('dp_pct'), sign=False)}(20일 평균의 {n(l1.get('dp_ratio'))}배), 5일 순체결 {ssh(l1.get('delta_total'))}주, "
           f"{kt(KO_DIV, l1.get('divergence'))} — {jo(q(ko(l1.get('scenario'))), '으로')} 판단합니다.")
    fee = l2.get("ctb_fee")
    two = (f"공매도 비중 {pc(l2.get('short_pct_latest'), sign=False)}(z {(l2.get('anomaly_z') or 0):+.1f}, 추세 {kt(KO_SLOPE, l2.get('slope_dir'))}), "
           + (f"대차 수수료 {n(fee)}%(전일 대비 {pc(l2.get('ctb_delta_pct'))})" if fee is not None else "대차 수수료 미수집")
           + f" — {jo(q(KO_CASE.get(l2.get('case'), l2.get('case') or 'N/A')), '으로')} 판단합니다.")
    if a["meta"].get("is_index"):
        one = "지수에는 다크풀 레이어가 적용되지 않습니다."
        two = "지수에는 공매도 레이어가 적용되지 않습니다."
    flip = l3.get("flip_zone")
    reg = ("양감마" if flip and p > flip else "음감마" if flip else kt(KO_REGIME, l3.get("gamma_regime")))
    three = (f"Max Pain {usd(l3.get('max_pain'))}(만기까지 {l3.get('dte')}일), 감마 플립 {usd(flip) if flip else '없음'}({reg}), "
             f"콜월 {usd(l3.get('call_wall'))} / 풋월 {usd(l3.get('put_wall'))} — {jo(q(ko(l3.get('scenario'))), '으로')} 판단합니다.")
    return [one, two, three]


def conclusions(a: Dict) -> List[str]:
    l1, l2, l3, l4 = a["l1"], a["l2"], a["l3"], a["l4"]
    s, ph = a["scenarios"], a["phase"]
    p = a["meta"]["price"]
    T = a["meta"]["ticker"]
    px = paradoxes(a)
    code = _top(s)[0]
    intent = {"A": "물량을 모은 뒤 상승을 준비하는", "B": "방향을 정하지 않고 가격을 일정 범위에 묶어 두는",
              "C": "물량을 내보내며 가격을 낮추는"}[code]
    contrib = s.get("contributions") or {}
    lead = max(("L1", "L2", "L3", "L4"), key=lambda k: abs(contrib.get(k, 0)))
    if px:
        h, x = _split(px[0])
        first = f"{T} 분석에서 가장 눈에 띄는 신호는 {q(h)}입니다. {x}. "
    else:
        first = f"{T} 분석에서는 레이어 사이에 뚜렷하게 엇갈리는 신호가 없습니다. "
    p1 = (first
          + (f"이와 함께 {q(_split(px[1])[0])} 신호도 보입니다. " if len(px) > 1 else "")
          + f"4개 레이어의 가중 점수는 {s['raw_score']:+.3f}(분산 {s.get('dispersion', 0):.2f})이며, 시나리오 확률은 상승 {s['A_bullish']}%, "
          f"중립 {s['B_neutral']}%, 하락 {s['C_bearish']}%입니다. 점수에 가장 큰 영향을 준 것은 {LAYER_KO[lead]}"
          f"({contrib.get(lead, 0):+.3f})이고, 거시 유동성은 {q(KO_ENV.get(s.get('macro'), s.get('macro') or 'N/A'))} 환경으로 확률을 "
          f"{contrib.get('macro', 0) * 100:+.1f}%p 조정했습니다. 종합하면 대형 자금은 <u>{intent} 단계</u>에 있는 것으로 보입니다.")
    flip = l3.get("flip_zone")
    cw, pw = l3.get("call_wall"), l3.get("put_wall")
    p2 = (f"레이어별로 보면, 다크풀 비중 {pc(l1.get('dp_pct'), sign=False)}와 5일 순체결 {ssh(l1.get('delta_total'))}주는 "
          f"{q(ko(l1.get('scenario')))} 흐름을 보여 주고, 공매도는 {q(KO_CASE.get(l2.get('case'), l2.get('case') or 'N/A'))} 성격입니다. "
          + ("옵션에서는 " + (f"현재가가 감마 플립 {usd(flip)} {'위의 양감마(변동성 억제)' if p > flip else '아래의 음감마(변동성 확대)'} 구간에 있고"
                            if flip else "감마 플립이 관측 범위 밖에 있어 감마 구간이 한 가지로 유지되고")
             + (f", 풋월과 콜월({usd(pw)}~{usd(cw)})이 단기 가격 범위를 정하고 있습니다. " if cw and pw else " 있습니다. "))
          + f"차트는 {q(ko(l4.get('scenario')))}(이동평균 {kt(KO_MA, l4.get('ma_alignment'))})이며, 다른 레이어의 신호를 뒤따라 확인하는 역할을 합니다. "
          "순체결 방향이 바뀌거나, 대차 비용이 급등해 공매도가 방향성 베팅으로 바뀌면 이 해석은 수정해야 합니다.")
    trig = []
    if flip:
        trig.append(f"종가 기준 감마 플립 {usd(flip)} {'하향 이탈(음감마 전환, 하락 가속)' if p > flip else '회복(양감마 전환, 안정화)'}")
    if cw:
        trig.append(f"콜월 {usd(cw)} 돌파(딜러 헤지 매수 유발)")
    if pw:
        trig.append(f"풋월 {usd(pw)} 하회(딜러 헤지 매도 유발)")
    trig.append("거래량이 30일 평균의 150% 이상으로 늘면서 볼린저밴드 확장(본격적인 움직임 시작)")
    if l2.get("ctb_fee") is not None:
        trig.append(f"대차 수수료({n(l2['ctb_fee'])}%)가 전일 대비 5% 이상 계속 상승(공격적인 공매도로 전환)")
    cats = ph.get("catalysts") or []
    cat_s = f" 일정으로는 {cats[0]['date']} {cats[0]['event']} 일정이 Phase 2에서 Phase 3으로 넘어가는 기준 시점입니다." if cats else ""
    p3 = ("가장 먼저 확인할 트리거는 다음과 같습니다. " + "; ".join(trig) + "." + cat_s +
          f" Phase 3({ph['p3_date']})의 기본 방향은 {kt(KO_DIR, ph.get('p3_direction'))}이며, 1차 목표는 위로 {usd(ph.get('p3_target_hi'))}, "
          f"아래로 {usd(ph.get('p3_target_lo'))}입니다. 트리거가 현재 가설과 반대 방향으로 나오면 시나리오 전체를 다시 계산합니다.")
    return [p1, p2, p3]


# ─── 한줄평 · 수급 주체 보드 · 다음 확인 포인트 · 섹션 핵심 ─────────────────────
def _ret(a: Dict, n_: int = 5) -> Optional[float]:
    oh = a.get("ohlcv") or []
    if len(oh) <= n_:
        return None
    base = oh[-1 - n_]["close"]
    return (oh[-1]["close"] / base - 1) * 100 if base else None


def _gamma_clause(a: Dict) -> Tuple[str, str, str]:
    """(원인형 '…쉬워', 양보형 '…쉽지만', 성격) — 성격: vol(변동성 확대)·range(범위 고정)·calm(안정)·gap(이벤트)."""
    l3, p = a["l3"], a["meta"]["price"]
    flip, reg = l3.get("flip_zone"), l3.get("gamma_regime")
    neg = (flip and p < flip) or (not flip and reg == "NEGATIVE")
    pos = (flip and p > flip) or (not flip and reg == "POSITIVE")
    cw, pw = l3.get("call_wall"), l3.get("put_wall")
    d = 0 if p >= 20 else 2
    if l3.get("binary_event"):
        return "옵션 시장이 큰 폭의 갭에 대비하고 있어", "옵션 시장이 큰 폭의 갭에 대비하고 있지만", "gap"
    if neg:
        return "음감마 구간이라 변동성이 커지기 쉬워", "음감마 구간이라 변동성이 커지기 쉽지만", "vol"
    if pos and cw and pw and pw < p < cw:
        r = f"양감마 구간이라 {usd(pw, d)}~{usd(cw, d)} 범위에 묶이기 "
        return r + "쉬워", r + "쉽지만", "range"
    if pos:
        return "양감마 구간이라 급등락이 억제되는 편이어서", "양감마 구간이라 급등락이 억제되는 편이지만", "calm"
    return "", "", ""


def one_liner(a: Dict) -> str:
    """종목 한줄평: 누가(수급 주체) · 구조(공매도/감마) · 결론(우세 시나리오)."""
    l1, l2 = a["l1"], a["l2"]
    s = a["scenarios"]
    code, name, prob = _top(s)
    verdict = {"A": f"상승 우위({prob:.0f}%)입니다.", "B": f"방향이 확인되기 전까지는 중립({prob:.0f}%)이 우세합니다.",
               "C": f"하락 우위({prob:.0f}%)입니다."}[code]
    r5 = _ret(a)
    rs = "" if r5 is None else f"{abs(r5):.0f}%"
    # 구조 절: 공매도 특이 → 감마.  결론과 같은 방향이면 원인형, 어긋나면 양보형.
    if l2.get("low_ctb_paradox"):
        g_c, g_x, g_t = "낮은 비용의 공매도가 쌓이고 있어", "낮은 비용의 공매도가 쌓이고 있지만", "bear"
    elif l2.get("case") == "CASE_2_DIRECTIONAL_SHORT":
        g_c, g_x, g_t = "방향성 공매도가 늘고 있어", "방향성 공매도가 늘고 있지만", "bear"
    else:
        g_c, g_x, g_t = _gamma_clause(a)
    fits = {"bear": code in ("B", "C"), "range": code == "B", "calm": code in ("A", "B"), "vol": code != "A", "gap": True}.get(g_t, True)
    g = g_c if fits else g_x
    if a["meta"].get("is_index"):
        mv = f"5일간 {pc(r5)} 움직였고" if r5 is not None else "최근 흐름은 뚜렷하지 않고"
        return f"{mv}, {g} {verdict}" if g else f"{mv}, {verdict}"
    inst = l1.get("delta_inst")
    v = inst if inst is not None else l1.get("delta_total")
    adv = l1.get("avg_vol20") or 0
    st = (v / adv) if (v is not None and adv) else 0
    up, dn = (r5 or 0) > 2, (r5 or 0) < -2
    if v is None or abs(st) < 0.03:
        stem, side = f"{'기관' if inst is not None else '순체결'} 수급은 뚜렷한 방향이 없", 0
    elif inst is not None:
        x = sh(abs(v))
        side = 1 if v > 0 else -1
        stem = (f"주가가 5일간 {rs} 밀리는 동안 기관은 {x}주를 사들였" if side > 0 and dn else
                f"기관이 5일간 {x}주를 사들이며 {rs} 상승을 이끌었" if side > 0 and up else
                f"기관이 5일간 {x}주를 조용히 사들였" if side > 0 else
                f"주가가 5일간 {rs} 오르는 동안 기관은 {x}주를 팔았" if up else
                f"기관이 5일간 {x}주를 팔며 {rs} 하락을 주도했" if dn else
                f"기관이 5일간 {x}주를 덜어냈")
    else:
        x = sh(abs(v))
        side = 1 if v > 0 else -1
        stem = (f"주가가 5일간 {rs} 밀리는 동안 순체결은 {x}주 매수 우위였" if side > 0 and dn else
                f"5일간 순매수 체결 {x}주와 함께 {rs} 올랐" if side > 0 and up else
                f"5일간 순체결은 {x}주 매수 우위였" if side > 0 else
                f"주가가 5일간 {rs} 오르는 동안 순체결은 {x}주 매도 우위였" if up else
                f"5일간 순매도 체결 {x}주와 함께 {rs} 내렸" if dn else
                f"5일간 순체결은 {x}주 매도 우위였")
    contra = (side > 0 and code == "C") or (side < 0 and code == "A") or (side != 0 and code == "B")
    conn = "지만" if contra else "고"
    if conn == "지만" and g and g == g_x:      # '~지만, ~지만' 중복 방지
        conn = "고"
    return f"{stem}{conn}, {g} {verdict}" if g else f"{stem}{conn}, {verdict}"


def flow_board(a: Dict) -> List[Dict[str, Any]]:
    """수급 주체별 '무엇을 하고 있나' — actor/action/evidence/tag/kind/strength(-1..1, 방향 없음=None)."""
    l1, l2, l3, l4 = a["l1"], a["l2"], a["l3"], a["l4"]
    p = a["meta"]["price"]
    rows: List[Dict[str, Any]] = []
    adv = l1.get("avg_vol20") or 0

    def clamp(x: float) -> float:
        return max(-1.0, min(1.0, x))

    def add(actor, action, evidence, tag, kind, strength=None):
        rows.append({"actor": actor, "action": action, "evidence": evidence, "tag": tag, "kind": kind,
                     "strength": None if strength is None else round(clamp(strength), 3)})
    if not a["meta"].get("is_index"):
        fl = l1.get("flow")
        inst, ret_, pro = l1.get("delta_inst"), l1.get("delta_retail"), l1.get("delta_pro")
        if inst is not None:
            sti = inst / adv if adv else 0
            act = ("뚜렷한 방향 없음" if abs(sti) < 0.03 else "순매수" if inst > 0 else "순매도")
            add("기관 (대량 체결)", act, f"5일 {ssh(inst)}주 · ADV {pc(sti * 100)} · IAR {n(l1.get('iar'))}",
                "매수" if inst > 0 and abs(sti) >= 0.03 else "매도" if inst < 0 and abs(sti) >= 0.03 else "중립",
                "pos" if inst > 0 and abs(sti) >= 0.03 else "neg" if inst < 0 and abs(sti) >= 0.03 else "warn", sti / 0.15)
        else:
            v = l1.get("delta_total")
            st = (l1.get("obv_strength") or 0)
            add("전체 순체결 (추정)", "순매수" if st > 0.03 else "순매도" if st < -0.03 else "뚜렷한 방향 없음",
                f"5일 {ssh(v)}주 · ADV {pc(st * 100)} (주체별 분해 없음)",
                "매수" if st > 0.03 else "매도" if st < -0.03 else "중립",
                "pos" if st > 0.03 else "neg" if st < -0.03 else "warn", st / 0.15)
        if ret_ is not None:
            str_ = ret_ / adv if adv else 0
            opp = inst is not None and inst * ret_ < 0 and abs(inst) > abs(ret_) * 0.5
            act = ("뚜렷한 방향 없음" if abs(str_) < 0.01 else "순매수" if ret_ > 0 else "순매도")
            if opp and abs(str_) >= 0.01:
                act += " (기관과 반대 → 손바뀜)"
            bj = (fl or {}).get("bjzz_imbalance")
            add("리테일 (소량 체결)", act, f"5일 {ssh(ret_)}주 · 프로 {ssh(pro)}주" + (f" · 서브페니 불균형 {bj:+.2f}" if bj is not None else ""),
                "참고", "muted", None)
        dp, ratio = l1.get("dp_pct"), l1.get("dp_ratio")
        dvd = l1.get("dp_vwap_dist_pct")
        if dp is not None:
            if dvd is not None and abs(dvd) > 0.5:
                act = "종가보다 비싸게 매수" if dvd > 0 else "종가보다 싸게 처분"
                tag, kind, stv = ("상방", "pos", dvd / 2) if dvd > 0 else ("하방", "neg", dvd / 2)
            else:
                act = ("장외 거래 확대" if (ratio or 1) > 1.1 else "장외 거래 축소" if (ratio or 1) < 0.9 else "평소 수준")
                tag, kind, stv = "중립", "warn", None
            add("다크풀 (장외)", act, f"비중 {pc(dp, sign=False)} (20일 평균의 {n(ratio)}배)"
                + (f" · 장외 VWAP 종가 대비 {pc(dvd, 2)}" if dvd is not None else ""), tag, kind, stv)
        if l2.get("short_pct_latest") is not None:
            case = l2.get("case")
            act = {"CASE_1_MM_DELTA_HEDGE": "옵션 헤지용 공매도", "CASE_2_DIRECTIONAL_SHORT": "방향성 공매도 증가",
                   "CASE_3_SYNTHETIC_HEDGE": "보유 물량 헤지", "SHORT_COVERING": "숏커버링(공매도 축소)"}.get(case, "뚜렷한 성격 없음")
            if l2.get("low_ctb_paradox"):
                act = "낮은 비용으로 공매도 축적"
            fee = l2.get("ctb_fee")
            ev = f"비중 {pc(l2.get('short_pct_latest'), sign=False)} (z {(l2.get('anomaly_z') or 0):+.1f})" + \
                 (f" · 대차 수수료 {n(fee)}% (전일 대비 {pc(l2.get('ctb_delta_pct'))})" if fee is not None else " · 대차 수수료 미수집")
            sc = l2.get("score") or 0
            add("공매도", act, ev, "하방" if sc < -0.2 else "상방" if sc > 0.2 else "중립",
                "neg" if sc < -0.2 else "pos" if sc > 0.2 else "warn", sc)
    flip, reg = l3.get("flip_zone"), l3.get("gamma_regime")
    if l3.get("net_gex") is not None:
        neg = (flip and p < flip) or (not flip and reg == "NEGATIVE")
        add("옵션 딜러 (감마)", "변동성 확대 쪽 헤지" if neg else "변동성 억제 쪽 헤지",
            f"{'음' if neg else '양'}감마 · Net GEX {money_big(l3.get('net_gex'))}/1% · "
            + (f"플립 {usd(flip)}" if flip else "플립 없음") + f" · 콜월 {usd(l3.get('call_wall'))} / 풋월 {usd(l3.get('put_wall'))}",
            "변동성↑" if neg else "변동성↓", "warn" if neg else "info", None)
    pvo, pvv, sk = l3.get("pc_oi"), l3.get("pc_vol"), l3.get("skew_25d")
    if pvo is not None and pvv is not None:
        if pvv < pvo * 0.8:
            act, tag, kind, stv = "신규 콜 매수 증가", "상방", "pos", 0.5
        elif pvv > pvo * 1.2:
            act, tag, kind, stv = "신규 풋 매수(하락 대비) 증가", "하방", "neg", -0.5
        else:
            act, tag, kind, stv = "기존 포지션과 비슷한 구성", "중립", "warn", 0.0
        add("옵션 신규 포지션", act, f"풋/콜 거래량 {n(pvv)} vs OI {n(pvo)}" + (f" · 25델타 스큐 {sk:+.1f}%p" if sk is not None else ""),
            tag, kind, stv)
    if l4.get("ma_alignment"):
        sc = l4.get("score") or 0
        add("차트 추세 (후행)", ko(l4.get("scenario")), f"이동평균 {kt(KO_MA, l4.get('ma_alignment'))} · RSI {n(l4.get('rsi14'), 0)} · "
            f"거래량 30일 평균의 {n(l4.get('vol_ratio30'))}배", "상방" if sc > 0.2 else "하방" if sc < -0.2 else "중립",
            "pos" if sc > 0.2 else "neg" if sc < -0.2 else "warn", sc)
    return rows


def watch_points(a: Dict) -> List[Dict[str, str]]:
    """다음 확인 포인트 — 위/아래 가장 가까운 핵심 가격, 거래량 트리거, 가까운 일정."""
    l3, l4, l2 = a["l3"], a["l4"], a["l2"]
    p = a["meta"]["price"]
    # (이름, 가격, 위에 있을 때 설명, 아래에 있을 때 설명)
    cands = [("콜월", l3.get("call_wall"), "종가로 넘어서면 딜러 헤지 매수로 상승이 빨라질 수 있습니다", "아래로 밀리면 상단 저항이 낮아집니다"),
             ("풋월", l3.get("put_wall"), "회복하면 하단 지지가 다시 살아납니다", "종가로 깨지면 딜러 헤지 매도로 하락이 빨라질 수 있습니다"),
             ("감마 플립", l3.get("flip_zone"), "회복하면 양감마로 바뀌며 변동성이 줄어듭니다", "이탈하면 음감마로 바뀌며 변동성이 커집니다"),
             ("1차 저항", l4.get("immediate_resistance"), "종가로 넘어서면 다음 저항까지 길이 열립니다", ""),
             ("1차 지지", l4.get("immediate_support"), "", "종가로 깨지면 다음 지지까지 열립니다"),
             ("Max Pain", l3.get("max_pain"), "만기 주간에 가격이 끌려 올라갈 수 있는 수준입니다", "만기 주간에 가격이 끌려 내려갈 수 있는 수준입니다")]
    ups = sorted([(v, nm, w) for nm, v, w, _ in cands if v and v > p * 1.001 and w])
    dns = sorted([(v, nm, w) for nm, v, _, w in cands if v and v < p * 0.999 and w], reverse=True)
    out = []
    if ups:
        v, nm, why = ups[0]
        out.append({"kind": "pos", "label": "위쪽 첫 관문", "text": f"{nm} {usd(v)} ({pc((v - p) / p * 100)}) — {why}"})
    if dns:
        v, nm, why = dns[0]
        out.append({"kind": "neg", "label": "아래쪽 첫 지지", "text": f"{nm} {usd(v)} ({pc((v - p) / p * 100)}) — {why}"})
    vr = l4.get("vol_ratio30")
    if vr is not None:
        out.append({"kind": "warn", "label": "움직임 시작 신호",
                    "text": f"거래량이 30일 평균의 150%를 넘고 볼린저밴드가 넓어지는지 (현재 {n(vr)}배)"})
    if l2.get("ctb_fee") is not None and not a["meta"].get("is_index"):
        out.append({"kind": "info", "label": "공매도 성격 변화",
                    "text": f"대차 수수료({n(l2['ctb_fee'])}%)가 전일 대비 5% 이상 오르면 공격적인 공매도 전환 신호"})
    ev = [e for e in a.get("events") or [] if e.get("significance") == "HIGH" and (e.get("days_until") or 99) <= 14]
    if ev:
        out.append({"kind": "info", "label": "가까운 주요 일정", "text": f"{ev[0]['date']} {ev[0]['event']} (D-{ev[0]['days_until']})"})
    return out


def takeaways(a: Dict) -> Dict[str, str]:
    """섹션 상단 '핵심 판단' 한 문장."""
    l4, mac, s = a["l4"], a["macro"], a["scenarios"]
    tl = three_lines(a)
    env = KO_ENV.get(mac.get("env"), mac.get("env") or "N/A")
    hi = [e for e in a.get("events") or [] if e.get("significance") == "HIGH"]
    s0 = (f"거시 유동성은 {q(env)} 환경(점수 {mac.get('score', 0):+d})입니다. "
          + (f"30일 안의 고중요도 일정은 {len(hi)}건이며, 가장 가까운 것은 {hi[0]['date']} {hi[0]['event']}입니다."
             if hi else "30일 안에 고중요도 일정은 없습니다."))
    s4 = (f"이동평균 {kt(KO_MA, l4.get('ma_alignment'))}, RSI {n(l4.get('rsi14'), 1)}, 볼린저밴드 {kt(KO_WIDTH, l4.get('bb_width_trend'))} — "
          f"{jo(q(ko(l4.get('scenario'))), '으로')} 판단합니다.")
    code, name, prob = _top(s)
    s5 = (f"네 레이어를 합친 점수는 {s['raw_score']:+.3f}이며, {name} 시나리오가 {prob:.0f}%로 가장 높습니다"
          f"(상승 {s['A_bullish']:.0f}% · 중립 {s['B_neutral']:.0f}% · 하락 {s['C_bearish']:.0f}%).")
    return {"s0": s0, "s1": tl[0], "s2": tl[1], "s3": tl[2], "s4": s4, "s5": s5}
