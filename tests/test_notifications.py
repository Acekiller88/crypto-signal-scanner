"""Notifications engine tests: event derivation, rendering, dry-run safety."""
from scanner.config import Config
from scanner.notifications import (collect_events, render_telegram,
                                   render_discord, send_events)

from conftest import make_signal, T0, MS_15M


def _cfg():
    return Config.load()


def _sig(id_, status, **kw):
    return make_signal(id=id_, status=status, **kw)


class TestCollectEvents:
    def test_new_signal_above_min_score(self):
        cfg = _cfg()
        new = [_sig("S-NEW", "WAITING_TRIGGER", symbol="BTCUSDT")]
        events = collect_events([], [], new, {"health": "HEALTHY"}, cfg, T0)
        kinds = {e["kind"] for e in events}
        assert "new_signal" in kinds
        assert any(e["symbol"] == "BTCUSDT" for e in events)

    def test_new_signal_below_min_score_not_notified(self):
        cfg = _cfg()
        new = [_sig("S-LOW", "WAITING_TRIGGER", symbol="BTCUSDT", score=70.0)]
        events = collect_events([], [], new, {"health": "HEALTHY"}, cfg, T0)
        assert not any(e["kind"] == "new_signal" for e in events)

    def test_triggered_event_on_transition(self):
        cfg = _cfg()
        prev = [_sig("S1", "WAITING_TRIGGER", symbol="ETHUSDT")]
        updated = [_sig("S1", "TRIGGERED", symbol="ETHUSDT", triggeredAt=T0,
                        entryPrice=110.0)]
        events = collect_events(prev, updated, [], {"health": "HEALTHY"}, cfg, T0)
        assert any(e["kind"] == "triggered" for e in events)

    def test_no_duplicate_triggered(self):
        cfg = _cfg()
        prev = [_sig("S1", "TRIGGERED", symbol="ETHUSDT", triggeredAt=T0)]
        updated = [_sig("S1", "TRIGGERED", symbol="ETHUSDT", triggeredAt=T0)]
        events = collect_events(prev, updated, [], {"health": "HEALTHY"}, cfg, T0)
        assert not any(e["kind"] == "triggered" for e in events)

    def test_resolved_event(self):
        cfg = _cfg()
        prev = [_sig("S1", "TRIGGERED", symbol="ETHUSDT", triggeredAt=T0)]
        updated = [_sig("S1", "WIN", symbol="ETHUSDT", triggeredAt=T0,
                        closedAt=T0 + 3 * MS_15M, rMultiple=3.0, entryPrice=110.0)]
        events = collect_events(prev, updated, [], {"health": "HEALTHY"}, cfg, T0)
        assert any(e["kind"] == "resolved" and e["status"] == "WIN" for e in events)

    def test_scan_health_degraded(self):
        cfg = _cfg()
        events = collect_events([], [], [], {"health": "DEGRADED"}, cfg, T0)
        assert any(e["kind"] == "scan_health" for e in events)

    def test_events_disabled(self):
        cfg = _cfg()
        cfg._data["notifications"]["events"]["newSignal"] = False
        new = [_sig("S-NEW", "WAITING_TRIGGER", symbol="BTCUSDT")]
        events = collect_events([], [], new, {"health": "HEALTHY"}, cfg, T0)
        assert not any(e["kind"] == "new_signal" for e in events)


class TestRendering:
    def test_telegram_message_contains_symbol(self):
        events = [{"kind": "new_signal", "symbol": "BTCUSDT", "direction": "LONG",
                   "msg": "New A+ LONG BTCUSDT"}]
        text = render_telegram(events, "HTML")
        assert "BTCUSDT" in text
        assert "<b>" in text

    def test_discord_payload_shape(self):
        cfg = _cfg()
        events = [{"kind": "new_signal", "symbol": "BTCUSDT", "direction": "LONG",
                   "msg": "New A+ LONG BTCUSDT"}]
        payload = render_discord(events, cfg)
        assert "content" in payload
        assert "BTCUSDT" in payload["content"]

    def test_empty(self):
        assert "No new events" in render_telegram([], "HTML")
        assert "No new events" in render_discord([], _cfg())["content"]


class TestSendDryRun:
    def test_dry_run_without_credentials_is_safe(self, capsys):
        cfg = _cfg()
        events = [{"kind": "new_signal", "symbol": "BTCUSDT", "direction": "LONG",
                   "msg": "New A+ LONG BTCUSDT"}]
        # dry_run never performs an HTTP POST regardless of credentials; without
        # any env vars no channel is activated at all.
        import os
        saved = {k: os.environ.get(k) for k in
                 ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL")}
        for k in saved:
            os.environ.pop(k, None)
        try:
            results = send_events(events, cfg, dry_run=True)
            assert isinstance(results, dict)
            assert results == {}  # no credentials -> nothing activated
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
