#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Valuation Desk CLI — 적정가치는 SMA 수급 분석과 분리된 별도 기능이다 (SMA 는 발행된 수치만 읽는다).

  python3 valuation.py prepare MRVL --out WORK_DIR        # 리서치 패키지 생성 (재무 다이제스트 · 기준선 · SEC 링크 · 템플릿)
  python3 valuation.py publish MRVL --research WORK_DIR/MRVL_research.json [--dry-run]
                                                          # 리서치 검증 → 계산 → 발행 (reports/valuation/MRVL.json 등)
  python3 valuation.py refresh [MRVL ...] [--watchlist]   # 저장된 리서치 + 최신 재무·주가로 재계산·재발행 (리서치 없으면 기준선만)
  python3 valuation.py show MRVL [--json]                 # 발행 없이 화면 출력 (기계적 기준선 + 저장된 리서치)
  python3 valuation.py status                             # 워치리스트 발행 현황 (방식 · 리서치 경과일)

리서치 작성 지침: docs/VALUATION_RESEARCH.md · 알고리즘: docs/VALUATION.md
환경변수: SMA_ERP · SMA_FUND_TTL_DAYS · SMA_VAL_PEERS · SMA_VAL_MC · SMA_STATE_DIR · SMA_REPORTS_DIR
종료 코드: 0 성공 · 1 실패 · 2 리서치 검증 실패
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
ROOT = Path(__file__).resolve().parent


def watchlist() -> list:
    p = ROOT / "tickers.txt"
    return [s.strip().upper() for s in p.read_text(encoding="utf-8").splitlines()
            if s.strip() and not s.strip().startswith("#")] if p.exists() else []


