"""Candle windows, aggregation, and gap detection. Missing minutes are reported, not invented."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from app.core.timeutil import RESOLUTIONS, as_utc
from app.data.normalizer import CandleDraft


def iter_chunks(start: int, end: int, step: int, chunk_candles: int) -> list[tuple[int, int]]:
    """Half-open unix ranges [start, end), each spanning at most chunk_candles bars."""
    if step <= 0 or chunk_candles <= 0:
        raise ValueError("step and chunk size must be positive")
    if end <= start:
        return []
    width = step * chunk_candles
    cursor = start
    windows: list[tuple[int, int]] = []
    while cursor < end:
        nxt = min(end, cursor + width)
        windows.append((cursor, nxt))
        cursor = nxt
    return windows


def api_end_inclusive(exclusive_end: int) -> int:
    """Delta's ``end`` includes a candle whose open equals ``end``. Pass one second earlier."""
    return exclusive_end - 1


def missing_opens(open_times: list, step: int) -> list:
    missing = []
    ordered = sorted(as_utc(item) for item in open_times)
    for earlier, later in zip(ordered, ordered[1:]):
        delta = int((later - earlier).total_seconds())
        if delta == step:
            continue
        if delta < step:
            continue
        slots = delta // step - 1
        for index in range(1, slots + 1):
            missing.append(earlier + timedelta(seconds=step * index))
    return missing


@dataclass
class FormingBook:
    timeframe: str
    current: CandleDraft | None = None

    def apply(self, draft: CandleDraft) -> tuple[list[CandleDraft], str]:
        """Return candles to persist and a sequence label: ok, update, gap, or non_monotonic."""
        step = RESOLUTIONS[self.timeframe]
        if draft.timeframe != self.timeframe:
            raise ValueError("timeframe mismatch")
        if self.current is None:
            self.current = draft
            return [draft], "ok"
        current_open = as_utc(self.current.open_time)
        new_open = as_utc(draft.open_time)
        if new_open == current_open:
            revised = draft if not self.current.is_closed else replace(draft, is_closed=True)
            self.current = revised
            return [revised], "update"
        if new_open < current_open:
            return [draft], "non_monotonic"
        label = "gap" if new_open > current_open + timedelta(seconds=step) else "ok"
        closing = self.current if self.current.is_closed else replace(self.current, is_closed=True)
        self.current = draft
        return [closing, draft], label

    def close_if_due(self, now) -> CandleDraft | None:
        if self.current is None or self.current.is_closed:
            return None
        step = RESOLUTIONS[self.timeframe]
        if as_utc(now) >= as_utc(self.current.open_time) + timedelta(seconds=step):
            self.current = replace(self.current, is_closed=True)
            return self.current
        return None


def aggregate(candles: list[CandleDraft], target: str) -> tuple[list[CandleDraft], list[dict]]:
    """Build higher-timeframe bars from closed 1-minute bars.

    A bucket is emitted only when every 1-minute slot inside it is present.
    """
    if target not in RESOLUTIONS or target == "1m":
        raise ValueError("target must be a higher timeframe")
    step = RESOLUTIONS[target]
    closed = [item for item in candles if item.timeframe == "1m" and item.is_closed]
    chosen: dict[int, CandleDraft] = {}
    for candle in closed:
        opened = int(as_utc(candle.open_time).timestamp())
        previous = chosen.get(opened)
        if previous is None or as_utc(candle.received_at) >= as_utc(previous.received_at):
            chosen[opened] = candle
    buckets: dict[int, list[CandleDraft]] = {}
    for opened, candle in chosen.items():
        buckets.setdefault(opened // step * step, []).append(candle)
    built: list[CandleDraft] = []
    skipped: list[dict] = []
    expected = step // 60
    for bucket_open in sorted(buckets):
        rows = sorted(buckets[bucket_open], key=lambda item: item.open_time)
        unique = {int(as_utc(item.open_time).timestamp()) for item in rows}
        if len(unique) != expected:
            skipped.append(
                {
                    "bucket_open": bucket_open,
                    "present": len(unique),
                    "expected": expected,
                    "reason": "The bucket was not emitted because at least one closed 1-minute slot is missing. No candle was invented.",
                }
            )
            continue
        first, last = rows[0], rows[-1]
        received = max(item.received_at for item in rows)
        built.append(
            CandleDraft(
                symbol=first.symbol,
                timeframe=target,
                open_time=datetime.fromtimestamp(bucket_open, tz=timezone.utc),
                open=first.open,
                high=max(item.high for item in rows),
                low=min(item.low for item in rows),
                close=last.close,
                volume=sum((item.volume for item in rows), start=first.volume.__class__("0")),
                source="aggregated_1m",
                is_closed=True,
                exchange_time_raw=str(bucket_open),
                timestamp_unit="s",
                received_at=received,
                aligned=True,
            )
        )
    return built, skipped
