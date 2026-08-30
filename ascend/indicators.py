"""Indicator engine for ASCEND-30 — pure, deterministic, no lookahead.

Every function takes plain lists and returns a list aligned to the input,
using ``None`` during the warm-up period. A value at index ``i`` is computed
only from data at or before ``i`` (no repaint by construction).

Conventions
-----------
* EMA  -- seeded with SMA(period), then ema[i]=v*k+ema[i-1]*(1-k), k=2/(period+1).
* RSI  -- Wilder smoothing, first avg = simple mean of first `period` changes.
* ATR  -- Wilder, TR = max(h-l, |h-Cprev|, |l-Cprev|), seeded with SMA(period).
* ADX  -- Wilder +DM/-DM, DI+/DI-, DX, Wilder MA of DX (first valid at 2*period-1).
* Relative volume -- volume[i] / mean(volume[i-lookback:i]) (current bar excluded).
* Donchian -- highest/high and lowest/low over the trailing window ending at i.
* CLV  -- close-location value per bar (0..1) -- order-flow proxy.
* VEI  -- Volatility Expansion Index = ATR(i)/EMA(ATR, lookback): >1 expanding.
* Rolling VWAP -- sum(typical*vol)/sum(vol) over trailing window.
"""
from __future__ import annotations

from typing import Optional


def sma(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if period <= 0:
        raise ValueError("period must be > 0")
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if period <= 0:
        raise ValueError("period must be > 0")
    if len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def rsi(closes: list[float], period: int = 14) -> list[Optional[float]]:
    n = len(closes)
    out: list[Optional[float]] = [None] * n
    if n <= period or period <= 0:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        ch = closes[i] - closes[i - 1]
        gains[i] = ch if ch > 0 else 0.0
        losses[i] = -ch if ch < 0 else 0.0
    ag = sum(gains[1:period + 1]) / period
    al = sum(losses[1:period + 1]) / period
    out[period] = _rsi_value(ag, al)
    for i in range(period + 1, n):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out[i] = _rsi_value(ag, al)
    return out


def _rsi_value(ag: float, al: float) -> float:
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def true_range(highs, lows, closes) -> list[float]:
    out = [0.0] * len(closes)
    for i, (h, l, c) in enumerate(zip(highs, lows, closes)):
        if i == 0:
            out[i] = h - l
        else:
            pc = closes[i - 1]
            out[i] = max(h - l, abs(h - pc), abs(l - pc))
    return out


def atr(highs, lows, closes, period: int = 14) -> list[Optional[float]]:
    n = len(closes)
    out: list[Optional[float]] = [None] * n
    if period <= 0 or n < period:
        return out
    tr = true_range(highs, lows, closes)
    prev = sum(tr[:period]) / period
    out[period - 1] = prev
    for i in range(period, n):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def adx(highs, lows, closes, period: int = 14) -> dict:
    n = len(closes)
    nones: list[Optional[float]] = [None] * n
    res = {"adx": list(nones), "plusDi": list(nones), "minusDi": list(nones)}
    if period <= 0 or n < 2 * period:
        return res

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    tr = true_range(highs, lows, closes)
    sm_tr = sum(tr[1:period + 1])
    sm_plus = sum(plus_dm[1:period + 1])
    sm_minus = sum(minus_dm[1:period + 1])
    plus_di: list[Optional[float]] = list(nones)
    minus_di: list[Optional[float]] = list(nones)
    dxs: list[tuple[int, float]] = []
    for i in range(period, n):
        if i > period:
            sm_tr = sm_tr - sm_tr / period + tr[i]
            sm_plus = sm_plus - sm_plus / period + plus_dm[i]
            sm_minus = sm_minus - sm_minus / period + minus_dm[i]
        atr_v = sm_tr if sm_tr > 0 else 1e-12
        pdi = 100.0 * sm_plus / atr_v
        mdi = 100.0 * sm_minus / atr_v
        plus_di[i] = pdi
        minus_di[i] = mdi
        di_sum = pdi + mdi
        dx = 100.0 * abs(pdi - mdi) / di_sum if di_sum > 0 else 0.0
        dxs.append((i, dx))
    first_adx_idx = dxs[period - 1][0]
    seed = sum(dx for _, dx in dxs[:period]) / period
    res["adx"][first_adx_idx] = seed
    prev = seed
    for i, dx in dxs[period:]:
        prev = (prev * (period - 1) + dx) / period
        res["adx"][i] = prev
    res["plusDi"] = plus_di
    res["minusDi"] = minus_di
    return res


def relative_volume(volumes: list[float], lookback: int = 20) -> list[Optional[float]]:
    n = len(volumes)
    out: list[Optional[float]] = [None] * n
    if lookback <= 0:
        raise ValueError("lookback must be > 0")
    for i in range(n):
        start = max(0, i - lookback)
        window = volumes[start:i]
        if not window:
            continue
        base = sum(window) / len(window)
        if base > 0:
            out[i] = volumes[i] / base
    return out


def donchian(highs, lows, period: int = 20) -> tuple[list[Optional[float]], list[Optional[float]]]:
    """Return (highest_high, lowest_low) over the trailing `period` bars ending at i."""
    n = len(highs)
    hh: list[Optional[float]] = [None] * n
    ll: list[Optional[float]] = [None] * n
    if period <= 0:
        return hh, ll
    for i in range(n):
        start = max(0, i - period + 1)
        hh[i] = max(highs[start:i + 1])
        ll[i] = min(lows[start:i + 1])
    return hh, ll


def rolling_vwap(highs, lows, closes, volumes, window: int = 48) -> list[Optional[float]]:
    n = len(closes)
    out: list[Optional[float]] = [None] * n
    if window <= 0:
        raise ValueError("window must be > 0")
    pv = 0.0
    vv = 0.0
    q: list[tuple[float, float]] = []
    for i in range(n):
        typical = (highs[i] + lows[i] + closes[i]) / 3.0
        pv += typical * volumes[i]
        vv += volumes[i]
        q.append((typical * volumes[i], volumes[i]))
        if len(q) > window:
            opv, ovv = q.pop(0)
            pv -= opv
            vv -= ovv
        if len(q) == window and vv > 0:
            out[i] = pv / vv
    return out


def vei(highs, lows, closes, period: int = 14, smooth: int = 20) -> list[Optional[float]]:
    """Volatility Expansion Index = ATR(i) / EMA(ATR, smooth). >1 = expanding."""
    a = atr(highs, lows, closes, period)
    vals = [x if x is not None else 0.0 for x in a]
    base = ema(vals, smooth)
    out: list[Optional[float]] = [None] * len(closes)
    for i in range(len(closes)):
        if a[i] is None or base[i] is None or base[i] <= 0:
            continue
        out[i] = a[i] / base[i]
    return out


def value_at(series, index: int):
    if 0 <= index < len(series):
        return series[index]
    return None


def last_valid(series):
    for v in reversed(series):
        if v is not None:
            return v
    return None
