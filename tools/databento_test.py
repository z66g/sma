#!/usr/bin/env python3
"""
Databento 테스트 — ASTS 2026-04-20 체결 데이터 받아서 MC 스타일 코호트 분류.

사용법:
    export DATABENTO_API_KEY="db-xxxxxxxx..."
    pip install databento
    python3 tools/databento_test.py

목적:
  1. Databento API 연결 확인
  2. 1 ticker 1 day trades 비용 실측
  3. per-trade tick rule + size 기반 코호트 분류로 MC와 비교

MC 벤치마크 (2026-04-20 기준, 사용자 제공):
  - Retail:       +1.2M shares
  - Professional: +579k shares
  - Institutional: +315k shares
"""
import os
import sys
from datetime import datetime
import pandas as pd

try:
    import databento as db
except ImportError:
    print("ERROR: pip install databento", file=sys.stderr)
    sys.exit(1)

API_KEY = os.environ.get("DATABENTO_API_KEY", "").strip()
if not API_KEY:
    print("ERROR: DATABENTO_API_KEY env var not set", file=sys.stderr)
    sys.exit(1)

TICKER = "ASTS"
DATE   = "2026-04-17"   # Databento DBEQ.BASIC은 T-1~T-2 지연 (available end 확인됨)
END    = "2026-04-18"   # Databento end는 exclusive
# DBEQ.BASIC = consolidated US equities (Nasdaq + NYSE + TRF/dark pool 포함)
DATASET = "DBEQ.BASIC"
SCHEMA  = "trades"

# ── MC 스타일 절대 임계치 (shares) ─────────────────────────────
RETAIL_MAX = 300        # <300 = retail
PRO_MAX    = 10_000     # 300~9,999 = professional
# >= 10,000              = institutional

print(f"[databento] fetching {TICKER} trades for {DATE}...")
client = db.Historical(API_KEY)

# 비용 사전 견적 — GB 기반
try:
    cost = client.metadata.get_cost(
        dataset=DATASET,
        schema=SCHEMA,
        symbols=[TICKER],
        start=DATE,
        end=END,
    )
    print(f"[databento] estimated cost: ${cost}")
except Exception as e:
    print(f"[databento] cost estimate failed: {e}")

# 실제 데이터 fetch
data = client.timeseries.get_range(
    dataset=DATASET,
    schema=SCHEMA,
    symbols=[TICKER],
    start=DATE,
    end=END,
    stype_in="raw_symbol",
)
df = data.to_df()

if df.empty:
    print("ERROR: empty dataframe — check date/ticker/dataset")
    sys.exit(1)

print(f"[databento] received {len(df):,} trades")
print(f"[databento] columns: {list(df.columns)}")
print(df.head(3))
print(df.tail(3))

# ── tick rule signing (per-trade) ─────────────────────────────
df = df.sort_values("ts_event").reset_index(drop=True)
df["prev_price"] = df["price"].shift(1)

def tick_sign(row):
    if pd.isna(row["prev_price"]):
        return 0
    if row["price"] > row["prev_price"]:
        return +1
    if row["price"] < row["prev_price"]:
        return -1
    return 0  # zero-tick: 중립 (MC는 여기서 bid/ask 쓰지만 우리는 quotes 없음)

df["sign"] = df.apply(tick_sign, axis=1)

# size 필드명이 버전마다 다를 수 있음
size_col = "size" if "size" in df.columns else "quantity"
if size_col not in df.columns:
    print(f"ERROR: size column not found. columns={list(df.columns)}")
    sys.exit(1)

df["signed_size"] = df["sign"] * df[size_col]

def classify(sz):
    if sz >= PRO_MAX: return "inst"
    if sz >= RETAIL_MAX: return "pro"
    return "retail"

df["cohort"] = df[size_col].apply(classify)

# ── 집계 ─────────────────────────────────────────────────────
agg = df.groupby("cohort").agg(
    signed_shares = ("signed_size", "sum"),
    total_shares  = (size_col, "sum"),
    trade_count   = (size_col, "size"),
)
print("\n=== Cohort Summary ===")
print(agg)

print("\n=== Signed shares vs MC benchmark ===")
mc = {"retail": 1_200_000, "pro": 579_000, "inst": 315_000}
for k in ("retail", "pro", "inst"):
    ours = agg.loc[k, "signed_shares"] if k in agg.index else 0
    mc_v = mc[k]
    diff = (ours - mc_v) / mc_v * 100 if mc_v != 0 else None
    print(f"  {k:7s}: ours={ours:+,.0f}  |  MC={mc_v:+,}  |  diff={diff:+.1f}%" if diff is not None
          else f"  {k:7s}: ours={ours:+,.0f}  |  MC={mc_v:+,}")

# 총 signed (참고)
print(f"\n  total signed = {df['signed_size'].sum():+,.0f}")
print(f"  total volume = {df[size_col].sum():,.0f}")
