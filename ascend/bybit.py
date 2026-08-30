"""Bybit v5 public market-data client (stdlib only, no API key).

Public market data ONLY. Used by ASCEND-30 for USDT perpetual linear futures.
* Retrieves the full ticker list (top-N volume universe, funding, OI) in one call.
* Retrieves OHLCV klines per interval (15/60/240/D).
* Drops the in-progress candle so signals are always computed on *closed*
  candles (first line of defence against repainting).
* Retries with exponential backoff on network/5xx/429; never fabricates data.

Endpoint reference (Bybit v5):
    GET /v5/market/tickers?category=linear    -> result.list[] (ticker + funding + OI)
    GET /v5/market/kline?category=linear&symbol=&interval=&limit=  -> result.list[]
    kline row = [start, open, high, low, close, volume, turnover]
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

INTERVAL_MS = {"1": 60_000, "3": 180_000, "5": 300_000, "15": 900_000,
               "30": 1_800_000, "60": 3_600_000, "120": 7_200_000,
               "240": 14_400_000, "360": 21_600_000, "720": 43_200_000,
               "D": 86_400_000, "M": 2_592_000_000, "W": 604_800_000}

# Bybit returns /v5/market/tickers -> result.list with these keys
TICKER_KEYS = ("symbol", "lastPrice", "turnover24h", "volume24h",
               "openInterest", "fundingRate", "nextFundingTime",
               "highPrice24h", "lowPrice24h", "price24hPcnt", "markPrice")


class BybitError(Exception):
    """Unrecoverable Bybit market-data failure after retries (or a 4xx)."""


class BybitClient:
    """Small resilient GET client for Bybit public market data."""

    def __init__(self, base: str = "https://api.bybit.com", category: str = "linear",
                 timeout: float = 12.0, max_retries: int = 3,
                 backoff: float = 1.5, max_requests: int = 300):
        self.base = base.rstrip("/")
        self.category = category
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.max_requests = max_requests
        self.stats = Stats()
        self._last_error: str | None = None

    # ------------------------------------------------------------------ core
    def _get(self, path: str, params: dict | None = None) -> dict:
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        url = self.base + path + query
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if self.stats.requests >= self.max_requests:
                raise BybitError("request budget exhausted for this scan")
            self.stats.requests += 1
            try:
                payload = self._fetch(url)
                if not isinstance(payload, dict):
                    raise BybitError("unexpected response shape")
                if payload.get("retCode") not in (0, "0", None):
                    self.stats.errors += 1
                    raise BybitError(f"bybit api error {payload.get('retCode')}: "
                                     f"{payload.get('retMsg')}")
                return payload
            except BybitError as exc:
                last_exc = exc
                self.stats.errors += 1
                self._last_error = str(exc)
                if attempt < self.max_retries:
                    self.stats.retries += 1
                    time.sleep(self.backoff * (attempt + 1))
        raise BybitError(f"all attempts failed: {last_exc}")

    def _fetch(self, url: str) -> dict:
        request = urllib.request.Request(url, headers={
            "User-Agent": "ascend-30/1.0 (public market data only)",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            self.stats.errors += 1
            raise BybitError(f"HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.stats.errors += 1
            raise BybitError(f"network error: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            self.stats.errors += 1
            raise BybitError(f"malformed JSON: {exc}") from exc

    # ----------------------------------------------------------- api surface
    def server_time_ms(self) -> int:
        data = self._get("/v5/market/time")
        try:
            return int(data["result"]["timeSecond"]) * 1000
        except (KeyError, TypeError, ValueError):
            return int(time.time() * 1000)

    def tickers(self) -> list[dict]:
        """Return all linear tickers (symbol, lastPrice, turnover24h, funding, OI)."""
        data = self._get("/v5/market/tickers", {"category": self.category})
        rows = (data.get("result") or {}).get("list") or []
        out: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rec: dict = {"symbol": row.get("symbol")}
            for k in TICKER_KEYS:
                if k in row and row.get(k) not in (None, ""):
                    rec[k] = row[k]
            out.append(rec)
        return out

    def klines(self, symbol: str, interval: str, limit: int = 200) -> list[Candle]:
        """Return closed candles for the interval, oldest first."""
        if interval not in INTERVAL_MS:
            raise ValueError(f"unsupported interval {interval}")
        params = {"category": self.category, "symbol": symbol,
                  "interval": interval, "limit": min(int(limit), 1000)}
        data = self._get("/v5/market/kline", params)
        rows = (data.get("result") or {}).get("list") or []
        now_ms = int(time.time() * 1000)
        candles: list[Candle] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            try:
                start = int(row[0])
                o = _f(row[1]); h = _f(row[2]); l = _f(row[3]); c = _f(row[4])
                v = _f(row[5]) if len(row) > 5 and row[5] else 0.0
            except (ValueError, TypeError, IndexError):
                continue
            if not (l <= h and l > 0 and start > 0):
                continue
            close_time = start + INTERVAL_MS[interval] - 1
            if close_time >= now_ms:
                continue  # in-progress candle -> never used (no repaint)
            candles.append(Candle(start, o, h, l, c, v, close_time))
        candles.sort(key=lambda k: k.openTime)
        return candles

    def endpoint_label(self) -> str:
        return f"bybit:{self.category}"


@dataclass
class Candle:
    openTime: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    closeTime: int

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low if self.high >= self.low else 0.0

    @property
    def clv(self) -> float:
        """Close-Location Value in [0,1]: where price closed within the range."""
        r = self.range
        if r <= 0:
            return 0.5
        return max(0.0, min(1.0, (self.close - self.low) / r))


@dataclass
class Stats:
    """Request/error telemetry for the scan status."""
    requests: int = 0
    errors: int = 0
    retries: int = 0
    status: int = 0

    def as_dict(self) -> dict:
        return {"requests": self.requests, "errors": self.errors,
                "retries": self.retries, "status": self.status}


def _f(value) -> float:
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        raise ValueError("non-finite price")
    return out
