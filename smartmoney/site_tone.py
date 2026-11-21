"""사이트 페이지(대시보드·종목 페이지·적정가 상세)의 색 톤.

3-Layer 리포트 본문(render_html)은 리포트 규격 색을 그대로 쓴다. 적정가 카드·상세 페이지는 그 규격의 부품을 빌려 쓰므로,
사이트에 내보낼 때만 같은 자리의 색을 사이트 톤으로 바꾸고, 둥근 알약 배지를 각진 배지로, 그림 문자를 글자로 바꾼다."""
from __future__ import annotations

import re

# 리포트 규격 색 → 사이트 색 (따뜻한 무채색 바탕 + 채도를 낮춘 의미색)
COLOR_MAP = {
    "#1A7F5A": "#2F7254", "#CF222B": "#B23B34", "#9A6700": "#94630F", "#0969DA": "#2C4C9C", "#A32D2D": "#8E3A33",
    "#DAFBE1": "#EDF4EF", "#FFEBE9": "#F8EDEB", "#FFF8C5": "#F6F0E2", "#DDF4FF": "#EDF1F8",
    "#F6F8FA": "#FAFAF8", "#EAEEF2": "#F2F1EE", "#D0D7DE": "#E2E0DB", "#1F2328": "#1C1B19", "#656D76": "#6E6A63",
}
_RE = re.compile("|".join(re.escape(k) for k in COLOR_MAP), re.IGNORECASE)
_EMOJI = [("⚠️ ", ""), ("⚠ ", ""), ("⚠", "!"), ("⏳", ""), ("📄", ""), ("📝 ", ""), ("✅ ", ""), ("❌ ", ""), ("▶ ", "")]


# 섹션 제목: 리포트의 황토색 제목·밑줄 대신 짙은 글자와 회색 선
_PAGE_CSS = ("<style>.sec{border-bottom:1px solid #CFCCC5 !important}.sec-t{color:#1C1B19 !important;letter-spacing:-0.01em}"
             "</style>")


def tone(html: str) -> str:
    if not html:
        return html
    out = _RE.sub(lambda m: COLOR_MAP[m.group(0).upper()], html)
    if "</head>" in out and _PAGE_CSS not in out:
        out = out.replace("</head>", _PAGE_CSS + "</head>", 1)
    out = out.replace("border-radius:12px;font-size:11px;font-weight:600;white-space:nowrap;",
                      "border-radius:3px;font-size:11px;font-weight:600;white-space:nowrap;")
    for a, b in _EMOJI:
        out = out.replace(a, b)
    return out
