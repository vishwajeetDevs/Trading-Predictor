"""Descriptive derivatives states. There is no weighted score and no trade instruction."""

from __future__ import annotations

from decimal import Decimal


def context_states(
    funding: dict | None,
    oi_state: str | None,
    mark_percent: Decimal | None,
    imbalance: Decimal | None,
    *,
    mark_threshold: Decimal,
    imbalance_threshold: Decimal,
    available: bool,
) -> list[dict]:
    states = []
    if not available:
        states.append(_state("derivatives_data_unavailable", "No derivatives observation is stored for this contract."))
        return states
    if funding:
        sign = funding.get("sign")
        historical = funding.get("historical")
        if sign in ("positive", "negative", "near_zero"):
            states.append(_state("funding_%s" % sign, funding.get("note")))
        if historical == "historically_elevated":
            states.append(_state("funding_elevated", "The current rate is at or above the 90th percentile of the stored lookback."))
        elif historical == "historically_depressed":
            states.append(_state("funding_depressed", "The current rate is at or below the 10th percentile of the stored lookback."))
        elif historical == "INSUFFICIENT_HISTORY":
            states.append(_state("insufficient_historical_data", "The funding lookback does not yet have the minimum number of stored observations."))
    if oi_state == "INSUFFICIENT_HISTORY":
        states.append(_state("insufficient_historical_data", "Open interest and price do not both have an earlier observation for this interval."))
    elif oi_state:
        if oi_state.endswith("oi_rising"):
            states.append(_state("open_interest_expanding", "Stored open interest is higher than the earlier observation."))
        elif oi_state.endswith("oi_falling"):
            states.append(_state("open_interest_contracting", "Stored open interest is lower than the earlier observation."))
        if oi_state in ("price_rising_oi_falling", "price_falling_oi_rising"):
            states.append(_state("price_oi_divergence", "The stored price change and the stored open-interest change have opposite signs."))
    if mark_percent is not None and abs(mark_percent) >= mark_threshold:
        states.append(_state("mark_last_divergence", "The mark price differs from the last traded price by at least the documented threshold. Mark price is not an executable price."))
    if imbalance is not None and abs(imbalance) >= imbalance_threshold:
        states.append(_state("order_book_imbalance", "Bid and ask depth differ by at least the documented threshold. This is not a directional signal."))
    if not states:
        states.append(_state("no_threshold_state", "Stored observations are inside the documented thresholds."))
    return _unique(states)


def _state(name: str, detail: str | None) -> dict:
    return {"name": name, "detail": detail}


def _unique(states: list[dict]) -> list[dict]:
    seen = set()
    output = []
    for state in states:
        if state["name"] in seen:
            continue
        seen.add(state["name"])
        output.append(state)
    return output
