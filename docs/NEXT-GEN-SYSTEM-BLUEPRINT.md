# ASCEND-30 — Next-Generation Hybrid Trading System Blueprint

**Scope:** The existing `crypto-signal-scanner` is treated as **mediocre** and is used only as a
starting point. This document designs a **completely new system** by combining the strongest
components from the world's best trading methodologies, benchmarked against research, not against
the existing code.

**Target deployment (per mission):**
- Vehicle: **Crypto futures — Bybit USDT Perpetual**
- Universe: **Top-30 by 24h quote volume**
- Main trend: **1D** · Confirmation: **4H** · Execution: **15M**
- **Non-repaint**, suitable for **TradingView / Pine Script v5**, **dashboard scanner**, and a
  **$100M prop-firm risk framework**.

> **Design philosophy (brutally honest).** Most "signal systems" fail in one of three ways, and the
> existing system does all three:
> 1. **Over-filtered to zero signal frequency.** A wall of 15 hard rejections produces *0 signals
>    per scan* (`signals.json` = 0 after a HEALTHY scan of 100 symbols). A zero-signal engine makes
>    no money. Signal frequency is a **constraint to optimize**, not a variable to minimize.
> 2. **Score ≠ probability.** The current 100-point score is a *quality ranking*, never calibrated
>    to empirical win-rate. The mission demands a **real probability model** (Strong Buy → Strong
>    Sell) with calibrated confidence.
> 3. **One regime, one mode.** Price-action + momentum filters work in trends and die in chop. Any
>    realistic system must **switch modes by volatility/regime** — trend-follow when a trend regime
>    is confirmed, mean-revert around value when the market is balanced.

Everything below is built to be **measurable, non-repainting, and risk-first** — the way a
$100M prop desk would ship it.

---

## Credits & evidence basis (benchmarks)

