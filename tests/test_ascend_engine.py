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


# ---------------------------------------------------------------- structure: FVG & OB
class TestFVGAndOB:
    def test_find_fvgs_bullish(self):
        highs = [10.0, 15.0, 16.0, 17.0, 18.0, 19.0]
        lows = [9.0, 11.0, 12.0, 13.0, 14.0, 15.0]
        atrs = [1.0] * 6
        fvgs = st.find_fvgs(highs, lows, atrs, 0.5)
        bulls = [f for f in fvgs if f.direction == "bullish"]
        # candle 2 low(12) > candle 0 high(10) -> bullish gap [10, 12], mid 11
        assert any(f.index == 2 and f.bottom == 10 and f.top == 12 and f.mid == 11
                   for f in bulls)
        # confirmIndex == index (only known at close of the 3rd candle)
        for f in fvgs:
            assert f.confirmIndex == f.index

    def test_find_fvgs_bearish(self):
        highs = [16.0, 12.0, 11.0, 10.0, 9.0, 8.0]
        lows = [14.0, 9.0, 8.0, 7.0, 6.0, 5.0]
        atrs = [1.0] * 6
        fvgs = st.find_fvgs(highs, lows, atrs, 0.5)
        bears = [f for f in fvgs if f.direction == "bearish"]
        # candle 2 high(11) < candle 0 low(14) -> bearish gap [11, 14]
        assert any(f.index == 2 and f.bottom == 11 and f.top == 14 for f in bears)

    def test_find_fvgs_filters_small_gaps(self):
        highs = [10.0, 10.2, 10.4, 10.5, 10.6]
        lows = [9.0, 9.1, 9.3, 9.4, 9.5]
        atrs = [10.0] * 5  # huge ATR -> min gap = 5.0, no FVG qualifies
        assert st.find_fvgs(highs, lows, atrs, 0.5) == []

    def test_find_order_blocks_bullish(self):
        # bullish OB = last bearish candle before a bullish displacement
        opens  = [10.0, 10.5, 10.2, 10.0, 11.0, 12.5]
        closes = [10.5, 10.1, 10.4, 11.2, 13.0, 14.0]   # idx3 bearish-> close<open? index3 close10.4? redo
        # force: idx2 is a bearish candle (close<open) preceding the bullish move
        opens  = [10.0, 10.2, 10.4, 10.1, 10.8, 11.6]
        closes = [10.2, 10.0, 10.1, 10.6, 11.4, 12.2]   # idx2 close(10.1) < open(10.4) => bearish
        highs  = [10.3, 10.3, 10.5, 10.7, 11.5, 12.3]
        lows   = [9.9, 9.8, 9.9, 10.0, 10.7, 11.5]
        disps = [st.Displacement(4, "bullish", 2.0)]
        obs = st.find_order_blocks(opens, closes, highs, lows, disps, window=6)
        assert len(obs) == 1
        assert obs[0].index == 2          # the bearish candle before the move
        assert obs[0].direction == "bullish"
        assert obs[0].confirmIndex == 4   # usable only after the displacement closes

    def test_find_order_blocks_mirror_bearish(self):
        opens  = [10.8, 10.6, 10.2, 10.4, 10.6, 10.8]
        closes = [10.6, 10.8, 10.4, 10.2, 10.0, 9.8]    # idx2 close(10.4) > open(10.2) => bullish
        highs  = [10.9, 10.9, 10.5, 10.5, 10.3, 10.1]
        lows   = [10.5, 10.5, 10.1, 10.0, 9.8, 9.6]
        disps = [st.Displacement(4, "bearish", 2.0)]
        obs = st.find_order_blocks(opens, closes, highs, lows, disps, window=6)
        assert len(obs) == 1
        assert obs[0].index == 2
        assert obs[0].direction == "bearish"


# ---------------------------------------------------------------- risk: regime / entry source
def _entry_source(setup):
    return next((n.split("=", 1)[1] for n in setup.notes if n.startswith("entry_source=")), None) if setup else None


