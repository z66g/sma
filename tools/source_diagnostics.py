#!/usr/bin/env python3
"""데이터 소스 가용성 진단. 비밀값은 출력하지 않는다 (상태 코드·건수만)."""
from __future__ import annotations

import ftplib
import io
import os
import sys
from datetime import date, timedelta

import requests

UA = {"User-Agent": "SmartMoneyAnalyzer/3.0 (diagnostics)"}
KEY = os.environ.get("POLYGON_API_KEY", "").strip()


def line(name: str, status: str, detail: str = "") -> None:
    print(f"| {name} | {status} | {detail} |")


def last_weekday() -> date:
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def main() -> int:
    print("| Source | Status | Detail |\n|---|---|---|")
    d = last_weekday()

    # IBKR shortstock FTP (iborrowdesk 원천)
    try:
        ftp = ftplib.FTP("ftp3.interactivebrokers.com", timeout=20)
        ftp.login("shortstock", "")
        names = ftp.nlst()
        buf = io.BytesIO()
        ftp.retrbinary("RETR usa.txt", buf.write)
        ftp.quit()
        text = buf.getvalue().decode("utf-8", "replace").splitlines()
        hdr = [l for l in text[:3]]
        tsla = [l for l in text if l.startswith("TSLA|")][:1]
        line("IBKR FTP usa.txt", "OK", f"files={len(names)} lines={len(text)} hdr={hdr!r} TSLA={tsla!r}")
    except Exception as e:  # noqa: BLE001
        line("IBKR FTP usa.txt", "FAIL", repr(e)[:200])

    try:
        r = requests.get("https://iborrowdesk.com/api/ticker/TSLA", headers=UA, timeout=20)
        line("iborrowdesk API", str(r.status_code), f"bytes={len(r.content)}")
    except Exception as e:  # noqa: BLE001
        line("iborrowdesk API", "FAIL", repr(e)[:160])

    if not KEY:
        line("Polygon", "SKIP", "POLYGON_API_KEY not set")
        return 0
    probes = {
        "aggs 1m": f"https://api.polygon.io/v2/aggs/ticker/TSLA/range/1/minute/{d}/{d}?limit=5",
        "v3 trades": f"https://api.polygon.io/v3/trades/TSLA?timestamp={d}&limit=5",
        "v3 quotes": f"https://api.polygon.io/v3/quotes/TSLA?timestamp={d}&limit=5",
        "options snapshot": "https://api.polygon.io/v3/snapshot/options/TSLA?limit=5",
        "short volume": f"https://api.polygon.io/stocks/v1/short-volume?ticker=TSLA&limit=5",
        "short interest": f"https://api.polygon.io/stocks/v1/short-interest?ticker=TSLA&limit=5",
    }
    for name, url in probes.items():
        try:
            r = requests.get(url, params={"apiKey": KEY}, timeout=20)
            try:
                j = r.json()
            except ValueError:
                j = {}
            res = j.get("results")
            n = len(res) if isinstance(res, list) else (1 if res else 0)
            extra = ""
            if name == "v3 trades" and isinstance(res, list) and res:
                extra = f" keys={sorted(res[0].keys())}"
            line(f"Polygon {name}", str(r.status_code), f"status={j.get('status')} n={n}{extra} msg={str(j.get('message', ''))[:90]}")
        except Exception as e:  # noqa: BLE001
            line(f"Polygon {name}", "FAIL", repr(e)[:160])
    return 0


if __name__ == "__main__":
    sys.exit(main())
