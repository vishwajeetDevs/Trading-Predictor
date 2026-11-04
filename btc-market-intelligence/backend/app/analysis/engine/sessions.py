"""L7. Sessions and timing. Phase 1 covers sessions and killzones; the calendar arrives in Phase 2."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.analysis.engine.types import CAT_SESSIONS, GATE_ANY, Evidence, LayerResult, MarketData

IST = timezone(timedelta(hours=5, minutes=30))
SESSIONS_UTC = {"asia": (0, 8), "london": (7, 16), "new_york": (12, 21)}
KILLZONES_UTC = {"london_killzone": (7, 10), "new_york_killzone": (12, 15)}


def _next_open(now: datetime) -> tuple[str, datetime]:
    best_name = None
    best_time = None
    for name, (start, _end) in SESSIONS_UTC.items():
        candidate = now.replace(hour=start, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        if best_time is None or candidate < best_time:
            best_time = candidate
            best_name = name
    return best_name, best_time


def run(data: MarketData) -> LayerResult:
    result = LayerResult(name="sessions")
    now = data.now.astimezone(timezone.utc)
    hour = now.hour
    active = [name for name, (start, end) in SESSIONS_UTC.items() if start <= hour < end]
    killzones = [name for name, (start, end) in KILLZONES_UTC.items() if start <= hour < end]
    weekend = now.weekday() >= 5
    low_liquidity = weekend or (not active) or (active == ["asia"] and hour >= 3)
    next_name, next_time = _next_open(now)
    if weekend:
        result.add(Evidence(CAT_SESSIONS, "weekend", 0.0, 0.0, "clock", "Weekend session: thinner liquidity, wider spreads, higher sweep risk.", GATE_ANY))
    if killzones:
        result.add(Evidence(CAT_SESSIONS, "killzone", 0.0, 0.0, "clock", "Inside the %s. Displacement moves are more common in this window." % killzones[0].replace("_", " "), GATE_ANY))
    result.findings = {
        "utc": now.isoformat(),
        "ist": now.astimezone(IST).isoformat(),
        "active_sessions": active,
        "killzones": killzones,
        "weekend": weekend,
        "low_liquidity": low_liquidity,
        "next_session": next_name,
        "next_session_open_utc": next_time.isoformat(),
        "minutes_to_next_open": int((next_time - now).total_seconds() // 60),
        "session_table_utc": {name: {"start_hour": start, "end_hour": end} for name, (start, end) in SESSIONS_UTC.items()},
        "high_impact_event_within_2h": None,
        "calendar_note": "Economic calendar integration is scheduled for Phase 2. The event flag is unavailable.",
    }
    result.summary = "%s%s. %s." % (
        "Active: %s" % ", ".join(active) if active else "No major session active",
        " (%s)" % ", ".join(k.replace("_", " ") for k in killzones) if killzones else "",
        "Low-liquidity window" if low_liquidity else "Normal liquidity window",
    )
    return result
