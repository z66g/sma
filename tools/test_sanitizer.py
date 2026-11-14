#!/usr/bin/env python3
"""narrative.sanitize_html 회귀 테스트 — XSS 페이로드가 무력화되는지 확인.

실행: python3 tools/test_sanitizer.py   (실패 시 exit 1)
추가로 narrative.js 의 SYSTEM_PROMPT_CORE 가 narrative.py SYSTEM_PROMPT 와 같은지 확인한다.
"""
from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from narrative import SYSTEM_PROMPT, sanitize_html  # noqa: E402

ATTACKS = [
    '<img src=x onerror=alert(1)>',
    '<IMG SRC=x OnErRoR="alert(1)">',
    '<svg/onload=alert(1)>',
    '<svg><script>alert(1)</script></svg>',
    '<a href="&#106;avascript:alert(1)">x</a>',
    '<a href="&#x6A;&#x61;vascript&colon;alert(1)">x</a>',
    '<a href="jav&#x09;ascript:alert(1)">x</a>',
    '<a href=" javascript:alert(1)">x</a>',
    '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">x</a>',
    '<a href="&amp;#106;avascript:alert(1)">x</a>',
    '<scr<script>ipt>alert(1)</scr</script>ipt>',
    '<script><script>alert(1)</script></script>',
    '<<script>script>alert(1)<</script>/script>',
    '<div onmouseover="alert(1)">hover</div>',
    '<p style="background:url(javascript:alert(1))">x</p>',
    '<p style="width:expression(alert(1))">x</p>',
    '<p style="background:u&#114;l(//evil/x.png)">x</p>',
    '<p style="behavior:url(x.htc)">x</p>',
    '<iframe src="https://evil"></iframe>',
    '<object data="x"></object><embed src="x">',
    '<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>',
    '<noscript><p title="</noscript><img src=x onerror=alert(1)>"></noscript>',
    '<!--<img src=x onerror=alert(1)>-->',
    '<form action="https://evil"><button formaction="javascript:alert(1)">go</button></form>',
    '<base href="https://evil/"><meta http-equiv="refresh" content="0;url=https://evil">',
    '<style>body{background:url(https://evil)}</style>',
    '<table><td colspan="2" onclick="alert(1)">x</td></table>',
    '<details open ontoggle=alert(1)>x</details>',
    '<span id="x" srcdoc="<script>alert(1)</script>">x</span>',
]

ALLOWED_ATTRS = {"class", "style", "href", "colspan", "rowspan", "rel", "target"}
BAD_TAGS = {"script", "style", "iframe", "object", "embed", "svg", "math", "img", "form", "button",
            "base", "meta", "link", "noscript", "details", "mglyph", "mtext", "input", "textarea"}


class Inspector(HTMLParser):
    """sanitize 결과를 다시 파싱해 위험 요소가 남았는지 검사 (브라우저 관점 근사)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.problems = []

    def _check(self, tag, attrs):
        if tag in BAD_TAGS:
            self.problems.append(f"tag <{tag}>")
        for name, value in attrs:
            if name not in ALLOWED_ATTRS:
                self.problems.append(f"attr {name}")
            v = re.sub(r"[\x00-\x20]+", "", (value or "")).lower()
            if name == "href" and not (v.startswith("http://") or v.startswith("https://")):
                self.problems.append(f"href {value!r}")
            if name == "style" and ("url(" in v or "expression" in v or "javascript" in v or "behavior" in v):
                self.problems.append(f"style {value!r}")

    def handle_starttag(self, tag, attrs):
        self._check(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._check(tag, attrs)


def inspect(out: str):
    p = Inspector()
    p.feed(out)
    p.close()
    low = out.lower()
    probs = list(p.problems)
    for needle in ("<script", "javascript:", "onerror", "onload=", "onmouseover=", "ontoggle="):
        # 텍스트로 escape 된 경우(&lt;script)는 무해 — 실제 태그/속성 형태만 검사
        if needle.startswith("<") and needle in low:
            probs.append(f"raw {needle}")
    if re.search(r"<[a-z]+[^>]*\son\w+\s*=", low):
        probs.append("event handler attribute")
    return probs


def main() -> int:
    failures = 0
    for payload in ATTACKS:
        out = sanitize_html(payload)
        probs = inspect(out)
        status = "FAIL" if probs else "ok"
        if probs:
            failures += 1
        print(f"[{status}] {payload[:60]!r:66} -> {out[:70]!r}" + (f"  {probs}" if probs else ""))

    # 정상 콘텐츠는 보존돼야 함
    benign = ('<div style="font-family:-apple-system;color:#1F2328;"><h1 style="font-size:20px;">T</h1>'
              '<table><tr><td colspan="2" style="padding:6px 8px;">x &amp; y</td></tr></table>'
              '<a href="https://www.sec.gov/x?a=1&amp;b=2">src</a><br><span class="pill">A</span></div>')
    out = sanitize_html(benign)
    keep = ['style="font-family:-apple-system;color:#1F2328;"', '<h1 style="font-size:20px;">',
            'colspan="2"', 'href="https://www.sec.gov/x?a=1&amp;b=2"', 'rel="noopener noreferrer nofollow"',
            "<br>", 'class="pill"', "x &amp; y", "</div>"]
    for k in keep:
        if k not in out:
            failures += 1
            print(f"[FAIL] benign content lost: {k!r}\n       out={out!r}")
    if sanitize_html("```html\n<p>hi</p>\n```") != "<p>hi</p>":
        failures += 1
        print("[FAIL] fenced code stripping")
    if sanitize_html("<div><p>unclosed") != "<div><p>unclosed</p></div>":
        failures += 1
        print("[FAIL] unclosed tags not balanced:", sanitize_html("<div><p>unclosed"))

    # narrative.js 프롬프트 동기화 확인
    js = (ROOT / "reports" / "narrative.js").read_text(encoding="utf-8")
    m = re.search(r"const SYSTEM_PROMPT_CORE = `(.*?)`;", js, re.S)
    if not m or m.group(1) != SYSTEM_PROMPT:
        failures += 1
        print("[FAIL] reports/narrative.js SYSTEM_PROMPT_CORE != narrative.py SYSTEM_PROMPT")
    else:
        print("[ok] narrative.js system prompt in sync")

    print(f"\n{len(ATTACKS)} payloads · {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
