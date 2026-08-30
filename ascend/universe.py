"""Dynamic top-30 universe builder for ASCEND-30.

Rules:
1. Take the Bybit linear ticker list (24h stats, funding, OI).
2. Exclude leveraged-token style symbols and stablecoin/stable-like base assets.
3. Apply a minimum 24h quote-turnover floor (liquidity).
4. Rank by 24h quote turnover (desc), take the top N.
Recomputed from live data each scan (deterministic tie-break by symbol).
"""
from __future__ import annotations

from .bybit import BybitClient, BybitError


def _matches_exclusion(symbol: str, patterns: list[str]) -> bool:
    up = symbol.upper()
    return any(p.upper() in up for p in patterns)


def build_universe(client: BybitClient, cfg) -> dict:
    ucfg = cfg.get("universe")
    patterns = ucfg.get("excludeSymbolPatterns", [])
    excluded_assets = {a.upper() for a in ucfg.get("excludeBaseAssets", [])}
    min_turnover = float(ucfg.get("minTurnover24hUsd", 0))
    top_n = int(ucfg.get("topN", 30))

    try:
        tickers = client.tickers()
    except BybitError as exc:
        # Distinguish empty (no data) from hard failure for the caller
        raise

    candidates: list[tuple[str, float]] = []
    for t in tickers:
        sym = t.get("symbol") or ""
        base = sym[:-4]
        if base.upper() in excluded_assets:
            continue
        if _matches_exclusion(sym, patterns):
            continue
        try:
            turnover = float(t.get("turnover24h") or 0)
        except (TypeError, ValueError):
            continue
        if turnover <= 0 or turnover != turnover:
            continue
        if turnover < min_turnover:
            continue
        candidates.append((sym, turnover))

    candidates.sort(key=lambda x: (-x[1], x[0]))  # turnover desc, symbol asc tie-break
    selected = candidates[:top_n]

    by_symbol: dict[str, dict] = {}
    for t in tickers:
        if t.get("symbol"):
            by_symbol[t["symbol"]] = t

    universe = {
        "symbols": [{"symbol": s, "turnover24hUsd": round(v, 2), "rank": i + 1}
                    for i, (s, v) in enumerate(selected)],
        "source": client.endpoint_label(),
        "counts": {"candidates": len(candidates), "selected": len(selected)},
    }
    return universe, by_symbol


def symbol_list(universe: dict) -> list[str]:
    return [e["symbol"] for e in universe.get("symbols", [])]
