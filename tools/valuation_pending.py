#!/usr/bin/env python3
"""조사가 필요한 종목을 한 줄(공백 구분)로 출력 — 리서치 없는 관심종목 + 분석 대기 검토 종목.

GitHub Actions(valuation-on-add.yml)가 의존성 설치 없이 실행한다(표준 라이브러리만 사용)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartmoney import review_list as RL  # noqa: E402

print(" ".join(RL.pending()))
