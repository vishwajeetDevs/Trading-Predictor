"""Public Delta REST client. GET only, and only the market-data paths this phase uses."""

from __future__ import annotations

import logging
import random
import time
from typing import Iterator
from urllib.parse import urljoin

import httpx

from app.data.normalizer import loads_decimal

log = logging.getLogger(__name__)

ALLOWED_PREFIXES = (
    "/v2/products",
    "/v2/history/candles",
    "/v2/tickers/",
    "/v2/l2orderbook/",
    "/v2/trades/",
)


class DeltaRestError(Exception):
    pass


class Throttle:
    def __init__(self, interval: float, sleep=time.sleep, clock=time.monotonic):
        self.interval = max(0.0, interval)
        self.sleep = sleep
        self.clock = clock
        self._next = 0.0

    def wait(self) -> None:
        now = self.clock()
        if now < self._next:
            self.sleep(self._next - now)
        self._next = self.clock() + self.interval


def backoff_seconds(attempt: int, rng: random.Random, *, base: float = 1.0, cap: float = 60.0) -> float:
    """Exponential delay with jitter. ``attempt`` starts at 1. The result is at least ``base``."""
    if attempt < 1:
        attempt = 1
    delay = min(cap, base * (2 ** (attempt - 1)))
    return delay + rng.uniform(0, delay * 0.2)


def retry_wait_seconds(attempt: int, status: int | None, headers: dict, rng: random.Random) -> float | None:
    """Return a wait before the next try, or None when the status must not be retried."""
    if status == 429:
        reset = None
        for key, value in headers.items():
            if key.lower() == "x-rate-limit-reset":
                reset = value
                break
        if reset not in (None, ""):
            return max(0.0, int(float(reset)) / 1000.0)
        return backoff_seconds(attempt, rng)
    if status is None or status >= 500:
        return backoff_seconds(attempt, rng)
    return None


class DeltaRestClient:
    def __init__(
        self,
        base_url: str,
        user_agent: str,
        timeout: float = 20.0,
        min_interval: float = 0.25,
        max_retries: int = 4,
        client: httpx.Client | None = None,
        sleep=time.sleep,
        rng: random.Random | None = None,
    ):
        self.base_url = base_url.rstrip("/") + "/"
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.throttle = Throttle(min_interval, sleep=sleep)
        self._client = client
        self._owned = client is None

    def __enter__(self) -> "DeltaRestClient":
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, headers=self._headers())
        return self

    def __exit__(self, *_) -> None:
        if self._owned and self._client is not None:
            self._client.close()
            self._client = None

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "User-Agent": self.user_agent}

    def _check_path(self, path: str) -> None:
        if not path.startswith(ALLOWED_PREFIXES) or any(part in path.lower() for part in ("/orders", "/positions", "/wallet", "/leverage")):
            raise DeltaRestError("refusing non-market-data path %s" % path)

    def get(self, path: str, params: dict | None = None):
        self._check_path(path)
        url = urljoin(self.base_url, path.lstrip("/"))
        last_error = "no response"
        for attempt in range(1, self.max_retries + 1):
            self.throttle.wait()
            status = None
            headers: dict = {}
            try:
                response = self._client.get(url, params=params, headers=self._headers())
                status = response.status_code
                headers = dict(response.headers)
                if status == 200:
                    return loads_decimal(response.content)
                last_error = "HTTP %s" % status
            except httpx.HTTPError as exc:
                last_error = str(exc)
            wait = retry_wait_seconds(attempt, status, headers, self.rng)
            if wait is None or attempt == self.max_retries:
                break
            log.warning("delta rest retry path=%s status=%s wait=%.2f", path, status, wait)
            self.sleep(wait)
        raise DeltaRestError("Delta REST failed for %s (%s)" % (path, last_error))

    def iter_products(self, *, page_size: int = 100, max_pages: int = 20) -> Iterator[dict]:
        after = None
        seen: set[str] = set()
        for _ in range(max_pages):
            params = {
                "contract_types": "perpetual_futures",
                "states": "live",
                "page_size": str(page_size),
            }
            if after:
                params["after"] = after
            body = self.get("/v2/products", params)
            if body.get("success") is not True:
                raise DeltaRestError("product catalogue was not successful")
            rows = body.get("result") or []
            if isinstance(rows, dict):
                rows = [rows]
            yield from rows
            cursor = (body.get("meta") or {}).get("after")
            if not cursor or cursor in seen or not rows:
                return
            seen.add(cursor)
            after = cursor
        raise DeltaRestError("product catalogue pagination did not finish")

    def fetch_candles(self, symbol: str, resolution: str, start: int, end: int):
        return self.get(
            "/v2/history/candles",
            {"resolution": resolution, "symbol": symbol, "start": str(start), "end": str(end)},
        )

    def fetch_ticker(self, symbol: str):
        body = self.get("/v2/tickers/%s" % symbol)
        if body.get("success") is not True or not isinstance(body.get("result"), dict):
            raise DeltaRestError("ticker response was not successful")
        return body["result"]

    def fetch_orderbook(self, symbol: str, depth: int):
        body = self.get("/v2/l2orderbook/%s" % symbol, {"depth": str(depth)})
        if body.get("success") is not True or not isinstance(body.get("result"), dict):
            raise DeltaRestError("order book response was not successful")
        return body["result"]

    def fetch_trades(self, symbol: str):
        body = self.get("/v2/trades/%s" % symbol)
        if body.get("success") is not True:
            raise DeltaRestError("trades response was not successful")
        return body.get("result")
