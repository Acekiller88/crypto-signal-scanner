"""Signal evaluation for ASCEND-30.

Given one symbol's analysis, this evaluates BOTH the long and short candidates,
builds each structure-based setup, scores each, and picks the better side. It
returns a full scorecard (all sub-scores + probability + label) plus an entry
object if a side clears the publish threshold. Deterministic, non-repaint.
"""
from __future__ import annotations

from . import risk as rk
from . import scoring as sc


def evaluate_symbol(a: dict, cfg) -> dict:
    """Run both candidate directions; return the scorecard + (optional) signal."""
    setups: dict[str, rk.Setup | None] = {}
    reasons: dict[str, str] = {}
    for direction in ("long", "short"):
        setup, reason = rk.build_setup(direction, a, cfg)
        setups[direction] = setup
        reasons[direction] = reason

    results = {}
    for direction in ("long", "short"):
        setup = setups[direction]
        scorecard = sc.score_signal(a, direction, setup, cfg)
        if setup is None:
            scorecard["setup"] = None
            scorecard["rejection"] = reasons[direction]
        else:
            scorecard["setup"] = _setup_dict(setup, a, cfg)
            scorecard["rejection"] = ""
        results[direction] = scorecard

    # pick the dominant direction by confidence (trend favours the aligned side)
    (long_s, short_s) = (results["long"]["score"], results["short"]["score"])
    better = "long" if long_s >= short_s else "short"
    setups_for_decide = {"_analysis": a, "long": setups["long"], "short": setups["short"]}
    decision = sc.decide(better, setups_for_decide, cfg)

    return {
        "symbol": a["symbol"],
        "price": a.get("price"),
        "regime": a.get("regime"),
        "bias1d": a.get("bias1d", {}).get("bias"),
        "bias4h": a.get("bias4h", {}).get("bias"),
        "long": results["long"],
        "short": results["short"],
        "decision": decision,
        "info": {
            "last15mCloseTime": a.get("last15mCloseTime"),
            "atr15m": a.get("atr15m"),
            "adx1d": a.get("adx1d"),
            "relVol15m": a.get("relVol15m"),
            "vei15m": a.get("vei15m"),
            "valuePosition": (a.get("profile") or {}).get("valuePosition"),
            "fundingRate": (a.get("ticker") or {}).get("fundingRate"),
            "openInterest": (a.get("ticker") or {}).get("openInterest"),
        },
        "setup": _setup_dict(setups[better], a, cfg) if decision else None,
    }


def build_exit_plan(s: rk.Setup, cfg, a: dict) -> dict:
    """Managed-exit ladder for a setup (Turtle + prop discipline).

    Publish the rules so a downstream executor (or the dashboard) can act:
      * breakeven  -- move stop to entry once the trade reaches +1R.
      * partial    -- trim ``partialFraction`` at ``partialAtR`` R.
      * trail      -- trail the stop trailAtrMultiple*ATR beyond structure.
      * timeStop   -- exit if not > breakeven within ``timeStopBars`` 15M bars.
    """
    sm = cfg.get("signalModel", {})
    atr = a.get("atr15m") or 0.0
    risk_dist = abs(s.entry - s.stop)
    be_at_r = sm.get("breakevenAtR", 1.0)
    partial_at_r = sm.get("partialAtR", 1.0)
    partial_frac = sm.get("partialFraction", 0.5)
    trail_atr = sm.get("trailAtrMultiple", 0.5)
    time_stop = sm.get("timeStopBars", 24)

    # price at which each event fires (LONG: +R up; SHORT: +R down)
    if s.direction == "long":
        be_price = s.entry + be_at_r * risk_dist
        partial_price = s.entry + partial_at_r * risk_dist
        trail_offset = trail_atr * atr
    else:
        be_price = s.entry - be_at_r * risk_dist
        partial_price = s.entry - partial_at_r * risk_dist
        trail_offset = trail_atr * atr

    # structure trail level = most recent confirmed swing beyond the entry
    last = a.get("last15mIndex")
    f15 = a["frames"]["15"]
    trail_level = None
    if s.direction == "long":
        highs = [sw.price for sw in (f15.get("swingHighs") or []) if sw.confirmIndex <= last]
        trail_level = max(highs) if highs else None
        trail_level = (trail_level - trail_offset) if trail_level else None
    else:
        lows = [sw.price for sw in (f15.get("swingLows") or []) if sw.confirmIndex <= last]
        trail_level = min(lows) if lows else None
        trail_level = (trail_level + trail_offset) if trail_level else None

    return {
        "breakeven": {"atR": be_at_r, "price": round(be_price, 10)},
        "partial": {"atR": partial_at_r, "fraction": partial_frac,
                    "price": round(partial_price, 10)},
        "trail": {"atrMultiple": trail_atr, "offset": round(trail_offset, 10),
                  "structureLevel": round(trail_level, 10) if trail_level else None},
        "timeStop": {"bars": time_stop},
        "stopPrice": s.stop,
    }


def _setup_dict(s: rk.Setup | None, a: dict | None = None, cfg=None) -> dict:
    if s is None:
        return None
    entry_source = next((n.split("=", 1)[1] for n in s.notes if n.startswith("entry_source=")), None)
    out = {"direction": s.direction.upper(), "entry": s.entry,
           "entryLow": s.entryLow, "entryHigh": s.entryHigh, "stop": s.stop,
           "target1": s.target1, "target2": s.target2,
           "invalidation": s.invalidation, "rr": s.rr,
           "target1Kind": s.target1Kind, "target2Kind": s.target2Kind,
           "risk": s.risk, "sizeUnits": s.size_units, "entrySource": entry_source}
    if a is not None and cfg is not None:
        out["exitPlan"] = build_exit_plan(s, cfg, a)
    return out


def to_signal_row(ev: dict, cfg) -> dict:
    """Turn an evaluate_symbol result into a persisted signal row (or None)."""
    if ev.get("decision") is None:
        return None
    d = ev["decision"]
    setup = ev["setup"]
    sig = {
        "id": "ASC-" + ev["symbol"] + "-" + str(d["direction"]) + "-" + str(ev["info"]["last15mCloseTime"]),
        "symbol": ev["symbol"],
        "direction": d["direction"],
        "score": d["score"],
        "prob": d["prob"],
        "label": d["label"],
        "components": d["components"],
        "regime": ev["regime"],
        "bias1d": ev["bias1d"],
        "bias4h": ev["bias4h"],
        "price": ev["price"],
        "entry": setup["entry"] if setup else None,
        "stop": setup["stop"] if setup else None,
        "target1": setup["target1"] if setup else None,
        "target2": setup["target2"] if setup else None,
        "rr": setup["rr"] if setup else None,
        "invalidation": setup["invalidation"] if setup else None,
        "entrySource": setup["entrySource"] if setup else None,
        "exitPlan": setup["exitPlan"] if setup else None,
        "signalCandleCloseTime": ev["info"]["last15mCloseTime"],
        "atr15m": ev["info"]["atr15m"],
        "valuePosition": ev["info"]["valuePosition"],
        "fundingRate": ev["info"]["fundingRate"],
        "openInterest": ev["info"]["openInterest"],
    }
    return sig
