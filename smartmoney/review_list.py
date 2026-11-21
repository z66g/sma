"""검토 종목(review.txt) — 관심종목에 넣기 전에 적정가만 먼저 보는 종목 목록.

표준 라이브러리만 쓴다(GitHub Actions 워크플로가 의존성 설치 없이 읽는다).

파일 형식: 한 줄에 `TICKER [REQUESTED_AT]`. `#` 으로 시작하는 줄은 주석.
  REQUESTED_AT = 대시보드에서 '분석 요청'을 누른 시각(UTC ISO 8601, 예: 2026-11-21T09:30:00Z).
  발행된 리서치가 없거나, 요청 시각이 마지막 발행 시각보다 늦으면 '분석 대기'다 — 처리 여부를 따로 기록하지 않아도
  발행이 끝나면 자동으로 해소된다(발행 파일의 `published_at` 이 요청 시각보다 늦어진다).
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
REVIEW_FILE = ROOT / "review.txt"
WATCHLIST_FILE = ROOT / "tickers.txt"
PUB_DIR = ROOT / "reports" / "valuation"
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
REPRICE_DAYS = 28            # 검토 종목은 분석 후 4주까지만 매주 재계산(주가·금리), 그 뒤에는 '오래됨'으로 둔다
HEADER = ["# Smart Money Analyzer — 검토 종목 (적정가 먼저 보는 종목)",
          "# 대시보드에서 관리. 한 줄에 `TICKER [분석 요청 시각(UTC ISO)]`. #으로 시작하는 줄은 주석.",
          "# 관심종목(tickers.txt)과 달리 매일 수급 분석을 하지 않는다. 적정가만 발행·재계산한다.", ""]


def parse(text: str) -> List[Dict[str, Optional[str]]]:
    out, seen = [], set()
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        t = parts[0].upper()
        if not TICKER_RE.match(t) or t in seen:
            continue
        seen.add(t)
        req = parts[1] if len(parts) > 1 and re.match(r"^\d{4}-\d{2}-\d{2}(T[\d:.]+Z?)?$", parts[1]) else None
        out.append({"ticker": t, "requested_at": req})
    return out


def load(path: Optional[Path] = None) -> List[Dict[str, Optional[str]]]:
    p = path or REVIEW_FILE
    try:
        return parse(p.read_text(encoding="utf-8"))
    except OSError:
        return []


def render(entries: List[Dict[str, Optional[str]]]) -> str:
    rows = [e["ticker"] + (f" {e['requested_at']}" if e.get("requested_at") else "") for e in sorted(entries, key=lambda e: e["ticker"])]
    return "\n".join(HEADER + rows) + "\n"


def watchlist(path: Optional[Path] = None) -> List[str]:
    p = path or WATCHLIST_FILE
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [s.strip().upper() for s in lines if s.strip() and not s.strip().startswith("#")]


def _ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = str(s).strip()
    try:
        if len(s) == 10:
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def published(ticker: str, pub_dir: Optional[Path] = None) -> Optional[dict]:
    """발행된 리서치 기반 수치 (기계적 기준선만 있으면 None)."""
    try:
        p = json.loads(((pub_dir or PUB_DIR) / f"{ticker.upper()}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return p if isinstance(p, dict) and p.get("method") == "RESEARCH" else None


def published_at(pub: Optional[dict]) -> Optional[datetime]:
    """마지막 발행 시각. 예전 파일은 날짜만 있으므로 그날 00:00 UTC 로 본다(같은 날 요청은 대기로 잡힌다)."""
    if not pub:
        return None
    return _ts(pub.get("published_at")) or _ts(pub.get("published"))


def status(entry: Dict[str, Optional[str]], today: Optional[date] = None, pub_dir: Optional[Path] = None) -> Dict[str, object]:
    """검토 종목 한 줄의 상태: PENDING(분석 대기) · FRESH(재계산 대상) · EXPIRED(4주 경과, 재계산 중단)."""
    day = today or datetime.now(timezone.utc).date()
    pub = published(entry["ticker"], pub_dir)
    req = _ts(entry.get("requested_at"))
    pa = published_at(pub)
    as_of = str((pub or {}).get("research", {}).get("as_of") or (pub or {}).get("published") or "")[:10] if pub else ""
    age = None
    if as_of:
        try:
            age = (day - date.fromisoformat(as_of)).days
        except ValueError:
            age = None
    if pub is None:
        st = "PENDING"
    elif req is not None and (pa is None or req > pa):
        st = "PENDING"
    elif age is not None and age > REPRICE_DAYS:
        st = "EXPIRED"
    else:
        st = "FRESH"
    return {"ticker": entry["ticker"], "status": st, "requested_at": entry.get("requested_at"), "has_research": pub is not None,
            "research_as_of": as_of or None, "age_days": age}


def pending(today: Optional[date] = None) -> List[str]:
    """조사가 필요한 종목: 리서치가 없는 관심종목 + 분석 대기 중인 검토 종목 (관심종목에 있으면 관심종목 규칙을 따른다)."""
    wl = watchlist()
    out = [t for t in wl if published(t) is None]
    for e in load():
        if e["ticker"] in wl:
            continue
        if status(e, today)["status"] == "PENDING":
            out.append(e["ticker"])
    return out
