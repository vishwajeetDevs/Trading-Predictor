import asyncio
import json
from datetime import timedelta

from app.core.timeutil import epoch_to_utc, from_epoch_seconds
from app.data.delta_websocket import StreamParser, SubscriptionBook, reconnect_delay
from app.services.ingestion_service import RuntimeStatus, run_public_socket
from tests.conftest import load_json, make_settings


def test_public_messages_parse_and_cst_is_the_candle_open():
    captured = load_json("ws_public_messages.json")
    candle = captured["candlestick_1m"]
    opened = from_epoch_seconds(1793747280)
    parser = StreamParser("BTCUSD")
    as_of = opened + timedelta(seconds=16)
    event = parser.parse(json.dumps(candle), as_of)
    assert event.kind == "candle"
    assert event.candles[0].open_time == opened
    assert event.candles[0].timestamp_unit == "us"
    assert event.candles[0].is_closed is False
    published, unit = epoch_to_utc(candle["ts"])
    assert unit == "us"
    assert published != event.candles[0].open_time
    assert published > event.candles[0].open_time

    update = parser.parse(json.dumps(captured["candlestick_1m_update"]), as_of)
    assert update.sequence == "update"
    assert update.candles[0].volume == event.candles[0].volume.__class__("85")

    heartbeat = parser.parse(json.dumps(captured["heartbeat"]), as_of)
    assert heartbeat.kind == "heartbeat"
    assert parser.parse(json.dumps(captured["system_status"]), as_of).detail["status"] == "live"
    mark = parser.parse(json.dumps(captured["mark_price"]), as_of)
    assert mark.kind == "mark_price"
    assert "MARK:BTCUSD" == mark.detail["symbol"]
    assert parser.parse(json.dumps(captured["ticker"]), as_of).kind == "ticker"
    marked = dict(candle)
    marked["sy"] = "MARK:BTCUSD"
    assert parser.parse(json.dumps(marked), as_of).kind == "malformed"


def test_subscription_is_sent_once_and_restored_on_a_new_connection():
    book = SubscriptionBook()
    first = book.message_for("BTCUSD")
    names = [item["name"] for item in first["payload"]["channels"]]
    assert names == ["candlestick_1m", "ticker", "mark_price", "funding_rate", "ob_updates", "system_status"]
    assert book.message_for("BTCUSD") is None
    restored = SubscriptionBook().message_for("BTCUSD")
    assert [item["name"] for item in restored["payload"]["channels"]] == names


def test_reconnect_delay_backs_off():
    import random

    first = reconnect_delay(1, random.Random(2))
    later = reconnect_delay(6, random.Random(2))
    capped = reconnect_delay(20, random.Random(2))
    assert 1 <= first <= 1.2
    assert later > first
    assert capped <= 72


def test_consumer_reconnects_after_a_dropped_socket(monkeypatch):
    calls = {"n": 0}

    async def consume(settings, factory, stop, status):
        calls["n"] += 1
        status.ws_connected = True
        if calls["n"] == 1:
            raise ConnectionError("socket dropped")
        stop.set()

    async def no_wait(seconds, stop):
        return None

    monkeypatch.setattr("app.services.ingestion_service._consume", consume)
    monkeypatch.setattr("app.services.ingestion_service._record", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.ingestion_service._wait", no_wait)
    status = RuntimeStatus()
    asyncio.run(run_public_socket(make_settings(), lambda: None, asyncio.Event(), status))
    assert calls["n"] == 2
    assert status.last_error == "socket dropped"
    assert status.ws_connected is False


def test_public_socket_does_not_arm_the_library_ping():
    from app.data.delta_websocket import public_socket_options

    assert public_socket_options() == {"ping_interval": None, "ping_timeout": None}


def test_heartbeat_deadline_shrinks_and_a_pong_is_recognized():
    from app.data.delta_websocket import seconds_until_heartbeat_deadline

    opened = from_epoch_seconds(1_700_000_000)
    assert seconds_until_heartbeat_deadline(opened, opened + timedelta(seconds=5), 35) == 30
    assert seconds_until_heartbeat_deadline(opened, opened + timedelta(seconds=35), 35) == 0
    parser = StreamParser("BTCUSD")
    assert parser.parse('{"type": "pong"}', opened).kind == "pong"
    assert parser.parse('{"type": "ping"}', opened).kind == "ping"


def test_candle_traffic_does_not_extend_the_heartbeat_deadline(monkeypatch):
    from app.services.ingestion_service import _consume

    sent = []

    class FakeSocket:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def send(self, raw):
            sent.append(json.loads(raw))

        async def recv(self):
            self.calls += 1
            if self.calls == 1:
                return json.dumps(load_json("ws_public_messages.json")["candlestick_1m"])
            await asyncio.sleep(5)
            return "{}"

    def connect(*args, **kwargs):
        assert kwargs["ping_interval"] is None
        assert kwargs["ping_timeout"] is None
        return FakeSocket()

    monkeypatch.setattr("app.services.ingestion_service.websockets.connect", connect)
    monkeypatch.setattr("app.services.ingestion_service._load_symbol", lambda *args: "BTCUSD")
    monkeypatch.setattr("app.services.ingestion_service._run_recovery", lambda *args: None)
    monkeypatch.setattr("app.services.ingestion_service._store_candles", lambda *args: None)
    monkeypatch.setattr("app.services.ingestion_service._record", lambda *args: None)
    settings = make_settings(ws_heartbeat_timeout_seconds=0.2)

    async def run():
        await _consume(settings, lambda: None, asyncio.Event(), RuntimeStatus())

    try:
        asyncio.run(run())
    except ConnectionError as exc:
        assert "heartbeat" in str(exc)
    else:
        raise AssertionError("the socket stayed up without a heartbeat")
    assert sent[0]["type"] == "enable_heartbeat"
    assert sent[1]["type"] == "subscribe"
