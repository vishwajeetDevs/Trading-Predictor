import random

import httpx
import pytest

from app.data.delta_rest import DeltaRestClient, DeltaRestError, Throttle, backoff_seconds, retry_wait_seconds


class Scripted:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params, headers))
        status, body, header = self.responses.pop(0)
        return httpx.Response(status, content=body, headers=header, request=httpx.Request("GET", url))


def test_throttle_spaces_requests():
    clock = {"now": 0.0}
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        clock["now"] += seconds

    throttle = Throttle(0.25, sleep=sleep, clock=lambda: clock["now"])
    throttle.wait()
    throttle.wait()
    assert slept == [0.25]


def test_retry_waits_follow_status_and_reset_header():
    rng = random.Random(1)
    assert retry_wait_seconds(1, 404, {}, rng) is None
    assert retry_wait_seconds(1, 429, {"X-RATE-LIMIT-RESET": "2500"}, rng) == 2.5
    assert retry_wait_seconds(1, 500, {}, random.Random(1)) >= 1
    delay = backoff_seconds(1, random.Random(1))
    assert 1 <= delay <= 1.2
    assert backoff_seconds(8, random.Random(1), cap=60) <= 72


def test_client_retries_429_then_returns_body():
    slept = []
    scripted = Scripted(
        [
            (429, b"{}", {"X-RATE-LIMIT-RESET": "5"}),
            (200, b'{"success": true, "result": []}', {}),
        ]
    )
    client = DeltaRestClient("https://api.india.delta.exchange", "test", min_interval=0, client=scripted, sleep=slept.append, rng=random.Random(1))
    body = client.get("/v2/products", {"states": "live"})
    assert body["success"] is True
    assert slept == [0.005]
    assert scripted.calls[0][2]["User-Agent"] == "test"


def test_client_does_not_retry_a_normal_error_and_refuses_trading_paths():
    scripted = Scripted([(404, b"{}", {})])
    client = DeltaRestClient("https://api.india.delta.exchange", "test", min_interval=0, max_retries=4, client=scripted, sleep=lambda _: None)
    with pytest.raises(DeltaRestError):
        client.get("/v2/products")
    assert len(scripted.calls) == 1
    with pytest.raises(DeltaRestError):
        client.get("/v2/orders")
    assert len(scripted.calls) == 1


def test_product_pages_follow_the_cursor_once():
    page_one = b'{"success": true, "result": [{"symbol": "A"}], "meta": {"after": "cursor-1"}}'
    page_two = b'{"success": true, "result": [{"symbol": "BTCUSD"}], "meta": {}}'
    scripted = Scripted([(200, page_one, {}), (200, page_two, {})])
    client = DeltaRestClient("https://api.india.delta.exchange", "test", min_interval=0, client=scripted, sleep=lambda _: None)
    rows = list(client.iter_products())
    assert [row["symbol"] for row in rows] == ["A", "BTCUSD"]
    assert scripted.calls[1][1]["after"] == "cursor-1"
    assert scripted.calls[0][1]["contract_types"] == "perpetual_futures"
    assert scripted.calls[0][1]["states"] == "live"
