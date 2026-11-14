#!/usr/bin/env python3
"""세션 폴더(reports/{SESSION_DATE}) 상태 점검 / 레거시 파일 보존 — daily 워크플로용.

  python3 tools/session_files.py complete DATE T1 [T2 ...]
      → 모든 티커에 v3 JSON(schema>=3, date==DATE) + HTML 이 있으면 exit 0, 아니면 exit 1.
        (휴장일·중복 dispatch 시 재분석 skip 판단용. 레거시 v2 파일은 '완료'로 치지 않음)

  python3 tools/session_files.py preserve-legacy DATE
      → reports/DATE/ 안의 레거시 v2 파일(schema 없음, 파일명=KST 실행일 → 실제로는 직전 세션 데이터)을
        reports/DATE/_v2/ 로 옮긴다. v3 엔진이 같은 이름으로 덮어써 과거 세션이 사라지는 것을 방지.
        build_index.py 는 _v2/ 도 스캔하므로 시계열/링크는 유지된다.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILE_RE = re.compile(r"^([A-Z0-9.\-]+)_(\d{4}-\d{2}-\d{2})\.json$")


def _load(p: Path):
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
        return j if isinstance(j, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _schema(j: dict) -> int:
    try:
        return int(j.get("schema") or 2)
    except (TypeError, ValueError):
        return 2


def complete(date: str, tickers) -> int:
    d = REPORTS / date
    missing = []
    for t in tickers:
        t = t.strip().upper()
        if not t:
            continue
        j = _load(d / f"{t}_{date}.json")
        html_ok = (d / f"{t}_3Layer_Forensic_{date}.html").exists()
        if not j or _schema(j) < 3 or j.get("date") != date or not html_ok:
            missing.append(t)
    if missing:
        print(f"incomplete: {len(missing)} ticker(s) without v3 report for {date}: {' '.join(missing)}")
        return 1
    print(f"complete: all {len(tickers)} ticker(s) have v3 reports for {date}")
    return 0


def preserve_legacy(date: str) -> int:
    d = REPORTS / date
    if not d.is_dir():
        print(f"preserve-legacy: {d} does not exist (nothing to do)")
        return 0
    moved = 0
    for jp in sorted(d.glob("*.json")):
        m = FILE_RE.match(jp.name)
        if not m:
            continue
        j = _load(jp)
        if j is None or _schema(j) >= 3:
            continue
        t, nd = m.group(1), m.group(2)
        dest = d / "_v2"
        dest.mkdir(exist_ok=True)
        for name in (jp.name, f"{t}_3Layer_Forensic_{nd}.html", f"SmartMoney_{t}_{nd}.md", f"{t}_Index_{nd}.html"):
            src = d / name
            if src.exists():
                shutil.move(str(src), str(dest / name))
        moved += 1
    print(f"preserve-legacy: moved {moved} legacy v2 report(s) to {d / '_v2'}" if moved
          else f"preserve-legacy: no legacy v2 reports in {d}")
    return 0


def main(argv) -> int:
    if len(argv) < 3 or argv[1] not in ("complete", "preserve-legacy") or not DATE_RE.match(argv[2]):
        print(__doc__, file=sys.stderr)
        return 2
    if argv[1] == "complete":
        return complete(argv[2], argv[3:])
    return preserve_legacy(argv[2])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
