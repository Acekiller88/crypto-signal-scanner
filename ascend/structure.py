"""Deterministic market-structure engine for ASCEND-30 (non-repaint by construction).

All detections are **causal**: a swing at index ``i`` becomes usable only at its
confirmation index ``i + k`` (k completed bars each side). A structure event
(BOS / MSS) is emitted only when the confirming candle has *closed*, so the level
presented to the caller never changes later — the core non-repaint guarantee.

Definitions (documented in docs/NEXT-GEN-SYSTEM-BLUEPRINT.md):
* Swing high/low -- 2-bar strict fractal.
* BOS  -- a *close* beyond the most recent confirmed swing, in the direction of
          the established trend (continuation).
* MSS  -- a *close* beyond the most recent confirmed swing *against* the trend
          (Market Structure Shift = ICT "character change" / CHoCH).
* Displacement -- per-candle |body| >= multiple * ATR(i); it *validates* a
          structure event. An MSS/BOS without displacement is weak and graded low.
* Liquidity sweep -- wick takes out a prior confirmed swing (excursion <=
          max*ATR) but closes back on the original side.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Swing:
    index: int
    confirmIndex: int
    price: float
    kind: str            # 'high' | 'low'

    @property
    def label(self) -> str:
        return "swing_high" if self.kind == "high" else "swing_low"


@dataclass
class StructureEvent:
    index: int           # candle whose CLOSE broke the level (only known at close)
    type: str            # 'BOS_up' | 'BOS_down' | 'MSS_up' | 'MSS_down'
    level: float
    swingIndex: int
    trendAfter: str      # 'up' | 'down'
    displacement: bool   # validated by a >= 1.5x ATR body?
    strength: float      # displacement body/ATR if displacement else 0


@dataclass
class Sweep:
    index: int
    direction: str       # 'bullish' (swept a low) | 'bearish' (swept a high)
    level: float
    swingIndex: int
    wick: float


@dataclass
class FVG:
    index: int           # the (closed) candle whose LOW/HIGH creates the gap
    confirmIndex: int    # = index: the gap is only known once candle `index` closes
    direction: str       # 'bullish' | 'bearish'
    bottom: float        # gap floor  (bullish: high[i-2])
    top: float           # gap ceiling (bullish: low[i])
    mid: float           # consequent-encroachment entry = midpoint of the gap

    @property
    def label(self) -> str:
        return "fvg"


@dataclass
class OrderBlock:
    index: int           # the (closed) candle that is the OB
    confirmIndex: int    # = the displacement/structure-break candle that validates it
    direction: str       # 'bullish' | 'bearish'
    bottom: float
    top: float
    mid: float           # consequent-encroachment entry

    @property
    def label(self) -> str:
        return "order_block"


def find_swing_highs(highs: list[float], k: int = 2) -> list[Swing]:
    out: list[Swing] = []
    n = len(highs)
    for i in range(k, n - k):
        ok = all(highs[i] > highs[i - j] and highs[i] > highs[i + j] for j in range(1, k + 1))
        if ok:
            out.append(Swing(i, i + k, highs[i], "high"))
    return out


def find_swing_lows(lows: list[float], k: int = 2) -> list[Swing]:
    out: list[Swing] = []
    n = len(lows)
    for i in range(k, n - k):
        ok = all(lows[i] < lows[i - j] and lows[i] < lows[i + j] for j in range(1, k + 1))
        if ok:
            out.append(Swing(i, i + k, lows[i], "low"))
    return out


@dataclass
class Displacement:
    index: int
    direction: str       # 'bullish' | 'bearish'
    strength: float      # body / atr


def find_displacements(opens: list[float], closes: list[float],
                       atr_series: list[Optional[float]],
                       body_atr_multiple: float = 1.5) -> list[Displacement]:
    out: list[Displacement] = []
    for i in range(len(closes)):
        a = atr_series[i]
        if a is None or a <= 0:
            continue
        body = abs(closes[i] - opens[i])
        if body >= body_atr_multiple * a and body > 0:
            out.append(Displacement(i, "bullish" if closes[i] > opens[i] else "bearish",
                                    round(body / a, 4)))
    return out


def find_fvgs(highs: list[float], lows: list[float],
              atr_series: list[Optional[float]],
              min_gap_atr_multiple: float = 0.5) -> list[FVG]:
    """Detect Fair Value Gaps (3-candle imbalances), causal & non-repaint.

    A bullish FVG exists at candle ``i`` when ``low[i] > high[i-2]`` (candle 3's
    low leaves a gap above candle 1's high); the zone is
    [high[i-2], low[i]]. The bearish mirror is ``high[i] < low[i-2]``. The gap
    is only known once candle ``i`` *closes*, so ``confirmIndex == i``.

    Only gaps at least ``min_gap_atr_multiple * ATR(i)`` wide are kept -- a
    sub-pip sliver is not a real imbalance.
    """
    out: list[FVG] = []
    n = len(highs)
    for i in range(2, n):
        atr_v = atr_series[i] or 0.0
        min_gap = min_gap_atr_multiple * max(atr_v, 1e-12)
        bullish_gap = lows[i] - highs[i - 2]
        if bullish_gap > 0 and bullish_gap >= min_gap:
            bottom, top = highs[i - 2], lows[i]
            out.append(FVG(i, i, "bullish", round(bottom, 10), round(top, 10),
                           round((bottom + top) / 2.0, 10)))
            continue
        bearish_gap = lows[i - 2] - highs[i]
        if bearish_gap > 0 and bearish_gap >= min_gap:
            bottom, top = highs[i], lows[i - 2]
            out.append(FVG(i, i, "bearish", round(bottom, 10), round(top, 10),
                           round((bottom + top) / 2.0, 10)))
    return out


def find_order_blocks(opens: list[float], closes: list[float],
                      highs: list[float], lows: list[float],
                      displacements: list[Displacement],
                      window: int = 6) -> list[OrderBlock]:
    """Detect Order Blocks: the last *opposite* candle before each displacement.

    A bullish OB is the most recent bearish candle immediately preceding a
    bullish displacement/structure break; price is expected to retrace into it
    (consequent encroachment) before continuation. The OB is only *usable* once
    the validating displacement candle closes, so ``confirmIndex == d.index``.

    Deterministic, causal, non-repaint. Multiple displacements may share the
    same OB candle; duplicates are collapsed to the last one per candle.
    """
    out: list[OrderBlock] = []
    seen: set[int] = set()
    for d in displacements:
        lo = max(0, d.index - window)
        ob_idx: int | None = None
        # scan backwards, biased to pick the *closest* opposite candle to the move
        for j in range(d.index - 1, lo - 1, -1):
            if d.direction == "bullish" and closes[j] < opens[j]:   # bearish candle
                ob_idx = j
                break
            if d.direction == "bearish" and closes[j] > opens[j]:   # bullish candle
                ob_idx = j
                break
        if ob_idx is None or ob_idx in seen:
            continue
        seen.add(ob_idx)
        if d.direction == "bullish":
            bottom, top = lows[ob_idx], max(highs[ob_idx], opens[ob_idx], closes[ob_idx])
        else:
            bottom, top = min(lows[ob_idx], opens[ob_idx], closes[ob_idx]), highs[ob_idx]
        out.append(OrderBlock(ob_idx, d.index, d.direction, round(bottom, 10),
                              round(top, 10), round((bottom + top) / 2.0, 10)))
    out.sort(key=lambda x: x.confirmIndex)
    return out


def detect_structure_events(closes: list[float], swing_highs: list[Swing],
                            swing_lows: list[Swing],
                            displacements: list[Displacement],
                            max_swings: int = 40) -> list[StructureEvent]:
    """Chronological BOS / MSS events via a close-break state machine.

    Only swings whose confirmIndex <= current candle exist yet (no lookahead).
    Only *closes* break levels (wicks never count). An event is marked
    ``displacement=True`` when a qualifying displacement candle coincides with
    (or precedes within a bar) the break, which grades it stronger.
    """
    events: list[StructureEvent] = []
    highs_sorted = sorted(swing_highs, key=lambda s: s.confirmIndex)[-max_swings:]
    lows_sorted = sorted(swing_lows, key=lambda s: s.confirmIndex)[-max_swings:]
    hi_iter = iter(highs_sorted)
    lo_iter = iter(lows_sorted)
    upcoming_high: Optional[Swing] = next(hi_iter, None)
    upcoming_low: Optional[Swing] = next(lo_iter, None)
    active_high: Optional[Swing] = None
    active_low: Optional[Swing] = None
    trend: Optional[str] = None

    disp_by_index = {d.index: d for d in displacements}

    for i, close in enumerate(closes):
        while upcoming_high is not None and upcoming_high.confirmIndex <= i:
            active_high = upcoming_high
            upcoming_high = next(hi_iter, None)
        while upcoming_low is not None and upcoming_low.confirmIndex <= i:
            active_low = upcoming_low
            upcoming_low = next(lo_iter, None)

        if active_high is not None and close > active_high.price:
            ev_type = "BOS_up" if trend in (None, "up") else "MSS_up"
            disp = _validating_displacement(disp_by_index, i)
            events.append(StructureEvent(i, ev_type, active_high.price, active_high.index,
                                         "up", bool(disp), disp.strength if disp else 0.0))
            trend = "up"
            active_high = None
        if active_low is not None and close < active_low.price:
            ev_type = "BOS_down" if trend in (None, "down") else "MSS_down"
            disp = _validating_displacement(disp_by_index, i)
            events.append(StructureEvent(i, ev_type, active_low.price, active_low.index,
                                         "down", bool(disp), disp.strength if disp else 0.0))
            trend = "down"
            active_low = None
    return events


def _validating_displacement(disp_by_index: dict[int, Displacement], idx: int) -> Optional[Displacement]:
    """The displacement validating this break: exactly at, or within 1 bar before."""
    d = disp_by_index.get(idx)
    if d is None:
        d = disp_by_index.get(idx - 1)
    return d


def find_liquidity_sweeps(highs: list[float], lows: list[float], closes: list[float],
                          swing_highs: list[Swing], swing_lows: list[Swing],
                          atr_series: list[Optional[float]],
                          min_age_bars: int = 3,
                          max_exceed_atr: float = 1.0) -> list[Sweep]:
    out: list[Sweep] = []
    for s in swing_highs:
        for i in range(max(s.confirmIndex, s.index + min_age_bars), len(closes)):
            atr_v = atr_series[i] or 0.0
            wick = highs[i] - s.price
            if wick > 0 and closes[i] < s.price:
                if wick <= max_exceed_atr * max(atr_v, 1e-12):
                    out.append(Sweep(i, "bearish", s.price, s.index, round(wick, 10)))
                break
    for s in swing_lows:
        for i in range(max(s.confirmIndex, s.index + min_age_bars), len(closes)):
            atr_v = atr_series[i] or 0.0
            wick = s.price - lows[i]
            if wick > 0 and closes[i] > s.price:
                if wick <= max_exceed_atr * max(atr_v, 1e-12):
                    out.append(Sweep(i, "bullish", s.price, s.index, round(wick, 10)))
                break
    out.sort(key=lambda x: x.index)
    return out


def equal_levels(swings: list[Swing], atr_series: list[Optional[float]],
                 atr_tolerance: float = 0.1) -> list[dict]:
    out: list[dict] = []
    for a, b in zip(swings, swings[1:]):
        atr_v = atr_series[b.index] or 0.0
        if abs(b.price - a.price) <= atr_tolerance * max(atr_v, 1e-12):
            out.append({"kind": a.kind, "priceA": a.price, "priceB": b.price,
                        "indexA": a.index, "indexB": b.index,
                        "level": round((a.price + b.price) / 2, 10),
                        "confirmIndex": b.confirmIndex})
    return out
