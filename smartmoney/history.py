"""과거 데이터 접근: 이전 리포트 JSON + 자체 누적 상태(state/).

- 리포트: reports/{folder}/{T}_{date}.json. v2(스키마 없음)는 파일 날짜가 KST 실행일이므로
  calendar.legacy_session_for() 로 실제 세션 날짜로 보정하고 (ticker, session) 단위 중복 제거.
- state/borrow/{T}.json : IBKR 대차 스냅샷 일별 누적 (FTP 는 히스토리 제공 안 함)
- state/options/{T}.json: 만기별 OI 합계·Max Pain 일별 누적 (OI 추세·핀닝 사후검증용)
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import calendar as cal

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = Path(os.environ.get("SMA_REPORTS_DIR", str(ROOT / "reports")))
STATE_DIR = Path(os.environ.get("SMA_STATE_DIR", str(ROOT / "state")))
_FNAME = re.compile(r"^(?P<t>[A-Z0-9.\-]+)_(?P<d>\d{4}-\d{2}-\d{2})\.json$")


def load_report_history(ticker: str, before: Optional[date] = None, limit: int = 400,
                        min_schema: int = 3) -> List[Dict[str, Any]]:
    """해당 종목 과거 리포트 JSON 목록 (session 오름차순, session < before).

    기본은 v3(schema>=3) 리포트만 — v2 는 방법론(OBV 정의·대차 출처·판정 규칙)이 달라 분석·해석에 쓰지 않는다.
    v2 파일은 reports/ 아카이브에 그대로 보존된다 (min_schema=2 로만 조회)."""
    best: Dict[str, Dict[str, Any]] = {}
    if not REPORTS_DIR.exists():
        return []
    paths = list(REPORTS_DIR.glob(f"*/{ticker}_*.json")) + list(REPORTS_DIR.glob(f"*/_v2/{ticker}_*.json"))
    for p in paths:
        m = _FNAME.match(p.name)
        if not m or m.group("t") != ticker:
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if int(j.get("schema") or 2) < min_schema:
            continue
        try:
            if int(j.get("schema") or 2) >= 3:
                sess = cal.parse_date(j.get("date") or m.group("d"))
                rank = (3, j.get("generated_at") or "")
            else:
                sess = cal.legacy_session_for(cal.parse_date(m.group("d")))
                rank = (2, m.group("d"))
        except ValueError:
            continue
        if before and sess >= before:
            continue
        key = sess.isoformat()
        j["_session"] = key
        j["_rank"] = rank
        j["_path"] = str(p.relative_to(REPORTS_DIR)) if p.is_relative_to(REPORTS_DIR) else str(p)
        if key not in best or rank > best[key]["_rank"]:
            best[key] = j
    rows = [best[k] for k in sorted(best)]
    return rows[-limit:]


def _state_file(kind: str, ticker: str) -> Path:
    return STATE_DIR / kind / f"{ticker}.json"


def read_state(kind: str, ticker: str) -> List[Dict[str, Any]]:
    p = _state_file(kind, ticker)
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
        return v if isinstance(v, list) else []
    except (OSError, ValueError):
        return []


def upsert_state(kind: str, ticker: str, row: Dict[str, Any], keep: int = 400) -> List[Dict[str, Any]]:
    """date 키 기준 upsert 후 오름차순 저장. 쓰기 실패는 무시(분석은 계속)."""
    rows = [r for r in read_state(kind, ticker) if r.get("date") != row.get("date")]
    rows.append(row)
    rows.sort(key=lambda r: r.get("date", ""))
    rows = rows[-keep:]
    p = _state_file(kind, ticker)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass
    return rows


def merge_state(kind: str, ticker: str, new_rows: List[Dict[str, Any]], keep: int = 520) -> List[Dict[str, Any]]:
    """여러 행을 date 기준으로 병합 저장 (새 값 우선)."""
    by = {r.get("date"): r for r in read_state(kind, ticker)}
    for r in new_rows:
        if r.get("date"):
            by[r["date"]] = r
    rows = [by[k] for k in sorted(by) if k][-keep:]
    p = _state_file(kind, ticker)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass
    return rows
