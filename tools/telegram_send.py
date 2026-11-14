#!/usr/bin/env python3
"""텔레그램 메시지 전송 (워크플로 공용). 메시지는 stdin, 토큰/채팅ID 는 env.

  echo "$MSG" | TG_TOKEN=... TG_CHAT=... python3 tools/telegram_send.py

- 실패 원인(HTTP 상태 · Telegram description)을 로그에 남긴다 (토큰은 절대 출력하지 않음).
- HTML 파싱 오류(400 "can't parse entities")면 태그를 제거한 일반 텍스트로 1회 재전송.
- 4096자 제한 초과 시 줄 단위로 잘라 여러 메시지로 보낸다.
- 알림 실패가 분석 잡을 실패시키지 않도록 항상 exit 0 (대신 ::warning:: 출력).
"""
from __future__ import annotations

import html
import os
import re
import sys
import time

import requests

LIMIT = 4000


def _chunks(text: str):
    buf = ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > LIMIT and buf:
            yield buf
            buf = ""
        buf += line
    if buf:
        yield buf


def _post(token: str, chat: str, text: str, parse_html: bool):
    data = {"chat_id": chat, "text": text, "disable_web_page_preview": "true"}
    if parse_html:
        data["parse_mode"] = "HTML"
    for attempt in range(3):
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20)
            try:
                j = r.json()
            except ValueError:
                j = {}
            if r.status_code == 429 and attempt < 2:
                time.sleep(int((j.get("parameters") or {}).get("retry_after", 3)))
                continue
            return r.status_code, j
        except requests.RequestException as e:
            if attempt == 2:
                return None, {"description": type(e).__name__}
            time.sleep(2)
    return None, {}


def main() -> int:
    token = os.environ.get("TG_TOKEN", "").strip()
    chat = os.environ.get("TG_CHAT", "").strip()
    if not token or not chat:
        print("::warning::telegram: TG_TOKEN / TG_CHAT secret 미설정 — 알림 생략")
        return 0
    text = sys.stdin.read().strip()
    if not text:
        print("telegram: empty message — skip")
        return 0
    ok_all = True
    for part in _chunks(text):
        code, j = _post(token, chat, part, parse_html=True)
        if code == 200 and j.get("ok"):
            continue
        desc = str(j.get("description", ""))[:200]
        print(f"::warning::telegram HTML send failed: HTTP {code} — {desc}")
        plain = html.unescape(re.sub(r"<[^>]+>", "", part))
        code2, j2 = _post(token, chat, plain, parse_html=False)
        if code2 == 200 and j2.get("ok"):
            print("telegram: sent as plain text (fallback)")
        else:
            ok_all = False
            print(f"::warning::telegram plain send failed: HTTP {code2} — {str(j2.get('description', ''))[:200]}")
    if ok_all:
        print("telegram: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
