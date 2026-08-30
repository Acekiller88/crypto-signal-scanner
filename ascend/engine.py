"""ASCEND-30 scan orchestrator.

Full flow (one scan):
    Bybit tickers (top-30 universe + funding/OI)
      -> fetch 1D/4H/15M closed klines per symbol
      -> analyze_symbol (pure)
      -> evaluate_symbol (risk + scoring + probability)
      -> build screener + signals + status + performance
      -> persist to /data and mirror to /frontend/data

Failure policy: if market data cannot be retrieved, the scan is marked
degraded/failed, previous valid data is retained, and no fake values are ever
produced. Exit codes: 0 = ok/degraded, 1 = hard failure.
"""
from __future__ import annotations

import time

from .config import Config
from .bybit import BybitClient, BybitError
from .universe import build_universe, symbol_list
from .analysis import analyze_symbol
from .signal import evaluate_symbol, to_signal_row
from .scoring import calibrate_score, label_from_prob
from .sessions import session_at
from . import persist


class ScanLog:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def _add(self, level: str, msg: str) -> None:
        e = {"ts": int(time.time() * 1000), "level": level, "msg": msg}
        self.entries.append(e)
        print(f"[{level.upper()}] {msg}", flush=True)

    def info(self, m): self._add("info", m)
    def warn(self, m): self._add("warn", m)
    def error(self, m): self._add("error", m)

    def tail(self, n: int) -> list[dict]:
        return self.entries[-n:]


def make_client(cfg: Config) -> BybitClient:
    d = cfg.get("data")
    m = cfg.get("market")
    return BybitClient(base=m.get("base"), category=m.get("category"),
                       timeout=d.get("requestTimeoutSeconds", 12),
                       max_retries=d.get("maxRetries", 3),
                       backoff=d.get("retryBackoffSeconds", 1.5),
                       max_requests=d.get("maxRequestsPerScan", 300))


def collect_klines(client: BybitClient, symbols: list[str], cfg: Config, log: ScanLog):
    tfs = (cfg.get("timeframes.trend"), cfg.get("timeframes.confirm"), cfg.get("timeframes.entry"))
    limits = cfg.get("data.klineLimits", {})
    ok: dict[str, dict] = {}
    failed: list[str] = []
    for i, sym in enumerate(symbols):
        frames: dict[str, list] = {}
        try:
            for tf in tfs:
                frames[tf] = client.klines(sym, tf, limits.get(tf, 300))
        except BybitError as exc:
            failed.append(sym)
            log.warn(f"klines failed for {sym}: {exc}")
            continue
        ok[sym] = frames
        if (i + 1) % 10 == 0:
            log.info(f"klines collected {i + 1}/{len(symbols)}")
        time.sleep(0.05)
    return ok, failed


