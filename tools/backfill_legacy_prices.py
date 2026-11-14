#!/usr/bin/env python3
"""v2 리포트의 price=NaN 결손을 실제 세션 종가로 보정 (1회성 유지보수 도구).

원인: 구 엔진이 KST 오전 실행 시 Yahoo 의 '미완성 당일 봉'(Close=NaN)을 종가로 사용.
보정: 파일명(KST 실행일) → calendar.legacy_session_for() 로 실제 세션을 구해 그 날 종가를 기록.
      price 만 채우고 "price_backfilled": true 표기. 다른 지표(NaN 기반 L4 등)는 건드리지 않는다.

  python3 tools/backfill_legacy_prices.py            # 보정 실행
  python3 tools/backfill_legacy_prices.py --dry-run  # 대상만 출력
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartmoney import calendar as cal  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent / "reports"
FNAME = re.compile(r"^([A-Z0-9.\-]+)_(\d{4}-\d{2}-\d{2})\.json$")
NAN_TOKEN = re.compile(r'("price":\s*)NaN')


def main() -> int:
    dry = "--dry-run" in sys.argv
    todo = defaultdict(list)
    for p in sorted(ROOT.glob("*/*.json")) + sorted(ROOT.glob("*/_v2/*.json")):
        m = FNAME.match(p.name)
        if not m:
            continue
        txt = p.read_text(encoding="utf-8")
        try:
            j = json.loads(txt)
        except ValueError:
            continue
        if int(j.get("schema") or 2) >= 3 or j.get("is_index"):
            continue
        pr = j.get("price")
        if pr is None or (isinstance(pr, float) and math.isnan(pr)):
            todo[m.group(1)].append((p, cal.legacy_session_for(cal.parse_date(m.group(2)))))
    n = sum(len(v) for v in todo.values())
    print(f"[backfill] {n} file(s) across {len(todo)} ticker(s)")
    if dry or not n:
        return 0
    import yfinance as yf
    fixed = 0
    for t, items in todo.items():
        h = yf.Ticker(t).history(period="2y", interval="1d", auto_adjust=False)
        closes = {i.date().isoformat(): float(c) for i, c in h["Close"].items() if not math.isnan(c)}
        for p, sess in items:
            c = closes.get(sess.isoformat())
            if c is None:
                print(f"[backfill] {t} {sess}: close unavailable — skip")
                continue
            j = json.loads(p.read_text(encoding="utf-8"))
            j["price"] = round(c, 4)
            j["price_backfilled"] = True
            p.write_text(json.dumps(j, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            fixed += 1
    print(f"[backfill] fixed {fixed}/{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
