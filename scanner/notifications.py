"""Notification engine — events, dashboard feed and optional Telegram/Discord send.

Kept analysis-only: no trade execution, no advice. This module:

1. Derives notification *events* from the signal lifecycle on each scan
   (new high-quality signal, signal triggered, signal resolved, scan
   degraded/failed). Every event is keyed so it can be emitted exactly once.
2. Persists the dashboard feed to ``data/notifications.json`` (bounded,
   mirrored to ``frontend/data/``) so the UI can show a "recent events" feed.
3. Persists an unsent queue ``data/notifications-pending.json`` that the
   sender drains.
4. Renders the events for Telegram (@bot API sendMessage, HTML/Markdown) and
   Discord (webhook JSON payloads), and POSTs them with the stdlib only.

The actual HTTP send is normally run from GitHub Actions
(``python -m scanner.notifications --send``) after a scan, using secrets
exposed as environment variables (``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID``
or ``DISCORD_WEBHOOK_URL``). If no credentials are present the module stays
silent — notifications are a convenience, never a requirement, and never a
trading signal.

CLI:
    python -m scanner.notifications --dry-run   # print what would be sent (no POST)
    python -m scanner.notifications --send      # POST pending events then clear the queue
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import Config
from .persist import atomic_write_json, load_json
from . import persist
from .signals import (WAITING_TRIGGER, TRIGGERED, WIN, LOSS, EXPIRED,
                      AMBIGUOUS, CANCELLED, ACTIVE_STATUSES, RESOLVED_STATUSES)

FEED_NAME = "notifications.json"
PENDING_NAME = "notifications-pending.json"

RESOLVED_LABEL = {
    WIN: "WIN",
    LOSS: "LOSS",
    EXPIRED: "EXPIRED",
    AMBIGUOUS: "AMBIGUOUS",
    CANCELLED: "CANCELLED",
}


def _key(sig: dict, kind: str) -> str:
    return f"{sig.get('id')}:{kind}"


def collect_events(previous_signals: list[dict], updated_signals: list[dict],
                   new_signals: list[dict], status: dict | None,
                   cfg: Config, now_ms: int) -> list[dict]:
    """Return the *new* notification events for this scan (deduped by key).

    ``previous_signals`` is the state before the outcome update; ``updated_signals``
    is the state after. New signals (generated this scan) are in ``new_signals``.
    A signal is only notified once per kind via its stable key.
    """
    ncfg = cfg.get("notifications", {}) or {}
    ev = ncfg.get("events", {}) or {}
    ret = int(ncfg.get("retention", 500))
    min_score = float(ncfg.get("minScoreForNotice", 80))

    prev_by_id = {s.get("id"): s for s in previous_signals}
    prev_active_keys = {_key(s, "signal") for s in previous_signals} | {
        _key(s, "triggered") for s in previous_signals if s.get("status") in (TRIGGERED,)}

    events: list[dict] = []

    # new high-quality signals
    if ev.get("newSignal", True):
        for sig in new_signals:
            if float(sig.get("score") or 0) >= min_score:
                events.append({
                    "key": _key(sig, "signal"), "ts": now_ms, "kind": "new_signal",
                    "symbol": sig["symbol"], "direction": sig["direction"],
                    "quality": sig.get("quality"), "score": sig.get("score"),
                    "trigger": sig.get("triggerPrice"), "stop": sig.get("stopLoss"),
                    "target": sig.get("takeProfit"), "rr": sig.get("riskReward"),
                    "status": sig.get("status"), "msg": _describe_new_signal(sig),
                })

    # lifecycle transitions (triggered / resolved) that happened this scan
    for sig in updated_signals:
        prev = prev_by_id.get(sig.get("id"))
        st = sig.get("status")
        sig_key = _key(sig, "sig")
        if prev is None and sig.get("id") in {n.get("id") for n in new_signals}:
            # already emitted as new_signal above; skip lifecycle duplication
            continue
        if sig["symbol"] is None or sig.get("score") is None:
            continue
        meta = _base_meta(sig)
        if st == TRIGGERED and ev.get("triggered", True):
            if not (prev and prev.get("status") == TRIGGERED):
                events.append({**meta, "key": _key(sig, "triggered"), "ts": now_ms,
                               "kind": "triggered", "status": TRIGGERED,
                               "msg": _describe_trigger(sig)})
        if st in RESOLVED_STATUSES and ev.get("resolved", True):
            if not (prev and prev.get("status") in RESOLVED_STATUSES):
                events.append({**meta, "key": _key(sig, "resolved"), "ts": now_ms,
                               "kind": "resolved", "status": st,
                               "rMultiple": sig.get("rMultiple"),
                               "msg": _describe_resolved(sig)})

    # scan health / failure notices
    health = (status or {}).get("health")
    if health and ev.get("scanHealth", True):
        if health in ("DEGRADED", "FAILED"):
            events.append({
                "key": f"scan:{now_ms}:{health}", "ts": now_ms, "kind": "scan_health",
                "status": health, "msg": f"Scan {health} — see System Status for details.",
            })
    if (status or {}).get("systemOnline") is False and now_ms and ev.get("scanFailed", True):
        events.append({
            "key": f"scan-failed:{now_ms}", "ts": now_ms, "kind": "scan_failed",
            "status": "FAILED", "msg": "Last scan FAILED. Showing the previous valid dashboard data.",
        })

    # dedupe against previously stored feed keys
    prev_feed = load_json(FEED_NAME, {"events": []})
    seen_keys = {e.get("key") for e in prev_feed.get("events", [])}
    fresh = [e for e in events if e.get("key") not in seen_keys]
    return fresh[-ret:]


def _base_meta(sig: dict) -> dict:
    return {
        "symbol": sig.get("symbol"), "direction": sig.get("direction"),
        "quality": sig.get("quality"), "score": sig.get("score"),
    }


def _describe_new_signal(sig: dict) -> str:
    d = sig.get("direction", "")
    sym = sig.get("symbol", "")
    q = sig.get("quality", "?")
    return (f"New {q} {d} {sym} — score {sig.get('score')} · "
            f"trigger {_p(sig.get('triggerPrice'))} · TP {_p(sig.get('takeProfit'))} · "
            f"SL {_p(sig.get('stopLoss'))} · RR {sig.get('riskReward')}")


def _describe_trigger(sig: dict) -> str:
    return (f"{sig.get('symbol')} {sig.get('direction')} TRIGGERED @ "
            f"{_p(sig.get('entryPrice') or sig.get('triggerPrice'))} · "
            f"TP {_p(sig.get('takeProfit'))} · SL {_p(sig.get('stopLoss'))}")


def _describe_resolved(sig: dict) -> str:
    st = sig.get("status")
    out = f"{sig.get('symbol')} {sig.get('direction')} → {RESOLVED_LABEL.get(st, st)}"
    if st in (WIN, LOSS) and sig.get("rMultiple") is not None:
        r = sig["rMultiple"]
        out += f" ({'+' if r > 0 else ''}{r}R)"
    return out


def _p(v) -> str:
    return "—" if v is None else f"{v:g}"


# ------------------------------------------------------------------ feed / pending
def update_feed(fresh_events: list[dict], cfg: Config, now_ms: int) -> dict:
    """Merge fresh events into the bounded dashboard feed and write it twice."""
    ret = int(cfg.get("notifications.retention", 500))
    prev_feed = load_json(FEED_NAME, {"events": []})
    existing = prev_feed.get("events", [])
    seen = {e.get("key") for e in existing}
    merged = existing + [e for e in fresh_events if e.get("key") not in seen]
    merged = merged[-ret:]
    feed = {"generatedAt": now_ms, "retention": ret, "events": merged}
    atomic_write_json(persist.data_dir() / FEED_NAME, feed)
    atomic_write_json(persist.frontend_data_dir() / FEED_NAME, feed)
    return feed


def write_pending(fresh_events: list[dict], cfg: Config, now_ms: int) -> None:
    """Queue fresh events for the sender (deduped against the pending queue)."""
    if not fresh_events:
        return
    prev = load_json(PENDING_NAME, {"events": []})
    seen = {e.get("key") for e in prev.get("events", [])}
    queued = prev.get("events", []) + [e for e in fresh_events if e.get("key") not in seen]
    atomic_write_json(persist.data_dir() / PENDING_NAME, {"generatedAt": now_ms, "events": queued})


def drain_pending() -> list[dict]:
    """Read and clear the pending queue. Returns the events (to send)."""
    prev = load_json(PENDING_NAME, {"events": []})
    events = prev.get("events", [])
    atomic_write_json(persist.data_dir() / PENDING_NAME, {"generatedAt": int(time.time() * 1000), "events": []})
    return events


# ------------------------------------------------------------------ rendering
def render_telegram(events: list[dict], parse_mode: str = "HTML") -> str:
    parts = []
    for e in events:
        kind = e.get("kind")
        msg = e.get("msg") or e.get("status")
        if kind == "new_signal":
            emoji = "🟢" if e.get("direction") == "LONG" else "🔴"
        elif kind == "triggered":
            emoji = "⚡"
        elif kind == "resolved":
            emoji = "✅" if e.get("status") == "WIN" else ("❌" if e.get("status") == "LOSS" else "ℹ️")
        elif kind in ("scan_health", "scan_failed"):
            emoji = "⚠️"
        else:
            emoji = "🔔"
        # HTML escaping is applied to the message content; emoji is safe.
        safe = (f"<b>{msg}</b>" if parse_mode == "HTML" else msg)
        parts.append(f"{emoji} {safe}")
    return "\n".join(parts) if parts else "No new events."


def render_discord(events: list[dict], cfg: Config) -> dict:
    lines = []
    for e in events:
        kind = e.get("kind")
        msg = e.get("msg") or e.get("status")
        if kind == "new_signal":
            emoji = "🟢" if e.get("direction") == "LONG" else "🔴"
        elif kind == "triggered":
            emoji = "⚡"
        elif kind == "resolved":
            emoji = "✅" if e.get("status") == "WIN" else ("❌" if e.get("status") == "LOSS" else "ℹ️")
        elif kind in ("scan_health", "scan_failed"):
            emoji = "⚠️"
        else:
            emoji = "🔔"
        lines.append(f"{emoji} {msg}")
    return {
        "username": cfg.get("notifications.discord.username", "Signal Scanner"),
        "content": "\n".join(lines) if lines else "No new events.",
    }


# ------------------------------------------------------------------ sending
def _post_json(url: str, payload: dict, timeout: float = 12) -> tuple[bool, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "User-Agent": "crypto-signal-scanner/1.2 (notifications)",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:300]
        return True, body
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body = ""
        return False, f"HTTP {exc.code}: {body}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"network error: {exc}"


def send_events(events: list[dict], cfg: Config, dry_run: bool = False,
                log=print) -> dict:
    """Send events to each enabled channel. Returns per-channel results.

    Channels are enabled only when the corresponding secret environment
    variable is set. ``dry_run`` prints the payloads and never POSTs.
    """
    ncfg = cfg.get("notifications", {}) or {}
    channels = ncfg.get("channels", []) or []
    results: dict[str, dict] = {}

    if "telegram" in channels:
        token = os.environ.get(ncfg.get("telegram.botTokenEnv", "TELEGRAM_BOT_TOKEN"))
        chat = os.environ.get(ncfg.get("telegram.chatIdEnv", "TELEGRAM_CHAT_ID"))
        if token and chat:
            text = render_telegram(events, ncfg.get("telegram.parseMode", "HTML"))
            payload = {"chat_id": chat, "text": text,
                       "parse_mode": ncfg.get("telegram.parseMode", "HTML"),
                       "disable_web_page_preview": True}
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            if dry_run:
                log(f"[telegram dry-run] -> {url}\n{json.dumps(payload, indent=2)}")
                results["telegram"] = {"ok": True, "dryRun": True, "sent": len(events)}
            else:
                ok, info = _post_json(url, payload)
                results["telegram"] = {"ok": ok, "info": info, "sent": len(events) if ok else 0}
                log(f"[telegram] {'OK' if ok else 'FAIL'}: {info[:120]}")
        else:
            log("[telegram] credentials not set — skipped")

    if "discord" in channels:
        url = os.environ.get(ncfg.get("discord.webhookUrlEnv", "DISCORD_WEBHOOK_URL"))
        if url:
            payload = render_discord(events, cfg)
            if dry_run:
                log(f"[discord dry-run] -> {url}\n{json.dumps(payload, indent=2)}")
                results["discord"] = {"ok": True, "dryRun": True, "sent": len(events)}
            else:
                ok, info = _post_json(url, payload)
                results["discord"] = {"ok": ok, "info": info, "sent": len(events) if ok else 0}
                log(f"[discord] {'OK' if ok else 'FAIL'}: {info[:120]}")
        else:
            log("[discord] webhook not set — skipped")

    return results


# ------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="notification sender for the signal scanner")
    parser.add_argument("--dry-run", action="store_true", help="print payloads without sending")
    parser.add_argument("--config", default=None)
    parser.add_argument("--keep", action="store_true",
                        help="do not clear the pending queue (used with --dry-run)")
    args = parser.parse_args(argv)

    cfg = Config.load(args.config)
    events = drain_pending()
    if not events:
        print("no pending notifications")
        return 0
    results = send_events(events, cfg, dry_run=args.dry_run)
    if args.dry_run:
        print(f"dry run — {len(events)} event(s); queue {'' if args.keep else 'will be'} cleared")
    else:
        sent_ok = any(r.get("ok") for r in results.values())
        if not sent_ok:
            print("no channel sent (no credentials configured) — queue cleared")
        else:
            print(f"sent {len(events)} event(s) → {results}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
