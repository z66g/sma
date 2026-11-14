#!/usr/bin/env python3
"""세션 폴더의 리포트 JSON(v3) 데이터 품질 요약.

  python3 tools/quality_summary.py reports/2026-11-13 [--tickers "A B C"] [--telegram-out FILE]

- $GITHUB_STEP_SUMMARY 가 설정돼 있으면 마크다운 표(ticker, 품질 등급/점수, 누락 레이어/필드, 경고 수)를 append
- stdout 에 짧은 텍스트 요약 출력
- --telegram-out: 텔레그램용 한 줄 경고(HTML-escape 됨, 문제 없으면 빈 파일) 기록
- 항상 exit 0 (요약 도구 — 잡 실패 판단은 워크플로가 함)
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
from collections import Counter
from pathlib import Path

FILE_RE = re.compile(r"^([A-Z0-9.\-]+)_(\d{4}-\d{2}-\d{2})\.json$")

# 품질 객체가 없을 때(레거시 등) 대신 검사할 핵심 필드
FALLBACK_FIELDS = {
    "l1": ["dp_pct", "delta_total"],
    "l2": ["short_pct_latest", "ctb_fee", "shares_available"],
    "l3": ["max_pain", "net_gex", "flip_zone"],
    "l4": ["sma20", "bb_width_pct"],
}
LABELS = {
    "ctb_fee": "CTB", "ctb_delta_pct": "CTB Δ", "shares_available": "대차가능잔고", "borrow": "대차",
    "short_interest": "Short Interest", "short_pct_latest": "Short%", "dp_pct": "다크풀%",
    "delta_inst": "기관 OBV", "delta_pro": "프로 OBV", "delta_retail": "리테일 OBV", "iar": "IAR",
    "dp_vwap": "DP VWAP", "max_pain": "Max Pain", "net_gex": "GEX", "flip_zone": "GEX Flip",
    "option_chain": "옵션체인", "call_wall": "Call Wall", "put_wall": "Put Wall", "news": "뉴스",
    "macro": "매크로", "ohlcv": "OHLCV",
}


def _is_missing(v) -> bool:
    return v is None or (isinstance(v, float) and v != v)


def analyze(row: dict) -> dict:
    q = row.get("quality") if isinstance(row.get("quality"), dict) else None
    missing_layers, missing_fields = [], []
    if q:
        for lname, layer in (q.get("layers") or {}).items():
            if not isinstance(layer, dict):
                continue
            st = layer.get("status") or "?"
            if st != "OK":
                missing_layers.append(f"{lname}:{st}")
            for f in layer.get("missing") or []:
                missing_fields.append(f"{lname}.{f}")
    else:
        for lname, fields in FALLBACK_FIELDS.items():
            layer = row.get(lname) or {}
            miss = [f for f in fields if _is_missing(layer.get(f))]
            if miss:
                missing_layers.append(f"{lname}:{'MISSING' if len(miss) == len(fields) else 'PARTIAL'}")
                missing_fields += [f"{lname}.{f}" for f in miss]
    return {
        "grade": (q or {}).get("grade"),
        "score": (q or {}).get("score"),
        "schema": row.get("schema") or 2,
        "missing_layers": missing_layers,
        "missing_fields": missing_fields,
        "warnings": len(row.get("warnings") or []),
    }


def label(field: str) -> str:
    base = field.split(".", 1)[-1]
    return LABELS.get(base, field)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report_dir")
    ap.add_argument("--tickers", default="", help="기대 티커 목록 (공백 구분) — 리포트 없는 티커 표시")
    ap.add_argument("--telegram-out", default="")
    args = ap.parse_args()

    d = Path(args.report_dir)
    date = d.name
    rows = {}
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            m = FILE_RE.match(p.name)
            if not m or m.group(2) != date:
                continue
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                rows[m.group(1)] = {"error": "JSON parse error"}
                continue
            if isinstance(j, dict) and not j.get("is_index"):
                rows[m.group(1)] = analyze(j)

    expected = [t.strip().upper() for t in args.tickers.split() if t.strip()]
    no_report = [t for t in expected if t not in rows]
    n = len(rows)

    field_counter = Counter()
    for r in rows.values():
        for f in set(r.get("missing_fields") or []):
            field_counter[f] += 1
    grades = Counter((r.get("grade") or ("v2" if r.get("schema", 2) < 3 else "?")) for r in rows.values())

    # ── markdown ──
    md = [f"### 데이터 품질 요약 — 세션 {date}", ""]
    if n:
        md += ["| Ticker | Grade | Score | Missing layers | Missing fields | Warnings |",
               "|---|---|---|---|---|---|"]
        for t in sorted(rows):
            r = rows[t]
            if "error" in r:
                md.append(f"| {t} | — | — | {r['error']} | | |")
                continue
            score = f"{r['score']:.0f}" if isinstance(r.get("score"), (int, float)) else "—"
            grade = r.get("grade") or ("v2" if r["schema"] < 3 else "—")
            mf = ", ".join(r["missing_fields"][:8]) + (" …" if len(r["missing_fields"]) > 8 else "")
            md.append(f"| {t} | {grade} | {score} | {', '.join(r['missing_layers']) or 'OK'} | {mf or '—'} | {r['warnings']} |")
    else:
        md.append("_리포트 JSON 없음_")
    md.append("")
    md.append(f"- 리포트 {n}개 · 등급 분포: " + (", ".join(f"{g} {c}" for g, c in sorted(grades.items())) or "—"))
    if field_counter:
        md.append("- 가장 흔한 누락: " + ", ".join(f"`{f}` {c}/{n}" for f, c in field_counter.most_common(6)))
    if no_report:
        md.append(f"- 리포트 없음: {' '.join(no_report)}")
    md.append("")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(md) + "\n")

    # ── stdout ──
    print(f"[quality] session {date}: {n} report(s) · grades " +
          (" ".join(f"{g}:{c}" for g, c in sorted(grades.items())) or "-"))
    for f, c in field_counter.most_common(5):
        print(f"[quality]   missing {f}: {c}/{n}")
    if no_report:
        print(f"[quality]   no report: {' '.join(no_report)}")

    # ── telegram line ──
    if args.telegram_out:
        parts, seen = [], set()
        for f, c in field_counter.most_common():
            lb = label(f)
            if lb in seen:
                continue
            seen.add(lb)
            parts.append(f"{lb} 누락 {c}/{n}")
            if len(parts) == 3:
                break
        line = ("⚠ 데이터 경고: " + " · ".join(parts)) if parts else ""
        low = [t for t, r in rows.items() if str(r.get("grade") or "").upper()[:1] in ("D", "F")]
        if low:
            line += (" · " if line else "⚠ ") + f"품질 D/F: {' '.join(sorted(low))}"
        Path(args.telegram_out).write_text(html.escape(line, quote=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
