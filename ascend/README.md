# ASCEND-30

A **regime-adaptive, probability-calibrated, non-repainting** signal engine for
Bybit USDT perpetuals. It fuses five sub-signals (trend, structure, value/liquidity,
momentum, session) into a single graded confidence, then converts that confidence
to a **calibrated probability** `P(profit ≥ 1R)` via a logistic curve.

**Analysis-only.** This engine never places an order. It publishes signal
scorecards and risk/setup levels; execution is out of scope by design.

## What it is, and what it is not

- ✅ Bybit USDT perp *public market data* (no API key needed).
- ✅ Top **30** by 24h quote turnover, excluding leveraged tokens & stablecoins.
- ✅ 1D (trend) / 4H (confirm) / 15M (entry).
- ✅ **Non-repainting** by construction.
- ✅ Regime-adaptive (TREND / RANGE / MIXED).
- ✅ Prop-firm risk framing (risk % of remaining drawdown buffer, heat cap, RR).
- ❌ No trading, no order placement, no leverage.

## Package layout

| Module | Responsibility |
|---|---|
| `config.py` | All thresholds/weights, deep-merged over safe defaults. |
| `bybit.py` | Public market-data client (tickers, klines), retries, request budget. |
| `universe.py` | Top-30 universe with exclusions + turnover floor. |
| `indicators.py` | Pure no-lookahead SMA/EMA/RSI/ATR/ADX/relVol/Donchian/VWAP/VEI. |
| `profile.py` | Deterministic volume profile: POC / VA / VAH / VAL / HVN / LVN. |
| `structure.py` | Fractal swings, BOS/MSS, displacement, liquidity sweeps, equal levels. |
| `analysis.py` | Multi-TF snapshot: biases, regime, per-frame indicators, structure, profile. |
| `risk.py` | Entry/stop/target (RR), ATR-buffer invalidation, buffer-based sizing. |
| `scoring.py` | 7 sub-scores → weighted 0–100 → logistic P(≥1R) → direction-aware label. |
| `signal.py` | Evaluate both sides, pick the better, build the scorecard + signal row. |
| `engine.py` | Scan orchestrator: universe → klines → analyze → evaluate → persist. |
| `sessions.py` | ICT kill-zone weighting (display/context). |
| `persist.py` | Atomic JSON writes to `/data`, mirrored to `/frontend/data`. |
| `validate_data.py` | CI JSON integrity checker. |
| `main.py` | CLI (`python -m ascend.main`). |

## Hard non-repaint guarantees

1. **Closed candles only.** `BybitClient.klines()` drops any candle whose
   `close_time >= now` (the in-progress bar is never used).
2. **Causal structures.** A fractal swing at index `i` is only *usable* at
   `confirmIndex = i + swingLookback`; `find_swing_highs/lows` requires a
   strict 2-bar fractal on each side.
3. **Close-only breaks.** BOS/MSS fire only on a *close* beyond a level
   (wicks never count), and only once the level's swing is confirmed.
4. **Deterministic.** Identical input candles → identical output
   (`analyze_symbol` and every stage are pure).

## Config

Edit `config/ascend.json` (or rely on `ascend/config.py` defaults). Key groups:

- `universe` — `topN`, `minTurnover24hUsd`, exclusion lists.
- `timeframes` — `trend`/`confirm`/`entry`.
- `indicators` — all lengths.
- `structure` — swing lookback, displacement body multiple, sweep max excursion,
  equal-level tolerance, MSS window.
- `profile` — value-area %, rows.
- `signalModel` — `minScoreForSignal`, probability thresholds, `minRr`/`preferredRr`,
  stop ATR multiples, time-stop, break-even R.
- `scoring` — component **weights** (must sum to 1.0) and logistic `{a, b}`.
- `risk` — buffer risk factor, min/max risk %, heat cap, daily/weekly loss caps,
  max drawdown.

> The logistic `{a, b}` in `scoring.logistic` is an honest *initial* placeholder.
> Re-fit it from resolved trade history before trusting probabilities at scale;
> the dashboard always shows sample size so a small sample is never mistaken for
> a guarantee.

## Running

```bash
# Full top-30 scan (writes /data + /frontend/data). Needs Bybit reachable
# (works in GitHub Actions; the local sandbox allows only PyPI).
python -m ascend.main

# Fast offline smoke test with an explicit symbol list (no universe call)
python -m ascend.main --symbols BTCUSDT ETHUSDT --dry-run

# Validate the written JSON (used by CI)
python -m ascend.main --validate-only
```

## Tests

Offline, synthetic-data only (no network) — run from the repo root:

```bash
python -m pytest tests/test_ascend_engine.py -q
```

They assert the hard guarantees (closed-candle-only, no-lookahead indicators,
deterministic output, level ordering, probability calibration monotonicity,
direction-aware labels) plus that a synthetic long/short market resolves to the
correct side.

## Exit codes

`scan_once` / `main` return `0` on OK/degraded, `1` on a hard failure such as an
empty universe or zero valid data. On failure the previous valid JSON is
retained — the engine never fabricates market data.

## Data files

- `data/ascend-signals.json` — `{generatedAt, signal: [{...}]}`.
- `data/ascend-screener.json` — full per-symbol sub-score grid.
- `data/ascend-system-status.json` — health, last-scan stats, API telemetry, logs.
- `data/ascend-performance.json` — current-scan + cumulative label/direction counts
  (setup-confluence only; no win-rate until outcomes are resolved).
