"""Confidence / scoring engine for ASCEND-30.

Computes the seven sub-scores (0..1) that feed the 0-100 weighted confidence,
then maps that to a **calibrated probability** P(profit >= 1R) via a logistic
curve, and finally to a label: Strong Buy / Buy / Neutral / Sell / Strong Sell.

The logistic shape is an honest, annotated starting point (config.scoring.logistic)
but the caller re-fits it from resolved history (see engine.calibrate) before
trusting the probabilities at scale. Every published score carries its sample
size so a small-sample "Strong Buy" is never mistaken for a guarantee.
"""
from __future__ import annotations

import math

from .risk import Setup

STRONG_BUY = "Strong Buy"
BUY = "Buy"
NEUTRAL = "Neutral"
SELL = "Sell"
STRONG_SELL = "Strong Sell"


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- components
def trend_component(a: dict, direction: str) -> float:
    want = 1 if direction == "long" else -1
    b1 = a.get("bias1d", {}).get("dir", 0)
    b4 = a.get("bias4h", {}).get("dir", 0)

    def _score(d):
        if d == want:
            base = 0.8
            strength = a.get("bias1d", {}).get("strength", 0) if d == b1 else a.get("bias4h", {}).get("strength", 0)
            return _clamp(base + 0.1 * min(strength, 2.0))
        if d == 0:
            return 0.4
        return 0.0

    return _clamp(0.6 * _score(b1) + 0.4 * _score(b4))


def structure_component(a: dict, cfg, direction: str) -> float:
    f15 = a["frames"]["15"]
    last = a.get("last15mIndex", 0)
    window = int(cfg.get("structure.mssWindowBars", 12))
    lo = max(2, last - window)
    want_types = ("MSS_up", "BOS_up") if direction == "long" else ("MSS_down", "BOS_down")
    events = [e for e in (f15.get("events") or [])
              if e.type in want_types and lo <= e.index <= last]
    if not events:
        # opposing event recently (bad structure) -> low
        oppose = ["MSS_down", "BOS_down"] if direction == "long" else ["MSS_up", "BOS_up"]
        opp = [e for e in (f15.get("events") or [])
               if e.type in oppose and lo <= e.index <= last]
        return _clamp(0.45 if not opp else 0.15)
    e = events[-1]
    age = last - e.index
    base = 0.9 if e.type.startswith("MSS") else 0.8   # reversal (MSS) slightly stronger
    if e.displacement:
        base = min(1.0, base + 0.08 * min(e.strength / 1.5, 1.0))
    if age <= 6:
        base = min(1.0, base + 0.05)
    return _clamp(base)


def liquidity_component(a: dict, cfg, direction: str) -> float:
    f15 = a["frames"]["15"]
    last = a.get("last15mIndex", 0)
    window = int(cfg.get("structure.mssWindowBars", 12))
    lo = max(2, last - window)
    want_sweep = "bullish" if direction == "long" else "bearish"
    sweeps = [s for s in (f15.get("sweeps") or []) if s.direction == want_sweep and lo <= s.index <= last]
    prof = a.get("profile") or {}
    vpos = prof.get("valuePosition", "unknown")
    # value position: long wants discount, short wants premium
    if direction == "long":
        vp_score = 1.0 if vpos == "discount" else (0.6 if vpos == "at_value" else 0.2)
    else:
        vp_score = 1.0 if vpos == "premium" else (0.6 if vpos == "at_value" else 0.2)
    score = 0.0
    if sweeps:
        score += 0.55
    score += 0.35 * vp_score
    # confluence with value area / HVN shelf
    if direction == "long" and prof.get("val") and a.get("price", 1e9) and a["price"] >= prof.get("val", 0):
        score += 0.10
    if direction == "short" and prof.get("vah") and a.get("price") and a["price"] <= prof.get("vah", 1e9):
        score += 0.10
    return _clamp(score)


def momentum_component(a: dict, direction: str) -> float:
    want = 1 if direction == "long" else -1
    adx = a.get("adx15m")
    plus, minus = a.get("plusDi15m"), a.get("minusDi15m")
    rsi_v = a.get("rsi15m")
    di_ok = (plus is not None and minus is not None and plus > minus) if want == 1 else \
            (plus is not None and minus is not None and minus > plus)
    score = 0.0
    if adx is not None:
        if adx >= 30:
            score += 0.45
        elif adx >= 20:
            score += 0.35
        elif adx >= 15:
            score += 0.25
        else:
            score += 0.10
    if di_ok:
        score += 0.3
    if rsi_v is not None:
        if want == 1 and 50 <= rsi_v <= 72:
            score += 0.25
        elif want == -1 and 28 <= rsi_v <= 50:
            score += 0.25
        elif (want == 1 and rsi_v > 45) or (want == -1 and rsi_v < 55):
            score += 0.12
    return _clamp(score)


