#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""적정가치 단독 실행 CLI — 수급 분석 없이 펀더멘털 적정가만 빠르게 본다.

사용:
  python3 valuation.py MRVL                 # 요약 + 모델별 표
  python3 valuation.py MRVL TSLA KBR        # 여러 종목
  python3 valuation.py MRVL --refresh       # 재무 스냅샷 강제 갱신 (기본: state/fundamentals TTL 재사용)
  python3 valuation.py MRVL --html out/     # out/MRVL/valuation.html 상세 페이지 생성
  python3 valuation.py MRVL --json          # 요약 JSON (리포트 JSON 의 `valuation` 키와 동일)

환경변수: SMA_ERP(주식위험프리미엄, 기본 0.045) · SMA_FUND_TTL_DAYS · SMA_VAL_PEERS · SMA_VAL_MC · SMA_STATE_DIR
종료 코드: 0 성공 · 1 하나라도 실패
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)


def _show(v: dict) -> None:
    k, p, a = v["wacc"], v["assumptions"], v["archetype"]
    print(f"\n=== {v['ticker']} · 현재가 {v['price']:,.2f} ===")
    print(v.get("summary"))
    for r in a.get("reasons") or []:
        print(f"  · {r}")
    if a.get("traits"):
        print(f"  · 속성: {', '.join(a['traits'])}")
    print(f"  가정: 성장 {p['g1'] * 100:+.0f}%/{p['g2'] * 100:+.0f}% → {p['gT'] * 100:.1f}% · 영업이익률 {p['m0'] * 100:.0f}% → {p['mT'] * 100:.0f}% · "
          f"WACC {k['wacc'] * 100:.1f}% (자기자본비용 {k['ke'] * 100:.1f}%, 베타 {k['beta']:.2f})")
    print(f"  {'':2}{'모델':<26}{'보수':>10}{'기준':>10}{'낙관':>10}{'가중치':>8}")
    for m in v["models"]:
        if m["applicable"]:
            tag = "*" if m.get("included") else " "
            w = f"{m['weight'] * 100:.0f}%" if m.get("included") else "참고"
            flags = " ⚠ " + ", ".join(c["name"] for c in m["checks"] if not c["ok"]) if any(not c["ok"] for c in m["checks"]) else ""
            print(f"  {tag} {m['name']:<26}{m['low']:>10,.2f}{m['mid']:>10,.2f}{m['high']:>10,.2f}{w:>8}{flags}")
    na = [m for m in v["models"] if not m["applicable"]]
    if na:
        print("  미적용: " + " / ".join(f"{m['name']}({m['reason'][:28]})" for m in na))
    rd, mc = v.get("reverse_dcf"), v.get("monte_carlo")
    if rd:
        print(f"  역DCF: {rd.get('text')}")
    if mc:
        print(f"  몬테카를로: P10 {mc['p10']:,.2f} · P50 {mc['p50']:,.2f} · P90 {mc['p90']:,.2f} · 적정가>현재가 확률 {mc['prob_above_price'] * 100:.0f}%")
    u = v.get("uncertainty") or {}
    if u.get("reasons"):
        print(f"  불확실성 {u.get('label')}({u.get('score')}): {' · '.join(u['reasons'])}")
    for w in v.get("warnings") or []:
        print(f"  ! {w}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fair value (fundamental) calculator")
    ap.add_argument("tickers", nargs="+", help="예: MRVL TSLA")
    ap.add_argument("--refresh", action="store_true", help="재무 스냅샷 강제 갱신")
    ap.add_argument("--price", type=float, default=None, help="현재가 직접 지정 (종목 1개일 때)")
    ap.add_argument("--html", default=None, metavar="DIR", help="DIR/{T}/valuation.html 상세 페이지 생성")
    ap.add_argument("--json", action="store_true", help="요약 JSON 만 출력")
    args = ap.parse_args()

    from smartmoney import valuation as V
    from smartmoney.render_valuation import page

    rc, out = 0, {}
    for t in args.tickers:
        t = t.upper().strip()
        try:
            v = V.run(t, price=args.price if len(args.tickers) == 1 else None, force=args.refresh)
        except Exception as e:  # noqa: BLE001
            rc = 1
            print(f"{t}: 실패 — {e}", file=sys.stderr)
            if not args.json:
                traceback.print_exc()
            continue
        out[t] = V.compact(v)
        if not args.json:
            _show(v)
        if args.html:
            p = Path(args.html) / t / "valuation.html"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(page(v, None, home="./", dash="../../"), encoding="utf-8")
            if not args.json:
                print(f"  HTML: {p}")
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
