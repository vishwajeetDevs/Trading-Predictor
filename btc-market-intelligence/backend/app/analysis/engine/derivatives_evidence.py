"""Derivatives evidence from the stored funding, open-interest, mark, and order-book views."""

from __future__ import annotations

from app.analysis.engine.types import CAT_VOLUME, GATE_ANY, GATE_MEAN_REVERSION, STATUS_UNAVAILABLE, Evidence, LayerResult, MarketData

OI_QUADRANTS = {
    "price_rising_oi_rising": ("new_longs", 1.0, "Price and open interest both rising: new long positioning."),
    "price_falling_oi_falling": ("long_liquidation", -0.4, "Price and open interest both falling: long positions closing."),
    "price_rising_oi_falling": ("short_covering", 0.4, "Price rising while open interest falls: short covering, not fresh demand."),
    "price_falling_oi_rising": ("new_shorts", -1.0, "Price falling while open interest rises: new short positioning."),
}


def _float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def run(data: MarketData) -> LayerResult:
    result = LayerResult(name="derivatives")
    body = data.derivatives
    if not body or body.get("availability") != "AVAILABLE":
        result.status = STATUS_UNAVAILABLE
        result.summary = "No derivatives observations are stored."
        return result
    stale = bool(body.get("stale"))
    freshness = 0.5 if stale else 1.0
    funding = (body.get("funding") or {}).get("values") or {}
    interest = (body.get("open_interest") or {}).get("values") or {}
    mark = (body.get("mark_price") or {}).get("values") or {}
    book = (body.get("orderbook") or {}).get("values") or {}
    findings: dict = {"stale": stale}

    rate = _float(funding.get("funding_rate"))
    classification = funding.get("classification") or {}
    findings["funding_rate"] = rate
    findings["funding_class"] = classification
    if rate is not None:
        historical = classification.get("historical")
        if historical == "historically_elevated":
            result.add(Evidence(CAT_VOLUME, "funding_elevated", -0.6, 0.45, "derivatives", "Funding is at or above its 90th percentile: crowded longs pay; a contrarian headwind.", GATE_MEAN_REVERSION, freshness))
        elif historical == "historically_depressed":
            result.add(Evidence(CAT_VOLUME, "funding_depressed", 0.6, 0.45, "derivatives", "Funding is at or below its 10th percentile: crowded shorts pay; a contrarian tailwind.", GATE_MEAN_REVERSION, freshness))
        elif classification.get("sign") == "positive":
            result.add(Evidence(CAT_VOLUME, "funding_positive", 0.2, 0.15, "derivatives", "Funding is positive but inside its historical range: mild long bias in positioning.", GATE_ANY, freshness))
        elif classification.get("sign") == "negative":
            result.add(Evidence(CAT_VOLUME, "funding_negative", -0.2, 0.15, "derivatives", "Funding is negative but inside its historical range: mild short bias in positioning.", GATE_ANY, freshness))

    quadrants = []
    for change in interest.get("changes") or []:
        state = change.get("state")
        quadrant = OI_QUADRANTS.get(state)
        if quadrant is None:
            continue
        name, direction, text = quadrant
        weight = {"1h": 0.3, "4h": 0.4, "24h": 0.3}.get(change.get("interval"), 0.3)
        quadrants.append({"interval": change.get("interval"), "quadrant": name, "oi_change": change.get("open_interest_change"), "price_change": change.get("price_change")})
        result.add(Evidence(CAT_VOLUME, "oi_%s_%s" % (name, change.get("interval")), direction, weight, "derivatives", "%s (%s window)." % (text, change.get("interval")), GATE_ANY, freshness))
    findings["open_interest_quadrants"] = quadrants
    findings["open_interest_contracts"] = _float(interest.get("contracts"))

    mark_pct = _float(mark.get("mark_last_percent"))
    index_pct = _float(mark.get("mark_index_percent"))
    findings["mark_last_percent"] = mark_pct
    findings["mark_index_percent"] = index_pct
    # The stored field is a ratio despite its name: (mark - index) / index.
    if index_pct is not None and abs(index_pct) >= 0.0005:
        result.add(Evidence(CAT_VOLUME, "basis", -0.3 if index_pct > 0 else 0.3, 0.2, "derivatives", "Mark trades %.3f%% %s index: %s basis." % (abs(index_pct) * 100.0, "above" if index_pct > 0 else "below", "rich" if index_pct > 0 else "discounted"), GATE_MEAN_REVERSION, freshness))

    imbalance = _float(book.get("imbalance"))
    findings["book_imbalance"] = imbalance
    findings["spread"] = _float(book.get("spread"))
    if imbalance is not None and abs(imbalance) >= 0.2:
        result.add(Evidence(CAT_VOLUME, "book_imbalance", 0.5 if imbalance > 0 else -0.5, min(0.35, abs(imbalance)), "derivatives", "Top-of-book depth imbalance %.2f (%s-heavy). Resting depth can be pulled; this is weak evidence." % (imbalance, "bid" if imbalance > 0 else "ask"), GATE_ANY, freshness))

    result.findings = findings
    result.summary = "Funding %s, OI quadrant %s, book imbalance %s%s." % (
        "n/a" if rate is None else "%.5f" % rate,
        quadrants[0]["quadrant"] if quadrants else "n/a",
        "n/a" if imbalance is None else "%.2f" % imbalance,
        " (stale)" if stale else "",
    )
    if stale:
        result.warnings.append("Derivatives observations are stale; their evidence weight is halved.")
    return result
