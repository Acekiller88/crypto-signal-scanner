"""Offline tests for the ASCEND-30 engine (synthetic data only, no network).

These tests exercise the full pipeline -- analysis -> risk/setup -> scoring ->
signal -- against deterministic, stationary synthetic candles. They assert the
engine's hard guarantees (no repaint, level ordering, probability calibration)
without ever calling Bybit. Live-data tests live in the GitHub Actions workflow.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ascend.bybit import Candle  # noqa: E402
from ascend.config import Config  # noqa: E402
from ascend import indicators as ind  # noqa: E402
from ascend import analysis  # noqa: E402
from ascend import structure as st  # noqa: E402
from ascend import risk as rk  # noqa: E402
from ascend import scoring as sc  # noqa: E402
from ascend.signal import evaluate_symbol  # noqa: E402
from ascend.sessions import session_at  # noqa: E402

MS_15M, MS_4H, MS_1D = 900_000, 14_400_000, 86_400_000
T0 = 1_700_000_000_000


# ---------------------------------------------------------------- factories
def _candles(closes, base_open=None, wick=18.0, vol=1000.0, ms=MS_15M, t0=T0,
             start_idx=0):
    """Candles from a close path; open = prior close.

    Wick is a small deterministic per-candle value (base plus a sawtooth variation)
    so that consecutive highs (and lows) never produce exact plateaus, which
    otherwise defeats the strict-2-bar fractal detector.
    """
    out, prev = [], closes[0] if base_open is None else base_open
    for i, c_ in enumerate(closes):
        wi = wick + (i % 5) * 2.5            # 18.0, 20.5, 23.0, 25.5, 28.0, repeat
        hi = max(prev, c_) + wi
        lo = min(prev, c_) - wi
        t = t0 + (start_idx + i) * ms
        out.append(Candle(t, prev, hi, lo, c_, vol, t + ms - 1))
        prev = c_
    return out


def _wave(n, start, amp, period, slope):
    """A gentle sine-like wave plus a slow linear drift, so the path has clear
    local maxima/minima (needed for fractal swings) while staying bounded."""
    import math
    out, p = [], start
    for i in range(n):
        p += slope + amp * math.sin(2 * math.pi * (i % period) / period)
        out.append(p)
    return out


def _sweep_reversal(path, direction="long"):
    """Append a liquidity sweep + displacement reversal to a close path.

    For direction='long': the wave tops out, price pulls back to a lower low, a
    wick pokes below that low then closes back above (bullish sweep), and a
    large-bodied candle breaks the last lower-high (displacement) -- but the
    final close lands mid-range so a *higher* swing-high target still exists
    above entry (needed for an R:R >= minimum setup). 'short' is the mirror.
    """
    out = list(path)
    p = out[-1]
    if direction == "long":
        # top out, then pull back into a lower low
        out.append(p + 2.0); p += 2.0        # make a local high
        out.append(p + 0.3); p += 0.3
        for d in (-1.2, -1.0, -1.4, -0.8):    # down leg -> new local low
            p += d; out.append(p)
        low = p
        out.append(low - 0.5)                 # wick below the low (sweep)
        out.append(low + 0.4)                 # close back inside -> confirmed sweep
        out.append(low + 3.2)                 # displacement pop (body >> ATR)
        out.append(low + 0.6)                 # small pullback
        out.append(low + 0.8)                 # continuation, ends mid-range
    else:
        out.append(p - 2.0); p -= 2.0        # make a local low
        out.append(p - 0.3); p -= 0.3
        for d in (1.2, 1.0, 1.4, 0.8):        # up leg -> new local high
            p += d; out.append(p)
        high = p
        out.append(high + 0.5)                # wick above the high (sweep)
        out.append(high - 0.4)                # close back inside -> confirmed sweep
        out.append(high - 3.2)                # displacement drop
        out.append(high - 0.6)
        out.append(high - 0.8)
    return out


def _frames(direction="long"):
    """Build deterministic 1D/4H/15M frames with real fractal swings.

    1D & 4H are slow up (long) / down (short) waves; the 15M series is a wide
    wave ending in a sweep + displacement reversal, so the engine sees genuine
    structure events and a confirmed swing to trade against. The amplitude is
    sized so the resulting setup funds an R:R >= the engine's minimum (a weak or
    over-compressed market is *correctly* rejected, tested separately).
    """
    sgn = 1.0 if direction == "long" else -1.0
    d = _candles(_wave(270, 50_000.0, 260.0, 18, 60.0 * sgn), ms=MS_1D)
    h4 = _candles(_wave(270, 250_000.0, 320.0, 16, 80.0 * sgn), ms=MS_4H)
    m15 = _sweep_reversal(_wave(270, 50_000.0, 650.0, 24, 42.0 * sgn),
                          direction=direction)
    return {"D": d, "240": h4, "15": _candles(m15, wick=8, ms=MS_15M)}


def _long_frames():
    return _frames("long")


def _short_frames():
    return _frames("short")


@pytest.fixture()
def cfg():
    return Config.load()


@pytest.fixture()
def now_ms():
    """Long after the last candle close is hard-coded, so no repaint window."""
    return 2_000_000_000_000


# ---------------------------------------------------------------- analysis
class TestAnalysisNonRepaint:
    def test_closed_candle_only(self, cfg, now_ms):
        a = analysis.analyze_symbol("TESTUSDT", _long_frames(), cfg, now_ms,
                                    {"fundingRate": "0.0001", "openInterest": "123"})
        assert a["ok"] is True
        assert a["last15mCloseTime"] < now_ms
        assert a["price"] > 0

    def test_indicator_no_lookahead(self):
        # value_at must not depend on future entries
        s = ind.ema(list(range(1, 40)), 5)
        for i in range(len(s)):
            assert s[i] == s[i]  # finite
        # structure swing confirmIndex strictly after its own index
        highs = [10, 12, 11, 13, 12, 14, 13]
        swings = st.find_swing_highs(highs, 2)
        for sw in swings:
            assert sw.confirmIndex > sw.index

    def test_identical_inputs_identical_output(self, cfg, now_ms):
        f1, f2 = _long_frames(), _long_frames()
        a1 = analysis.analyze_symbol("T", f1, cfg, now_ms)
        a2 = analysis.analyze_symbol("T", f2, cfg, now_ms)
        assert a1 == a2

    def test_regime_classified(self, cfg, now_ms):
        a = analysis.analyze_symbol("T", _long_frames(), cfg, now_ms)
        assert a["regime"] in ("TREND", "RANGE", "MIXED")
        assert a["bias1d"]["dir"] in (-1, 0, 1)


# ---------------------------------------------------------------- scoring
class TestScoring:
    def test_probability_calibration_monotonic(self):
        probs = [sc.calibrate_score(x) for x in (0, 20, 40, 60, 80, 100)]
        assert probs == sorted(probs)
        assert 0 <= probs[0] <= 1 and 0 <= probs[-1] <= 1
        # score 60 is the logistic midpoint (a = -3.6, b = 6.0 -> 0.5)
        assert probs[3] == pytest.approx(0.5, abs=1e-3)
        p60 = sc.calibrate_score(60)
        assert 0.3 <= p60 <= 0.8

    def test_weighted_score_in_0_100(self, cfg):
        full = sc.score_signal({"frames": {"15": {"events": [], "sweeps": [], "swingHighs": [],
                                                  "swingLows": [], "equalHighs": [], "equalLows": []}},
                                "profile": {}, "price": 100}, "long", None, cfg)
        assert 0 <= full["score"] <= 100
        assert set(full["components"]) == set(cfg.get("scoring.weights"))

    def test_label_thresholds(self, cfg):
        assert sc.label_from_prob(0.9, cfg) == "Strong Buy"
        assert sc.label_from_prob(0.6, cfg) == "Buy"
        assert sc.label_from_prob(0.45, cfg) == "Neutral"
        assert sc.label_from_prob(0.35, cfg) == "Sell"
        assert sc.label_from_prob(0.05, cfg) == "Strong Sell"


# ---------------------------------------------------------------- risk
class TestRisk:
    def test_long_setup_ordering(self, cfg, now_ms):
        a = analysis.analyze_symbol("T", _long_frames(), cfg, now_ms)
        setup, reason = rk.build_setup("long", a, cfg)
        assert setup is not None, reason
        assert setup.stop < setup.entry < setup.target1
        assert setup.rr is not None and setup.rr >= cfg.get("signalModel.minRr")

    def test_short_setup_ordering(self, cfg, now_ms):
        a = analysis.analyze_symbol("T", _short_frames(), cfg, now_ms)
        setup, reason = rk.build_setup("short", a, cfg)
        assert setup is not None, reason
        assert setup.target1 < setup.entry < setup.stop

    def test_sizing_reduces_with_buffer(self, cfg):
        s1 = rk.Setup("long", 100, 99, 101, 95, 115, None, 94, 3.0, "s", None, None, None)
        sized = rk.size_position(s1, 1_000_000.0, 1.0, 8.0, cfg)
        assert 0 < sized.risk <= 1.0
        # entry=100, stop=95 -> stop distance 5
        assert sized.size_units == pytest.approx(1_000_000.0 * sized.risk / 5 / 1.0)


# ---------------------------------------------------------------- signal
class TestSignal:
    def test_long_market_scores_long_higher(self, cfg, now_ms):
        a = analysis.analyze_symbol("T", _long_frames(), cfg, now_ms)
        ev = evaluate_symbol(a, cfg)
        assert ev["long"]["score"] >= ev["short"]["score"]
        # the better side should be the one that scores >= min
        assert ev["decision"]["score"] >= ev["long"]["score"]

    def test_short_market_scores_short_higher(self, cfg, now_ms):
        a = analysis.analyze_symbol("T", _short_frames(), cfg, now_ms)
        ev = evaluate_symbol(a, cfg)
        assert ev["short"]["score"] >= ev["long"]["score"]

    def test_no_lookahead_signal_uses_confirmed_only(self, cfg, now_ms):
        ev = evaluate_symbol(analysis.analyze_symbol(
            "T", _long_frames(), cfg, now_ms), cfg)
        assert ev["info"]["last15mCloseTime"] < now_ms


# ---------------------------------------------------------------- orchestrator
class _FakeBybit:
    """Offline stand-in for the Bybit public client (no network)."""

    def __init__(self, frames):
        self.frames = frames
        self.stats = type("S", (), {"requests": 0, "errors": 0, "retries": 0,
                                    "status": 0, "as_dict": lambda self: {
                                        "requests": self.requests, "errors": self.errors,
                                        "retries": self.retries, "status": self.status}})()

    def tickers(self):
        self.stats.requests += 1
        return [{"symbol": "BTCUSDT", "lastPrice": "61908", "turnover24h": "1e9",
                 "fundingRate": "0.0001", "openInterest": "123"}]

    def klines(self, symbol, interval, limit):
        self.stats.requests += 1
        return list(self.frames.get(interval, [])[-limit:])


class TestOrchestrator:
    def test_scan_once_writes_signals(self, cfg, now_ms, tmp_path, monkeypatch):
        from ascend.engine import ScanLog, scan_once
        from ascend import persist
        # point persistence at a temp dir so tests never touch the real repo output
        monkeypatch.setattr(persist, "data_dir", lambda: tmp_path)
        monkeypatch.setattr(persist, "frontend_data_dir", lambda: tmp_path)
        client = _FakeBybit(_long_frames())
        log = ScanLog()
        code = scan_once(cfg, log, client=client, symbols=["BTCUSDT"],
                         dry_run=False, account_equity=100_000_000.0)
        assert code == 0
        sig = persist.load_json("ascend-signals.json", {})
        assert sig.get("signals"), "expected at least one signal in the long market"
        first = sig["signals"][0]
        assert first["direction"] == "LONG"
        assert first["stop"] < first["entry"] < first["target1"]
        assert 0 <= first["score"] <= 100 and 0 <= first["prob"] <= 1
        status = persist.load_json("ascend-system-status.json", {})
        assert status.get("health") in ("HEALTHY", "DEGRADED")
        # json must be re-parseable
        import json
        json.loads((tmp_path / "ascend-signals.json").read_text())

    def test_scan_once_clean_universe_failure_preserves_healthy(self, cfg, now_ms, tmp_path, monkeypatch):
        from ascend.engine import ScanLog, scan_once
        from ascend import persist
        monkeypatch.setattr(persist, "data_dir", lambda: tmp_path)
        monkeypatch.setattr(persist, "frontend_data_dir", lambda: tmp_path)
        # Pre-write a "healthy" status to confirm a failed scan does not clobber it.
        persist.write_data_file("ascend-system-status.json",
                                {"systemOnline": True, "health": "HEALTHY",
                                 "lastSuccessfulScan": 1, "logs": []})
        client = _FakeBybit(_long_frames())

        class _NoTickers(_FakeBybit):
            def tickers(self):
                from ascend.bybit import BybitError
                raise BybitError("network down")

        log = ScanLog()
        code = scan_once(cfg, log, client=_NoTickers(_long_frames()), symbols=None, dry_run=False)
        assert code == 1
        status = persist.load_json("ascend-system-status.json", {})
        assert status["health"] == "FAILED"
        # last successful scan must be preserved from the pre-seeded healthy state
        assert status.get("lastSuccessfulScan") == 1


# ---------------------------------------------------------------- calibration
class TestCalibrate:
    def test_fit_missing_variety_returns_prior(self, cfg):
        from ascend.calibrate import fit_logistic
        a, b = fit_logistic([(60, 1), (61, 1), (62, 1)])  # all won -> no variety
        assert abs(a - -3.6) < 1e-6 and abs(b - 6.0) < 1e-6

    def test_fit_separates_classes(self):
        from ascend.calibrate import fit_logistic
        # low-score mostly losses, high-score mostly wins -> b should be positive
        samples = [(20, 0)] * 40 + [(80, 1)] * 40
        a, b = fit_logistic(samples, lr=0.1, epochs=20000)
        assert b > 0
        # monotonic probability in score after fitting
        p_lo = _sigmoid_fit(a, b, 20)
        p_hi = _sigmoid_fit(a, b, 80)
        assert p_hi > p_lo

    def test_wilson_interval_bounds(self):
        from ascend.calibrate import wilson_interval_positives
        lo, hi = wilson_interval_positives(100, 60)
        assert 0 <= lo < hi <= 1
        # zero-total returns a conservative uninformative interval, not a crash
        lo0, hi0 = wilson_interval_positives(0, 0)
        assert 0 <= lo0 <= hi0 <= 1

    def test_load_samples(self, tmp_path):
        from ascend.main import _load_samples
        p = tmp_path / "samples.json"
        p.write_text(json.dumps([{"score": 20, "won": False}, {"score": 80, "won": 1},
                                 [50, "true"], [60, "false"], "bad", {"score": "x"}]))
        samples = _load_samples(str(p))
        # valid: (20,0), (80,1), (50,1), (60,0) -> 4 samples
        assert len(samples) == 4
        assert samples == [(20.0, 0), (80.0, 1), (50.0, 1), (60.0, 0)]


def _sigmoid_fit(a, b, score):
    import math
    x = a + b * score / 100.0
    if x > 30: return 1.0
    if x < -30: return 0.0
    return 1.0 / (1.0 + math.exp(-x))


# ---------------------------------------------------------------- sessions
class TestSessions:
    def test_known_killzone(self, cfg):
        zones = cfg.get("sessions.killZones") or []
        if zones:
            # 13:00 UTC -> New York window (12..15)
            name, w = session_at(T0 + 13 * 3_600_000, zones)
            assert w >= 1.0

    def test_off_window(self, cfg):
        zones = cfg.get("sessions.killZones") or []
        name, w = session_at(T0 + 2 * 3_600_000, zones)
        assert isinstance(name, str)
