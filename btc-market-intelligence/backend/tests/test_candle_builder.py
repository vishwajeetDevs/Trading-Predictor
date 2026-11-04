from datetime import timedelta
from decimal import Decimal

from app.core.timeutil import from_epoch_seconds
from app.data.candle_builder import FormingBook, aggregate, api_end_inclusive, iter_chunks, missing_opens
from app.data.normalizer import draft_from_ohlc
from app.data.reconciliation import differences


def _bar(epoch: int, *, close: str = "10", volume: str = "1", closed_at_offset: int = 120):
    opened = from_epoch_seconds(epoch)
    return draft_from_ohlc(
        symbol="BTCUSD",
        timeframe="1m",
        open_time=opened,
        timestamp_unit="s",
        exchange_time_raw=str(epoch),
        open_="10",
        high="20",
        low="9",
        close=close,
        volume=volume,
        source="delta_rest",
        as_of=opened + timedelta(seconds=closed_at_offset),
    )


def test_chunks_stay_under_the_page_cap_and_end_is_inclusive_on_the_wire():
    start = 1_700_000_000
    end = start + 60 * 25
    windows = iter_chunks(start, end, 60, 10)
    assert windows[0] == (start, start + 600)
    assert windows[-1][1] == end
    assert all((stop - begin) <= 600 for begin, stop in windows)
    assert api_end_inclusive(windows[0][1]) == windows[0][1] - 1


def test_missing_minutes_are_reported_and_not_synthesised():
    start = 1_700_000_000
    present = [start, start + 60, start + 180]
    missing = missing_opens([from_epoch_seconds(item) for item in present], 60)
    assert [int(item.timestamp()) for item in missing] == [start + 120]
    assert missing_opens([from_epoch_seconds(start), from_epoch_seconds(start)], 60) == []


def test_aggregation_requires_every_minute():
    start = 1_699_999_800
    assert start % 300 == 0
    full = [_bar(start + 60 * index, close=str(10 + index), volume=str(index + 1)) for index in range(5)]
    built, skipped = aggregate(full, "5m")
    assert skipped == []
    assert len(built) == 1
    assert built[0].open == Decimal("10")
    assert built[0].close == Decimal("14")
    assert built[0].high == Decimal("20")
    assert built[0].low == Decimal("9")
    assert built[0].volume == Decimal("15")
    assert built[0].source == "aggregated_1m"
    assert int(built[0].open_time.timestamp()) == start
    partial, skipped_partial = aggregate(full[:4], "5m")
    assert partial == []
    assert skipped_partial[0]["present"] == 4
    assert skipped_partial[0]["expected"] == 5
    assert "not emitted" in skipped_partial[0]["reason"]


def test_duplicate_minute_does_not_inflate_aggregate_volume():
    start = 1_699_999_800
    bars = [_bar(start + 60 * index, volume="1") for index in range(5)]
    bars.append(_bar(start, volume="4", closed_at_offset=180))
    built, skipped = aggregate(bars, "5m")
    assert skipped == []
    assert built[0].volume == Decimal("8")


def test_reconciliation_reports_exact_field_differences():
    start = 1_699_999_800
    native = _bar(start)
    other = aggregate([_bar(start + 60 * index, close="11") for index in range(5)], "5m")[0][0]
    # The 5m bar is a different slot shape; compare two 5m drafts by rebuilding one field.
    derived = aggregate([_bar(start + 60 * index) for index in range(5)], "5m")[0][0]
    changed = derived.__class__(**{**derived.__dict__, "close": Decimal("99")})
    assert differences(derived, derived) is None
    report = differences(derived, changed, tick_size=Decimal("0.5"))
    assert report["kept"] == "exchange"
    assert report["fields"]["close"]["exceeds_one_tick"] is True
    assert "exchange candle" in report["reason"].lower()
    assert native.open == Decimal("10")
    assert other.close == Decimal("11")


def test_forming_book_closes_the_previous_minute_and_flags_gaps():
    start = 1_699_999_800
    book = FormingBook("1m")
    first = _bar(start, closed_at_offset=10)
    stored, label = book.apply(first)
    assert label == "ok" and stored[0].is_closed is False
    updated = _bar(start, close="11", volume="2", closed_at_offset=20)
    stored, label = book.apply(updated)
    assert label == "update" and stored[0].close == Decimal("11")
    jumped = _bar(start + 180, closed_at_offset=10)
    stored, label = book.apply(jumped)
    assert label == "gap"
    assert stored[0].is_closed is True
    assert int(stored[0].open_time.timestamp()) == start
    assert int(stored[1].open_time.timestamp()) == start + 180
    late = _bar(start, closed_at_offset=10)
    _, label = book.apply(late)
    assert label == "non_monotonic"
    assert int(book.current.open_time.timestamp()) == start + 180
