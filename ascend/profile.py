"""Volume-profile engine for ASCEND-30 (POC / value area / HVN / LVN).

Builds a deterministic price-bin histogram over a rolling window of *closed*
candles and derives:
  Point of Control (POC)   -- the price with the most traded volume.
  Value Area (VA)          -- the narrowest price range containing ~70% of volume,
                              bounded by Value Area High (VAH) / Low (VAL).
  Value position           -- is last close in premium or discount vs the POC.
  High/Low volume nodes    -- local maxima/minima (shelves / air-pockets).

No lookahead: only closed candles are passed in.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Profile:
    poc: float | None
    vah: float | None
    val: float | None
    valueAreaPct: float
    valuePosition: str           # 'discount' | 'premium' | 'at_value' | 'unknown'
    hvns: list[float] = field(default_factory=list)
    lvns: list[float] = field(default_factory=list)
    rows: int = 0
    low: float | None = None
    high: float | None = None

    @property
    def value_range(self) -> tuple[float | None, float | None]:
        return (self.val, self.vah)

    def as_dict(self) -> dict:
        return {
            "poc": self.poc, "vah": self.vah, "val": self.val,
            "valueAreaPct": self.valueAreaPct, "valuePosition": self.valuePosition,
            "hvns": self.hvns[:6], "lvns": self.lvns[:6],
            "rows": self.rows, "low": self.low, "high": self.high,
        }


def _bin_edges(low: float, high: float, rows: int) -> list[float]:
    """Return rows+1 evenly spaced bin edges covering [low, high]."""
    span = high - low
    if span <= 0:
        return [low] * (rows + 1)
    step = span / rows
    return [low + i * step for i in range(rows + 1)]


def build_profile(closes: list[float], highs: list[float], lows: list[float],
                  volumes: list[float], value_area_pct: float = 0.70,
                  rows: int = 48, candies: list | None = None) -> Profile:
    """Build a volume profile over the provided candle series (closed only)."""
    n = len(closes)
    if n < 5 or rows <= 0:
        return Profile(None, None, None, value_area_pct, "unknown", [], [], rows)

    lo = min(lows)
    hi = max(highs)
    if hi <= lo:
        return Profile(None, None, None, value_area_pct, "unknown", [], [], rows)

    edges = _bin_edges(lo, hi, rows)
    counts = [0.0] * rows
    # distribute each candle's volume across the bins its range overlaps
    for i in range(n):
        c_lo, c_hi = lows[i], highs[i]
        if c_hi < lo or c_lo > hi or volumes[i] is None:
            continue
        # find overlapping bins (clamped)
        idx_lo = max(0, _index_of(c_lo, edges) - 1)
        idx_hi = min(rows - 1, _index_of(c_hi, edges))
        vol = float(volumes[i] or 0)
        span = max(1e-12, c_hi - c_lo)
        for b in range(max(0, idx_lo), min(rows, idx_hi + 1)):
            bin_lo = edges[b]
            bin_hi = edges[b + 1]
            overlap = max(0.0, min(c_hi, bin_hi) - max(c_lo, bin_lo))
            if overlap > 0:
                counts[b] += vol * (overlap / span)

    total = sum(counts)
    if total <= 0:
        return Profile(None, None, None, value_area_pct, "unknown", [], [], rows)

    # POC = bin with the highest volume; centre price of that bin
    poc_idx = max(range(rows), key=lambda b: counts[b])
    poc = (edges[poc_idx] + edges[poc_idx + 1]) / 2.0

    # Value area: expand outward from the POC to contain value_area_pct of volume
    sorted_by_vol = sorted(range(rows), key=lambda b: -counts[b])
    chosen = {poc_idx}
    acc = counts[poc_idx]
    target = total * value_area_pct
    for b in sorted_by_vol:
        if acc >= target:
            break
        if b not in chosen:
            chosen.add(b)
            acc += counts[b]
    chosen = sorted(chosen)
    val = edges[chosen[0]]
    vah = edges[chosen[-1] + 1]

    last = closes[-1]
    if last is None:
        vpos = "unknown"
    elif last < val:
        vpos = "discount"
    elif last > vah:
        vpos = "premium"
    else:
        vpos = "at_value"

    hvns = [_bin_centre(edges, b) for b in _local_extrema(counts, mode="high")]
    lvns = [_bin_centre(edges, b) for b in _local_extrema(counts, mode="low")]

    return Profile(poc, vah, val, value_area_pct, vpos, hvns, lvns, rows, lo, hi)


def _index_of(price: float, edges: list[float]) -> int:
    """Binary search for the bin index a price falls into."""
    lo, hi = 0, len(edges) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if edges[mid] <= price:
            lo = mid + 1
        else:
            hi = mid
    return max(1, lo)


def _bin_centre(edges: list[float], idx: int) -> float:
    return (edges[idx] + edges[idx + 1]) / 2.0


def _local_extrema(counts: list[float], mode: str, window: int = 2) -> list[int]:
    """Indices of local maxima (mode='high') or minima (mode='low')."""
    out: list[int] = []
    n = len(counts)
    for i in range(window, n - window):
        window_slice = counts[i - window:i + window + 1]
        if mode == "high" and counts[i] == max(window_slice) and counts[i] > 0:
            out.append(i)
        elif mode == "low" and counts[i] == min(window_slice) and counts[i] < max(window_slice):
            out.append(i)
    return out