def volume_component(a: dict, direction: str) -> float:
    want = 1 if direction == "long" else -1
    rv = a.get("relVol15m")
    clv = a.get("avgClv15m")
    funding = (a.get("ticker") or {}).get("fundingRate")
    score = 0.0
    if rv is not None:
        score += 0.4 * _clamp(rv / 2.0)
    if clv is not None:
        # positive CLV aligns with long; negative CLV aligns with short
        if want == 1:
            score += 0.3 * _clamp(clv)
        else:
            score += 0.3 * _clamp(1.0 - clv)
    if funding is not None:
        try:
            f = float(funding) * 100.0  # percent per interval
        except (TypeError, ValueError):
            f = 0.0
        # extreme funding against the trade reduces the score
        if want == 1 and f > 0.10:
            score -= 0.15
        elif want == -1 and f < -0.10:
            score -= 0.15
        else:
            score += 0.15
    return _clamp(score)


def volatility_component(a: dict, direction: str) -> float:
    atr_pct = a.get("atrPercent15m")
    vei = a.get("vei15m")
    regime = a.get("regime")
    score = 0.0
    if atr_pct is not None and 0.1 <= atr_pct <= 4.0:
        score += 0.5
    # volatility expansion should match the mode
    if regime == "TREND" and vei is not None and vei >= 1.0:
        score += 0.5
    elif regime == "RANGE" and vei is not None and vei < 1.0:
        score += 0.5
    else:
        score += 0.25
    return _clamp(score)


def risk_component(setup: Setup | None, cfg) -> float:
    if setup is None or setup.rr is None:
        return 0.0
    min_rr = float(cfg.get("signalModel.minRr", 2.5))
    pref_rr = float(cfg.get("signalModel.preferredRr", 3.0))
    if setup.rr >= pref_rr:
        return 1.0
    if setup.rr >= min_rr:
        return 0.6 + 0.4 * (setup.rr - min_rr) / max(pref_rr - min_rr, 1e-9)
    return 0.0


# ---------------------------------------------------------------- aggregate
def score_signal(a: dict, direction: str, setup: Setup | None, cfg) -> dict:
    w = cfg.get("scoring.weights", {})
    comps = {
        "trend": trend_component(a, direction),
        "structure": structure_component(a, cfg, direction),
        "liquidity": liquidity_component(a, cfg, direction),
        "momentum": momentum_component(a, direction),
        "volume": volume_component(a, direction),
        "volatility": volatility_component(a, direction),
        "risk": risk_component(setup, cfg),
    }
    raw = sum(w.get(k, 0) * comps[k] for k in comps)
    score = round(100.0 * _clamp(raw), 1)
    return {"score": score, "components": {k: round(v, 3) for k, v in comps.items()}}


def calibrate_score(score: float, a: float = -3.6, b: float = 6.0) -> float:
    """Logistic: P(profit>=1R) = 1/(1+exp(-(a + b*score/100)))."""
    x = a + b * score / 100.0
    if x > 30:
        return 1.0
    if x < -30:
        return 0.0
    return round(1.0 / (1.0 + math.exp(-x)), 4)


def label_from_prob(prob: float, cfg) -> str:
    s = cfg.get("signalModel", {})
    if prob >= s.get("strongBuyProb", 0.70):
        return STRONG_BUY
    if prob >= s.get("buyProb", 0.55):
        return BUY
    if prob >= s.get("sellProb", 0.40):
        return NEUTRAL
    if prob >= 0.30:
        return SELL
    return STRONG_SELL


def decide(direction: str, setups: dict, cfg) -> dict | None:
    """Produce a signal decision for one candidate direction, or None.

    The confidence label is *direction-aware*: a high-probability LONG is a
    'Strong Buy', a high-probability SHORT is a 'Strong Sell'. The probability
    is always P(profit >= 1R) for that direction's setup.
    """
    sm = cfg.get("signalModel", {})
    setup = setups.get(direction)
    if setup is None:
        return None
    sc = score_signal(setups["_analysis"], direction, setup, cfg)
    prob = calibrate_score(sc["score"], cfg.get("scoring.logistic.a", -3.6),
                           cfg.get("scoring.logistic.b", 6.0))
    plain = label_from_prob(prob, cfg)
    if direction == "long":
        label = plain
    else:
        # map the probability band to the short-side label
        if prob >= sm.get("strongBuyProb", 0.70):
            label = STRONG_SELL
        elif prob >= sm.get("buyProb", 0.55):
            label = SELL
        elif prob >= sm.get("sellProb", 0.40):
            label = NEUTRAL
        else:
            label = BUY if prob >= 0.30 else STRONG_BUY
    min_score = sm.get("minScoreForSignal", 70)
    if sc["score"] < min_score:
        return None
    return {"direction": direction.upper(), "score": sc["score"],
            "components": sc["components"], "prob": prob, "label": label}



