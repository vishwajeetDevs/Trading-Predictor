"""L11. Risk plan: entry zone, invalidation, targets, R:R, expected value, quarter-Kelly, size."""

from __future__ import annotations

from app.analysis.engine.types import clamp

TIME_STOP_HORIZONS = 2
ALT_STOP_ATR = 1.5
BUFFER_ATR = 0.5
MIN_RR = 2.0


def _targets(direction: int, entry: float, stop: float, candidates: list[float]) -> list[dict]:
    risk = abs(entry - stop)
    if risk <= 0:
        return []
    out = []
    ordered = sorted((price for price in candidates if (price > entry if direction > 0 else price < entry)), key=lambda price: abs(price - entry))
    for price in ordered:
        multiple = abs(price - entry) / risk
        if multiple < 0.8:
            continue
        out.append({"price": price, "r_multiple": round(multiple, 2)})
        if len(out) == 3:
            break
    if not out:
        for multiple in (1.0, 2.0, 3.0):
            out.append({"price": entry + direction * risk * multiple, "r_multiple": multiple, "synthetic": True})
    return out


def build_plan(
    direction: int,
    close: float,
    atr_value: float | None,
    entry_zone: dict | None,
    sweep_extreme: float | None,
    pools_opposite: list[float],
    zones_opposite: list[float],
    p_direction: float,
    account_size: float | None,
    risk_percent: float,
    product: dict | None,
    horizon: str,
) -> dict:
    """Return a trade plan for the given direction. direction is 1 (long) or -1 (short)."""
    if direction == 0 or close <= 0:
        return {"status": "NO_DIRECTION", "note": "The bias is ranging or unknown; no directional plan is built."}
    atr_value = atr_value or close * 0.005
    if entry_zone is not None:
        entry = entry_zone["midpoint"]
        zone_far = entry_zone["bottom"] if direction > 0 else entry_zone["top"]
        entry_note = "Midpoint of the %s %s." % ("bullish" if direction > 0 else "bearish", entry_zone["kind"].replace("_", " "))
    else:
        entry = close
        zone_far = None
        entry_note = "No fresh zone in the bias direction; the plan is measured from the latest close and is informational."
    candidates = []
    if sweep_extreme is not None and ((direction > 0 and sweep_extreme < entry) or (direction < 0 and sweep_extreme > entry)):
        candidates.append(sweep_extreme - direction * atr_value * BUFFER_ATR)
    if zone_far is not None:
        candidates.append(zone_far - direction * atr_value * BUFFER_ATR)
    structural_stop = None
    if candidates:
        structural_stop = min(candidates) if direction > 0 else max(candidates)
    alternative_stop = entry - direction * atr_value * ALT_STOP_ATR
    stop = structural_stop if structural_stop is not None else alternative_stop
    if abs(entry - stop) > atr_value * 4:
        stop = alternative_stop
    risk_per_unit = abs(entry - stop)
    targets = _targets(direction, entry, stop, pools_opposite + zones_opposite)
    first_rr = targets[0]["r_multiple"] if targets else None
    p = clamp(p_direction, 0.01, 0.99)
    ev = None if first_rr is None else p * first_rr - (1.0 - p)
    kelly = None if first_rr is None or first_rr <= 0 else max(0.0, 0.25 * (p - (1.0 - p) / first_rr))
    sizing = None
    if account_size and account_size > 0 and risk_per_unit > 0:
        risk_amount = account_size * clamp(risk_percent, 0.01, 10.0) / 100.0
        contract_value = None
        try:
            contract_value = float((product or {}).get("contract_value") or 0) or None
        except (TypeError, ValueError):
            contract_value = None
        base_units = risk_amount / risk_per_unit
        contracts = None
        if contract_value:
            contracts = base_units / contract_value
        sizing = {
            "account_size": account_size,
            "risk_percent": risk_percent,
            "risk_amount": round(risk_amount, 2),
            "risk_per_unit": round(risk_per_unit, 2),
            "base_asset_units": round(base_units, 6),
            "contracts": None if contracts is None else int(contracts),
            "contract_value": contract_value,
            "note": "Contracts use the product's contract value. Round down to the exchange lot size before use.",
        }
    return {
        "status": "OK" if first_rr is not None else "NO_TARGET",
        "direction": "long" if direction > 0 else "short",
        "entry": round(entry, 2),
        "entry_note": entry_note,
        "entry_zone": entry_zone,
        "invalidation": round(stop, 2),
        "invalidation_note": "Structural: beyond the sweep extreme or zone far edge plus 0.5 ATR." if structural_stop is not None and stop == structural_stop else "Volatility: 1.5 ATR from entry.",
        "alternative_invalidation": round(alternative_stop, 2),
        "time_stop": "Exit if the thesis has not progressed within %d x %s." % (TIME_STOP_HORIZONS, horizon),
        "targets": targets,
        "risk_reward_first_target": first_rr,
        "meets_min_rr": None if first_rr is None else first_rr >= MIN_RR,
        "expected_value_r": None if ev is None else round(ev, 3),
        "quarter_kelly_fraction": None if kelly is None else round(kelly, 4),
        "sizing": sizing,
        "atr": round(atr_value, 2),
    }
