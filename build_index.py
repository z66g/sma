#!/usr/bin/env python3
"""
reports/ 디렉토리를 스캔해 최신순 인덱스 HTML 생성.
GitHub Pages 루트(/reports/index.html)에서 모든 리포트를 브라우징 가능.
"""
from __future__ import annotations
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'

def main():
    # 파일 수집: reports/YYYY-MM-DD/TICKER_3Layer_Forensic_DATE.html
    by_date = defaultdict(list)     # date -> [(ticker, html_path, md_path)]
    by_ticker = defaultdict(list)   # ticker -> [(date, html_path, md_path)]

    for html_path in sorted(REPORTS.glob("*/*_3Layer_Forensic_*.html")):
        m = re.match(r"([A-Z.\-]+)_3Layer_Forensic_(\d{4}-\d{2}-\d{2})\.html", html_path.name)
        if not m:
            continue
        ticker, date = m.group(1), m.group(2)
        md_path = html_path.parent / f"SmartMoney_{ticker}_{date}.md"
        rel_html = html_path.relative_to(REPORTS).as_posix()
        rel_md   = md_path.relative_to(REPORTS).as_posix() if md_path.exists() else None
        by_date[date].append((ticker, rel_html, rel_md))
        by_ticker[ticker].append((date, rel_html, rel_md))

    dates = sorted(by_date.keys(), reverse=True)
    tickers = sorted(by_ticker.keys())

    # 티커별 최신 리포트
    latest_rows = []
    for t in tickers:
        entries = sorted(by_ticker[t], key=lambda x: x[0], reverse=True)
        if not entries: continue
        d, html_r, md_r = entries[0]
        md_link = f' · <a href="{md_r}">md</a>' if md_r else ""
        latest_rows.append(
            f'<tr><td><b>{t}</b></td><td>{d}</td>'
            f'<td><a href="{html_r}">리포트</a>{md_link}</td>'
            f'<td>{len(entries)}개</td></tr>'
        )

    # 날짜별 전체 목록
    date_sections = []
    for d in dates:
        items = sorted(by_date[d], key=lambda x: x[0])
        lis = "".join(
            f'<li><b>{t}</b> · <a href="{html_r}">html</a>'
            + (f' · <a href="{md_r}">md</a>' if md_r else "") + "</li>"
            for t, html_r, md_r in items
        )
        date_sections.append(f'<h3 style="margin-top:20px;color:#9A6700;">{d} ({len(items)}개)</h3><ul style="line-height:1.8;">{lis}</ul>')

    total_reports = sum(len(v) for v in by_date.values())
    generated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    html_out = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Smart Money Analyzer — Reports</title>
<style>
  body {{ font-family:{FONT}; background:#FFFFFF; color:#1F2328; margin:0; padding:24px; }}
  .wrap {{ max-width:1000px; margin:0 auto; }}
  h1 {{ font-size:22px; border-bottom:1px solid #D0D7DE; padding-bottom:8px; }}
  h2 {{ font-size:15px; color:#9A6700; margin-top:24px; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  th,td {{ border:0.5px solid #D0D7DE; padding:6px 8px; text-align:left; }}
  th {{ background:#EAEEF2; color:#656D76; font-weight:600; }}
  tr:nth-child(even) {{ background:#F6F8FA; }}
  a {{ color:#0969DA; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .meta {{ color:#656D76; font-size:11px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Smart Money Analyzer — Reports</h1>
  <div class="meta">Generated {generated} · {total_reports} reports · {len(tickers)} tickers</div>

  <h2>티커별 최신 리포트</h2>
  <table>
    <thead><tr><th>Ticker</th><th>최신 날짜</th><th>링크</th><th>전체 히스토리</th></tr></thead>
    <tbody>
      {''.join(latest_rows) or '<tr><td colspan="4">아직 리포트가 없습니다.</td></tr>'}
    </tbody>
  </table>

  <h2>날짜별 전체 리포트</h2>
  {''.join(date_sections) or '<p>리포트 없음.</p>'}
</div>
</body>
</html>
"""
    out = REPORTS / "index.html"
    out.write_text(html_out, encoding="utf-8")
    print(f"[index] {out} written ({total_reports} reports, {len(tickers)} tickers)")

if __name__ == "__main__":
    main()
