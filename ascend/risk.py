"""Risk & setup engine for ASCEND-30.

Builds a structure-based entry / stop / target with an ATR (N) buffer and the
Turtle "let it run" target philosophy, then computes the risk-reward and the
prop-grade position size (risk a % of the remaining drawdown buffer).

For LONG (SHORT is the exact mirror):
  invalidation = the swept level / recent confirmed swing low being tested.
  entry limit  = a retracement of the displacement leg (default 0.5 retracement)
                 or the volume-profile value-area low.
  SL           = invalidation - stopBuffer*ATR, clamped to [stopMin, stopMax]*ATR.
  TP           = nearest real liquidity target (swing high / equal-high / HVN / POC)
                 above the trigger that funds RR >= minRr; prefer RR >= preferredRr.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Setup:
    direction: str          # 'long' | 'short'
    entry: float            # limit / trigger price
    entryLow: float
    entryHigh: float
    stop: float
    target1: float
    target2: float | None
    invalidation: float
    rr: float | None
    target1Kind: str
    target2Kind: str | None
    risk: float | None      # risk per trade as a fraction (0..1), set by sizing
    size_units: float | None
    notes: list = field(default_factory=list)


def _liquidity_targets(direction: str, a: dict, entry: float, min_rr: float,
                       preferred_rr: float) -> list[tuple[float, str]]:
    """Real liquidity targets above (long) / below (short) the entry.

    Pulls confirmed swing highs/lows, equal-high/low pools and volume-profile
    HVN / POC. Returns ascending (long) / descending (short) candidates.
    """
    f15 = a["frames"]["15"]
    swings = f15.get("swingHighs") or []
    swings_lo = f15.get("swingLows") or []
    eqh = f15.get("equalHighs") or []
    eql = f15.get("equalLows") or []
    prof = a.get("profile") or {}
    hvns = prof.get("hvns") or []
    poc = prof.get("poc")
    last = a.get("last15mIndex")
    cand: list[tuple[float, str]] = []

    if direction == "long":
        for s in swings:
            if s.confirmIndex <= last and s.price > entry:
                cand.append((s.price, "swing_high"))
        for e in eqh:
            if e.get("confirmIndex", 0) <= last and e["level"] > entry:
                cand.append((e["level"], "equal_highs"))
        for h in hvns:
            if h > entry:
                cand.append((h, "hvn"))
        if poc and poc > entry:
            cand.append((poc, "poc"))
        cand = sorted(cand, key=lambda x: x[0])
    else:
        for s in swings_lo:
            if s.confirmIndex <= last and s.price < entry:
                cand.append((s.price, "swing_low"))
        for e in eql:
            if e.get("confirmIndex", 0) <= last and e["level"] < entry:
                cand.append((e["level"], "equal_lows"))
        for h in hvns:
            if h < entry:
                cand.append((h, "hvn"))
        if poc and poc < entry:
            cand.append((poc, "poc"))
        cand = sorted(cand, key=lambda x: -x[0])
    return cand


def _rr(direction: str, entry: float, stop: float, target: float) -> float | None:
    denom = (entry - stop) if direction == "long" else (stop - entry)
    numer = (target - entry) if direction == "long" else (entry - target)
    if denom <= 0 or numer <= 0:
        return None
    return round(numer / denom, 4)


def _fvg_for(direction: str, a: dict, last: int) -> "structure.FVG | None":
    """Most recent confirmed FVG aligned with the trade direction."""
    f15 = a["frames"]["15"]
    want = {"long": "bullish", "short": "bearish"}[direction]
    cands = [f for f in (f15.get("fvgs") or [])
             if f.direction == want and f.confirmIndex <= last]
    return cands[-1] if cands else None


def _ob_for(direction: str, a: dict, last: int) -> "structure.OrderBlock | None":
    """Most recent confirmed order block aligned with the trade direction."""
    f15 = a["frames"]["15"]
    want = {"long": "bullish", "short": "bearish"}[direction]
    cands = [o for o in (f15.get("orderBlocks") or [])
             if o.direction == want and o.confirmIndex <= last]
    return cands[-1] if cands else None


def _default_entry(direction: str, a: dict, cfg, invalidation: float,
                   atr: float, price: float, lo: int, last: int) -> tuple[float, str]:
    """Regime-aware fallback entry.

    TREND/MIXED -> 0.5 retracement of the displacement leg (Turtle continuation).
    RANGE -> mean-revert to the value-area edge (discount VAL for a long,
    premium VAH for a short), which is the honest counter-trend location.
    """
    f15 = a["frames"]["15"]
    regime = a.get("regime")
    prof = a.get("profile") or {}
    if direction == "long":
        leg_high = max(f15["h"][lo:last + 1])
        entry_by_retrace = invalidation + 0.5 * (leg_high - invalidation)
        if regime == "RANGE":
            val = prof.get("val")
            if val and invalidation < val < price:
                return val, "value_area"
        entry = min(entry_by_retrace, price) if price else entry_by_retrace
        entry = max(entry, invalidation + 0.25 * atr)
        entry = min(entry, price - 0.05 * atr)
        return entry, "retracement"
    else:
        leg_low = min(f15["l"][lo:last + 1])
        entry_by_retrace = invalidation - 0.5 * (invalidation - leg_low)
        if regime == "RANGE":
            vah = prof.get("vah")
            if vah and price < vah < invalidation:
                return vah, "value_area"
        entry = max(entry_by_retrace, price) if price else entry_by_retrace
        entry = min(entry, invalidation - 0.25 * atr)
        entry = max(entry, price + 0.05 * atr)
        return entry, "retracement"


def _try_setup(direction: str, a: dict, cfg, entry: float, inlet_kind: str,
               invalidation: float, atr: float, price: float,
               min_rr: float, pref_rr: float,
               buf: float, stop_max: float, stop_min: float) -> tuple[Setup | None, str]:
    """Given a specific entry, build stop + targets and validate ordering/RR."""
    f15 = a["frames"]["15"]
    last = a.get("last15mIndex")
    # entry must sit on the correct side of invalidation and price
    if direction == "long":
        if not (invalidation < entry < price):
            return None, "long entry outside invalidation/price"
        stop = invalidation - buf * atr
        if stop >= entry:
            return None, "long stop not below entry"
    else:
        if not (price < entry < invalidation):
            return None, "short entry outside invalidation/price"
        stop = invalidation + buf * atr
        if stop <= entry:
            return None, "short stop not above entry"
    stop_dist = abs(entry - stop)
    if stop_dist < stop_min * atr:
        stop = entry - stop_min * atr if direction == "long" else entry + stop_min * atr
        stop_dist = abs(entry - stop)
    if stop_dist > stop_max * atr:
        stop = entry - stop_max * atr if direction == "long" else entry + stop_max * atr
        stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return None, "degenerate stop"

    cands = _liquidity_targets(direction, a, entry, min_rr, pref_rr)
    chosen1 = chosen2 = None
    for level, kind in cands:
        rr = _rr(direction, entry, stop, level)
        if rr is not None and rr >= min_rr:
            chosen1 = (level, kind, rr)
            if rr >= pref_rr:
                break
    if chosen1 is None:
        return None, "no liquidity target funds min RR"
    target1, t1kind, rr1 = chosen1
    for level, kind in cands:
        if (direction == "long" and level > target1) or (direction == "short" and level < target1):
            chosen2 = (level, kind)
            break
    target2, t2kind = chosen2 if chosen2 else (None, None)

    setup = Setup(direction=direction, entry=round(entry, 10),
                  entryLow=round(min(entry, price), 10), entryHigh=round(max(entry, price), 10),
                  stop=round(stop, 10), target1=round(target1, 10),
                  target2=round(target2, 10) if target2 else None,
                  invalidation=round(invalidation, 10), rr=rr1,
                  target1Kind=t1kind, target2Kind=t2kind, risk=None, size_units=None,
                  notes=[f"entry_source={inlet_kind}"])
    if direction == "long" and not (setup.stop < setup.entry and setup.target1 > setup.entry):
        return None, "long ordering invalid"
    if direction == "short" and not (setup.stop > setup.entry and setup.target1 < setup.entry):
        return None, "short ordering invalid"
    return setup, ""


def build_setup(direction: str, a: dict, cfg) -> tuple[Setup | None, str]:
    """Build the best entry/stop/target for the candidate direction.

    Entry sources are tried in priority order (regime-tiered):
      TREND/MIXED -> FVG consequent-encroachment > order block > retracement.
      RANGE       -> value-area mean-revert > FVG > order block > retracement.
    The first source that yields a valid (ordered, RR-gated) setup wins; so a
    weak FVG never forces a bad trade -- it falls through to the classic entry.
    """
    sm = cfg.get("signalModel")
    atr = a.get("atr15m")
    price = a.get("price")
    if not atr or not price:
        return None, "missing atr/price"
    min_rr = sm.get("minRr", 2.5)
    pref_rr = sm.get("preferredRr", 3.0)
    buf = sm.get("stopBufferAtrMultiple", 0.5)
    stop_max = sm.get("stopMaxAtrMultiple", 2.5)
    stop_min = sm.get("stopMinAtrMultiple", 1.0)

    f15 = a["frames"]["15"]
    last = a.get("last15mIndex")
    window = int(cfg.get("structure.mssWindowBars", 12))
    lo = max(2, last - window)
    regime = a.get("regime")

    sweeps = [s for s in (f15.get("sweeps") or [])
              if s.direction == ("bullish" if direction == "long" else "bearish")
              and lo <= s.index <= last]
    sweep = sweeps[-1] if sweeps else None

    # invalidation level
    if direction == "long":
        invalidation = sweep.level if sweep else None
        if invalidation is None:
            lows = f15["l"]
            recent_swing_lows = [s for s in (f15.get("swingLows") or [])
                                 if s.confirmIndex <= last]
            invalidation = (max(recent_swing_lows, key=lambda s: s.index).price
                            if recent_swing_lows else min(lows[lo:last + 1]))
    else:
        invalidation = sweep.level if sweep else None
        if invalidation is None:
            highs = f15["h"]
            recent_swing_highs = [s for s in (f15.get("swingHighs") or [])
                                  if s.confirmIndex <= last]
            invalidation = (min(recent_swing_highs, key=lambda s: s.index).price
                            if recent_swing_highs else max(highs[lo:last + 1]))
    if invalidation is None or invalidation <= 0:
        return None, "no invalidation"

    # build the candidate list, tiered by regime
    candidates: list[tuple[float, str]] = []
    fvg = _fvg_for(direction, a, last)
    ob = _ob_for(direction, a, last)
    default = _default_entry(direction, a, cfg, invalidation, atr, price, lo, last)

    if regime == "RANGE":
        # mean-revert first, then FVG/OB, then the classic retracement
        if default[1] == "value_area":
            candidates.append(default)
        if fvg:
            candidates.append((fvg.mid, "fvg"))
        if ob:
            candidates.append((ob.mid, "order_block"))
        if default[1] != "value_area":
            candidates.append(default)
    else:
        # TREND / MIXED: location-first (FVG/OB) then continuation retracement
        if fvg:
            candidates.append((fvg.mid, "fvg"))
        if ob:
            candidates.append((ob.mid, "order_block"))
        candidates.append(default)

    # try candidates in priority order; first valid wins
    tried: set[float] = set()
    for entry, kind in candidates:
        if entry <= 0 or entry in tried:
            continue
        tried.add(entry)
        setup, reason = _try_setup(direction, a, cfg, entry, kind, invalidation,
                                   atr, price, min_rr, pref_rr, buf, stop_max, stop_min)
        if setup is not None:
            return setup, ""
    return None, "no candidate entry funds min RR"


def size_position(setup: Setup, account_equity: float, contract_value: float,
                  remaining_buffer_pct: float, cfg) -> Setup:
    """Size using the Turtle / prop-firm buffer-risk formula.

    risk_pct = clamp(remaining_buffer_pct * riskOfBufferFactor, minRiskPct, maxRiskPct).
    size_units = (risk_amount) / (stop_distance * contract_value).
    """
    r = cfg.get("risk", {})
    stop_dist = abs(setup.entry - setup.stop)
    if stop_dist <= 0:
        setup.risk = None
        setup.size_units = None
        return setup
    risk_pct = max(r.get("minRiskPct", 0.25),
                   min(r.get("maxRiskPct", 1.0),
                       remaining_buffer_pct * r.get("riskOfBufferFactor", 0.08)))
    risk_amount = account_equity * risk_pct / 100.0
    units = risk_amount / (stop_dist * contract_value) if contract_value else None
    setup.risk = round(risk_pct / 100.0, 6)
    setup.size_units = round(units, 6) if units is not None else None
    return setup
