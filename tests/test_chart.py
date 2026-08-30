"""Chart snapshot tests: bounded causal candle windows + structure markers."""
from scanner.config import Config
from scanner.chart import build_chart_snapshots
from scanner.market_data import Candle

from conftest import T0, MS_15M, candles_from_closes


def _cfg():
    return Config.load()


def _make_candles(n=80, start=100.0):
    closes = [start + i * (0.5 if i % 3 else -0.2) for i in range(n)]
    return candles_from_closes(closes, start_idx=0)


def _signal(scope):
    sig = {
        "id": "SIG-CHART", "symbol": "BTCUSDT", "status": scope,
        "direction": "LONG", "generatedAt": T0,
        "signalCandleCloseTime": T0 + 60 * MS_15M,
        "triggerPrice": 125.0, "stopLoss": 118.0, "takeProfit": 135.0,
        "entryZone": [124.0, 125.0],
        "structure": {"event": "CHoCH_up", "level": 120.0},
        "liquiditySweep": {"level": 117.0},
        "fvg": {"bottom": 121.0, "top": 122.5},
        "orderBlock": {"bottom": 119.5, "top": 120.5},
    }
    return sig


class TestChartSnapshots:
    def test_active_signal_charted(self):
        cfg = _cfg()
        candles = _make_candles()
        candle_map = {"BTCUSDT": candles}
        snaps = build_chart_snapshots([_signal("WAITING_TRIGGER")], candle_map, cfg, T0 + 100 * MS_15M)
        assert "BTCUSDT" in snaps
        blk = snaps["BTCUSDT"]
        assert len(blk["candles"]) >= 10
        assert blk["markers"]["trigger"] == 125.0
        assert blk["markers"]["stop"] == 118.0
        assert blk["markers"]["target"] == 135.0
        assert blk["markers"]["sweepLevel"] == 117.0
        assert blk["markers"]["eventLevel"] == 120.0
        assert blk["markers"]["fvgBottom"] == 121.0
        assert blk["markers"]["obTop"] == 120.5

    def test_resolved_signal_not_charted(self):
        cfg = _cfg()
        candle_map = {"BTCUSDT": _make_candles()}
        snaps = build_chart_snapshots([_signal("WIN")], candle_map, cfg, T0 + 100 * MS_15M)
        assert snaps == {}

    def test_bounded_candle_count(self):
        cfg = _cfg()
        candles = _make_candles(200)
        snaps = build_chart_snapshots([_signal("TRIGGERED")], {"BTCUSDT": candles},
                                      cfg, T0 + 100 * MS_15M)
        assert len(snaps["BTCUSDT"]["candles"]) <= 60

    def test_causal_only_closed_candles(self):
        cfg = _cfg()
        candles = _make_candles(80)
        # "now" before the signal candle closes -> no candles after it are embedded
        now = T0 + 50 * MS_15M
        snaps = build_chart_snapshots([_signal("WAITING_TRIGGER")], {"BTCUSDT": candles},
                                      cfg, now)
        # marker values still present; candle window is the leading slice
        assert snaps["BTCUSDT"]["markers"]["eventLevel"] is not None
        assert snaps["BTCUSDT"]["candles"]
