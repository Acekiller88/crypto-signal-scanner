"""Multi-timeframe analysis for ASCEND-30: 1D trend, 4H confirmation, 15M execution.

``analyze_symbol`` is **pure**: it receives already-closed OHLCV series per
timeframe (the caller guarantees no in-progress candles) and returns every
derived value the engines need. Deterministic: identical inputs -> identical
output. No lookahead: values at index i never touch index > i, and structure
events are only exposed once confirmed + closed.
"""
from __future__ import annotations

from .config import Config
from . import indicators as ind
from . import structure as st
from . import profile as prof

TIMEFRAMES = ("D", "240", "15")   # 1D, 4H, 15M


def directional_bias(closes: list[float], ema_fast: list, ema_mid: list, ema_slow: list,
                     adx_value: float | None, plus_di: float | None, minus_di: float | None,
                     donchian_hh: float | None, donchian_ll: float | None) -> dict:
    """Classify a timeframe as strong_bullish..strong_bearish using Donchian + EMA + ADX."""
    if not closes:
        return {"bias": "unknown", "dir": 0, "strength": 0.0}
    c = closes[-1]
    f = ema_fast[-1] if ema_fast else None
    m = ema_mid[-1] if ema_mid else None
    s = ema_slow[-1] if ema_slow else None
    if f is None or m is None or s is None:
        return {"bias": "unknown", "dir": 0, "strength": 0.0}

    # Donchian direction (breakout)
    don_dir = 0
    if donchian_hh is not None and c > donchian_hh:
        don_dir = 1
    elif donchian_ll is not None and c < donchian_ll:
        don_dir = -1

    # EMA stack direction
    ema_dir = 0
    if f > m > s:
        ema_dir = 1
    elif f < m < s:
        ema_dir = -1

    adx_dir = 0
    if adx_value is not None and adx_value >= 20:
        if plus_di is not None and minus_di is not None:
            if plus_di > minus_di:
                adx_dir = 1
            elif minus_di > plus_di:
                adx_dir = -1

    # Combined direction: 2 of 3 agree
    votes = don_dir + ema_dir + adx_dir
    if votes >= 1:
        direction = 1
    elif votes <= -1:
        direction = -1
    else:
        direction = 0

    if direction == 0:
        return {"bias": "neutral", "dir": 0, "strength": 0.0}

    # strength 0..2 based on how many legs agree and ADX magnitude
    agree = (don_dir == direction) + (ema_dir == direction) + (adx_dir == direction)
    strength = min(2.0, 0.5 * agree)
    if adx_value is not None:
        strength += 0.5 if adx_value >= 30 else 0.0
    strength = min(2.0, strength)

    if direction == 1:
        bias = "strong_bullish" if strength >= 1.6 else "bullish"
    else:
        bias = "strong_bearish" if strength >= 1.6 else "bearish"
    return {"bias": bias, "dir": direction, "strength": round(strength, 3)}


def classify_regime(adx_1d: float | None, vei_15m: float | None) -> str:
    """TREND / RANGE / MIXED from 1D ADX strength + 15M volatility expansion."""
    if adx_1d is None:
        return "MIXED"
    if adx_1d >= 25 and (vei_15m is None or vei_15m >= 1.0):
        return "TREND"
    if adx_1d < 18 and (vei_15m is None or vei_15m < 1.0):
        return "RANGE"
    return "MIXED"


def _series(candles):
    return ([c.open for c in candles], [c.high for c in candles],
            [c.low for c in candles], [c.close for c in candles],
            [c.volume for c in candles])