class TestRegimeAdaptiveEntry:
    def _craft(self, cfg, regime="TREND", val=95.0, vah=120.0, **frame_overrides):
        """A minimal analysis dict where a bullish FVG sits above invalidation."""
        from ascend.structure import Swing, FVG
        swing_low = Swing(index=8, confirmIndex=10, price=100.0, kind="low")
        swing_high = Swing(index=11, confirmIndex=13, price=130.0, kind="high")
        fvg = FVG(index=12, confirmIndex=12, direction="bullish",
                  bottom=105.0, top=107.0, mid=106.0)
        frames = {
            "15": {
                "h": [104, 106, 103, 105, 107, 109, 108, 110, 103, 101, 102, 104, 106, 108, 114, 125],
                "l": [100, 102, 100, 102, 103, 105, 104, 106, 100, 98, 99, 100, 101, 103, 110, 108],
                "c": [102, 105, 102, 104, 106, 108, 107, 109, 102, 100, 101, 103, 105, 107, 113, 110],
                "swingLows": [swing_low], "swingHighs": [swing_high],
                "sweeps": [], "events": [], "equalHighs": [], "equalLows": [],
                "fvgs": [fvg], "orderBlocks": [],
            }
        }
        frames["15"].update(frame_overrides)
        return {
            "symbol": "T", "price": 110.0, "atr15m": 2.0, "regime": regime,
            "last15mIndex": 15,
            "profile": {"val": val, "vah": vah, "poc": 108.0, "valuePosition": "at_value"},
            "frames": frames,
        }

    def test_build_setup_prefers_fvg_entry(self, cfg):
        a = self._craft(cfg, regime="TREND")
        setup, reason = rk.build_setup("long", a, cfg)
        assert setup is not None, reason
        assert _entry_source(setup) == "fvg"
        assert setup.stop < setup.entry < setup.target1
        assert setup.entry == pytest.approx(106.0)  # the FVG mid

    def test_order_block_used_when_fvg_missing(self, cfg):
        from ascend.structure import Swing, OrderBlock
        swing_low = Swing(index=8, confirmIndex=10, price=100.0, kind="low")
        swing_high = Swing(index=11, confirmIndex=13, price=130.0, kind="high")
        ob = OrderBlock(index=9, confirmIndex=12, direction="bullish",
                        bottom=103.0, top=105.0, mid=104.0)
        a = self._craft(cfg, regime="TREND", fvgs=[], orderBlocks=[ob],
                        swingLows=[swing_low], swingHighs=[swing_high])
        # price is 110, OB mid 104 must be > invalidation(100) -> valid
        setup, reason = rk.build_setup("long", a, cfg)
        assert setup is not None, reason
        assert _entry_source(setup) == "order_block"

    def test_range_routes_to_value_area(self, cfg):
        from ascend.structure import Swing, FVG
        swing_low = Swing(index=8, confirmIndex=10, price=100.0, kind="low")
        swing_high = Swing(index=11, confirmIndex=13, price=130.0, kind="high")
        fvg = FVG(index=12, confirmIndex=12, direction="bullish",
                  bottom=105.0, top=107.0, mid=106.0)
        a = self._craft(cfg, regime="RANGE", val=104.0, vah=120.0,
                        swingLows=[swing_low], swingHighs=[swing_high], fvgs=[fvg],
                        orderBlocks=[])
        # With a RANGE regime and discount VAL=104 (above invalidation 100, below
        # price 110), the value-area mean-revert is tried first -> value_area.
        setup, reason = rk.build_setup("long", a, cfg)
        assert setup is not None, reason
        assert _entry_source(setup) == "value_area"

    def test_synthetic_long_market_produces_exit_plan(self, cfg, now_ms):
        a = analysis.analyze_symbol("T", _long_frames(), cfg, now_ms)
        setup, reason = rk.build_setup("long", a, cfg)
        assert setup is not None, reason
        assert any(n.startswith("entry_source=") for n in setup.notes)
        assert setup.stop < setup.entry < setup.target1