def _show(v: dict) -> None:
    k, p, a = v["wacc"], v["assumptions"], v["archetype"]
    print(f"\n=== {v['ticker']} · 현재가 {v['price']:,.2f} · 방식 {v.get('method')} ===")
    print(v.get("summary"))
    R = v.get("research")
    if R:
        print(f"  논지: {R.get('thesis')}")
        for n, s in R["scenarios"].items():
            g = f"5년 CAGR {s['cagr5'] * 100:.0f}% · 목표마진 {s['target_margin'] * 100:.0f}%" if s.get("cagr5") is not None else s.get("how")
            print(f"  [{n:4}] {s['prob'] * 100:3.0f}%  {s['value']:>10,.2f}  ({g})")
        for o in R.get("options") or []:
            print(f"  [옵션] {o['name']}: {o['prob'] * 100:.0f}% × {o['value_per_share']:,.2f} = {o['expected']:,.2f}")
        print("  분해(주당): " + " · ".join(f"{n} 본업 {s['core']:,.2f} + 옵션 {R.get('option_value') or 0:,.2f}"
                                          + (f" (추가투자 PV {s['extra_investment_pv'] / 1e9:,.1f}B)" if s.get("extra_investment_pv") else "")
                                          for n, s in R["scenarios"].items()) + f" · 주식수 {v['metrics']['shares'] / 1e6:,.0f}M(엔진)")
        m = v.get("mechanical") or {}
        print(f"  기계적 기준선: {m.get('fair_value') and round(m['fair_value'], 2)} — {m.get('summary')}")
    else:
        for r in a.get("reasons") or []:
            print(f"  · {r}")
    print(f"  할인율: WACC {k['wacc'] * 100:.1f}% (자기자본비용 {k['ke'] * 100:.1f}%, 베타 {k['beta']:.2f}) · "
          f"기계적 가정: 성장 {p['g1'] * 100:+.0f}%/{p['g2'] * 100:+.0f}% · 영업이익률 {p['m0'] * 100:.0f}% → {p['mT'] * 100:.0f}%")
    print(f"  {'':2}{'기계적 모델':<26}{'보수':>10}{'기준':>10}{'낙관':>10}{'가중치':>8}")
    for m in v["models"]:
        if m.get("included"):
            flags = " ⚠ " + ", ".join(c["name"] for c in m["checks"] if not c["ok"]) if any(not c["ok"] for c in m["checks"]) else ""
            print(f"  * {m['name']:<26}{m['low']:>10,.2f}{m['mid']:>10,.2f}{m['high']:>10,.2f}{m['weight'] * 100:>7.0f}%{flags}")
    rd = v.get("reverse_dcf")
    if rd:
        print(f"  역DCF: {rd.get('text')}")
    u = v.get("uncertainty") or {}
    print(f"  불확실성 {u.get('label')}({u.get('score')}): {' · '.join(u.get('reasons') or [])}")
    for w in v.get("warnings") or []:
        print(f"  ! {w}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Valuation Desk — fair value as a separate function from SMA")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("prepare", help="리서치 패키지 생성")
    s.add_argument("ticker"); s.add_argument("--out", required=True); s.add_argument("--refresh", action="store_true")
    s = sub.add_parser("publish", help="리서치 검증 → 계산 → 발행")
    s.add_argument("ticker"); s.add_argument("--research", required=True); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--refresh", action="store_true")
    s = sub.add_parser("refresh", help="저장된 리서치 + 최신 재무·주가로 재발행")
    s.add_argument("tickers", nargs="*"); s.add_argument("--watchlist", action="store_true"); s.add_argument("--force-fetch", action="store_true")
    s = sub.add_parser("show", help="발행 없이 출력")
    s.add_argument("tickers", nargs="+"); s.add_argument("--json", action="store_true"); s.add_argument("--refresh", action="store_true")
    sub.add_parser("status", help="워치리스트 발행 현황")
    args = ap.parse_args()

    from smartmoney import valuation as V
    from smartmoney import valuation_desk as VD

    if args.cmd == "prepare":
        print(f"PACKAGE: {VD.prepare(args.ticker, args.out, args.refresh)}")
        print(f"GUIDE:   {VD.GUIDE}")
        return 0

    if args.cmd == "publish":
        T = args.ticker.upper()
        try:
            R = json.loads(Path(args.research).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"리서치 파일을 읽을 수 없음: {e}", file=sys.stderr)
            return 2
        from smartmoney import fundamentals as FN
        base = V.evaluate(FN.load(T, force=args.refresh), None, *V.market_rates())
        direct = base["archetype"]["code"] in ("BANK", "INSURANCE", "REIT")
        err, warn = VD.validate(R, T, direct)
        for w in warn:
            print(f"  경고: {w}")
        if err:
            print(f"검증 실패 {len(err)}건 — 발행하지 않음:", file=sys.stderr)
            for e in err:
                print(f"  ✗ {e}", file=sys.stderr)
            return 2
        v = VD.compute(T, research=R)
        for w in VD._order_check(v):
            print(f"  경고: {w}")
        _show(v)
        if args.dry_run:
            print("\n(dry-run: 발행하지 않음)")
            return 0
        for k, p in VD.publish(T, v, R).items():
            print(f"{k.upper():9}: {p}")
        return 0

    if args.cmd == "refresh":
        tickers = [t.upper() for t in args.tickers] or (watchlist() if args.watchlist else [])
        if not tickers:
            ap.error("종목을 지정하거나 --watchlist")
        rc = 0
        for t in tickers:
            try:
                v = VD.compute(t, refresh=args.force_fetch)
                VD.publish(t, v)
                print(f"{t}: {v.get('method')} · {v.get('summary')}")
            except Exception as e:  # noqa: BLE001
                rc = 1
                print(f"{t}: 실패 — {str(e)[:200]}", file=sys.stderr)
        return rc

    if args.cmd == "show":
        rc, out = 0, {}
        for t in args.tickers:
            try:
                v = VD.compute(t.upper(), refresh=args.refresh)
            except Exception as e:  # noqa: BLE001
                rc = 1
                print(f"{t}: 실패 — {e}", file=sys.stderr)
                if not args.json:
                    traceback.print_exc()
                continue
            out[t.upper()] = V.compact(v)
            if not args.json:
                _show(v)
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
        return rc

    if args.cmd == "status":
        print(f"{'종목':<7}{'방식':<12}{'적정가':>10}{'리서치일':>12}{'경과':>6}  비고")
        for t in watchlist():
            try:
                pub = json.loads((VD.PUB_DIR / f"{t}.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                print(f"{t:<7}{'미발행':<12}")
                continue
            r = pub.get("research") or {}
            shown = VD.read_published(t) or {}
            note = "갱신 필요" if shown.get("stale") else "" if pub.get("method") == "RESEARCH" else "리서치 없음 — SMA 미표시"
            print(f"{t:<7}{pub.get('method', ''):<12}{pub.get('fair_value') or 0:>10,.2f}{r.get('as_of') or '-':>12}"
                  f"{shown.get('age_days', '-'):>6}  {note}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
