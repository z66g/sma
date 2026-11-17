#!/usr/bin/env python3
"""텔레그램용 '한 장 요약' 카드 이미지 생성.

  python3 tools/telegram_card.py 2026-11-16 --tickers "ASTS COIN ..." --out card.png

- reports/{DATE}/{T}_{DATE}.json 만 읽는다 (분석 재실행 없음).
- 행 순서: 티커 알파벳 순, 지수는 맨 아래.
- 렌더: Playwright(있으면) → 헤드리스 Chrome/Chromium CLI. 둘 다 없으면 이미지 없이 종료(exit 0).
- 고정 행 높이로 이미지 크기를 정확히 계산 (빈 여백 없음).
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
W = 900
ROW_H, HEAD_H, COLH_H, SEC_H, FOOT_H = 46, 92, 30, 30, 34
FONT = ('-apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans CJK KR", '
        '"Noto Sans KR", "NanumGothic", "Malgun Gothic", sans-serif')
BULL, BEAR, WARN, INFO, MUTED, TXT = "#1A7F5A", "#CF222B", "#9A6700", "#0969DA", "#656D76", "#1F2328"
SC = {"A": ("상승", BULL, "#DAFBE1"), "B": ("중립", WARN, "#FFF8C5"), "C": ("하락", BEAR, "#FFEBE9")}
ST_COL = {"ACCUMULATION": BULL, "MARKUP": BULL, "DISTRIBUTION": WARN, "MARKDOWN": BEAR, "NEUTRAL": MUTED}


def E(x) -> str:
    return html.escape("" if x is None else str(x))


def load(date: str, tickers: list[str], base: Path | None = None) -> tuple[list[dict], list[dict]]:
    stocks, idx = [], []
    base = base or (ROOT / "reports" / date)
    names = tickers or sorted({Path(p).name.split("_")[0] for p in glob.glob(str(base / f"*_{date}.json"))})
    seen = set()
    for t in names + ["SPX", "NDX"]:
        t = t.lstrip("^")
        p = base / f"{t}_{date}.json"
        if t in seen or not p.exists():
            continue
        seen.add(t)
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        (idx if d.get("is_index") else stocks).append(d)
    return stocks, idx


def top(d: dict) -> tuple[str, float]:
    s = d.get("scenarios") or {}
    return max((("A", s.get("A_bullish") or 0), ("B", s.get("B_neutral") or 0), ("C", s.get("C_bearish") or 0)),
               key=lambda x: x[1])


def core(d: dict) -> str:
    st = d.get("short_take") or ""
    parts = [x for x in st.split(" · ") if x]
    if parts:                     # 마지막 조각(판정)은 별도 열에 있으므로 제외
        return " · ".join(parts[:-1]) or parts[0]
    return (d.get("one_liner") or d.get("headline") or "")[:60]


def row(d: dict, i: int) -> str:
    code, prob = top(d)
    lab, fg, bg = SC[code]
    chg = d.get("change_pct")
    cc = BULL if (chg or 0) > 0 else BEAR if (chg or 0) < 0 else MUTED
    rg = ((d.get("phase") or {}).get("regime") or {}).get("current") or {}
    reg = (f'<span style="color:{ST_COL.get(rg.get("state"), MUTED)};font-weight:700;">{E(rg.get("state_ko"))}</span>'
           f'<span class="mu"> {E(rg.get("days"))}일</span>' + (' <span style="color:#CF222B;">⚠</span>' if rg.get("contradicted") else "")
           ) if rg else '<span class="mu">—</span>'
    price = d.get("price")
    ps = "" if price is None else (f"${price:,.0f}" if price >= 1000 else f"${price:,.2f}")
    bgc = "#FFFFFF" if i % 2 == 0 else "#F6F8FA"
    return (f'<tr style="background:{bgc};"><td class="tk"><b>{E(d.get("ticker"))}</b><span class="mu">{E(ps)}</span></td>'
            f'<td style="color:{cc};font-weight:700;text-align:right;">{"" if chg is None else f"{chg:+.1f}%"}</td>'
            f'<td><span class="pill" style="background:{bg};color:{fg};">{lab} {prob:.0f}%</span></td>'
            f'<td class="rg">{reg}</td><td class="cr">{E(core(d))}</td></tr>')


def build_html(date: str, stocks: list[dict], idx: list[dict]) -> tuple[str, int]:
    stocks = sorted_stocks(stocks)
    cnt = {k: sum(1 for d in stocks if top(d)[0] == k) for k in "ABC"}
    macro = {"FAVORABLE": "우호", "NEUTRAL": "중립", "RESTRICTED": "제한"}.get((stocks or idx or [{}])[0].get("macro_env"), "—")
    head = ("<tr><th>종목</th><th style='text-align:right;'>등락</th><th>우세 시나리오</th><th>수급 국면</th>"
            "<th>핵심 (주체 · 구조)</th></tr>")
    body = "".join(row(d, i) for i, d in enumerate(stocks))
    ib = ""
    if idx:
        ib = (f'<tr><td colspan="5" class="sec">지수</td></tr>' + "".join(row(d, i) for i, d in enumerate(sorted(idx, key=lambda d: d.get("ticker") or ""))))
    h = HEAD_H + COLH_H + ROW_H * (len(stocks) + len(idx)) + (SEC_H if idx else 0) + FOOT_H + 24
    doc = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}} html,body{{margin:0;background:#FFFFFF}}
body{{width:{W}px;font-family:{FONT};color:{TXT};padding:12px 16px}}
.hd{{height:{HEAD_H - 12}px;border-bottom:2px solid {WARN};margin-bottom:0;padding-top:2px}}
.t1{{font-size:21px;font-weight:800}} .t1 span{{color:{WARN}}}
.t2{{font-size:13px;color:{MUTED};margin-top:6px}} .t2 b{{font-weight:700}}
table{{border-collapse:collapse;width:100%;table-layout:fixed}}
th{{height:{COLH_H}px;font-size:11.5px;color:{MUTED};text-align:left;padding:0 8px;background:#EAEEF2;font-weight:700}}
td{{height:{ROW_H}px;padding:0 8px;font-size:13.5px;border-bottom:0.5px solid #D0D7DE;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
td.tk b{{font-size:15px;margin-right:6px}} .mu{{color:{MUTED};font-size:12px}}
.pill{{display:inline-block;padding:3px 10px;border-radius:12px;font-weight:700;font-size:12.5px}}
td.sec{{height:{SEC_H}px;font-size:11.5px;font-weight:700;color:{MUTED};background:#F6F8FA}}
.ft{{height:{FOOT_H}px;display:flex;align-items:flex-end;justify-content:space-between;font-size:11px;color:{MUTED}}}
col.c1{{width:128px}} col.c2{{width:72px}} col.c3{{width:116px}} col.c4{{width:150px}}
</style></head><body>
<div class="hd"><div class="t1">스마트머니 데일리 <span>· {E(date)} 세션</span></div>
<div class="t2">미국 정규장 마감 기준 · {len(stocks)}종목 · 우세 시나리오 <b style="color:{BULL}">상승 {cnt['A']}</b> · <b style="color:{WARN}">중립 {cnt['B']}</b> · <b style="color:{BEAR}">하락 {cnt['C']}</b> · 거시 유동성 {E(macro)}</div></div>
<table><colgroup><col class="c1"><col class="c2"><col class="c3"><col class="c4"><col></colgroup>
<thead>{head}</thead><tbody>{body}{ib}</tbody></table>
<div class="ft"><span>⚠ = 국면 판정과 누적 리포트·체결 근거가 엇갈림 · 국면 옆 숫자 = 지속 거래일</span><span>analyzer.zbbg.kr</span></div>
</body></html>"""
    return doc, h


