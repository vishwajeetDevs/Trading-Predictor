"""Optional public connectivity check. Not part of the default offline run."""

import asyncio
import json
import os
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE_SMOKE") != "1", reason="set RUN_LIVE_SMOKE=1 to hit Delta")


def test_live_public_rest_and_websocket():
    import websockets

    from app.data.delta_rest import DeltaRestClient
    from app.data.delta_websocket import SubscriptionBook
    from app.data.normalizer import parse_rest_candles
    from app.data.product_resolver import select_btc_perpetual
    from tests.conftest import make_settings

    settings = make_settings(
        delta_rest_base="https://api.india.delta.exchange",
        delta_ws_url="wss://public-socket.india.delta.exchange",
        request_min_interval_seconds=0.3,
        http_timeout_seconds=20,
        max_retries=2,
        user_agent="btc-market-intelligence/0.1 (public market-data smoke)",
    )
    with DeltaRestClient(
        settings.delta_rest_base,
        settings.user_agent,
        timeout=settings.http_timeout_seconds,
        min_interval=settings.request_min_interval_seconds,
        max_retries=settings.max_retries,
    ) as client:
        product = select_btc_perpetual(client.iter_products())
        assert product["symbol"] == "BTCUSD"
        assert product["contract_type"] == "perpetual_futures"
        assert product["state"] == "live"
        end = int(time.time())
        payload = client.fetch_candles(product["symbol"], "1m", end - 900, end)
        drafts, errors = parse_rest_candles(
            payload,
            symbol=product["symbol"],
            timeframe="1m",
            source="delta_rest",
            as_of=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        assert drafts
        assert errors == []

    async def listen():
        from datetime import datetime, timezone

        from app.data.delta_websocket import StreamParser

        parser = StreamParser(product["symbol"])
        async with websockets.connect(
            settings.delta_ws_url,
            open_timeout=20,
            additional_headers={"User-Agent": settings.user_agent},
        ) as socket:
            await socket.send(json.dumps({"type": "enable_heartbeat"}))
            await socket.send(json.dumps(SubscriptionBook().message_for(product["symbol"])))
            seen = set()
            normalized = []
            deadline = time.time() + 25
            while time.time() < deadline and not normalized:
                raw = await asyncio.wait_for(socket.recv(), timeout=20)
                message = json.loads(raw)
                kind = str(message.get("type"))
                seen.add(kind)
                if kind == "candlestick_1m":
                    event = parser.parse(raw, datetime.now(timezone.utc))
                    if event.kind == "candle" and event.candles:
                        normalized.append(event.candles[0])
            return seen, normalized

    seen, normalized = asyncio.run(listen())
    assert "candlestick_1m" in seen
    assert normalized[0].symbol == "BTCUSD"
    assert normalized[0].timeframe == "1m"
    assert normalized[0].timestamp_unit == "us"
