"""Logistic calibration for ASCEND-30.

``scoring.calibrate_score`` uses an initial, honest `{a, b}`. This module
re-fits `{a, b}` from *resolved* outcome history so that a published
P(profit>=1R) actually matches the observed empirical win-rate. It is stdlib-only
(no numpy/scipy): a small batch gradient-descent on the Bernoulli negative
log-likelihood, with an L2 prior that keeps `a`/`b` finite on tiny samples.

The caller must only feed **resolved** trades (the scalar outcome did the trade
reach +1R before its stop/time-stop, 0/1). Fitting on unresolved or
in-progress trades contaminates the calibration and is deliberately not done.
"""
from __future__ import annotations

import math

DEFAULT_A, DEFAULT_B = -3.6, 6.0


def _sigmoid(x: float) -> float:
    if x > 30:
        return 1.0
    if x < -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def fit_logistic(samples: list[tuple[float, int]],
                 l2: float = 0.01, lr: float = 0.05,
                 epochs: int = 5000, a0: float = DEFAULT_A,
                 b0: float = DEFAULT_B,
                 tol: float = 1e-7) -> tuple[float, float]:
    """Fit `a, b` for P = sigmoid(a + b*score/100) on (score, won) samples.

    Returns the converged `(a, b)`. If there are < 2 samples or no label variety
    the fit does not move (returns the prior), because a 1-parameter family
    cannot be identified from a single outcome class.
    """
    if len(samples) < 2:
        return a0, b0
    labels = {y for _, y in samples}
    if len(labels) < 2:
        return a0, b0
    a, b = a0, b0
    n = float(len(samples))
    prev_loss: float | None = None
    for _ in range(epochs):
        ga, gb = 0.0, 0.0
        loss = 0.0
        for score, y in samples:
            x = a + b * score / 100.0
            p = _sigmoid(x)
            p = min(max(p, 1e-7), 1 - 1e-7)
            # gradient of NLL w.r.t. x = p - y
            dx = p - y
            ga += dx
            gb += dx * (score / 100.0)
            loss += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        # L2 prior to keep params finite; note the mean + prior / n
        ga = ga / n + (l2 / n) * a
        gb = gb / n + (l2 / n) * b
        loss = loss / n + (l2 / (2 * n)) * (a * a + b * b)
        a -= lr * ga
        b -= lr * gb
        if prev_loss is not None and abs(prev_loss - loss) < tol:
            break
        prev_loss = loss
    return round(a, 4), round(b, 4)


def wilson_interval_positives(total: int, k: int, z: float = 1.96) -> tuple[float, float]:
    """Standard Wilson score interval for a proportion, for the dashboard CI."""
    if total <= 0:
        return (0.0, 1.0)
    p_hat = k / total
    denom = 1 + z * z / total
    centre = p_hat + z * z / (2 * total)
    margin = z * math.sqrt((p_hat * (1 - p_hat) / total) + z * z / (4 * total * total))
    return (max(0.0, (centre - margin) / denom), min(1.0, (centre + margin) / denom))


def apply_fit(cfg, a: float, b: float) -> None:
    """Write fitted logistic params into the running config + persisted config file."""
    import json
    from .config import repo_root
    cfg._data["scoring"]["logistic"] = {"a": a, "b": b}
    path = repo_root() / "config" / "ascend.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("scoring", {}).setdefault("logistic", {})
        data["scoring"]["logistic"].update({"a": a, "b": b})
        path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")
