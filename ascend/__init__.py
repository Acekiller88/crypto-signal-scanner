"""ASCEND-30 — regime-adaptive, probability-calibrated, non-repainting signal engine.

Hybrid of the strongest components benchmarked in docs/NEXT-GEN-SYSTEM-BLUEPRINT.md:
Turtle trend-following (Donchian + ATR/N risk) × ICT/SMC liquidity hunting
(sweep -> MSS/displacement -> FVG/OB) × Wyckoff spring validation × Volume
Profile (POC/VAH/VAL/HVN/LVN) × order-flow confirmation (CLV delta) with
prop-grade buffer-based risk and a logistic-calibrated probability output.

Deployment target: Bybit USDT-Perpetual, top-30 by 24h quote turnover,
1D trend -> 4H confirmation -> 15M execution. Stdlib only (urllib/json) so it
runs on free GitHub Actions. Analysis only -- never places trades.
"""

__version__ = "1.0.0"