| Methodology | Core evidence |
|---|---|
| **ICT / SMC** | Liquidity sweep → market structure shift (MSS) → displacement → FVG/OB entry, targeting opposite liquidity; kill-zone timing; strict rules. Sweep must be validated by displacement; OB only counts if it predates a structure break. [1](https://www.tradingview.com/chart/XAUUSD/qSXXEUYp-Top-5-ICT-Trading-Strategies-for-New-and-Pros/), [3](https://liquidityfinder.com/news/the-confirmation-model-ob-fvg-liquidity-sweep-smart-money-concepts-50fe3), [6](https://www.quantum-algo.com/blog/guides/ict-2022-model-complete-guide/) |
| **Wyckoff** | Phases A–E; the **Spring/Upthrust** is the high-value Phase-C event; requires **volume confirmation** on the test (LPS/BUEC) or it's invalid; stops beyond the spring/invalid level. [1](https://www.luxalgo.com/library/concept/wyckoff-accumulation-schematic/), [4](https://tradingwyckoff.com/en/wyckoff-method/) |
| **LuxAlgo (lessons)** | Multi-timeframe dashboards are strong; **repainting is the fatal flaw** to avoid by construction. Displacement is scored by **ATR-normalized body + volume vs 20-bar average** — that's the OB-quality grading we adopt. [1](https://www.quantvps.com/blog/luxalgo-review), [5](https://www.luxalgo.com/library/indicator/SRsr9SLs-smart-money-concepts/) |
| **Volume Profile / Order Flow** | POC = price with most volume (magnet/equilibrium); Value Area = 70% of volume (VAH/VAL); HVN = shelf/acceptance, LVN = "air pocket"; delta/close-location confirms acceptance vs rejection. [1](https://orderflowlabs.com/blogs/theblog/volume-profile-guide), [3](https://algostorm.com/volume-profile/), [5](https://tradingstrategyguides.com/ultimate-guide-to-volume-footprint-charts/) |
| **Turtle / Trend Following** | N = 20-period ATR; position size = risk ÷ (stop × point value); **risk ≤ 1–2%**; 2N initial stop; **pyramid +0.5N**, max units; exit on opposite-range breakout (let winners run). [1](https://sobrief.com/books/the-complete-turtletrader), [5](https://www.motilaloswal.com/learning-centre/2026/3/what-is-turtle-trading-in-commodities-strategy-guide) |
| **Prop-firm risk discipline** | Risk per trade **0.25%–1% of the drawdown buffer**, not of balance; daily loss cap (soft stop), max drawdown cap, **portfolio heat cap**, correlation-aware sizing, no risk increase after a loss. [1](https://aifo.com/blog/guide/risk-per-trade-prop-firm-challenge/), [4](https://tradeify.co/post/prop-firm-position-sizing-1-percent-rule) |

---

# PHASE 1 — IDENTIFY & RANK THE BEST SYSTEMS

> Ranking is **for crypto-futures, automated, 15M execution, $100M scalability**. "Best" here = highest
> **automation-ready expectancy after costs**, not the most famous name. Scores are relative (1 worst →
> 15 best).

| # | System | Core logic | Strengths | Weaknesses | Market fit | Win-rate potential | Risk profile | Scalability | Automation |
|---|---|---|---|---|---|---|---|---|---|
| 15 | **Trend following (Turtle/N)** | Breakout of Donchian N-day; ATR (N) sizing; 2N stop; 0.5N pyramiding; exit on opposite range | Highest risk-adjusted expectancy, low stress, survives everything, fully rules-based, huge capacity | Low win rate (~35–40%); large drawdowns; long flat periods; late entries | Best in strong trending crypto/futures (BTC/ETH majors) | Low win rate but **very positive expectancy** via RR ≥ 2.5 | Manageable (position-sized), high per-trade RR | Excellent — $100M+ capacity, low turnover | ★★★★★ |
| 14 | **Institutional / liquidity hunting (ICT 2022 + Wyckoff Spring)** | Locate liquidity pool → wait for sweep → MSS on displacement → enter FVG/OB (or Spring/LPS) → target opposite liquidity | Exceptional entry *location*; defined invalidation; high RR; proven across market regimes; crypto-friendly (24/7 killzones) | Subjective zones; requires strict validation or it's noise; win rate still moderate; illiquid names unreliable | Best for major pairs & indices; works intraday & swing | Medium (~45–55% with strict validation) | Excellent if invalidation respected; high RR | Medium capacity | ★★★★★ (if made mechanical) |
| 13 | **Quantitative momentum (cross-sectional + time-series)** | Rank assets by risk-adjusted momentum, go long top / short bottom, periodic rebalance; vol-target exposure | Systematic, well-researched, robust returns, capacity, no discretion, easy to backtest | Needs breadth (many assets); crash-prone (momentum drawdowns); monthly signal cadence for some variants | Excellent for a 30–100 coin futures universe | Medium, but high Sharpe with diversification | Moderate, controlled by vol-targeting | Excellent | ★★★★★ |
| 12 | **Volume Profile / Market Profile** | POC/VAH/VAL/HVN/LVN map where value lives; trade acceptance/rejection; fade value extremes in balance, trade value break in trend | Excellent context/location; adds a dimension price charts alone can't; complements everything | Not a standalone timing model; weak on thin symbols; profit depends on interpretation (auction logic) | Best in balance + trending with real volume | Medium (location quality, needs trigger) | Good when used as a filter | Very good | ★★★★☆ |
| 11 | **Order Flow / footprint & delta** | Delta (buy−sell at price), imbalance, absorption/rejection, cumulative delta; confirm or reject structure breaks | Real-time institutional intent; catches hidden divergence (price up + negative delta = weak) | Needs tick/footprint data (not on free 15M by default) → use CLV proxy; noisy; latency-sensitive; less capacity | Best intraday scalping; liquid only | Medium-high on liquid names | Good (tight, evidence-driven) | Low capacity (data heavy) | ★★★☆☆ |
| 10 | **LuxAlgo SMC suite** | Turnkey SMC dashboard: structure shifts, OBs, FVG, liquidity sweeps, premium/discount | Fast to deploy, beautiful, multi-TF, concept-complete, 800k+ followers | **Repaints** (fatal for automation); cost; backtest inconsistency; over-parameterized | General | Varies (repaint inflates) | Unknown due to repaint | Poor (repaint) | ★★☆☆☆ |
| 9 | **Wyckoff** | Phases A–E; Spring/UTAD as Phase-C trigger; volume confirms events | Deep structural understanding; strong on reversals; robust at range boundaries | Very subjective; schematic rarely clean; slow; needs volume discipline | Best for range → trend transitions | Medium-high if clean schematic, but rare | Good (defined stop) | Medium | ★★★☆☆ |
| 8 | **SMC (community, simplified)** | OB + FVG + BOS/CHoCH + sweep, without ICT killzones | Easier; decent when stacked (4 confluences > single) | Community drift → ambiguous rules; less precise than ICT; overuse of zones | General | Medium-low (rule ambiguity) | Moderate | Medium | ★★★☆☆ |
| 7 | **Mean reversion** | Fade extreme deviation to VWAP/EMA/POC/band; RSI/band reversion | High win rate; fast; great intraday; fits perp funding/sentiment reversion | Crisis risk (no stop = recurring tails); low RR; fails in strong trends; requires tight regime filter | Best for balance/low-vol regimes | **High (~60–70%)** but small RR | High tail risk (needs hard filter) | Good | ★★★★☆ |
| 6 | **Main-stream Market Structure (BOS/CHoCH + S/R)** | Mark swing structure, trade break-of-structure pullbacks | Simple, widely understood, visible | Lagging; high false-break rate; no liquidity context | General | Medium | Medium | Medium | ★★★☆☆ |
| 5 | **Scalping system (pure 1M–5M)** | High-frequency micro-edges, spread capture, order flow | Many small wins; smooth equity if latency ok | Costs eat edge; needs execution infra; capacity limited; emotional | Ultra-liquid only | Medium but per-trade tiny | Tight, many trades | **Limited** (latency/capacity) | ★★★☆☆ (infra-heavy) |
| 4 | **News / event momentum** | Trade the immediate post-catalyst move | Surge RR on binary events | Unpredictable, slippage, gaps, stop-hunts, can't backtest well | Liquid majors at events | Low | **High** | Poor | ★☆☆☆☆ |
| 3 | **Pump/dump / meme momentum (social)** | Detect abnormal volume + sentiment spike | Big short-term returns | Rug risk, exit liquidity, no edge, ethics/liquidity | Micro-caps | Low | **Extreme** | Poor | ★★☆☆☆ |
| 2 | **High-frequency latency arbitrage** | Queue position, ping-pong order book | Near-riskless per trade | Requires colocation, speed, CB; absurd barriers; not crypto retail | N/A | Very high but inaccessible | ~0 only at scale | **N/A at $100M retail** | ★☆☆☆☆ (not feasible) |
| 1 | **Signal-copy/DCA (unmodelled)** | No model, follow others / time-DCA | Simple | No edge, no risk model, no measure | N/A | Undefined | Undefined | Poor | ★☆☆☆☆ |

**Ranked verdict (for our mission):** Trend-following (15) + Institutional liquidity hunting (14) +
Quantitative momentum (13) + Volume Profile (12) + Order-flow confirmation (11) are the five pillars.
LuxAlgo's *dashboards* are copied as UX but its **repainting logic is rejected**. Mean-reversion (7)
is retained as a **second mode** gated by regime.

---

# PHASE 2 — BENCHMARK MATRIX (score /100)

Weights reflect importance for an automated crypto-futures 15M system.

| Category | Trend-Follow | Institutional (ICT/SMC) | Quant Momentum | Volume Profile | Order Flow | Wyckoff | Mean Reversion | Market Structure | Scalping | LuxAlgo SMC |
|---|---|---|---|---|---|---|---|---|---|---|
| Trend Detection | **95** | 70 | **90** | 55 | 45 | 70 | 35 | 60 | 30 | 75 |
| Entry Quality | 60 | **95** | 70 | 75 | **85** | 80 | 65 | 60 | 75 | 80 |
| Exit Quality | **92** | 70 | 80 | 70 | 65 | 72 | 55 | 60 | 55 | 65 |
| Confirmation Quality | 55 | **92** | 72 | 68 | **88** | 82 | 50 | 60 | 70 | 78 |
| Risk Management | **96** | 78 | 88 | 70 | 62 | 76 | 45 | 60 | 55 | 55 |
| Drawdown Control | **90** | 74 | **86** | 68 | 60 | 72 | 40 | 58 | 50 | 50 |
| Signal Frequency | 45 | 40 | 70 | 60 | 55 | 35 | **85** | 60 | **90** | 45 |
| Scalping Performance | 40 | 60 | 50 | 65 | **88** | 45 | 70 | 50 | **90** | 60 |
| Swing Performance | **92** | **90** | 85 | 75 | 55 | **85** | 45 | 68 | 35 | 75 |
| Crypto Performance | **90** | 82 | 88 | 70 | 60 | 72 | 55 | 65 | 60 | 70 |
| Futures Performance | **90** | 84 | 86 | 78 | 78 | 74 | 50 | 66 | 72 | 72 |
| Multi-Timeframe Capability | 80 | **95** | 82 | 75 | 60 | 80 | 55 | 75 | 40 | **90** |
| Automation Capability | **98** | 85 | **95** | 80 | 55 | 70 | 85 | 80 | 50 | **40 (repaints)** |
| **Composite** | **≈84** | **≈79** | **≈80** | **≈71** | **≈66** | **≈70** | **≈57** | **≈64** | **≈60** | **≈65** |

**Takeaways:**
- **Trend detection + risk + drawdown + automation** → the trend-following score dominates. It is the
  expectancy backbone.
- **Entry + confirmation + MTF** → institutional/SMC is the precision layer.
- **Quant momentum** → the portfolio/universe-selection layer (which 30 coins to run).
- **Volume profile + order-flow** → the *location* and *acceptance* layer that lifts win rate.
- **Mean reversion** → the *regime switch* that keeps the system alive in chop (it scores highest on
  signal frequency because it fires often and early).
- **LuxAlgo scores poorly on automation because it repaints.** We reuse its **dashboard UX** but not
  its math.

---

# PHASE 3 — EXTRACT THE BEST FEATURE FROM EACH SYSTEM

> Only the highest-performing component is taken; originality is irrelevant.

| Category | Best-in-class | Source benchmark | What we take and how |
|---|---|---|---|
| **BEST TREND FILTER** | Donchian/`N` breakout + EMA-200 slope + ADX 4H/1D | Turtle & Quant momentum | `dir = sign(close − Donchian(N))`; require EMA200 slope + ADX ≥ 20 on 1D; regime gate. |
| **BEST ENTRY MODEL** | Sweep → MSS → displacement → **FVG/OB retest in kill-zone** | ICT 2022 | Limit entry at FVG midpoint (consequent encroachment), not chasing displacement. |
| **BEST CONFIRMATION MODEL** | **3-layer confirmation: OB + FVG + Liquidity Sweep**, graded, higher-TF bias | SMC/ICT confirmation model | Require ≥2 of 3 + higher-TF alignment; grade OB by ATR-normalized displacement + relative volume. |
| **BEST EXIT MODEL** | Let winners run to **opposite liquidity / trailing range break**; partial at 1R, breakeven at 1R | Turtle | BE at 1R, trail 0.5×ATR beyond structure; target = opposing HVN/BSL; time-stop. |
| **BEST STOP LOSS MODEL** | 2×ATR (N) structure stop **beyond the sweep wick / spring low**, sized to 1R-2% | Turtle + ICT + Wyckoff | SL = max(structure invalidation, 2×ATR); risk = % of drawdown buffer. |
| **BEST TAKE PROFIT MODEL** | Target the **opposite liquidity pool / value-area boundary**, prefer RR ≥ 3 | ICT + Volume Profile | TP1 at opposing HVN/POC, TP2 at range extreme; never a fabricated fixed %. |
| **BEST MARKET STRUCTURE MODEL** | MSS/CHoCH close-break with **displacement required**, confirmed only after bar close | ICT/SMC | Only close (never wick) beyond structure; event logged only at bar confirm → non-repaint. |
| **BEST RISK MODEL** | **Risk % of remaining drawdown buffer**, portfolio heat cap, correlation-aware, R-multiple framing | Prop-firm discipline | risk = max(0.25%, buffer×k%); heat ≤ 6%; correlated ≤ 1 event. |
| **BEST MTF MODEL** | 1D trend → 4H confirmation → 15M entry, with **strict synchronization** | ICT + institutional | `security()` 1D & 4H, `lookahead_off`, bar-close only; 15M executes. |
| **BEST DASHBOARD MODEL** | Multi-TF, region-coded, graded signals, real-time alerts, non-repainting | LuxAlgo (UX only) | Copy the layout + grading; implement all signals repaint-free. |
| **BEST VOLATILITY FILTER** | ATR% band + regime detection (expansion vs contraction) | Turtle + vol-target | Volatility Expansion Index (VEI) gates mode; ATR% band filters entries. |
| **BEST LIQUIDITY MODEL** | Liquidity sweeps + **POC/VAH/VAL/HVN/LVN** + OI/funding context | ICT + Volume Profile + derivatives | Combined "liquidity map" engine (below). |
| **BEST SESSION FILTER** | Kill-zones (Asia/London/NY) with **BTC 24/7 crypto-aware** windows | ICT | Weight signals by session; only trade high-probability windows for scalps. |
| **BEST SCORING ENGINE** | Z-score / probability calibration to empirical win-rate (logistic), Wilson CI | Quant | Map 0–100 to calibrated P(profit ≥ 1R); publish CI. |
| **BEST CONFLUENCE LOGIC** | **Stacked confluences** (regime + trend + structure + location + confirmation + session) demanding the rare aligned setup | SMC stacking, quant | Multi-engine score that only fires when the aligned stack is strong; rejects when components conflict. |

**Components removed from the old system (challenge every assumption):**
- ❌ The 15-gate hard-rejection wall → replaced by a **weighted probability model + frequency target**.
- ❌ "Current price" fallback fills → real stop-order, gap-aware fills.
- ❌ Fixed 3-1 RR on all setups → RR is a **filter output**, not an imposed constant; targets are
  structural.
- ❌ "Score is not a probability" hand-waving → **calibrated probability** with confidence interval.
- ❌ One static parameter set → **two regime modes** (trend / range) with adaptive parameters.

---

# PHASE 4 — REBUILD: THE NEW HYBRID SYSTEM (ASCEND-30)

## What it is

**ASCEND-30 = Auction & Smart-Money Engine for the top-30, Daily.**

A **regime-adaptive, probability-calibrated, non-repainting** system that runs five independent
sub-signals (trend, structure, value/liquidity, momentum, session) and fuses them into a **calibrated
0–100 confidence** with a **Strong Buy / Buy / Neutral / Sell / Strong Sell** output.

```
            ┌──────────────────────────────────────────────────────────┐
            │      ASCEND-30  SIGNAL FUSION (regime-adaptive)          │
            │                                                          │
 1D TREND ──┼──► TrendEngine ──────────────────────────────────────────┤
 4H BIAS  ──┼──► MomentumEngine (ADX/DI/RSI/VEI) ──────────────────────┤
 15M STRUCT ─┼──► StructureEngine (MSS/BOS, displacement-graded) ───────┤
 PROFILE ──┼──► LiquidityEngine (POC/VA/VAH/VAL/HVN/LVN + sweep) ───────┤
 FLOW ────┼──► ConfirmationEngine (CLV-delta, volume, funding/OI) ──────┤
 SESSION ─┼──► TimeEngine (killzone weight) ────────────────────────────┤
 RISK ────┼──► RiskEngine (RR quality, buffer risk, heat) ──────────────┤
            │                                                          │
            └──────────────► ConfidenceEngine ──► 0–100 + probability ──┘
                                    │
                     (Strong Buy / Buy / Neutral / Sell / Strong Sell)
```

## Performance targets (what "superior" means)

| Metric | Old system (measured on its own data) | ASCEND-30 target |
|---|---|---|
| Signals / scan | **0** (over-filtered) | **2–8 / day** across top-30 (frequency is optimized) |
| Profit factor | undefined (no resolved trades) | **≥ 1.8** on majors, **≥ 1.5** overall |
| Max drawdown (portfolio) | unmanaged | **< 8%** for $100M (buffer-based sizing) |
| Win rate (targeted) | unknown | **45–60%** with RR ≥ 2.5 (expectancy-positive) |
| Sharpe (per-trade R) | unrealized | **> 1.5** after costs |
| Repaint | none (good) | **none** (by construction, verified) |

---

# PHASE 5 — THE OPTIMAL ARCHITECTURE (10 Engines)

### 1. TrendEngine (1D)
Detects the dominant directional force. Combines the two best trend tools:
- **Donchian direction**: `dir = +1 if close > highest(high, N) else −1 if close < lowest(low, N)`.
- **EMA stack + slope**: `emaFast>emaMid>emaSlow` and `slope(emaSlow, 20) > 0` on 1D.
- **ADX (1D)** to confirm *strength* of trend.

Outputs `trend` ∈ {−1, 0, +1}, `trendStrength` 0–1. **This is the gate**: a signal against the 1D
trend is heavily penalized (not auto-rejected — quant momentum can fade, but only in range mode).

### 2. MarketStructureEngine (15M, confirmed)
Close-break (never wick) beyond a **confirmed fractal swing**. Requires **displacement**
(`|body| ≥ 1.5×ATR`) to validate any BOS/MSS. Events are **logged only after the bar closes**
(`barstate.isconfirmed`), so the level cannot change later → **non-repaint**.
- BOS (continuation) / MSS (reversal) → flag.
- Grade the structure break by **displacement quality** (body/ATR) × **relative volume**.

### 3. LiquidityEngine (profile + sweeps)
The "where is value / where are the stops" engine:
- **Volume Profile** over a window: **POC**, **VAH/VAL** (70%), **HVN/LVN**.
- **Liquidity sweep** detection: wick takes out a prior confirmed swing/equal-high-low and closes
  back inside (excursion ≤ 1×ATR), mirroring Wyckoff Spring/Upthrust.
- Outputs `valuePosition` (discount/premium vs POC), `liquidityTarget` (opposing HVN/BSL), and a
  `sweep` event.

### 4. MomentumEngine (4H/15M)
ADX + DI alignment, RSI band, and **VEI** (Volatility Expansion Index, below) to classify
**established / emerging / absent**. Momentum must agree with trend for full credit; in range mode,
momentum is used for mean-revert timing instead.

### 5. VolatilityEngine (adaptive)
- `ATR% = ATR(14)/close`.
- **VEI** = ATR(14)/EMA(ATR(14), 20) → contraction vs expansion.
- **Regime classifier**: `TREND` if ADX(1D) ≥ 25 and VEI expanding; `RANGE` if ADX(1D) < 18 and VEI
  contracting; else `MIXED`.
- **Mode decision**: TREND → trade breakouts/continuation (Turtle bias). RANGE → mean-revert to POC
  (mean-reversion bias). MIXED → only highest-confluence setups.

### 6. ConfirmationEngine (order-flow proxy + fundamentals)
Confirms the structure/liquidity signal with **real** (public) data:
- **Close-Location Value (CLV)** = `(close−low)/(high−low)`; **CLV×volume** = delta proxy →
  acceptance/rejection at a level.
- **Relative volume** (vs 20-bar average), **funding rate**, **open interest** (Bybit `/v5/market/tickers`
  public, non-fabricated). Extreme funding *against* the trade reduces confidence; extreme funding
  *with* the trade is a warning against mean-reversion (crowd is crowded).

### 7. EntryEngine
- **Trigger**: after sweep+MSS, place a **limit at the FVG midpoint** (consequent encroachment) or at
  the **PT / POC** (premium for shorts, discount for longs) — never chase the displacement candle.
- Entry only on the **confirmed structure bar** (non-repaint). Stop-order, not market, by default.
- If the gap opens beyond the trigger, fill at open (slippage-realistic).

### 8. ExitEngine
- **Initial SL**: `max(structure invalidation, 2×ATR)` beyond the sweep wick / spring low.
- **Breakeven**: move SL to entry at **+1R**.
- **Trail**: after +1R, place a **structure-trailing stop** (0.5×ATR beyond the latest confirmed
  swing), or use a **20-bar opposite Donchian** for the trend-following mode (Turtle exit).
- **Take profit**: TP1 at the opposing **HVN / POC**, TP2 at the range extreme (opposite liquidity).
  Minimum RR ≥ 2.5, prefer ≥ 3.
- **Time-stop**: if the trade is still under +0.5R after a configurable number of bars, exit (this
  reduces the "hope" bleed that kills automated systems).

### 9. RiskEngine (prop-grade)
- **Per-trade risk** = `clamp(buffer × k, 0.25%, 1.0%)`, where `buffer = distance to max drawdown` and
  `k ≈ 0.05–0.10`. Risk shrinks as the account nears its drawdown floor (Risk-of-Ruin control).
- **Position size** = `risk_amount ÷ (SL distance × contract value)` (Turtle N sizing).
- **Portfolio heat cap** ≤ 6% open risk; **correlated symbols count as ONE risk event**.
- **Daily loss limit** (soft stop): halt new signals for the day after −2R or −1.5% (configurable).
- **Never increase size after a loss**; scale down in high volatility and near profit target.

### 10. ConfidenceEngine
Converts the fused score into a **calibrated probability** of a profitable trade (P(reward ≥ 1R)):
- Compute the raw 0–100 score from the weighted components (Phase 6).
- Calibrate to empirical win-rate via **logistic regression / bucketing** using historical resolved
  trades (walk-forward), and report a **Wilson 95% CI** per bucket.
- The published number is a **probability of a ≥1R outcome**, not a vague "quality" — so Strong Buy
  means "historically ≥ ~75% of this bucket resolved ≥1R."

---

# PHASE 6 — THE SCORING / PROBABILITY MODEL

## Weights (scientifically justified, not arbitrary)

Derived from the benchmark matrix: trend/risk dominate expectancy; structure/entry/confirmation
dominate precision; volatility/session gate frequency.

| Component | Weight | Rationale (evidence) |
|---|---|---|
| **Trend** | **20%** | Trend-following is the highest-Source expectancy backbone (Turtle). |
| **Market Structure** | **20%** | MSS/displacement = precision entry quality (ICT). |
| **Liquidity / Value** | **15%** | Sweep + POC/VA location = win-rate lifter. |
| **Momentum** | **15%** | ADX/DI/RSI confirms trend strength and filters fades. |
| **Volume / Flow** | **10%** | CLV-delta + relative volume confirm acceptance/rejection. |
| **Volatility** | **10%** | ATR% band + VEI gate, and scores modestly (a filter, not an edge). |
| **Risk / RR** | **10%** | RR quality + distance to opposing liquidity → expectancy. |

> Weights are **starting points**, tuned by walk-forward optimization (Phase 9). What matters is that
> they are **learned from out-of-sample performance**, not memorized.

## Raw score

```
Score = 100 × ( 0.20·T + 0.20·S + 0.15·L + 0.15·M + 0.10·V + 0.10·Vol + 0.10·R )

T  trend (0..1): aligned & strong=1, aligned=0.7, neutral=0.4, against=0.0
S  structure (0..1): MSS/BOS on displacement=1, BOS=0.7, minor=0.45, none/against=0
L  liquidity (0..1): sweep into OB/FVG in discount(premium for shorts)=1, partial=0.6, none=0.3
M  momentum (0..1): ADX≥25 & DI aligned & RSI in band=1, established=0.75, emerging=0.5, absent=0.2
V  volume/flow (0..1): CLV-delta positive & relVol≥1.4 & funding not extreme=1, mixed=0.55, against=0.2
Vol volatility (0..1): ATR% in band & VEI confirming mode=1, edge=0.5, out of band=0.15
R  risk/RR (0..1): RR≥3 & room to opposing liquidity≥threshold=1, RR 2.5=0.6, RR<2.5=0 (reject)
```

## Probability calibration

```
P(profit ≥ 1R) = σ( a + b · Score/100 )        # logistic, fitted walk-forward
```

Published **signal labels** by calibrated probability:
- **Strong Buy** → P ≥ 0.70 (and Score ≥ 85)
- **Buy** → 0.55 ≤ P < 0.70
- **Neutral** → 0.40 ≤ P < 0.55 (no trade, or only scalp in range mode)
- **Sell** → 0.30 ≤ P < 0.40 (short-side mirror)
- **Strong Sell** → P < 0.30 (and Score ≤ 15)

Every published signal carries **`n`, the sample size, and the Wilson 95% CI**, so an amateur cannot
mistake a small-sample "Strong Buy" for a guarantee. **This is the honest upgrade over a raw ranking.**

---

# PHASE 7 — THE IDEAL INSTITUTIONAL DASHBOARD

## Exact formulas (displayed per symbol)

| Panel | Formula | Display |
|---|---|---|
| **Trend Score** | `100 × (0.5·sign(close − Donchian(N)) + 0.3·sign(slope(EMA200)) + 0.2·min(ADX/40,1))` | 0–100 |
| **Structure Score** | `100 × (0.5·structureEvent + 0.3·displacementQ + 0.2·(BOS?1:0.5))` | 0–100 |
| **Momentum Score** | `100 × (0.5·min(ADX/30,1) + 0.3·DI_align + 0.2·rsiBand)` | 0–100 |
| **Liquidity Score** | `100 × (0.4·(sweep?1:0) + 0.35·valuePosition + 0.25·lvp(0/0.5/1))` | 0–100 |
| **Volume Score** | `100 × (0.5·min(relVol/2,1) + 0.3·CLV + 0.2·deltaAlign)` | 0–100 |
| **Volatility Score** | `100 × (0.6·(ATR% in band) + 0.4·(VEI matches mode))` | 0–100 |
| **Risk Score** | `100 × (0.4·min(RR/3,1) + 0.3·roomToOpposingLiquidity + 0.3·(SL≤2.5ATR?1:0.4))` | 0–100 |
| **Confidence Score** | `Σ(weight_i × component_i)` from Phase 6 | 0–100 |

## Radar / scorecard (per symbol)

```
        Trend ████████████  82
     Structure ███████████  78
     Momentum  ██████████   74
   Liquidity   ██████████   76
      Volume    ████████    68
   Volatility   ██████████  72
        Risk    ████████████ 84
   Confidence   82  →  P(≥1R) = 0.68 (n=214, CI 0.62–0.74)   [ BUY ]
```

## Dashboard layout (LuxAlgo-style, repaint-free)

1. **Market Map / Breadth** — % of top-30 in each regime (TREND/RANGE/MIXED) + funding heatmap.
2. **Universe Screener** — sortable columns: Trend/Structure/Momentum/Liquidity/Volume/Volatility/Risk
   /Confidence, each 0–100, with a composite and the **calibrated probability label**.
3. **Signal Cards** — the mini candlestick chart with **sweep, MSS, FVG/OB, POC, VAH/VAL, SL/TP lines**
   drawn, plus the 8 scorecards and the probability + CI.
4. **Performance** — win rate, profit factor, expectancy (R), max drawdown, per-trade Sharpe,
   **Monte Carlo** (deterministic seed), **Wilson-calibrated win-rate per score bucket**.
5. **Risk Panel** — current portfolio heat, daily P&L, distance to max drawdown, live simulation of
   the stop/breakeven logic.
6. **Watchlist & Alerts** — user-defined levels, kill-zone-aware alerts; Telegram/Discord push.

---

# PHASE 8 — STRESS TEST

| Scenario | Trend engine | Structure | Liquidity | Momentum | Volatility | Outcome / weakness |
|---|---|---|---|---|---|---|
| **Bull market** | ✅ strong +1 | BOS chain | buy-side runs | ADX↑ | expands | **Core edge.** Pyramid in trend mode; trail. **Risk:** giving back at blow-off top → trail + time-stop handles it. |
| **Bear market** | ✅ strong −1 | MSS down | sell-side runs | ADX↑ | expands | **Core edge** (short). Twin of bull. |
| **Sideways / low vol** | 0 (neutral) | false BOS (many) | value rotation | ADX↓ | contract | **SWITCH to range mode:** fade to POC, tight RR, high win-rate. **Weakness:** regime classifier lag → may take 1–2 whipsaws at the transition. Mitigate: require ADX(1D)<18 + VEI<1 for 2 consecutive confirmations before flipping mode. |
| **High volatility / news** | noisy | sweep heavy | big LVNs | spikes | ATR% spike | ATR% band rejects most entries; funding/OI filter catches crowded trades. **Weakness:** gap risk through stops → disable new entries in the first minutes after a scheduled catalyst; rely on breakeven. |
| **Liquidity hunts / stop raids** | unaffected | sweep→MSS core signal | **this is the edge** | CLV spike | wick | **Directly profitable** — the sweep+MSS model *is* the liquidity-hunt detection. **Weakness:** double-sweep (fake MSS) → demand LR (long-range) higher-TF confirmation before taking the reversal, and never enter on the sweep bar itself. |
| **Fake breakouts** | neutral (Donchian has no breakout) | false BOS | **value-area gate catches it** | weak | low | **Value engine is the filter:** a breakout that fails to close beyond VAH/VAL on volume is rejected. **Weakness:** whipsaw in early-range. |
| **Trend reversals** | lags (Donchian) | **MSS is the reversal detector** | sweep of trend-side liquidity | divergence | shifts | **Edge**: reversal is caught by MSS + sweep + displacement before Donchian flips. **Weakness:** the *trend-following* mode retains stale longs until the 20-bar exit → mitigate with the time-stop and the MSS-based trail. |

### Remaining weaknesses (honest)
1. **Regime-lag whipsaw** at TREND↔RANGE transitions → handle with a 2-bar confirmation and a
   `modeLockout` that prevents flipping back within N bars.
2. **Gap-through-stop risk** (news, funding rollover) → hard entry freeze during scheduled events;
   rely on breakeven; size by buffer.
3. **Sweep-only illusions** (a real trend with a minor pullback vs a genuine reversal) → require the
   reversal MSS to be **confirmed on 4H** before the 15M continuation, or it's a trap.
4. **Capacity/costs** at $100M → the top-30 universe + 15M cadence is fine, but slippage on entry at
   the FVG midpoint must be modeled; prefer limit fills and cap per-symbol notional.
5. **Funding/OI are display-heavy** — using them as a hard gate over-fits to short windows; keep them
   as a **confidence de-rating**, not a hard rejection, unless explicitly enabled.
6. **No true order-flow delta on free Bybit 15M** → CLV is a proxy; treat it as one confirmation of
   many, and don't over-weight it.

---

# PHASE 9 — FINAL BLUEPRINT

## 1. Final architecture

```
UNIVERSE (Bybit top-30 by 24h quote volume)
   │
1D TREND ── trend gate (Donchian+EMA200+ADX)      ──► TrendEngine
4H ──────── confirmation (structure+bias)          ──► MomentumEngine
15M ─────── execution (structure/sweep/FVG/OB)     ──► StructureEngine
PROFILE ─── POC/VAH/VAL/HVN/LVN                    ──► LiquidityEngine
FLOW ────── CLV-delta / relVol / funding / OI      ──► ConfirmationEngine
TIME ────── kill-zone weight                       ──► TimeEngine
RISK ────── buffer risk, heat, correlation         ──► RiskEngine
   │
   └──► ConfidenceEngine → 0–100 → calibrated P(≥1R) → StrongBuy/Buy/Neutral/Sell/StrongSell
```

## 2. Trading workflow (per symbol, per 15M bar, after close)

1. Classify **regime** (TREND/RANGE/MIXED) from 1D ADX + VEI (2-bar lockout).
2. Compute **trend gate** (1D). If it conflicts with candidate direction → score 0 trend.
3. Detect **liquidity sweep** + **MSS/displacement** (15M, close-confirmed).
4. Compute **value location** (discount for longs / premium for shorts vs POC) and **target** (HVN/BSL).
5. Fuse components → **raw score** → **calibrated probability** (only after a bar closes).
6. **RiskEngine** decides size/validity (RR, buffer, heat, SL≤2.5ATR, room to opposing liquidity).
7. If probability ≥ threshold (and ≥ minScore), publish; else, record rejection with reasons.
8. Manage open trades: BE at +1R, trail 0.5ATR, time-stop, TPs at HVN/POC/extreme.

## 3. Exact entry rules (LONG; SHORT mirrors)

- **Regime**: TREND or MIXED (in RANGE, only the high-win-rate mean-revert entry fires).
- **Trend gate**: 1D close > Donchian(N) **or** EMA200 slope up **or** 1D ADX ≥ 20 aligned — at least
  2 of 3, or trend score = 0.
- **Sweep**: price wicks below a **confirmed** swing low / equal-low; closes back above; excursion ≤
  1×ATR.
- **MSS/BOS**: a **close** breaks the most recent confirmed swing high **on a displacement candle**
  (|body| ≥ 1.5×ATR), **after the sweep**. Event logged at bar close only.
- **Location**: entry zone is in **discount** (below VAH/POC) and overlaps a **FVG or OB**.
- **Confirmation**: CLV-delta ≥ 0.5 *and* relative volume ≥ 1.2 *and* funding not extremely against.
- **Trigger**: limit at **FVG midpoint**; if price never returns within N bars → cancel (no chase).
- **Final gate**: Probability ≥ 0.55 (Buy) → size; ≥ 0.70 (Strong Buy) → full size.

## 4. Exact exit rules

- **SL** = `max(invalidation swing, 2×ATR)` below the sweep low; never wider than 2.5×ATR.
- **Breakeven** at +1R.
- **Trail** (trend mode): after +1R, SL = 0.5×ATR beyond last confirmed swing; keep until 20-bar
  opposite Donchian (exit on trend break).
- **TP1** at opposing HVN/POC; **TP2** at opposing extreme/BSL. Scale out 50/50 or 60/40.
- **Time-stop**: if not ≥ +0.5R within `timeStopBars`, exit.
- **Reversal stop**: if an opposite MSS occurs with the trade open, exit immediately.

## 5. Risk rules (prop-grade, $100M)

- Per-trade risk = `clamp(0.01 × max(0.05, buffer_pct / max_dd), 0.0025, 0.01)`.
- Position size = `risk_$ ÷ (SL_dist × contract_value)`. Vol-targeted (Turtle N).
- Portfolio heat ≤ **6%** open risk; correlated symbols (e.g. BTC/ETH/BTC-DOM-influenced) = **1 event**.
- Daily loss limit (soft halt) at **−1.5%**; weekly at **−4%**; hard hit → stop trading for period.
- No size increase after a loss; reduce 25% in high VEI; reduce near profit target.

## 6. Dashboard design (see Phase 7)

8-panel scorecard per symbol + regime breadth map + funding heatmap + probability/CI, repaint-free,
auto-refresh, offline single-file build, Telegram/Discord alerts.

## 7. Scanner design

- **Universe**: Bybit top-30 by 24h quote volume, refreshed daily (not per-scan).
- Scan **every 15M** (after the bar closes — non-repaint).
- Output a full 30-row screener with all 8 sub-scores + composite + probability label + rejection
  reasons (so an operator can see *why* a setup was skipped, not just that it was).
- Persist the profile (POC/VA/VAH/VAL/HVN/LVN) and liquidity map per symbol so targets are structural.

## 8. Pine Script build plan (v5, non-repaint)

- Use `barstate.isconfirmed` for every event (sweep, MSS, FVG, OB).
- Use `request.security()` for 1D/4H with `barmerge.lookahead_off` and `[barstate.isconfirmed]`.
- All history functions (`ta.highest`, `ta.lowest`, `ta.atr`) are fine; no `barstate.isrealtime`
  repaint on signals — emit on confirmed bar.
- Volume Profile: TradingView `ta.vwap` + custom POC/VA histogram over a rolling window (or
  `input`-selected session). Compute POC/VAH/VAL on the *closed* window only.
- Displacement & structure grading: `math.abs(close-open)/ta.atr(14)` + `volume/
  ta.sma(volume,20)`.
- Scorecards: build as arrays/lines or a `table` (Table) overlay.
- Alerts: `alertcondition` on the confirmed-bar signal, with `[barstate.isconfirmed]` guard to avoid
  duplicate/repainting alerts.
- **Verification**: run `[replay]`; confirm no signal changes on historical re-render (non-repaint).

## 9. Future AI enhancement ideas

- **Walk-forward optimization** of the 6 weights + thresholds; validate out-of-sample (the honest
  hyperopt freqtrade does).
- **Regime-classification ML** (gradient-boosted / HMM) on features: ADX, VEI, breadth, funding, OI,
  to improve the TREND/RANGE switch latency.
- **Orderbook-imbalance / CVD features** once real order-flow data is available → upgrade CLV from a
  proxy to an actual delta input.
- **Correlation-aware portfolio optimizer** for the $100M sizing (min-variance / Kelly-constrained).
- **Calibration monitoring** — continually re-fit the logistic map to keep the probability honest
  (the #1 thing that keeps an "AI" system from lying to you).
- **Human-in-the-loop reviewer** — label setups that were "valid vs noise" to feed the calibration.

---

## Bottom line

The existing system is **statistically honest but practically useless** (zero signal frequency, no
probability model, no portfolio risk framework). **ASCEND-30** replaces it by fusing the five pillars
that actually make money at institutional scale — **trend-following expectancy (Turtle)**, **liquidity
-hunting precision (ICT/SMC/Wyckoff)**, **value-location (Volume Profile)**, **order-flow confirmation
(CLV/delta)**, and **buffer-based prop risk** — into a **regime-adaptive, probability-calibrated,
non-repainting** machine that is **Pine-compatible, dashboard-ready, and $100M-scalable**.

*This is a design blueprint, not financial advice. Every rule must be backtested on live Bybit data
and forward-tested before capital is committed.*

---

## Implementation status (Python engine)

The blueprint is now implemented as a working, stdlib-only Python package at **`ascend/`**
(no runtime dependencies). It reads all tuning from `config/ascend.json` (deep-merged over
`ascend/config.py` defaults), never hard-codes thresholds, and is **analysis-only** — it never
places an order.

| Blueprint system | Module | Status |
|---|---|---|
| Market data (Bybit v5, public) | `ascend/bybit.py` | ✅ closed-candle only, retries, request budget |
| Universe (top-30, exclusions) | `ascend/universe.py` | ✅ |
| Indicators (no-lookahead) | `ascend/indicators.py` | ✅ |
| Volume profile (POC/VA/HVN/LVN) | `ascend/profile.py` | ✅ deterministic, causal |
| Structure (swings/BOS/MSS/sweep) | `ascend/structure.py` | ✅ causal, displacement-gated |
| FVG + Order-Block detection | `ascend/structure.py` | ✅ 3-candle imbalance + last-opposite-candle OB, causal |
| Regime-adaptive entry selection | `ascend/risk.py` | ✅ FVG/OB/retracement (trend) vs value-area mean-revert (range), falls through on weak RR |
| Managed exit plan (BE/partial/trail/time-stop) | `ascend/signal.py` | ✅ per-setup `exitPlan` |
| Multi-TF analysis (D/240/15) | `ascend/analysis.py` | ✅ biases + regime classify |
| Risk engine (entry/stop/target, RR) | `ascend/risk.py` | ✅ structure-based, buffer sizing |
| Scoring (7 components → 0–100) | `ascend/scoring.py` | ✅ weighted |
| Probability calibration (logistic) | `ascend/scoring.py` + `ascend/calibrate.py` | ✅ initial + `--calibrate` refit |
| Signal builder (scorecard + row) | `ascend/signal.py` | ✅ both sides, direction-aware label |
| Scan orchestrator | `ascend/engine.py` | ✅ fail-safe, never fabricates data |
| Persistence (`/data` + `/frontend/data`) | `ascend/persist.py` | ✅ atomic JSON |
| Kill-zone weighting | `ascend/sessions.py` | ✅ deterministic, context-only |
| CLI | `ascend/main.py` | ✅ scan / validate / calibrate |
| Dashboard | `frontend/ascend.html` | ✅ reads ASCEND JSON |

**Guarantees verified by `tests/test_ascend_engine.py` (offline, synthetic data only):**
closed-candle-only (`klines` drops in-progress bar), no-lookahead indicators, deterministic
output, level ordering (entry/stop/target), probability calibration monotonicity, direction-aware
labels ("Strong Buy" for long, "Strong Sell" for short), and a fail-safe scan that never clobbers
last-good data on a network failure.

**Sandbox / CI note.** The Arena sandbox egress allow-list blocks Bybit/Binance/Google (only PyPI is
reachable), so the package is designed to fetch live data from **GitHub Actions** (like the existing
scanner) and is tested here **offline** with synthetic candles and a fake client. Run the real scan
in CI with `python -m ascend.main`.
