"""Centralised ASCEND-30 configuration.

Loads /config/ascend.json, deep-merges over safe built-in defaults so a
partially specified file never crashes the engine, and exposes dotted-path
lookups. Every engine threshold and weight lives here -- never hard-coded.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "market": {
        "category": "linear",          # USDT perpetual
        "exchange": "bybit",
        "base": "https://api.bybit.com",
    },
    "universe": {
        "topN": 30,                    # top-30 by 24h quote turnover
        "minTurnover24hUsd": 5_000_000,
        "excludeSymbolPatterns": ["BULLUSDT", "BEARUSDT", "UPUSDT", "DOWNUSDT"],
        "excludeBaseAssets": ["USDC", "FDUSD", "TUSD", "DAI", "BUSD", "USDP", "AEUR"],
    },
    "timeframes": {
        "trend": "D",                  # 1D main trend
        "confirm": "240",              # 4H confirmation
        "entry": "15",                 # 15M execution
        "intervalMs": {"1": 60_000, "3": 180_000, "5": 300_000, "15": 900_000,
                       "30": 1_800_000, "60": 3_600_000, "120": 7_200_000,
                       "240": 14_400_000, "360": 21_600_000, "720": 43_200_000,
                       "D": 86_400_000, "M": 2_592_000_000, "W": 604_800_000},
    },
    "data": {
        "requestTimeoutSeconds": 12,
        "maxRetries": 3,
        "retryBackoffSeconds": 1.5,
        "maxRequestsPerScan": 300,
        "klineLimits": {"D": 260, "240": 300, "15": 500},
    },
    "indicators": {
        "emaFast": 20, "emaMid": 50, "emaSlow": 200,
        "rsiPeriod": 14, "adxPeriod": 14, "atrPeriod": 14,
        "relVolumeLookback": 20, "vwapWindow": 48,
        "donchianTrendN": 20, "donchianExitN": 10,
    },
    "structure": {
        "swingLookback": 2,            # fractal k
        "minSwingAgeBars": 3,
        "displacementBodyAtrMultiple": 1.5,
        "sweepMaxAtrMultiple": 1.0,
        "equalLevelAtrTolerance": 0.1,
        "maxStructureSwings": 40,
        "mssWindowBars": 12,           # lookback for the reversing MSS
        "fvgMinAtrMultiple": 0.5,      # min FVG gap width as a multiple of ATR
        "obWindowBars": 6,             # how far back to look for the OB candle
    },
    "profile": {
        "valueAreaPct": 0.70,
        "lookbackCandles": 120,        # rolling window for the volume profile
        "rows": 48,                    # price bins for the histogram (deterministic)
        "atrSmooth": 14,
    },
    "signalModel": {
        "minScoreForSignal": 70,       # min probability*100 to publish a signal
        "strongBuyProb": 0.70,
        "buyProb": 0.55,
        "sellProb": 0.40,
        "minRr": 2.5,
        "preferredRr": 3.0,
        "stopMaxAtrMultiple": 2.5,
        "stopMinAtrMultiple": 1.0,
        "stopBufferAtrMultiple": 0.5,
        "timeStopBars": 24,            # exit if < +0.5R within N 15M bars
        "breakevenAtR": 1.0,           # move stop to entry at this R
        "partialAtR": 1.0,             # take partial profit at this R
        "partialFraction": 0.5,        # fraction of units to trim at partialAtR
        "trailAtrMultiple": 0.5,       # trail stop 0.5*ATR beyond structure
    },
    "scoring": {
        "weights": {"trend": 0.20, "structure": 0.20, "liquidity": 0.15,
                    "momentum": 0.15, "volume": 0.10, "volatility": 0.10,
                    "risk": 0.10},
        # logistic calibration: P(profit>=1R) = 1/(1+exp(-(a + b*score/100)))
        # This is an initial, honest placeholder until enough resolved trades
        # accumulate; refit with --calibrate/real history. The dashboard always
        # shows the sample size + Wilson CI so small samples are not trusted.
        "logistic": {"a": -3.6, "b": 6.0},
        "minSampleForCalibration": 20,
    },
    "risk": {
        "perTradeRiskBasePct": 1.0,        # of drawdown buffer, clamped below
        "riskOfBufferFactor": 0.08,        # risk_pct = clamp(buffer_pct*factor, min,max)
        "minRiskPct": 0.25,
        "maxRiskPct": 1.0,
        "maxPortfolioHeatPct": 6.0,        # total open risk cap
        "dailyLossLimitPct": 1.5,          # soft halt for the day
        "weeklyLossLimitPct": 4.0,
        "maxDrawdownPct": 8.0,             # $100M prop floor
        "correlationGroupSize": 1,         # 1 => treat all as 1 event (conservative)
    },
    "retention": {
        "signals": 2000,
        "logEntries": 400,
    },
    "sessions": {
        "killZones": [
            {"name": "ASIA", "startUtcHour": 0, "endUtcHour": 6, "weight": 1.0},
            {"name": "LONDON", "startUtcHour": 7, "endUtcHour": 10, "weight": 1.2},
            {"name": "NEW_YORK", "startUtcHour": 12, "endUtcHour": 15, "weight": 1.1},
        ],
        "enabled": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    """Read-only accessor over the merged ASCEND configuration."""

    def __init__(self, data: dict[str, Any], path: Path | None = None):
        self._data = _deep_merge(DEFAULTS, data or {})
        self.path = path

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        p = Path(path) if path else repo_root() / "config" / "ascend.json"
        data: dict[str, Any] = {}
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
        return cls(data, p)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        value = self.get(dotted)
        if value is None:
            raise KeyError(f"missing config key: {dotted}")
        return value

    @property
    def raw(self) -> dict[str, Any]:
        return self._data

    def validate(self) -> list[str]:
        errors: list[str] = []
        w = self.get("scoring.weights", {})
        total = sum(w.values())
        if abs(total - 1.0) > 1e-6:
            errors.append(f"scoring.weights must sum to 1.0 (got {total})")
        s = self.get("signalModel", {})
        if not (s.get("minRr", 0) <= s.get("preferredRr", 0)):
            errors.append("signalModel.minRr must be <= preferredRr")
        if not (s.get("sellProb", 0) < s.get("buyProb", 0) < s.get("strongBuyProb", 0)):
            errors.append("signalModel probability thresholds must be increasing")
        r = self.get("risk", {})
        if not (r.get("minRiskPct", 0) <= r.get("maxRiskPct", 0)):
            errors.append("risk.minRiskPct must be <= maxRiskPct")
        k = self.get("profile", {})
        if not (0 < k.get("valueAreaPct", 0) <= 1):
            errors.append("profile.valueAreaPct must be in (0,1]")
        return errors


def repo_root() -> Path:
    """Repository root = parent of the ``ascend`` package directory."""
    return Path(__file__).resolve().parents[1]