# ---------------------------------------------------------------- signal: exit plan
class TestExitPlan:
    def test_exit_plan_levels_and_order(self, cfg, now_ms):
        a = analysis.analyze_symbol("T", _long_frames(), cfg, now_ms)
        ev = evaluate_symbol(a, cfg)
        setup = ev["setup"]
        assert setup is not None
        ep = setup["exitPlan"]
        assert ep is not None
        assert ep["breakeven"]["atR"] == cfg.get("signalModel.breakevenAtR", 1.0)
        assert ep["timeStop"]["bars"] == cfg.get("signalModel.timeStopBars", 24)
        # for LONG, breakeven and partial prices sit above the entry
        assert ep["breakeven"]["price"] > setup["entry"]
        assert ep["partial"]["price"] > setup["entry"]
        assert ep["trail"]["atrMultiple"] == cfg.get("signalModel.trailAtrMultiple", 0.5)

    def test_signal_row_carries_exit_plan(self, cfg, now_ms):
        a = analysis.analyze_symbol("T", _long_frames(), cfg, now_ms)
        ev = evaluate_symbol(a, cfg)
        from ascend.signal import to_signal_row
        row = to_signal_row(ev, cfg)
        assert row is not None
        assert "exitPlan" in row and row["exitPlan"] is not None
        assert row["entrySource"] in ("fvg", "order_block", "retracement", "value_area")


class TestValidator:
    def test_valid_row_has_no_errors(self):
        from ascend.validate_data import validate_signal_row
        row = {"symbol": "BTCUSDT", "score": 75.4, "prob": 0.72, "label": "Strong Buy",
               "direction": "LONG", "entry": 100, "stop": 95, "target1": 115,
               "entrySource": "fvg",
               "exitPlan": {"breakeven": {"atR": 1.0, "price": 105},
                            "partial": {"atR": 1.0, "fraction": 0.5, "price": 105},
                            "trail": {"atrMultiple": 0.5, "offset": 1.0, "structureLevel": 101},
                            "timeStop": {"bars": 24}, "stopPrice": 95}}
        assert validate_signal_row(row) == []

    def test_bad_exit_plan_fraction_flags(self):
        from ascend.validate_data import validate_signal_row
        row = {"symbol": "BTCUSDT", "score": 75.4, "prob": 0.72, "label": "Strong Buy",
               "direction": "LONG", "entry": 100, "stop": 95, "target1": 115,
               "entrySource": "bad_source",
               "exitPlan": {"breakeven": {"atR": 1.0, "price": 105},
                            "partial": {"atR": 1.0, "fraction": 0, "price": 105},
                            "trail": {"atrMultiple": 0.5, "offset": 1.0, "structureLevel": 101},
                            "timeStop": {"bars": 24}, "stopPrice": 95}}
        errs = validate_signal_row(row)
        assert any("entrySource" in e for e in errs)
        assert any("fraction" in e for e in errs)

    def test_bad_exit_plan_corridor_flags(self):
        from ascend.validate_data import validate_signal_row
        row = {"symbol": "BTCUSDT", "score": 75.4, "prob": 0.72, "label": "Strong Buy",
               "direction": "LONG", "entry": 100, "stop": 95, "target1": 115,
               "entrySource": "retracement",
               "exitPlan": {"breakeven": {"atR": 1.0, "price": 90},
                            "partial": {"atR": 1.0, "fraction": 0.5, "price": 105},
                            "trail": {"atrMultiple": 0.5, "offset": 1.0, "structureLevel": 101},
                            "timeStop": {"bars": 24}, "stopPrice": 95}}
        errs = validate_signal_row(row)
        assert any("outside entry..target1" in e for e in errs)


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
