# Smart Money Flow Analyzer — CLAUDE.md
## Algorithm Reference for Claude Code Implementation

> **Purpose**: This document is the complete algorithm specification for a Smart Money / Institutional Flow Analysis tool.  
> **Target**: Claude Code agent building a CLI or web-based analysis tool.  
> **Analysis Philosophy**: All analysis is conducted from the perspective of the "Architect" (market designer) and institutional investors. The goal is to identify structural capital flows, not to predict prices.

---

## TABLE OF CONTENTS

1. [Architecture Overview](#1-architecture-overview)
2. [Data Acquisition Layer](#2-data-acquisition-layer)
3. [L1 — Dark Pool Layer](#3-l1--dark-pool-layer)
4. [L2 — Short Volume Layer](#4-l2--short-volume-layer)
5. [L3 — Options Layer](#5-l3--options-layer)
6. [L4 — Chart / Technical Layer](#6-l4--chart--technical-layer)
7. [Cross-Layer Integration Rules](#7-cross-layer-integration-rules)
8. [Pattern Detection Algorithms](#8-pattern-detection-algorithms)
9. [Scenario & Probability Engine](#9-scenario--probability-engine)
10. [Output Specification](#10-output-specification)
11. [Design System](#11-design-system)
12. [File Output Rules](#12-file-output-rules)
13. [API Source Map](#13-api-source-map)

---

## 1. ARCHITECTURE OVERVIEW

### 1.1 Analysis Framework

```
INPUT PACKAGE
├── L1: Dark Pool Session Statistics + OBV (4-tab: Total/Retail/Professional/Institutional)
├── L2: Short Volume History (2 weeks) + CTB Fee timestamped history
├── L3: Options Chain + OI History + Volatility Skew + OI Distribution
├── L4: Chart Data (15min / 1hr / daily — whichever is provided)
└── CONTEXT: Ticker name + Event flags (earnings / news / expiry)

PROCESSING PIPELINE
├── SECTION 0: Macro + News + Events auto-collection (web search)
├── SECTION 1: Dark Pool Layer Analysis
├── SECTION 2: Short Volume Layer Analysis
├── SECTION 3: Options Layer Analysis
├── SECTION 4: Chart Layer Analysis
├── SECTION 5: 3-Layer Integrated Scenario
├── SECTION 6: Summary & Action Points
└── SECTION 7: Core Conclusions

OUTPUT
├── Interactive HTML Dashboard (Chart.js)
└── Markdown Archive (.md) with cumulative history table
```

### 1.2 Core Analytical Stance

- **Primary lens**: Smart Money movement and large-capital supply/demand flows
- **Bias**: Zero bullish/bearish bias — data and capital flows only
- **Attitude**: Critical — always interrogate the structural mechanism behind surface signals
- **Architect hypothesis**: Every unusual signal is first interpreted as intentional market design before being attributed to randomness

---

## 2. DATA ACQUISITION LAYER

### 2.1 Primary Data Sources (API-fetchable, no screenshots)

#### Dark Pool Data
```
Provider: Cboe Global Markets (via ChartExchange or Cboe DataShop)
Endpoint alternative: https://chartexchange.com/symbol/nasdaq/{TICKER}/dark-pool/
Data: Daily dark pool volume, price, % of total volume
Granularity: Daily sessions (Pre-market / Regular / After-hours)
Free API fallback: Quiver Quantitative dark pool endpoint
  GET https://api.quiverquant.com/beta/historical/darkpool/{TICKER}
  Headers: Authorization: Token {API_KEY}
Fields needed:
  - Date
  - Dark Pool Volume
  - Dark Pool %
  - VWAP (dark pool)
  - Block count

OBV Decomposition (4-way):
  Source: Requires tick-level trade classification (Lee-Ready algorithm or equivalent)
  Alternative: ChartExchange "OBV" tab data
  If unavailable via API: Parse from uploaded screenshot data
  Classification logic:
    Trade size < 100 shares OR < $5,000 notional → RETAIL
    Trade size 100–9,999 shares, non-block → PROFESSIONAL
    Trade size ≥ 10,000 shares OR block prints → INSTITUTIONAL
    OBV_Total = OBV_Retail + OBV_Professional + OBV_Institutional
```

#### Short Volume Data
```
Provider: FINRA (free, daily)
Endpoint: https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt
  (CNMS = consolidated tape)
Parse fields: Symbol | ShortVolume | ShortExemptVolume | TotalVolume | Market
Short% = ShortVolume / TotalVolume × 100

Alternative structured source:
  Quiver Quant: GET https://api.quiverquant.com/beta/historical/shortvolume/{TICKER}

CTB (Cost-to-Borrow) Fee:
  Source: Interactive Brokers IBKR (if authenticated), or
          Ortex: https://ortex.com/stock/{TICKER}/short-interest (scrape)
          iborrowdesk: https://iborrowdesk.com/report/{TICKER} (scrape/parse)
  Fields needed:
    - Timestamp (ideally hourly)
    - CTB Fee %
    - Available shares to borrow
    - Utilization %
```

#### Options Chain Data
```
Primary: Yahoo Finance (free)
  yfinance Python library: ticker.options, ticker.option_chain(expiry)
  Fields: strike, lastPrice, bid, ask, volume, openInterest, impliedVolatility, inTheMoney

Primary alternative: Tradier API (free tier)
  GET https://api.tradier.com/v1/markets/options/chains
  Params: symbol={TICKER}&expiration={YYYY-MM-DD}&greeks=true
  Headers: Authorization: Bearer {TOKEN}

OI History:
  Source: Cboe (official): https://www.cboe.com/delayed_quotes/options/{TICKER}
  Or: Market Chameleon options history page (requires scraping)

GEX (Gamma Exposure) Calculation:
  GEX per strike = OI × Delta × Gamma × 100 × SpotPrice
  Net GEX = Sum(Call GEX) - Sum(Put GEX)
  (See Section 5.3 for full GEX formula)

Max Pain:
  For each strike K:
    Pain_call(K) = Sum over all call strikes S < K of: OI_call(S) × (K - S)
    Pain_put(K)  = Sum over all put strikes S > K of: OI_put(S) × (S - K)
    Total_Pain(K) = Pain_call(K) + Pain_put(K)
  Max Pain = strike K that minimizes Total_Pain(K)
```

#### Chart / OHLCV Data
```
Primary: Yahoo Finance via yfinance
  ticker.history(period='3mo', interval='1d')   # daily
  ticker.history(period='5d', interval='60m')   # hourly
  ticker.history(period='1d', interval='15m')   # 15-min

Alternative: Polygon.io
  GET https://api.polygon.io/v2/aggs/ticker/{TICKER}/range/{multiplier}/{timespan}/{from}/{to}
  Params: adjusted=true&sort=asc&apiKey={KEY}

Indicators to compute (no external TA library required — implement directly):
  - SMA(20), SMA(50), SMA(200)
  - EMA(9), EMA(21)
  - Bollinger Bands: BB_mid = SMA(20), BB_std = rolling_std(20)
    BB_upper = BB_mid + 2×BB_std
    BB_lower = BB_mid - 2×BB_std
    BB_width = (BB_upper - BB_lower) / BB_mid × 100  [%]
  - RSI(14): standard Wilder smoothing
  - VWAP (intraday only)
  - Volume SMA(30) for anomaly detection
```

#### Macro / News Data
```
News & Events:
  Primary: Seeking Alpha, Yahoo Finance news endpoint
  yfinance: ticker.news  →  returns list of {title, publisher, link, providerPublishTime}
  
  SEC Filings:
    EDGAR full-text search: https://efts.sec.gov/LATEST/search-index?q={TICKER}&dateRange=custom&startdt={DATE-7d}&enddt={DATE}
    Form types to flag: 8-K, 4 (Form 4 = insider trading), S-3, 424B (shelf offerings), DEF14A

Macro indicators (web search or API):
  Fed funds rate: FRED API https://api.stlouisfed.org/fred/series/observations?series_id=FEDFUNDS&api_key={KEY}
  RRP: FRED series RRPONTSYD
  SOFR: FRED series SOFR
  DXY: yfinance ticker 'DX-Y.NYB' or 'UUP'
  WTI Oil: yfinance ticker 'CL=F'
```

### 2.2 Data Validation Rules

```python
# Before analysis, validate completeness:
REQUIRED_FIELDS = {
    'L1': ['dark_pool_pct', 'dark_pool_volume', 'obv_institutional', 'obv_professional', 'obv_retail'],
    'L2': ['short_pct', 'ctb_fee', 'shares_available'],
    'L3': ['option_chain', 'open_interest', 'implied_volatility'],
    'L4': ['ohlcv', 'volume']
}

# If a field is missing:
# - Mark section as PARTIAL
# - Continue analysis with available data
# - Flag missing fields prominently in output
# - Never fabricate missing values
```

---

## 3. L1 — DARK POOL LAYER

### 3.1 Session Volume Anomaly Detection

Dark pool sessions: **Pre-market**, **Regular Hours (09:30–16:00)**, **After-hours**

```
For each session S on date D:
  volume_anomaly_ratio(S, D) = volume(S, D) / rolling_avg(volume(S), 20_trading_days)

Thresholds:
  ≥ 3.0× → EXTREME anomaly (flag RED)
  ≥ 2.0× → HIGH anomaly (flag AMBER)
  ≥ 1.5× → MODERATE anomaly (flag BLUE)
  < 1.5× → Normal (no flag)

Dark Pool % of total volume:
  dp_pct = dark_pool_volume / total_daily_volume × 100
  
  > 50%: Institutional heavy — stealth accumulation/distribution mode
  40–50%: Elevated — significant non-lit activity
  30–40%: Normal institutional range
  < 30%: Retail-dominant or thin dark pool day

Dark Pool VWAP Anchor:
  If dp_vwap > market_close: Institutions buying above close → bullish pressure
  If dp_vwap < market_close: Institutions selling below close → bearish pressure
  Distance = |dp_vwap - close| / close × 100 [%]
  Significant if distance > 0.5%
```

### 3.2 OBV 4-Way Decomposition

```
OBV calculation (per classified trade bucket):
  For each trade i:
    if close_i > close_(i-1):
      OBV += volume_i (for that bucket)
    elif close_i < close_(i-1):
      OBV -= volume_i
    else:
      OBV unchanged

Delta OBV (daily change):
  ΔOBV_institutional = OBV_inst(today) - OBV_inst(yesterday)
  ΔOBV_professional  = OBV_pro(today)  - OBV_pro(yesterday)
  ΔOBV_retail        = OBV_ret(today)  - OBV_ret(yesterday)
  ΔOBV_total         = ΔOBV_inst + ΔOBV_pro + ΔOBV_ret
  
  NOTE: If computed ΔOBV_total ≠ provided Total tab value,
        use COMPUTED value and flag the discrepancy.

Direction determination:
  Positive ΔOBV = Net buying pressure in that cohort
  Negative ΔOBV = Net selling pressure in that cohort

Institutional Absorption Ratio (IAR):
  IAR = |ΔOBV_institutional| / (|ΔOBV_retail| + |ΔOBV_professional|)
  
  IAR > 1.5: Institutions dominating — directional intent likely
  IAR 0.8–1.5: Mixed — no clear institutional directional signal
  IAR < 0.8: Retail/professional leading — less reliable signal
```

### 3.3 OBV Divergence Detection

```
Price trend vs OBV trend comparison (rolling 5-day):
  price_slope = linregress(closes[-5:]).slope
  obv_inst_slope = linregress(obv_institutional[-5:]).slope

Divergence cases:
  BULLISH DIVERGENCE: price_slope < 0 AND obv_inst_slope > 0
    → Institutions accumulating into retail selling
    → Interpret as: Architect building position before move up
    
  BEARISH DIVERGENCE: price_slope > 0 AND obv_inst_slope < 0
    → Institutions distributing into retail buying
    → Interpret as: Architect offloading into strength
    
  CONVERGENCE: same sign → trend confirmation
  NEUTRAL: slopes near zero (< 0.001 threshold)
```

### 3.4 L1 Scenario Output Format

```
For each L1 analysis, produce:
  - Session anomaly table (Pre/Regular/AH × volume, ratio, flag)
  - OBV 4-Way horizontal bar chart (delta values)
  - Institutional Absorption Ratio (numeric)
  - Dark Pool VWAP vs Close (numeric + direction)
  - Dark Pool % (numeric + classification)
  - Divergence status (BULLISH / BEARISH / CONVERGENCE / NEUTRAL)
  - L1 Scenario: one of [ACCUMULATION / DISTRIBUTION / NEUTRAL / AMBIGUOUS]
  - Confidence: HIGH / MEDIUM / LOW
```

---

## 4. L2 — SHORT VOLUME LAYER

### 4.1 Short Volume Real Calculation

```
NEVER use reported short interest (2-week lag).
ALWAYS recompute from daily FINRA data:

  actual_short_vol(D) = short_volume(D)  [from FINRA file]
  total_vol(D) = total_volume(D)         [from FINRA file]
  short_pct(D) = actual_short_vol(D) / total_vol(D) × 100

Rolling metrics (14-day window):
  short_pct_avg_14d = mean(short_pct[-14:])
  short_pct_trend = linregress(short_pct[-14:]).slope
    positive slope = increasing short pressure
    negative slope = short covering / declining pressure

Actual short volume shares:
  short_shares(D) = short_pct(D) × total_vol(D) / 100
```

### 4.2 CTB Fee Analysis

```
CTB (Cost-to-Borrow) Fee interpretation:
  
  fee_delta_pct = (ctb_today - ctb_yesterday) / ctb_yesterday × 100
  
  NEVER report absolute CTB value as the signal.
  ALWAYS report DIRECTION and RATE OF CHANGE.

CTB Thresholds:
  < 1%: Easy-to-borrow (ETB) — low short-selling cost
  1–5%: Moderate borrow cost
  5–15%: Hard-to-borrow (HTB) territory
  > 15%: Extremely hard to borrow — short squeeze risk zone
  > 50%: Critical squeeze level — short covering likely forced

Available shares trend:
  shares_delta_pct = (shares_available_today - shares_available_yesterday) 
                     / shares_available_yesterday × 100
  
  Negative delta = supply shrinking → borrow harder
  Positive delta = supply expanding → easier to short

CTB + Availability combinations:
  CTB rising + shares falling → borrow tightening → squeeze risk rising
  CTB rising + shares rising  → counterintuitive — check for market maker hedge activity
  CTB stable/falling + shares falling → LOW-CTB PARADOX (see Section 8.4)
  CTB stable + shares stable  → Equilibrium — no structural change
```

### 4.3 Three-Case Short Classification

For each data point, classify the short activity into ONE of three cases:

```
CASE ①: Market Maker Delta Hedge
  Indicators:
    - Short% spike on same day as large options volume spike
    - CTB fee UNCHANGED despite short% increase
    - Dark pool activity concurrent (MM hedging in dark)
    - No directional short bias in options skew
  Interpretation: NOT bearish — mechanical hedge for options sold
  Action: Reduce bearish weight, check options position

CASE ②: Speculative Directional Short
  Indicators:
    - CTB fee RISING ahead of short% increase
    - Short% rising while dark pool % low (transparent shorting)
    - Put OI increasing simultaneously
    - No large options flow to explain hedge
  Interpretation: Directional bet against the stock
  Action: Bearish signal — check catalyst

CASE ③: Synthetic Hedge (Institutional Protection)
  Indicators:
    - Short% elevated BUT institutional OBV also rising (L1)
    - Long position + short sale = net flat (pairs trade or hedge)
    - CTB fee moderate, utilization < 70%
    - Large block dark pool prints concurrent
  Interpretation: Institution hedging long exposure — NOT net bearish
  Action: Ambiguous — do not assign strong direction
  
Cross-validation with L1:
  If L1 shows institutional accumulation AND L2 shows CASE ②:
    → Contradiction → flag as CONFLICTING SIGNALS
    → Dig deeper: check if different institutions (long vs short)
```

### 4.4 L2 Scenario Output Format

```
For each L2 analysis, produce:
  - Short% table (14-day) with trend arrow
  - Short volume shares (actual recomputed)
  - CTB fee table with Δ% column (not absolute)
  - Available shares with Δ% column
  - Short case classification: CASE ① / ② / ③
  - Cross-validation with L1: CONFIRMS / CONTRADICTS / NEUTRAL
  - L2 Scenario: [SHORT_SQUEEZE_RISK / DIRECTIONAL_SHORT / MM_HEDGE / SYNTHETIC_HEDGE / NEUTRAL]
  - Confidence: HIGH / MEDIUM / LOW
```

---

## 5. L3 — OPTIONS LAYER

### 5.1 Max Pain Calculation

```python
def calculate_max_pain(chain: dict) -> float:
    """
    chain: {'calls': [{strike, oi}, ...], 'puts': [{strike, oi}, ...]}
    Returns: max pain strike price
    """
    strikes = sorted(set(
        [c['strike'] for c in chain['calls']] + 
        [p['strike'] for p in chain['puts']]
    ))
    
    call_oi = {c['strike']: c['oi'] for c in chain['calls']}
    put_oi  = {p['strike']: p['oi'] for p in chain['puts']}
    
    total_pain = {}
    for K in strikes:
        pain = 0
        # Call pain: all calls with strike < K are ITM, lose if price=K
        for S in strikes:
            if S < K and S in call_oi:
                pain += call_oi[S] * (K - S)
        # Put pain: all puts with strike > K are ITM, lose if price=K
        for S in strikes:
            if S > K and S in put_oi:
                pain += put_oi[S] * (S - K)
        total_pain[K] = pain
    
    return min(total_pain, key=total_pain.get)

# IMPORTANT: Exclude rollover OI
# Rollover OI = OI that moved from previous expiry to current with identical strikes
# Detection: OI spike of >200% at specific strikes on rollover Monday
# These should be excluded from Max Pain to avoid distortion

# Pinning check (at market close):
def check_pinning(close_price: float, max_pain: float) -> str:
    distance_pct = abs(close_price - max_pain) / max_pain * 100
    if distance_pct <= 0.1:
        return "PINNING_SUCCESS"       # MM hedging cost minimized
    elif distance_pct <= 0.5:
        return "PINNING_ATTEMPT"       # Attempted but imperfect
    else:
        return "NO_PINNING"            # Price moved freely
```

### 5.2 IV Volatility Skew Analysis

```
Skew calculation:
  Standard skew = IV(25Δ Put) - IV(25Δ Call)
  Simplified (chain-based): IV at 10% OTM put vs 10% OTM call

Skew interpretation:
  Positive skew (put IV > call IV): Normal — fear of downside
  Negative skew (call IV > put IV): UNUSUAL — expected upside move or call buying pressure
  Flat skew: Balanced expectations

OTM IV spike detection (binary event):
  For each OTM strike S at distance d from ATM:
    if IV(S) > IV(ATM) × 1.5 AND d > 10%:
      → BINARY EVENT SIGNAL detected
      → Indicates market pricing in a discontinuous gap move
      → Check: earnings date, FDA decision, M&A rumor, regulatory event

Skew slope:
  skew_slope = linregress(strikes, IVs).slope
  Steep negative slope (call side IV rising with strikes): 
    → Gamma squeeze risk / call buying into upside strikes
  Steep positive slope (put side IV rising with distance): 
    → Standard crash protection buying

Term structure:
  Compare front-month IV vs next-month IV:
  Backwardation (front > back): Event risk in near term
  Contango (back > front): Normal carry
  Flat: Low event risk priced
```

### 5.3 GEX (Gamma Exposure) Calculation

```python
def calculate_gex(chain: dict, spot: float) -> dict:
    """
    Full GEX calculation with distance weighting.
    Returns: {strike: gex_value} dict, and flip_zone strike
    """
    results = {}
    
    for option_type in ['calls', 'puts']:
        for opt in chain[option_type]:
            strike = opt['strike']
            oi     = opt['oi']
            gamma  = opt['gamma']        # from Greeks
            delta  = opt['delta']
            
            # Distance weighting (reduces noise from far OTM strikes)
            distance_pct = abs(strike - spot) / spot * 100
            if distance_pct <= 2.5:   weight = 1.00
            elif distance_pct <= 5.0: weight = 0.90
            elif distance_pct <= 10:  weight = 0.65
            elif distance_pct <= 15:  weight = 0.35
            elif distance_pct <= 20:  weight = 0.18
            else:                     weight = 0.07
            
            # GEX formula: OI × Gamma × 100 (shares per contract) × Spot²
            # Sign: Calls contribute positive GEX (MM is long gamma)
            #       Puts contribute negative GEX (MM is short gamma if sold puts)
            raw_gex = oi * gamma * 100 * spot * spot
            
            if option_type == 'calls':
                gex = +raw_gex * weight
            else:
                gex = -raw_gex * weight
            
            results[strike] = results.get(strike, 0) + gex
    
    # GEX Flip Zone: strike nearest to zero crossing
    sorted_strikes = sorted(results.keys())
    flip_zone = None
    for i in range(len(sorted_strikes) - 1):
        g1 = results[sorted_strikes[i]]
        g2 = results[sorted_strikes[i+1]]
        if g1 * g2 < 0:  # sign change
            # interpolate
            flip_zone = (sorted_strikes[i] * abs(g2) + sorted_strikes[i+1] * abs(g1)) / (abs(g1) + abs(g2))
            break
    
    return {'gex_by_strike': results, 'flip_zone': flip_zone}

# GEX Interpretation:
# Positive Net GEX (overall): MM is long gamma → price pinning tendency → low volatility
# Negative Net GEX (overall): MM is short gamma → price amplification → higher volatility
# GEX Flip Zone: Price level where MM gamma transitions from positive to negative
#   If spot > flip_zone: Pinning regime (MM dampens moves)
#   If spot < flip_zone: Amplification regime (MM accelerates moves)
#   Flip zone crossing = volatility regime change
```

### 5.4 OI P/C Ratio Analysis

```
P/C Ratio (Open Interest based):
  pc_oi = total_put_OI / total_call_OI
  
  > 1.5: Heavy put loading — bearish hedge or directional short bet
  1.0–1.5: Normal range
  0.7–1.0: Slightly call-heavy
  < 0.7: Significant call buying — bullish or gamma squeeze setup

P/C Ratio (Volume based):
  pc_vol = daily_put_volume / daily_call_volume
  
  Volume P/C is more forward-looking than OI P/C.
  Compare vs OI P/C:
    vol_pc << oi_pc → Call buying accelerating despite put-heavy OI
                     → Potential reversal setup / squeeze catalyst
    vol_pc >> oi_pc → Put buying accelerating
                     → Directional downside bet increasing

Rolling OI trend (exclude rollover):
  OI_trend = (today_OI - 5d_ago_OI) / 5d_ago_OI × 100
  
  OI rising + price rising:     New longs entering → bullish confirmation
  OI rising + price falling:    New shorts entering → bearish confirmation
  OI falling + price rising:    Short covering → bullish but weak
  OI falling + price falling:   Long liquidation → bearish but weakening
```

### 5.5 L3 Scenario Output Format

```
For each L3 analysis, produce:
  - Max Pain strike (with distance from current price, %)
  - Pinning status (if near expiry ≤ 5 DTE)
  - IV Skew: direction + slope + binary event flag if applicable
  - P/C OI ratio + classification
  - P/C Volume ratio + comparison vs OI
  - OI trend (5-day)
  - Net GEX: positive / negative + flip zone strike
  - GEX chart: ALL strikes, Calls=positive bars (left not right), Puts=negative bars
  - L3 Scenario: [PINNING / GAMMA_SQUEEZE / VOLATILITY_EXPANSION / HEDGING / NEUTRAL]
  - Confidence: HIGH / MEDIUM / LOW
```

---

## 6. L4 — CHART / TECHNICAL LAYER

### 6.1 Trend Detection

```python
def detect_trend(ohlcv: list, timeframe: str) -> dict:
    closes = [c['close'] for c in ohlcv]
    volumes = [c['volume'] for c in ohlcv]
    
    # Short-term trend (5-bar)
    short_slope = linregress(range(5), closes[-5:]).slope
    # Medium-term trend (20-bar)
    med_slope   = linregress(range(20), closes[-20:]).slope
    # Long-term trend (50-bar)
    long_slope  = linregress(range(50), closes[-50:]).slope if len(closes) >= 50 else None
    
    # Moving average alignment
    sma20  = mean(closes[-20:])
    sma50  = mean(closes[-50:]) if len(closes) >= 50 else None
    sma200 = mean(closes[-200:]) if len(closes) >= 200 else None
    close  = closes[-1]
    
    # Bull/Bear alignment
    if sma200 and sma50:
        if close > sma20 > sma50 > sma200:
            ma_alignment = "FULL_BULL"
        elif close < sma20 < sma50 < sma200:
            ma_alignment = "FULL_BEAR"
        elif close > sma20 > sma50 and close < sma200:
            ma_alignment = "RECOVERING"
        else:
            ma_alignment = "MIXED"
    else:
        ma_alignment = "INSUFFICIENT_DATA"
    
    return {
        'short_slope': short_slope,
        'med_slope': med_slope,
        'ma_alignment': ma_alignment,
        'above_sma20': close > sma20,
        'above_sma50': close > sma50 if sma50 else None,
    }
```

### 6.2 Bollinger Band Analysis

```python
def analyze_bollinger(ohlcv: list, period: int = 20, std_mult: float = 2.0) -> dict:
    closes = [c['close'] for c in ohlcv]
    
    bb_mid   = mean(closes[-period:])
    bb_std   = stdev(closes[-period:])
    bb_upper = bb_mid + std_mult * bb_std
    bb_lower = bb_mid - std_mult * bb_std
    bb_width = (bb_upper - bb_lower) / bb_mid * 100  # %
    
    close = closes[-1]
    bb_position = (close - bb_lower) / (bb_upper - bb_lower)  # 0=lower, 1=upper
    
    # Width trend (expansion vs contraction)
    prev_width = (prev_upper - prev_lower) / prev_mid * 100  # prior 5-bar avg
    width_trend = "EXPANDING" if bb_width > prev_width * 1.1 else \
                  "CONTRACTING" if bb_width < prev_width * 0.9 else "NEUTRAL"
    
    # Squeeze detection (BB width near 52-week low)
    # Low width = low volatility = coiled spring
    
    # Band touches
    if close >= bb_upper:
        band_status = "UPPER_TOUCH"     # Overbought or momentum
    elif close <= bb_lower:
        band_status = "LOWER_TOUCH"     # Oversold or breakdown
    elif bb_position > 0.8:
        band_status = "UPPER_ZONE"
    elif bb_position < 0.2:
        band_status = "LOWER_ZONE"
    else:
        band_status = "MID_ZONE"
    
    return {
        'bb_upper': bb_upper,
        'bb_mid': bb_mid,
        'bb_lower': bb_lower,
        'bb_width_pct': bb_width,
        'bb_position': bb_position,
        'width_trend': width_trend,
        'band_status': band_status
    }
```

### 6.3 Support / Resistance Detection

```python
def find_support_resistance(ohlcv: list, lookback: int = 60) -> dict:
    """
    Find key S/R levels using:
    1. High-volume price nodes (Volume Profile approximation)
    2. Swing high/low pivots
    3. Round numbers proximity
    """
    closes = [c['close'] for c in ohlcv[-lookback:]]
    highs  = [c['high']  for c in ohlcv[-lookback:]]
    lows   = [c['low']   for c in ohlcv[-lookback:]]
    vols   = [c['volume'] for c in ohlcv[-lookback:]]
    
    current_price = closes[-1]
    
    # Swing pivots (5-bar pivot high/low)
    pivots = {'highs': [], 'lows': []}
    for i in range(2, len(highs) - 2):
        if highs[i] == max(highs[i-2:i+3]):
            pivots['highs'].append(highs[i])
        if lows[i] == min(lows[i-2:i+3]):
            pivots['lows'].append(lows[i])
    
    # Cluster nearby pivots (within 0.5% of each other)
    resistance_levels = cluster_prices(pivots['highs'], threshold_pct=0.5)
    support_levels    = cluster_prices(pivots['lows'],  threshold_pct=0.5)
    
    # Filter: above price = resistance, below = support
    resistances = sorted([r for r in resistance_levels if r > current_price])
    supports    = sorted([s for s in support_levels    if s < current_price], reverse=True)
    
    return {
        'immediate_resistance': resistances[0] if resistances else None,
        'key_resistance': resistances[1] if len(resistances) > 1 else None,
        'immediate_support': supports[0] if supports else None,
        'key_support': supports[1] if len(supports) > 1 else None,
    }
```

### 6.4 L4 Scenario Output Format

```
For each L4 analysis, produce:
  - Timeframe analyzed (15min / 1hr / daily)
  - MA alignment status + values
  - BB width trend + current band position
  - Key support/resistance levels (table)
  - Cross-validation with L1/L3: alignment check
  - L4 Scenario: [UPTREND / DOWNTREND / RANGE_BOUND / BREAKOUT_PENDING / BREAKDOWN_PENDING]
  - Confidence: HIGH / MEDIUM / LOW
```

---

## 7. CROSS-LAYER INTEGRATION RULES

### 7.1 Signal Matrix

```
Build a signal matrix for each layer × direction:

Layer  | Bullish | Neutral | Bearish
-------|---------|---------|--------
L1     |   ?     |    ?    |    ?
L2     |   ?     |    ?    |    ?
L3     |   ?     |    ?    |    ?
L4     |   ?     |    ?    |    ?

Assign weight:
  L1 (Dark Pool): weight = 0.35  [highest — direct institutional intent]
  L2 (Short):     weight = 0.20
  L3 (Options):   weight = 0.30
  L4 (Chart):     weight = 0.15  [lowest — lagging indicator]

Weighted score = Σ(layer_signal × layer_weight)
  where: Bullish = +1, Neutral = 0, Bearish = -1

Score → Scenario:
  > +0.5:  Strong Bullish
  +0.2 to +0.5: Mild Bullish
  -0.2 to +0.2: Neutral / Ambiguous
  -0.5 to -0.2: Mild Bearish
  < -0.5: Strong Bearish
```

### 7.2 Contradiction Resolution Rules

```
RULE 1: L1 contradicts L2+L3
  If L1=Bullish AND L2=Bearish AND L3=Bearish:
    → Investigate CASE ③ (Synthetic Hedge) for L2
    → Check if institutions are buying while hedging with options/shorts
    → Lean toward L1 signal but flag as CONFLICTED
    → Reduce L1 confidence to MEDIUM

RULE 2: L1 = L3 but L2 contradicts
  If L1=Bullish AND L3=Bullish AND L2=Bearish:
    → Very likely CASE ①: MM delta hedge
    → Classify L2 as MECHANICAL, not directional
    → Strengthen bullish bias

RULE 3: L4 contradicts L1+L2+L3
  If chart shows downtrend but L1+L2+L3 all bullish:
    → Accumulation phase — chart lags
    → Strong bullish setup — "Buy the downtrend being engineered"
    → This is the Architect's preferred setup (panic retail, accumulate institutional)

RULE 4: All layers bullish
  → Maximum conviction long setup
  → Assign [A] Bullish probability 65-75%

RULE 5: All layers bearish
  → Maximum conviction short setup
  → Assign [C] Bearish probability 65-75%

RULE 6: 2-2 split (2 bullish, 2 bearish)
  → Assign highest weight to whichever direction L1 points
  → L1 tie-breaks all 2-2 splits
```

---

## 8. PATTERN DETECTION ALGORITHMS

### 8.1 Theta Burn / L-Shape Consolidation

```
Detection criteria (all 3 must be true simultaneously for 3+ consecutive days):
  ① Volume check:
     daily_volume < rolling_30d_avg_volume × 0.60
     
  ② Price range check:
     daily_range = (high - low) / low × 100
     ATM_spread = (ATM_call_ask - ATM_call_bid + ATM_put_ask - ATM_put_bid) / 2
     daily_range < ATM_spread × 0.50  [in % terms]
     
  ③ Institutional OBV check:
     |ΔOBV_institutional| / ΔOBV_institutional_30d_avg < 0.20
     (institutional OBV nearly flat)

When ALL 3 criteria met for 3+ days:
  → Flag as THETA_BURN pattern
  → Interpretation: Architect engineering option premium decay
  → Alert: Waiting for breakout signal (see below)

BREAKOUT SIGNAL from Theta Burn:
  Trigger = volume_today > rolling_30d_avg_volume × 1.5
             AND bb_width expanding (current > prior 3-day avg × 1.1)
             AND |ΔOBV_institutional| > 5-day avg × 2.0
  
  When triggered:
    → MOVE INITIATION signal
    → Direction = sign of ΔOBV_institutional
```

### 8.2 Option Pinning Detection

```
Check only when DTE (Days To Expiry) ≤ 5

At each market close:
  close_vs_max_pain_distance = |close - max_pain| / max_pain × 100
  
  ≤ 0.1%: PINNING_SUCCESS
    → MM successfully minimized hedge costs
    → Not a "stuck" price — it's the optimal equilibrium for MM
    → Do not interpret as bullish or bearish independently
    
  ≤ 0.5%: PINNING_ATTEMPT
    → MM attempted pinning with partial success
    → Large position likely near max pain strike
    
  > 0.5%: BREAKAWAY
    → Either: new information overwhelmed MM hedging
    → Or: directional institutional flow overpowered gamma pinning

Key rule: PINNING is MM COST OPTIMIZATION, not retail trap.
  Failed pinning (price moves away from max pain) = new catalyst likely.
  Successful pinning after volatility = next week's strike cluster is the new target.
```

### 8.3 Final Absorption Pattern

```
Trigger: Detect potential market bottom

Check for 3 or more of the following 5 conditions over prior 2-5 days:

  ① Block deal concentration at close:
     block_trades_last_30min / total_block_trades > 0.50
     Sustained for 2+ consecutive days
     
  ② Price stability during block deals:
     Price impact of blocks < 0.3% (large volume, small price move = absorbed)
     
  ③ Dark pool dominance:
     dp_pct > 40% for 2+ consecutive days
     
  ④ CTB Fee stability or decline:
     ctb_delta_pct >= -5%  (not rising significantly)
     (Low/stable CTB = shorts not being squeezed, but also not adding)
     
  ⑤ Short% declining trend:
     short_pct_slope (5-day) < 0  (shorts covering / reducing)

RESULT:
  3 of 5 conditions: PARTIAL ABSORPTION (watch)
  4 of 5 conditions: PROBABLE ABSORPTION COMPLETE
  5 of 5 conditions: HIGH CONFIDENCE BOTTOM SIGNAL

Warning: Final Absorption does NOT guarantee immediate upward move.
  Distribution of phase timing:
    50% begin move within 2-5 trading days
    30% require additional 1-2 week consolidation
    20% are false signals (re-check if L1 goes bearish again)
```

### 8.4 Low-CTB Paradox Detection

```
Paradox condition:
  ctb_fee < 1.0%  (easy to borrow)
  AND
  shares_available_delta_pct < -5%  (availability shrinking)

This is COUNTERINTUITIVE: cheap to borrow but supply shrinking.

Interpretations:
  PRIMARY (most likely): 
    Market makers or large institutions are quietly building
    short positions at low cost before a catalyst they know about.
    Low CTB = they haven't tapped the visible supply yet.
    Shrinking availability = off-exchange lending being consumed.
    
  SECONDARY (less likely):
    Stock loan recall by large holders (passive ETF rebalancing)
    Technical supply shrinkage without directional intent.

Algorithm:
  if ctb < 1.0 and shares_delta < -5:
    → Flag LOW_CTB_PARADOX
    → Cross-check L1: If institutional OBV RISING despite paradox:
         → Overrides paradox → FINAL ABSORPTION interpretation
         → Institutions long + shorts hedging = net bullish
    → Cross-check L3: If put OI rising while CTB low:
         → Confirms CASE ①: MM selling puts, delta-hedging with short shares
         → Not directional bearish

Action rule:
  LOW_CTB_PARADOX without Final Absorption signals:
    → Mandatory warning: "Infinite pin risk — do not enter on technicals alone"
    → Wait for CTB to start rising (confirms short pressure real) OR
    → Wait for Final Absorption confirmation before long entry
```

### 8.5 Macro Liquidity Assessment

```
Collect via FRED API or web search at analysis time:

  1. Fed Funds Rate (FEDFUNDS): direction of last 3 decisions
  2. RRP (Overnight Reverse Repo, RRPONTSYD): level and trend
     High RRP = excess liquidity parked at Fed = bullish backdrop
     Low RRP = liquidity deployed = depends on where it went
  3. SOFR - IORB spread: measure of bank funding stress
     Positive and widening = stress
  4. DXY: strong dollar = headwind for risk assets
     DXY rising → risk-off typically → reduce upside probability 5-10%
  5. Oil (WTI): if > $90, inflationary pressure → Fed hawkish risk

Liquidity classification:
  FAVORABLE:
    Fed on hold or cutting + RRP elevated/declining slowly + DXY stable/falling
    → Add +5 to +10 percentage points to bullish scenario probability
    
  NEUTRAL:
    Mixed signals across indicators
    → No adjustment
    
  RESTRICTED:
    Fed hiking or hawkish pivot + RRP low + DXY rising + credit spreads widening
    → Add +5 to +10 percentage points to bearish scenario probability
    → Flag: "Macro headwind — reduce position size vs model output"

If API fetch fails:
  → Output: "(거시 유동성 검색 실패)"
  → Use only micro supply/demand data for probability calculation
  → Note the limitation clearly
```

---

## 9. SCENARIO & PROBABILITY ENGINE

### 9.1 Three-Phase Architect Framework

```
Every analysis produces a 3-phase timeline (Architect's playbook):

PHASE 1: SETUP [Already completed or in progress]
  - What the Architect has already done
  - Which accumulation/distribution signals are visible in hindsight
  - Time reference: Past dates only (completed events)

PHASE 2: CURRENT / TRANSITION [Present ± 1-2 weeks]
  - What is happening NOW
  - Whether consolidation, pinning, absorption is active
  - Time reference: Today and immediate future

PHASE 3: RESOLUTION [Future target]
  - Expected directional move
  - Target price range (based on BB width, OI cluster, S/R levels)
  - Expected trigger
  - Time reference: Future dates (trading days only, no weekends/holidays)

CRITICAL TIME RULES:
  ① All dates must be TRADING DAYS (exclude US market holidays)
  ② Past events get past dates
  ③ Future events get future dates
  ④ Never mix past/future in same phase
  ⑤ US market holidays to exclude: New Year's, MLK Day, Presidents Day,
     Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day,
     Thanksgiving, Christmas
```

### 9.2 Probability Matrix Calculation

```python
def calculate_scenario_probabilities(
    l1_signal: str,    # BULLISH / NEUTRAL / BEARISH
    l2_signal: str,
    l3_signal: str,
    l4_signal: str,
    macro_env: str,    # FAVORABLE / NEUTRAL / RESTRICTED
    patterns: list     # ['THETA_BURN', 'FINAL_ABSORPTION', etc.]
) -> dict:
    
    # Base weights
    weights = {'L1': 0.35, 'L2': 0.20, 'L3': 0.30, 'L4': 0.15}
    signal_map = {'BULLISH': 1, 'NEUTRAL': 0, 'BEARISH': -1}
    
    raw_score = (
        signal_map[l1_signal] * weights['L1'] +
        signal_map[l2_signal] * weights['L2'] +
        signal_map[l3_signal] * weights['L3'] +
        signal_map[l4_signal] * weights['L4']
    )
    
    # Base probabilities from raw score
    # Score range: -1.0 to +1.0
    # Map to probability distribution over [A]Bull, [B]Neutral, [C]Bear
    
    if raw_score > 0.5:
        prob_bull, prob_neutral, prob_bear = 0.65, 0.20, 0.15
    elif raw_score > 0.2:
        prob_bull, prob_neutral, prob_bear = 0.50, 0.30, 0.20
    elif raw_score > -0.2:
        prob_bull, prob_neutral, prob_bear = 0.30, 0.40, 0.30
    elif raw_score > -0.5:
        prob_bull, prob_neutral, prob_bear = 0.20, 0.30, 0.50
    else:
        prob_bull, prob_neutral, prob_bear = 0.15, 0.20, 0.65
    
    # Macro adjustment
    if macro_env == 'FAVORABLE':
        prob_bull = min(prob_bull + 0.07, 0.80)
        prob_bear = max(prob_bear - 0.07, 0.05)
    elif macro_env == 'RESTRICTED':
        prob_bear = min(prob_bear + 0.07, 0.80)
        prob_bull = max(prob_bull - 0.07, 0.05)
    
    # Pattern bonus/penalty
    if 'FINAL_ABSORPTION' in patterns:
        prob_bull += 0.05
        prob_bear -= 0.05
    if 'THETA_BURN' in patterns:
        prob_neutral += 0.05  # Still in consolidation
    if 'GAMMA_SQUEEZE_SETUP' in patterns:
        prob_bull += 0.08
    if 'SHORT_SQUEEZE_RISK' in patterns:
        prob_bull += 0.06
    
    # Normalize to sum to 1.0
    total = prob_bull + prob_neutral + prob_bear
    prob_bull    /= total
    prob_neutral /= total
    prob_bear    /= total
    
    # Cap: no scenario can exceed 80%
    prob_bull    = min(prob_bull, 0.80)
    prob_neutral = min(prob_neutral, 0.80)
    prob_bear    = min(prob_bear, 0.80)
    
    return {
        'A_bullish':  round(prob_bull * 100, 1),
        'B_neutral':  round(prob_neutral * 100, 1),
        'C_bearish':  round(prob_bear * 100, 1),
        'raw_score':  round(raw_score, 3),
        'macro':      macro_env
    }
```

### 9.3 Price Target Calculation

```
Upside target (if bullish scenario):
  T1 = immediate_resistance
  T2 = key_resistance OR (current_price × (1 + bb_width_pct/100))
  T3 = 52-week high if T2 > previous T2 estimate

Downside target (if bearish scenario):
  T1 = immediate_support
  T2 = key_support OR (current_price × (1 - bb_width_pct/100))
  T3 = 52-week low if T2 < previous T2 estimate

Max Pain gravity:
  If DTE ≤ 5: Add max_pain as a magnetic level (25% weight toward max pain)
  If DTE > 5: Max pain is directional target for current week (50% weight)

GEX flip zone:
  If price approaches flip zone:
    Below flip zone: volatility expansion expected → targets accelerate
    Above flip zone: volatility dampening → targets may not be reached cleanly
```

---

## 10. OUTPUT SPECIFICATION

### 10.1 Section Structure (Mandatory Order)

```
SECTION 0: News · Events · Filings Context [Auto Web Search]
SECTION 1: Dark Pool Layer
SECTION 2: Short Volume Layer  
SECTION 3: Options Layer
SECTION 4: Chart Layer (only if chart data provided)
SECTION 5: 3-Layer Integrated Scenario
SECTION 6: Summary & Action Points
SECTION 7: Core Conclusions [ALWAYS at bottom]
```

### 10.2 Section 0 Output

```
Three-card layout (responsive grid):

CARD 1: Upcoming Events (next 30 days)
  - Earnings call date + consensus EPS estimate
  - Investor conferences
  - Product launches / FDA dates / regulatory events
  - Options expiry dates (weekly and monthly)
  Format: Date | Event | Significance [HIGH/MED/LOW]

CARD 2: Recent News (last 7 days, max 5 items)
  - Headline
  - Source + date
  - Smart Money relevance: [BULLISH/BEARISH/NEUTRAL]
  Format: Date | Headline | Relevance badge

CARD 3: SEC Filings (max 3 recent)
  - Form type
  - Filing date
  - Key content summary (1 line)
  - Flag: insider buy/sell, dilution risk, shelf offering
  Format: Date | Form | Summary | Flag

CARD 4: Macro Environment
  - Fed policy stance: DOVISH/NEUTRAL/HAWKISH
  - Trade policy impact on sector: POSITIVE/NEUTRAL/NEGATIVE
  - Geopolitical risk level: LOW/MEDIUM/HIGH
  - DXY trend: RISING/STABLE/FALLING
  - Oil trend: RISING/STABLE/FALLING
  - AI sector momentum: HOT/COOLING/COLD (if relevant)
  - Overall liquidity environment: FAVORABLE/NEUTRAL/RESTRICTED
  Format: Indicator | Status | Impact on ticker
```

### 10.3 Section 5 — Integrated Scenario Format

```
Phase Table:
  Phase | Dates | Description | Status
  1     | past  | Setup/accumulation/distribution already done | COMPLETE
  2     | now   | Current regime (consolidation/pinning/absorption) | IN PROGRESS  
  3     | future| Resolution move + price targets | PENDING

Probability Bar Chart:
  [A] Bullish [████████████     ] XX%
  [B] Neutral [████████         ] XX%  
  [C] Bearish [██████           ] XX%
  Note: No scenario > 80%. Bars are horizontal.

Key Price Level Map (Table):
  Level Type      | Price  | Distance% | Significance
  Current Price   | $XX.XX | 0%        | —
  Max Pain        | $XX.XX | ±X%       | [HIGH/MED/LOW]
  GEX Flip Zone  | $XX.XX | ±X%       | [HIGH/MED/LOW]
  Immediate S/R   | $XX.XX | ±X%       | [HIGH/MED/LOW]
  Upside Target 1 | $XX.XX | +X%       | [HIGH/MED/LOW]
  Upside Target 2 | $XX.XX | +X%       | [HIGH/MED/LOW]
  Downside Target1| $XX.XX | -X%       | [HIGH/MED/LOW]
  Downside Target2| $XX.XX | -X%       | [HIGH/MED/LOW]
```

### 10.4 Section 6 — Summary Format

```
3-Line Summary (font-size: 11px):
  ① [DARK POOL] One sentence, most critical L1 finding
  ② [SHORT/CTB] One sentence, most critical L2 finding  
  ③ [OPTIONS]   One sentence, most critical L3 finding

Trigger Checklist (Table):
  Trigger             | Type    | Status      | Implication
  Volume spike >150%  | BULLISH | WATCHING    | Breakout start
  CTB fee > 5%        | BULLISH | NOT MET     | Squeeze setup
  Price > GEX flip    | BULLISH | [MET/NOT]   | Volatility dampened
  Price < Max Pain    | BEARISH | [MET/NOT]   | Downward gravity
  [Add 3-5 more relevant triggers based on ticker]

Risk Factors:
  - List 3-5 specific risks that could invalidate the primary scenario
  - Format: [RISK TYPE]: Description + potential impact
```

### 10.5 Section 7 — Core Conclusions Format

```
2-3 paragraphs of bold body text, minimum 80 words each:

Paragraph 1: Most important single finding across all layers
  - What is the paradox or unexpected signal?
  - Why does it matter?
  - What does it reveal about Architect intent?

Paragraph 2: Structural mechanism interpretation
  - What is the smart money doing and why?
  - How do the layers fit together?
  - What is the risk if this interpretation is wrong?

Paragraph 3 (optional): Decisive trigger
  - What single event/signal would confirm or deny the thesis?
  - What would force a complete reversal of the analysis?

PLACEMENT: Always the last section in both HTML widget and MD file.
```

---

## 11. DESIGN SYSTEM

### 11.1 Color Palette (Hard-coded HEX, no CSS variables)

```css
/* Backgrounds */
--bg-outer:     #FFFFFF
--bg-card:      #F6F8FA
--bg-panel:     #EAEEF2
--bg-border:    #D0D7DE

/* Text */
--text-primary: #1F2328
--text-secondary: #656D76

/* Semantic */
--color-bull:   #1A7F5A   /* bullish / call / buy / institutional */
--color-bear:   #CF222B   /* bearish / put / sell */
--color-warn:   #9A6700   /* warning / flip / caution / section headers */
--color-info:   #0969DA   /* info / institutional blue */

/* Alert backgrounds */
--alert-green:  #DAFBE1
--alert-red:    #FFEBE9
--alert-amber:  #FFF8C5
--alert-blue:   #DDF4FF

/* Charts */
--chart-bg:     #FFFFFF
--chart-grid:   #EAEEF2
--bar-opacity:  0.85
```

### 11.2 Typography

```css
font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
/* Apply this to ALL elements — widgets, charts, tables */
/* Chart.js: options.plugins.legend.labels.font.family = above string */
/* NEVER use CSS variables for fonts */
```

### 11.3 OBV 4-Way Bar Chart Specification

```
Chart type: Horizontal bar chart
Orientation: Horizontal bars extending left (negative) or right (positive)
Data: DELTA OBV values (change from previous session)

Color mapping:
  Institutional: #1A7F5A
  Professional:  #9A6700  
  Retail:        #CF222B
  Total:         #A32D2D  (slightly different from bear to distinguish)

Labels: Show numeric value at bar end (e.g., "+2.4M", "-1.1M")
X-axis: Symmetric around 0 (auto-scale to max absolute value)
Grid lines: #EAEEF2
Background: #FFFFFF
Font: as specified in 11.2
Title: "OBV 4-Way Decomposition (Δ from prior session)"
```

### 11.4 GEX Chart Specification

```
Chart type: Vertical bar chart
X-axis: All strikes (sorted ascending, must include ALL strikes from chain)
Y-axis: GEX value (positive up, negative down)

MANDATORY DIRECTION RULE (NEVER REVERSE):
  Call GEX bars: POSITIVE values → bars go UP (green)
  Put GEX bars: NEGATIVE values → bars go DOWN (red)
  
  Call OI is on the LEFT column of options chain
  Put OI is on the RIGHT column of options chain
  This maps to: Calls=Positive GEX, Puts=Negative GEX
  This rule is ABSOLUTE and cannot be reversed for any reason.

Color:
  Positive bars: #1A7F5A (opacity 0.85)
  Negative bars: #CF222B (opacity 0.85)
  
Flip zone marker: Vertical line or shaded zone at flip_zone strike
  Color: #9A6700
  Label: "GEX Flip: $XX.XX"

Current price marker: Vertical dashed line
  Color: #0969DA
  Label: "Current: $XX.XX"

Max Pain marker: Vertical dotted line
  Color: #9A6700
  Label: "Max Pain: $XX.XX"

Background: #FFFFFF
Grid: #EAEEF2
Title: "Gamma Exposure (GEX) Distribution — {EXPIRY DATE}"
```

### 11.5 Probability Bar Chart Specification

```
Chart type: Horizontal bar chart (3 bars)
X-axis: 0% to 80% (max cap)
Bar labels: At bar end, show "XX%"

Color:
  [A] Bullish: #1A7F5A
  [B] Neutral: #9A6700
  [C] Bearish: #CF222B

Format:
  [A] Bullish Scenario  [██████████████     ] 55%
  [B] Neutral / Delay   [████████           ] 30%
  [C] Bearish Scenario  [████               ] 15%

Include below chart:
  Raw Score: +0.35 | Macro: FAVORABLE
```

### 11.6 Section Header Format

```html
<!-- Section header with pill badges -->
<div style="display:flex; align-items:center; justify-content:space-between; 
            border-bottom: 0.5px solid #9A6700; margin-bottom:12px;">
  <span style="color:#9A6700; font-weight:700; font-size:14px;">
    ▶ SECTION N [SECTION NAME]
  </span>
  <div style="display:flex; gap:6px;">
    <!-- Positive finding badge -->
    <span style="background:#DAFBE1; color:#1A7F5A; 
                 padding:2px 8px; border-radius:12px; font-size:11px; font-weight:600;">
      Key finding text
    </span>
    <!-- Warning badge -->
    <span style="background:#FFF8C5; color:#9A6700; 
                 padding:2px 8px; border-radius:12px; font-size:11px; font-weight:600;">
      Another finding
    </span>
  </div>
</div>

<!-- Badge types:
  kp-pos:  background:#DAFBE1, color:#1A7F5A   (bullish finding)
  kp-neg:  background:#FFEBE9, color:#CF222B   (bearish finding)
  kp-warn: background:#FFF8C5, color:#9A6700   (caution / ambiguous)
  kp-info: background:#DDF4FF, color:#0969DA   (informational)
-->
```

### 11.7 Table Specification

```css
/* Header row */
th {
  background: #EAEEF2;
  color: #656D76;
  padding: 4px 6px;
  border: 0.5px solid #D0D7DE;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

/* Alternating rows */
tr:nth-child(odd)  { background: #FFFFFF; }
tr:nth-child(even) { background: #F6F8FA; }

/* Cell */
td {
  padding: 4px 6px;
  border: 0.5px solid #D0D7DE;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #1F2328;
}

/* Value coloring */
.value-bull { color: #1A7F5A; font-weight: 600; }
.value-bear { color: #CF222B; font-weight: 600; }
.value-warn { color: #9A6700; font-weight: 600; }

/* Highlighted rows */
.row-highlight-green { background: #DAFBE1 !important; }
.row-highlight-amber { background: #FFF8C5 !important; }
```

---

## 12. FILE OUTPUT RULES

### 12.1 Markdown Archive File

**Filename format**: `SmartMoney_{TICKER}_{YYYY-MM-DD}.md`

**Required sections**:
```markdown
# Smart Money Analysis: {TICKER}
**Analysis Date**: {YYYY-MM-DD}  
**Current Price**: ${XX.XX}  
**Options Expiry**: {YYYY-MM-DD}  
**Analyst**: Smart Money Analyzer v2.0

---

## Key Metrics Summary

| Metric | Value |
|--------|-------|
| Institutional OBV Δ | {value} |
| Professional OBV Δ | {value} |
| Retail OBV Δ | {value} |
| Total OBV Δ | {value} |
| Dark Pool % | {XX.X}% |
| Institutional Absorption Ratio | {X.XX} |
| Short % | {XX.X}% |
| CTB Fee | {X.XX}% |
| Available Shares | {XXX,XXX} |
| Max Pain | ${XX.XX} |
| GEX Flip Zone | ${XX.XX} |
| Net GEX | {+/-XXX}M |
| Scenario [A] Bullish | {XX}% |
| Scenario [B] Neutral | {XX}% |
| Scenario [C] Bearish | {XX}% |

---

## Architect Phase Structure
- **Phase 1 (Completed)**: {description}
- **Phase 2 (Current)**: {description}  
- **Phase 3 (Target)**: {description} | Target: ${XX.XX}–${XX.XX}

---

## Core Conclusion
{2-3 paragraph summary from Section 7}

---

## Cumulative History

| 날짜 | 종가 | 기관OBV | 프로OBV | 리테일OBV | 전체OBV | Short% | CTB | 가용잔고 | GEX플립 | MaxPain |
|------|------|---------|---------|-----------|---------|--------|-----|---------|---------|---------|
| {DATE} | ${CLOSE} | {INST} | {PRO} | {RETAIL} | {TOTAL} | {S%}% | {CTB}% | {AVAIL} | ${FLIP} | ${PAIN} |
```

**Cumulative history rules**:
- When a previous MD file is provided as input, PRESERVE all existing rows
- Only ADD a new row for the current analysis date
- Never delete or modify historical rows
- Sort by date ascending (oldest first)

### 12.2 HTML Standalone File

**Filename format**: `{TICKER}_3Layer_Forensic_{YYYY-MM-DD}.html`

**Requirements**:
```html
<!DOCTYPE html>
<!-- Standalone file — no external dependencies except CDN below -->
<!-- Chart.js CDN: https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js -->

<!-- Must include ALL of the following: -->
1. SECTION 0: Cards layout (news, events, filings, macro)
2. SECTION 1: Dark Pool table + OBV 4-Way horizontal bar chart (Chart.js)
3. SECTION 2: Short volume table + CTB fee trend
4. SECTION 3: Options table + GEX vertical bar chart + P/C data
5. SECTION 4: Chart summary (if data available)
6. SECTION 5: Phase table + Probability horizontal bar chart + Price level table
7. SECTION 6: 3-line summary + trigger checklist
8. SECTION 7: Core conclusions (bold, bottom of page)

<!-- Technical requirements: -->
- All charts: Chart.js 4.x
- All fonts: hardcoded (no CSS variables)
- All colors: hardcoded hex (no CSS variables)
- Responsive: max-width 1200px, centered
- Print-friendly: @media print { ... }
- No external API calls in HTML (static data embedded)
- Chart.js initialization BEFORE any markdown/archive blocks in code order
```

### 12.3 Delivery Rules

```
Both files MUST be generated for every analysis:
  1. .md file → /mnt/user-data/outputs/SmartMoney_{TICKER}_{YYYY-MM-DD}.md
  2. .html file → /mnt/user-data/outputs/{TICKER}_3Layer_Forensic_{YYYY-MM-DD}.html

Both files MUST be provided via present_files tool.
Never provide only one without the other.
If generation fails, output error details and retry.
```

---

## 13. API SOURCE MAP

### 13.1 Free-tier APIs (No payment required)

```
FINRA Short Volume (daily, T+1):
  URL: https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt
  Format: pipe-delimited text
  Latency: T+1 (available next morning)
  Parse: grep for ticker symbol

Yahoo Finance (via yfinance Python library):
  install: pip install yfinance
  Usage: yf.Ticker('{TICKER}')
  Provides: OHLCV, options chain, news, financials
  Rate limit: ~2000 requests/hour (unofficial)

FRED (Federal Reserve Economic Data):
  Base URL: https://api.stlouisfed.org/fred/series/observations
  Key: Free registration at fred.stlouisfed.org
  Series used:
    FEDFUNDS     → Fed funds rate
    RRPONTSYD    → Overnight reverse repo
    SOFR         → Secured Overnight Financing Rate
    DGS10        → 10-year Treasury yield
    DTWEXBGS     → Dollar index (trade-weighted)

SEC EDGAR:
  Full-text search: https://efts.sec.gov/LATEST/search-index
  Filings: https://www.sec.gov/cgi-bin/browse-edgar
  No auth required, rate limit: 10 req/sec

Polygon.io (free tier):
  Base URL: https://api.polygon.io/v2/
  Free: 5 API calls/minute, last 2 years of data
  Register: polygon.io
```

### 13.2 Paid APIs (Higher quality, preferred if budget allows)

```
Tradier (options data):
  URL: https://api.tradier.com/v1/markets/options/
  Tier: Developer (free) → $10/month for real-time
  Best for: Real-time options chain with Greeks

Quiver Quantitative:
  URL: https://api.quiverquant.com/
  Provides: Dark pool, short volume, political trades, lobbying
  Tier: ~$20/month for full access

Cboe DataShop:
  URL: https://datashop.cboe.com/
  Provides: Official options data, historical OI, GEX
  Tier: Varies by product ($50-500/month)

Ortex:
  URL: https://app.ortex.com/
  Provides: CTB fees, short interest, utilization
  Tier: ~$100/month

Unusual Whales:
  URL: https://unusualwhales.com/
  Provides: Dark pool prints, options flow, GEX
  Tier: ~$50/month
```

### 13.3 Data Pipeline Architecture

```python
# Recommended implementation structure for Claude Code:

class SmartMoneyAnalyzer:
    def __init__(self, ticker: str, analysis_date: str, config: dict):
        self.ticker = ticker
        self.date = analysis_date
        self.config = config  # API keys, preferences
    
    async def fetch_all_data(self) -> dict:
        """Parallel data fetching for all layers"""
        tasks = [
            self.fetch_l1_darkpool(),    # Dark pool + OBV
            self.fetch_l2_short(),       # Short volume + CTB
            self.fetch_l3_options(),     # Options chain + OI
            self.fetch_l4_chart(),       # OHLCV + indicators
            self.fetch_macro(),          # Fed, DXY, macro
            self.fetch_news(),           # News + SEC filings
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return self.validate_and_package(results)
    
    def run_analysis(self, data: dict) -> dict:
        """Main analysis pipeline"""
        l1 = self.analyze_darkpool(data['l1'])
        l2 = self.analyze_short(data['l2'])
        l3 = self.analyze_options(data['l3'])
        l4 = self.analyze_chart(data['l4'])
        
        patterns = self.detect_patterns(l1, l2, l3, l4)
        macro = self.classify_macro(data['macro'])
        scenarios = self.calculate_scenarios(l1, l2, l3, l4, macro, patterns)
        
        return {
            'sections': {
                's0': self.build_section0(data['news'], data['macro']),
                's1': self.build_section1(l1),
                's2': self.build_section2(l2),
                's3': self.build_section3(l3),
                's4': self.build_section4(l4),
                's5': self.build_section5(scenarios, patterns),
                's6': self.build_section6(l1, l2, l3, scenarios),
                's7': self.build_section7(l1, l2, l3, l4, scenarios),
            },
            'metadata': {
                'ticker': self.ticker,
                'date': self.date,
                'price': data['l4']['current_price'],
                'patterns_detected': patterns,
            }
        }
    
    def generate_outputs(self, analysis: dict) -> tuple[str, str]:
        """Generate HTML and MD files"""
        html = self.render_html(analysis)
        md   = self.render_markdown(analysis)
        return html, md
```

---

## APPENDIX: PROHIBITED OUTPUT PATTERNS

```
NEVER include in analysis output:
  ✗ Internal rule names: "규칙 A", "Rule K", "Pattern Detection Algorithm 8.3"
  ✗ Formula derivations unless specifically requested
  ✗ "I cannot determine" without trying
  ✗ Fabricated data for missing fields
  ✗ Weekend or holiday dates in Phase timelines
  ✗ Absolute CTB values without delta/direction
  ✗ Total OBV from screenshot if it differs from computed sum
  ✗ CSS variable references (var(--color-x))
  ✗ Bullish or bearish bias embedded in language (use data language only)
  ✗ Section 7 anywhere other than the very bottom of the report

ALWAYS include in every analysis:
  ✓ Both .md and .html files
  ✓ Section 7 core conclusions
  ✓ Institutional Absorption Ratio (numeric)
  ✓ Dark Pool VWAP vs Close comparison
  ✓ Short case classification (Case ①/②/③)
  ✓ Max Pain with distance from current price
  ✓ GEX flip zone with ALL strikes in chart
  ✓ Scenario probability % (summing to 100%)
  ✓ Phase 1/2/3 with trading-day-only dates
  ✓ Macro liquidity classification
  ✓ Cross-layer validation statement for each section
```

---

*End of CLAUDE.md — Smart Money Flow Analyzer Algorithm Specification*  
*Version: 2.0 | Format: Claude Code Implementation Reference*