def analyze_symbol(symbol: str, klines: dict[str, list], cfg: Config,
                   now_ms: int, ticker: dict | None = None) -> dict:
    """Compute the full ASCEND snapshot for one symbol.

    ``klines`` maps timeframe -> list[Candle] (closed candles only).
    ``ticker`` (optional) carries fundingRate / openInterest / turnover24h.
    """
    icfg = cfg.get("indicators")
    scfg = cfg.get("structure")
    pcfg = cfg.get("profile")
    out: dict = {"symbol": symbol, "ok": False, "reasons": []}

    frames: dict[str, dict] = {}
    for tf in TIMEFRAMES:
        candles = klines.get(tf) or []
        min_needed = icfg.get("emaSlow") + 5
        if len(candles) < min_needed:
            out["reasons"].append(f"{tf}: insufficient candles ({len(candles)}<{min_needed})")
            return out
        o, h, l, c, v = _series(candles)
        atr_s = ind.atr(h, l, c, icfg.get("atrPeriod"))
        adx_pack = ind.adx(h, l, c, icfg.get("adxPeriod"))
        don_hh, don_ll = ind.donchian(h, l, icfg.get("donchianTrendN"))
        frames[tf] = {
            "candles": candles, "o": o, "h": h, "l": l, "c": c, "v": v,
            "atr": atr_s,
            "emaFast": ind.ema(c, icfg.get("emaFast")),
            "emaMid": ind.ema(c, icfg.get("emaMid")),
            "emaSlow": ind.ema(c, icfg.get("emaSlow")),
            "rsi": ind.rsi(c, icfg.get("rsiPeriod")),
            "adx": adx_pack["adx"], "plusDi": adx_pack["plusDi"], "minusDi": adx_pack["minusDi"],
            "relVol": ind.relative_volume(v, icfg.get("relVolumeLookback")),
            "vei": ind.vei(h, l, c, icfg.get("atrPeriod"), 20),
            "donchianHigh": don_hh, "donchianLow": don_ll,
            "vwap": ind.rolling_vwap(h, l, c, v, icfg.get("vwapWindow")),
        }
        if tf == "15":
            swings_hi = st.find_swing_highs(h, scfg.get("swingLookback"))
            swings_lo = st.find_swing_lows(l, scfg.get("swingLookback"))
            disps = st.find_displacements(o, c, atr_s, scfg.get("displacementBodyAtrMultiple"))
            frames[tf]["swingHighs"] = swings_hi
            frames[tf]["swingLows"] = swings_lo
            frames[tf]["displacements"] = disps
            frames[tf]["events"] = st.detect_structure_events(
                c, swings_hi, swings_lo, disps, scfg.get("maxStructureSwings"))
            frames[tf]["sweeps"] = st.find_liquidity_sweeps(
                h, l, c, swings_hi, swings_lo, atr_s,
                scfg.get("minSwingAgeBars"), scfg.get("sweepMaxAtrMultiple"))
            frames[tf]["equalHighs"] = st.equal_levels(swings_hi, atr_s, scfg.get("equalLevelAtrTolerance"))
            frames[tf]["equalLows"] = st.equal_levels(swings_lo, atr_s, scfg.get("equalLevelAtrTolerance"))
            frames[tf]["fvgs"] = st.find_fvgs(h, l, atr_s, scfg.get("fvgMinAtrMultiple", 0.5))
            frames[tf]["orderBlocks"] = st.find_order_blocks(o, c, h, l, disps, scfg.get("obWindowBars", 6))
            frames[tf]["profile"] = prof.build_profile(
                c, h, l, v, pcfg.get("valueAreaPct", 0.70), pcfg.get("rows", 48))

    d, f4, d15 = frames["D"], frames["240"], frames["15"]
    last = len(d15["c"]) - 1

    # --- directional biases per timeframe
    def _bias(ft) -> dict:
        n = len(ft["c"]) - 1
        return directional_bias(
            ft["c"], ft["emaFast"], ft["emaMid"], ft["emaSlow"],
            ind.value_at(ft["adx"], n), ind.value_at(ft["plusDi"], n), ind.value_at(ft["minusDi"], n),
            ind.value_at(ft["donchianHigh"], n), ind.value_at(ft["donchianLow"], n))

    bias_1d = _bias(d)
    bias_4h = _bias(f4)

    adx_1d = ind.value_at(d["adx"], len(d["c"]) - 1)
    vei_15m = ind.value_at(d15["vei"], last)
    regime = classify_regime(adx_1d, vei_15m)

    atr_15 = ind.value_at(d15["atr"], last)
    close_now = d15["candles"][last].close
    out.update({
        "ok": True,
        "symbol": symbol,
        "last15mIndex": last,
        "last15mCloseTime": d15["candles"][last].closeTime,
        "last15mOpenTime": d15["candles"][last].openTime,
        "price": close_now,
        "regime": regime,
        "bias1d": bias_1d, "bias4h": bias_4h,
        "adx1d": adx_1d,
        "rsi15m": ind.value_at(d15["rsi"], last),
        "adx15m": ind.value_at(d15["adx"], last),
        "plusDi15m": ind.value_at(d15["plusDi"], last),
        "minusDi15m": ind.value_at(d15["minusDi"], last),
        "atr15m": atr_15,
        "atrPercent15m": (atr_15 / close_now * 100.0) if (atr_15 and close_now and close_now > 0) else None,
        "relVol15m": ind.value_at(d15["relVol"], last),
        "vei15m": vei_15m,
        "clv15m": d15["candles"][last].clv,
        "avgClv15m": round(sum(c.clv for c in d15["candles"][-10:]) / 10.0, 4) if len(d15["candles"]) >= 10 else None,
        "vwap15m": ind.value_at(d15["vwap"], last),
        "profile": d15["profile"].as_dict() if d15.get("profile") else None,
        "frames": frames,
        "ticker": ticker or {},
    })
    return out
