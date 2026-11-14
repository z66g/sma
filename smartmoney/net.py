"""HTTP 계층: 재시도 + 디스크 캐시 + 수집 로그.

- 같은 실행(워크플로) 안에서 17개 종목이 동일한 FINRA 일일 파일을 반복 다운로드하던
  문제를 디스크 캐시로 제거한다 (SMA_CACHE_DIR, 기본 /tmp/sma_cache).
- 모든 실패는 예외로 올리고, 호출자가 quality 레코드에 남긴다 (조용한 실패 금지).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

UA = os.environ.get("SMA_USER_AGENT", "SmartMoneyAnalyzer/3.0 (+https://analyzer.zbbg.kr)")
CACHE_DIR = Path(os.environ.get("SMA_CACHE_DIR", "/tmp/sma_cache"))
_SESSION: Optional[requests.Session] = None


def log(msg: str) -> None:
    print(f"[sma] {msg}", file=sys.stderr, flush=True)


def session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
        _SESSION = s
    return _SESSION


class FetchError(RuntimeError):
    pass


def _cache_path(key: str, suffix: str) -> Path:
    h = hashlib.sha1(key.encode()).hexdigest()[:20]
    return CACHE_DIR / f"{h}{suffix}"


def get(url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None,
        timeout: float = 20, retries: int = 2, ok_404: bool = False) -> Optional[requests.Response]:
    """GET with retry/backoff. 404 → None (ok_404) or FetchError."""
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            r = session().get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 404 and ok_404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                raise FetchError(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                raise FetchError(f"HTTP {r.status_code}")
            return r
        except (requests.RequestException, FetchError) as e:
            last = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise FetchError(f"{url.split('?')[0]}: {last}")


def post_json(url: str, body: Dict[str, Any], timeout: float = 25, retries: int = 2) -> Any:
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            r = session().post(url, json=body, timeout=timeout,
                               headers={"Content-Type": "application/json", "Accept": "application/json"})
            if r.status_code == 204 or not r.text.strip():
                return []
            if r.status_code >= 400:
                raise FetchError(f"HTTP {r.status_code}: {r.text[:120]}")
            return r.json()
        except (requests.RequestException, FetchError, ValueError) as e:
            last = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise FetchError(f"{url}: {last}")


def cached_text(url: str, *, max_age_s: float = 6 * 3600, ok_404: bool = True,
                timeout: float = 25, headers: Optional[dict] = None) -> Optional[str]:
    """텍스트 응답을 디스크 캐시. 404 는 '없음' 마커로 캐시해 반복 요청 방지."""
    p = _cache_path(url, ".txt")
    miss = p.with_suffix(".404")
    try:
        if p.exists() and time.time() - p.stat().st_mtime < max_age_s:
            return p.read_text(encoding="utf-8")
        if miss.exists() and time.time() - miss.stat().st_mtime < 1800:
            return None
    except OSError:
        pass
    r = get(url, ok_404=ok_404, timeout=timeout, headers=headers)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if r is None:
            miss.write_text("", encoding="utf-8")
            return None
        p.write_text(r.text, encoding="utf-8")
    except OSError:
        pass
    return r.text if r is not None else None


def cached_json(key: str, producer, max_age_s: float = 6 * 3600):
    """임의 producer() 결과(JSON 직렬화 가능)를 캐시."""
    p = _cache_path(key, ".json")
    try:
        if p.exists() and time.time() - p.stat().st_mtime < max_age_s:
            return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    val = producer()
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(val), encoding="utf-8")
    except (OSError, TypeError):
        pass
    return val