def scan_once(cfg: Config, log: ScanLog, client: BybitClient | None = None,
              symbols: list[str] | None = None, max_symbols: int | None = None,
              dry_run: bool = False, account_equity: float = 100_000_000.0,
              contract_value: float = 1.0) -> int:
    t0 = time.monotonic()
    now_ms = int(time.time() * 1000)
    client = client or make_client(cfg)

    prev_status = persist.load_json("ascend-system-status.json", {})
    prev_logs = list(prev_status.get("logs", []))[-int(cfg.get("retention.logEntries", 400)):]
    prev_signals = persist.load_json("ascend-signals.json", {"signals": []}).get("signals", [])

    log.info("ASCEND-30 scan start")

    tickers_map: dict = {}
    universe: dict = {}
    failure_reason: str | None = None
    if symbols:
        universe = {"symbols": [{"symbol": s, "turnover24hUsd": 0, "rank": i + 1}
                                for i, s in enumerate(symbols)],
                    "source": "manual"}
        scan_symbols = list(symbols)
    else:
        try:
            universe, tickers_map = build_universe(client, cfg)
            scan_symbols = symbol_list(universe)
            if max_symbols:
                scan_symbols = scan_symbols[:max_symbols]
        except BybitError as exc:
            log.error(f"universe build failed: {exc}")
            failure_reason = f"universe build failed: {exc}"
            scan_symbols = []

    # kill-zone session weight (context)
    kz = cfg.get("sessions.killZones") or []
    session_name, session_weight = (session_at(now_ms, kz) if kz else ("OFF", 1.0))
    log.info(f"session {session_name} weight {session_weight}")

    if failure_reason or not scan_symbols:
        status = _failed_status(log, prev_status, prev_logs, now_ms, t0,
                                failure_reason or "empty universe", client.stats.as_dict())
        if not dry_run:
            persist.write_data_file("ascend-system-status.json", status)
        return 1

    klines, failed_symbols = collect_klines(client, scan_symbols, cfg, log)
    analyses = []
    for sym, frames in klines.items():
        ticker = tickers_map.get(sym, {})
        a = analyze_symbol(sym, frames, cfg, now_ms, ticker)
        if a["ok"]:
            analyses.append(a)
        else:
            log.warn(f"{sym}: {a['reasons'][0]}")

    if not analyses:
        log.error("no valid symbol data -- marking scan FAILED, retaining previous data")
        status = _failed_status(log, prev_status, prev_logs, now_ms, t0,
                                "no valid market data", client.stats.as_dict())
        if not dry_run:
            persist.write_data_file("ascend-system-status.json", status)
        return 1

    # evaluate every symbol
    evals = {a["symbol"]: evaluate_symbol(a, cfg) for a in analyses}

    # build screener rows (all sub-scores per symbol, both directions)
    screener_rows = []
    for a in analyses:
        ev = evals[a["symbol"]]
        long_comp = ev["long"]["components"]
        short_comp = ev["short"]["components"]
        best = ev["decision"] or {"direction": ("LONG" if ev["long"]["score"] >= ev["short"]["score"] else "SHORT"),
                                  "score": max(ev["long"]["score"], ev["short"]["score"]),
                                  "label": label_from_prob(max(ev["long"]["prob"], ev["short"]["prob"]), cfg)}
        screener_rows.append({
            "symbol": a["symbol"],
            "rank": _rank_of(universe, a["symbol"]),
            "price": a.get("price"),
            "regime": a.get("regime"),
            "bias1d": a.get("bias1d", {}).get("bias"),
            "bias4h": a.get("bias4h", {}).get("bias"),
            "adx1d": round(a.get("adx1d"), 1) if a.get("adx1d") is not None else None,
            "atrPct": round(a.get("atrPercent15m"), 3) if a.get("atrPercent15m") is not None else None,
            "relVol": round(a.get("relVol15m"), 2) if a.get("relVol15m") is not None else None,
            "valuePosition": (a.get("profile") or {}).get("valuePosition"),
            "fundingRate": _fnum((a.get("ticker") or {}).get("fundingRate")),
            "longScore": round(ev["long"]["score"], 1),
            "shortScore": round(ev["short"]["score"], 1),
            "bestScore": best["score"],
            "bestLabel": best["label"],
            "trend": _round3(long_comp.get("trend")),
            "structure": _round3(long_comp.get("structure")),
            "liquidity": _round3(long_comp.get("liquidity")),
            "momentum": _round3(long_comp.get("momentum")),
            "volume": _round3(long_comp.get("volume")),
            "volatility": _round3(long_comp.get("volatility")),
            "riskScore": _round3(long_comp.get("risk")),
        })

    # signals above threshold
    signals = []
    for ev in evals.values():
        row = to_signal_row(ev, cfg)
        if row:
            signals.append(row)
    for s in signals:
        log.info(f"SIGNAL {s['symbol']} {s['direction']} {s['label']} "
                 f"score={s['score']} prob={s['prob']} entry={s['entry']} "
                 f"TP={s['target1']} SL={s['stop']} RR={s['rr']}")
    if not signals:
        log.info("no qualifying ASCEND setups this scan")

    # performance aggregate (setup stats; actual outcomes come from replay)
    performance = _performance(signals, prev_signals, now_ms)

    duration_ms = int((time.monotonic() - t0) * 1000)
    health = "HEALTHY"
    if failed_symbols or universe.get("source") != "bybit:linear" and not symbols:
        health = "DEGRADED"
    api_health = "HEALTHY" if client.stats.errors == 0 else (
        "DEGRADED" if client.stats.errors < len(scan_symbols) else "FAILED")

    status = {
        "systemOnline": True,
        "health": health,
        "engine": "ASCEND-30",
        "version": "1.0.0",
        "lastScan": {
            "executedAt": now_ms, "durationMs": duration_ms,
            "status": "OK" if health == "HEALTHY" else "DEGRADED",
            "universeSize": universe.get("counts", {}).get("selected", len(scan_symbols)),
            "universeSource": universe.get("source"),
            "symbolsScanned": len(scan_symbols),
            "symbolsValid": len(analyses),
            "dataFailures": len(failed_symbols),
            "signalsGenerated": len(signals),
            "session": session_name, "sessionWeight": session_weight,
            "apiStats": client.stats.as_dict(), "apiHealth": api_health,
        },
        "lastSuccessfulScan": now_ms,
        "logs": (prev_logs + log.entries)[-int(cfg.get("retention.logEntries", 400)):],
    }

    if dry_run:
        log.info("dry run -- no files written")
        return 0

    persist.write_data_file("ascend-signals.json", {"generatedAt": now_ms, "signals": signals})
    persist.write_data_file("ascend-screener.json", {"generatedAt": now_ms, "rows": screener_rows})
    persist.write_data_file("ascend-system-status.json", status)
    persist.write_data_file("ascend-performance.json", performance)
    log.info(f"ASCEND COMPLETE {len(scan_symbols)} symbols, {len(analyses)} valid, "
             f"{len(failed_symbols)} failures, {len(signals)} signals ({duration_ms} ms)")
    return 0