def text_line(d: dict) -> str:
    """텔레그램 텍스트용 한 줄: 이모지 판정 · 주체 · 구조 (HTML 이스케이프)."""
    code, prob = top(d)
    emo = {"A": "🟢", "B": "🟡", "C": "🔴"}[code]
    c = core(d)
    return html.escape(f"{emo} {SC[code][0]} {prob:.0f}%" + (f" · {c}" if c else ""), quote=False)


def sorted_stocks(stocks: list[dict]) -> list[dict]:
    return sorted(stocks, key=lambda d: d.get("ticker") or "")      # 알파벳 순


def _chrome() -> str | None:
    for c in (os.environ.get("CHROME_BIN"), "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if c and shutil.which(c):
            return shutil.which(c)
    for p in sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")):
        return p
    return None


def render(doc: str, h: int, out: str) -> bool:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page(viewport={"width": W, "height": h}, device_scale_factor=2)
            pg.set_content(doc, wait_until="load")
            pg.screenshot(path=out, full_page=True)
            b.close()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"card: playwright unavailable ({type(e).__name__}) → chrome CLI")
    exe = _chrome()
    if not exe:
        print("::warning::card: no Chrome/Chromium — 이미지 생략")
        return False
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "card.html"
        src.write_text(doc, encoding="utf-8")
        cmd = [exe, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
               f"--window-size={W},{h}", f"--screenshot={os.path.abspath(out)}", "--virtual-time-budget=3000", src.as_uri()]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if r.returncode != 0 or not Path(out).exists():
            print(f"::warning::card: chrome failed ({r.returncode}) {r.stderr[-300:]}")
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--tickers", default="")
    ap.add_argument("--out", default="card.png")
    ap.add_argument("--html", default="", help="디버그용 HTML 저장 경로")
    ap.add_argument("--dir", default="", help="JSON 디렉터리 (기본 reports/DATE)")
    ap.add_argument("--lines", action="store_true", help="이미지 대신 'TICKER<TAB>텍스트 한 줄' 목록 출력 (카드와 같은 순서)")
    a = ap.parse_args()
    stocks, idx = load(a.date, a.tickers.split(), Path(a.dir) if a.dir else None)
    if a.lines:
        idx = []
    if not stocks and not idx:
        print("card: no reports — skip")
        return 0
    if a.lines:
        for d in sorted_stocks(stocks):
            print(f"{d.get('ticker')}\t{text_line(d)}")
        return 0
    doc, h = build_html(a.date, stocks, idx)
    if a.html:
        Path(a.html).write_text(doc, encoding="utf-8")
    ok = render(doc, h, a.out)
    print(f"card: {'ok ' + a.out if ok else 'skipped'} ({len(stocks)} stocks, {len(idx)} indexes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
