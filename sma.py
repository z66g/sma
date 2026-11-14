#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smart Money Flow Analyzer — CLI (v3).

사용:
  python3 sma.py TSLA                  # 최근 완료된 미국 세션 기준 분석
  python3 sma.py TSLA --date 2026-11-13
  python3 sma.py ^SPX                  # 지수 (L3/L4 중심 리포트)
  python3 sma.py --print-session-date  # 최근 완료 세션 날짜만 출력 (워크플로용)

환경변수:
  SMA_OUT_DIR       출력 폴더 (기본 ./output/{SESSION_DATE})
  SMA_SESSION_DATE  세션 날짜 강제 (워크플로가 export)
  SMA_STATE_DIR     상태 저장소 (기본 ./state)
  POLYGON_API_KEY   (선택) 1분봉 · 공매도잔고 보조 소스

종료 코드: 0 성공 · 2 유효하지 않은/상장폐지 티커 · 1 기타 실패
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

from smartmoney import calendar as cal  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Smart Money Flow Analyzer v3")
    ap.add_argument("ticker", nargs="?", help="예: TSLA, ^SPX")
    ap.add_argument("--date", default=None, help="미국 세션 날짜 YYYY-MM-DD (기본: 최근 완료 세션)")
    ap.add_argument("--print-session-date", action="store_true", help="최근 완료 세션 날짜 출력 후 종료")
    args = ap.parse_args()

    if args.print_session_date:
        print(cal.last_completed_session().isoformat())
        return 0
    if not args.ticker:
        ap.error("ticker 필요")

    from smartmoney import commentary as C
    from smartmoney.engine import SmartMoneyAnalyzer
    from smartmoney.net import log
    from smartmoney.render_data import render_json, render_markdown
    from smartmoney.render_html import render

    an = SmartMoneyAnalyzer(args.ticker, args.date)
    log(f"Analyzing {an.ticker} @ session {an.date_str}")
    try:
        a, _raw = an.run()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    if a is None:
        log(f"SKIP {an.ticker}: no price data ({'; '.join(an.warnings)[:200]})")
        print(f"SKIP: {an.ticker}")
        return 2

    a["headline"] = C.headline(a)
    out_dir = Path(os.environ.get("SMA_OUT_DIR") or (Path(__file__).resolve().parent / "output" / an.date_str))
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = an.display_ticker
    jpath = out_dir / f"{tag}_{an.date_str}.json"
    if an.is_index:
        hpath = out_dir / f"{tag}_Index_{an.date_str}.html"
        hpath.write_text(render(a), encoding="utf-8")
        mpath = None
    else:
        md = render_markdown(a)
        mpath = out_dir / f"SmartMoney_{tag}_{an.date_str}.md"
        mpath.write_text(md, encoding="utf-8")
        hpath = out_dir / f"{tag}_3Layer_Forensic_{an.date_str}.html"
        hpath.write_text(render(a, md), encoding="utf-8")
    jpath.write_text(render_json(a), encoding="utf-8")
    q = a["quality"]
    log(f"done {tag} in {a['meta']['elapsed_s']}s · quality {q['grade']}({q['score']}) · warnings {len(a['meta']['warnings'])}")
    print(f"HTML: {hpath}")
    if mpath:
        print(f"MD:   {mpath}")
    print(f"JSON: {jpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