def _failed_status(log: ScanLog, prev_status: dict, prev_logs: list, now_ms: int,
                   t0, reason: str, stats: dict) -> dict:
    log.error("ASCEND scan FAILED -- retaining previous data")
    return {
        "systemOnline": False, "health": "FAILED", "engine": "ASCEND-30", "version": "1.0.0",
        "lastScan": {"executedAt": now_ms, "durationMs": int((time.monotonic() - t0) * 1000),
                     "status": "FAILED", "reason": reason, "apiStats": stats},
        "lastSuccessfulScan": prev_status.get("lastSuccessfulScan"),
        "logs": (prev_logs + log.entries)[-400:],
    }


def _performance(signals: list[dict], prev_signals: list[dict], now_ms: int) -> dict:
    def agg(items):
        scores = [s.get("score") for s in items if s.get("score") is not None]
        probs = [s.get("prob") for s in items if s.get("prob") is not None]
        n = len(items)
        return {
            "signals": n,
            "avgScore": round(sum(scores) / len(scores), 1) if scores else None,
            "avgProb": round(sum(probs) / len(probs), 4) if probs else None,
            "strongBuy": sum(1 for s in items if s.get("label") == "Strong Buy"),
            "buy": sum(1 for s in items if s.get("label") == "Buy"),
            "neutral": sum(1 for s in items if s.get("label") == "Neutral"),
            "sell": sum(1 for s in items if s.get("label") == "Sell"),
            "strongSell": sum(1 for s in items if s.get("label") == "Strong Sell"),
            "long": sum(1 for s in items if s.get("direction") == "LONG"),
            "short": sum(1 for s in items if s.get("direction") == "SHORT"),
        }
    # separate current-scan signals vs all tracked
    current = agg(signals)
    tracked = agg(prev_signals)
    return {"generatedAt": now_ms, "currentScan": current,
            "cumulative": tracked,
            "disclaimer": ("Setup-confluence statistics only. No outcome/win-rate is reported "
                           "until signals are resolved (see replay/backtest). The probability is "
                           "an initial calibrated estimate and must be re-fitted from resolved "
                           "history before being trusted at scale.")}


def _rank_of(universe: dict, symbol: str) -> int:
    for e in universe.get("symbols", []):
        if e["symbol"] == symbol:
            return e.get("rank", 0)
    return 0


def _fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _round3(v):
    return round(v, 3) if isinstance(v, (int, float)) else v
