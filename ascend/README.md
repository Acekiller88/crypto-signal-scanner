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
| `signal.py` | Evaluate both sides, pick the better, build the scorecard + signal row + exit plan. |
| `structure.py` | Also detects **FVG** (3-candle imbalance) and **order blocks** (the last opposite candle before a displacement). |
| `engine.py` | Scan orchestrator: universe → klines → analyze → evaluate → persist. |
| `sessions.py` | ICT kill-zone weighting (display/context). |
| `persist.py` | Atomic JSON writes to `/data`, mirrored to `/frontend/data`. |
| `validate_data.py` | CI JSON integrity checker. |
| `main.py` | CLI (`python -m ascend.main`). |

## Entry model (regime-adaptive)

`risk.build_setup` tries entry candidates in priority order and keeps the first
that yields a valid, R:R-gated setup:

| Regime | Entry priority |
|---|---|
| **TREND / MIXED** | FVG consequent-encroachment → order block → 0.5 displacement retracement |
| **RANGE** | value-area mean-revert (discount VAL for long / premium VAH for short) → FVG → order block → retracement |

A weak FVG never forces a bad trade — if it can't fund the minimum R:R it falls
through to the classic continuation entry. The chosen source is recorded in
`setup.entrySource` (`fvg` / `order_block` / `retracement` / `value_area`).

## Managed exit plan

Every published setup also carries an `exitPlan` (Turtle + prop discipline):

* **breakeven** — move the stop to entry at `+1R`.
* **partial** — trim `partialFraction` at `partialAtR`.
* **trail** — trail `trailAtrMultiple`·ATR beyond the most recent confirmed structure.
* **timeStop** — exit if not beyond breakeven within `timeStopBars` 15M bars.

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

## Deployment (make it live) — Cloudflare Pages + GitHub Actions

Two moving parts, both free (no API key, public Bybit data only):

1. **GitHub Actions runs the scan on a schedule** and commits the resulting JSON.
   The workflow at `.github/workflows/ascend.yml` runs `python -m ascend.main`
   every hour (plus on push to `main` and manual dispatch), then
   `python -m ascend.validate_data`, then commits/pushes only when `data/ascend-*`
   actually changed.
2. **Cloudflare Pages serves the static dashboard** from the `frontend/` directory,
   which contains `ascend.html` and `frontend/data/ascend-*.json`.

### One-time setup

**GitHub (the scheduler):**
1. Push the repo to GitHub and confirm the data files are present:
   `data/ascend-*.json` and `frontend/data/ascend-*.json` (committed as JSON-in-git,
   so Pages can serve them).
2. **Settings → Actions → General → Workflow permissions → Read and write
   permissions** (the scan workflow commits + pushes JSON). If it's set to
   read-only, the commit step will fail.
3. The `ascend.yml` workflow shows up under **Actions**. Trigger it once manually
   (Run workflow) to confirm it succeeds end-to-end.

**Cloudflare Pages (the host):**
1. Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git**.
2. Select the repository and branch `main` (or the branch you want live).
3. Build settings:
   - **Framework preset:** None
   - **Build command:** *(leave empty)*
   - **Build output directory:** `frontend`
4. Save and deploy. Your live URL is `https://<project>.pages.dev`.

### How updates flow

Every hour the workflow scans Bybit → overwrites `data/ascend-*.json` +
`frontend/data/ascend-*.json` → pushes. Cloudflare Pages automatically redeploys
on each commit, so the dashboard data refreshes itself — no manual step.

### Important notes for ASCEND

- **Bybit must be reachable from the GitHub runner.** GitHub-hosted runners have
  unrestricted egress, so this works; the *local sandbox* is different (it only
  allows PyPI), which is why live data is fetched in CI, not locally.
- **Metadata is analysis-only.** The dashboard and JSON never contain an
  instruction to trade; they publish a scorecard, probability, entry/stop/target
  and a managed exit plan.
- **Verification:** after the first scheduled run, check
  `data/ascend-system-status.json` for `health == "HEALTHY"` and a non-zero
  `signalsGenerated` before trusting the dashboard. If a run fails, the previous
  valid JSON is retained (the engine never fabricates data).
