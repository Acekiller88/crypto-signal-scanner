"""Mini candlestick chart snapshots for the dashboard signal cards.

The frontend originally could only show text levels. This module builds a
bounded, per-symbol window of closed 15M candles (plus the structure markers
already computed during the scan) so the dashboard can render a small
candlestick chart on every active signal card — sweep, CHoCH/BOS, FVG, order
block, trigger/entry/TP/SL — exactly matching the numbers shown elsewhere.

Design constraints:

* Causal only: every candle is a fully-closed 15M candle (the caller passes
  ``drop_incomplete`` output). No in-progress candle is ever embedded.
* Bounded: only active signals (WAITING_TRIGGER / TRIGGERED) are charted and
  each snapshot is capped at ``maxCandles`` (default 60). The file
  ``data/chart-candles.json`` is *overwritten every scan* so git does not grow.
* No lookahead markers: sweep/event/FVG/OB indices come from the signal, which
  itself was generated without future data.

CLI:
    python -m scanner.chart --symbols BTCUSDT --max-candles 60
"""
from __future__ import annotations

import time
from pathlib import Path

from .config import Config
from .persist import atomic_write_json, load_json
from . import persist
from .signals import ACTIVE_STATUSES

CHART_NAME = "chart-candles.json"


def _fmt(v, default=None):
    return default if v is None else round(float(v), 8)


def build_chart_snapshots(signals: list[dict], candle_map: dict[str, list],
                          cfg: Config, now_ms: int) -> dict:
    """Build ``{symbol: {candles: [...], markers: {...}}}`` for active signals."""
    max_candles = int(cfg.get("chart.maxCandles", 60))
    before_bars = int(cfg.get("chart.barsBeforeSignal", 40))
    candle_ms = int(cfg.get("lifecycle.candleMs", 900_000))
    out: dict = {}

    for sig in signals:
        if sig.get("status") not in ACTIVE_STATUSES:
            continue
        symbol = sig.get("symbol")
        candles = candle_map.get(symbol)
        if not candles:
            continue
        sig_close = sig.get("signalCandleCloseTime") or sig.get("generatedAt")
        # include candles up to the last closed candle <= now_ms
        window = [c for c in candles if c.closeTime <= now_ms]
        # focus on the setup window: lead up to and after the signal candle
        anchor = sig_close
        rel = [(i, c) for i, c in enumerate(window) if c.closeTime <= anchor]
        seed_idx = (rel[-1][0] if rel else len(window) - 1) - before_bars
        seed_idx = max(0, seed_idx)
        tail = window[seed_idx:][-max_candles:]
        if len(tail) < 10:
            continue

        ev = sig.get("structure") or {}
        fvg = sig.get("fvg") or {}
        ob = sig.get("orderBlock") or {}
        sweep = sig.get("liquiditySweep") or {}

        markers = {
            "trigger": _fmt(sig.get("triggerPrice")),
            "stop": _fmt(sig.get("stopLoss")),
            "target": _fmt(sig.get("takeProfit")),
            "entryLow": _fmt((sig.get("entryZone") or [None, None])[0]),
            "entryHigh": _fmt((sig.get("entryZone") or [None, None])[1]),
            "sweepLevel": _fmt(sweep.get("level")),
            "event": ev.get("event"),
            "eventLevel": _fmt(ev.get("level")),
            "fvgBottom": _fmt(fvg.get("bottom")),
            "fvgTop": _fmt(fvg.get("top")),
            "obBottom": _fmt(ob.get("bottom")),
            "obTop": _fmt(ob.get("top")),
            "direction": sig.get("direction"),
        }

        out[symbol] = {
            "symbol": symbol,
            "signalId": sig.get("id"),
            "candles": [
                {"t": c.openTime, "c": _fmt(c.close), "o": _fmt(c.open),
                 "h": _fmt(c.high), "l": _fmt(c.low), "v": _fmt(c.volume)}
                for c in tail
            ],
            "markers": markers,
            "signalCloseTime": sig_close,
        }
    return out


def write_chart_snapshots(snapshots: dict, now_ms: int) -> None:
    """Overwrite chart-candles.json in /data and /frontend/data (no git growth)."""
    payload = {"generatedAt": now_ms, "symbols": snapshots}
    atomic_write_json(persist.data_dir() / CHART_NAME, payload)
    atomic_write_json(persist.frontend_data_dir() / CHART_NAME, payload)


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="build chart-candles.json from signals")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--max-candles", type=int, default=None)
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)

    cfg = Config.load(args.config)
    if args.max_candles:
        cfg = Config({**cfg.raw, "chart": {"maxCandles": args.max_candles}}, cfg.path)
    signals = load_json("signals.json", {"signals": []}).get("signals", [])
    from .market_data import Candle
    # rebuild a candle map from the stored snapshot if present, else empty
    candle_map = _load_candle_map()
    if args.symbols:
        signals = [s for s in signals if s.get("symbol") in args.symbols]
    snapts = build_chart_snapshots(signals, candle_map, cfg, int(time.time() * 1000))
    write_chart_snapshots(snapts, int(time.time() * 1000))
    print(f"chart-candles.json built: {len(snapts)} symbol(s)")
    return 0


def _load_candle_map() -> dict:
    """Attempt to reconstruct a 15M candle map from an earlier scan snapshot.

    The scan does not persist raw candles (by design) so the standalone CLI is
    best used right after a scan. The in-scan path (main.py) is the primary
    consumer; this is a convenience for replaying a stored chart.
    """
    snap = load_json(CHART_NAME, {"symbols": {}})
    from .market_data import Candle
    out: dict = {}
    for sym, blk in (snap.get("symbols") or {}).items():
        candles = []
        for c in blk.get("candles", []):
            try:
                candles.append(Candle(
                    openTime=int(c["t"]), open=float(c["o"]), high=float(c["h"]),
                    low=float(c["l"]), close=float(c["c"]), volume=float(c.get("v", 0)),
                    closeTime=int(c["t"]) + 900_000, quoteVolume=0, trades=0))
            except (KeyError, ValueError, TypeError):
                continue
        if candles:
            out[sym] = candles
    return out


if __name__ == "__main__":
    raise SystemExit(main())
