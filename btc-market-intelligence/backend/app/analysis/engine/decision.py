"""L13. Rule-based trade decision: TAKE_TRADE or WAIT, with direction, levels, and an auditable checklist.

No model, no learned weights, no external service. Every line of the decision is a comparison
between a stored or calculated number and a documented threshold. The output is a description of
what the rules say; the person reading it still places (or does not place) the order.
"""

from __future__ import annotations

from app.analysis.engine.types import DISCLAIMER
from app.analysis.engine.verdict import AVOID_CONFIDENCE, ENTER_CONFIDENCE, ENTER_P, MIN_RR

ACTION_TAKE = "TAKE_TRADE"
ACTION_WAIT = "WAIT"
ACTION_HOLD = "HOLD"
ACTION_EXIT = "CONSIDER_EXIT"

DIRECTION_BULL = "BULLISH"
DIRECTION_BEAR = "BEARISH"
DIRECTION_NONE = "NONE"

ATR_EXTREME_PERCENTILE = 95.0
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
ADX_TREND = 25.0
VOLUME_Z_CONFIRM = 0.5
MIN_INDICATOR_AGREEMENT = 0.5  # at least half of the available indicator votes must agree
INDICATOR_RULES_NOTE = "Indicator votes are counted on the focus timeframe's closed bars. A vote agrees when the indicator points in the trade direction."

DECISION_NOTE = (
    "Deterministic rules on stored closed candles and public derivatives observations. "
    "No AI, machine learning, or external model is involved. The outcome of any trade is not guaranteed."
)


def _check(rule: str, passed: bool | None, value, threshold, detail: str) -> dict:
    return {"rule": rule, "passed": passed, "value": value, "threshold": threshold, "detail": detail}


def _round(value, digits=2):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def indicator_votes(view: dict | None, direction: int, close: float | None) -> dict:
    """Count how many focus-timeframe indicators point with the direction (+1 long, -1 short)."""
    votes: list[dict] = []
    if not view or direction == 0:
        return {"agree": 0, "disagree": 0, "neutral": 0, "available": 0, "agreement": None, "votes": votes, "note": INDICATOR_RULES_NOTE}

    def add(name: str, vote: int | None, value, reading: str) -> None:
        state = "unavailable" if vote is None else "agree" if vote == direction else "disagree" if vote == -direction else "neutral"
        votes.append({"indicator": name, "value": value, "reading": reading, "state": state})

    rsi = view.get("rsi")
    if rsi is None:
        add("RSI(14)", None, None, "unavailable")
    else:
        if rsi >= RSI_OVERBOUGHT:
            vote, reading = -1, "overbought (>= %.0f); against a new long" % RSI_OVERBOUGHT
        elif rsi <= RSI_OVERSOLD:
            vote, reading = 1, "oversold (<= %.0f); against a new short" % RSI_OVERSOLD
        elif rsi > 50:
            vote, reading = 1, "above 50: bullish momentum"
        elif rsi < 50:
            vote, reading = -1, "below 50: bearish momentum"
        else:
            vote, reading = 0, "at 50"
        add("RSI(14)", vote, _round(rsi, 1), reading)

    macd_value = view.get("macd")
    if not macd_value:
        add("MACD histogram", None, None, "unavailable")
    else:
        histogram = macd_value.get("histogram")
        change = view.get("macd_histogram_change")
        vote = 0 if histogram in (None, 0) else 1 if histogram > 0 else -1
        reading = "histogram %s%s" % (
            "positive" if vote > 0 else "negative" if vote < 0 else "flat",
            "" if change is None else (" and rising" if change > 0 else " and falling" if change < 0 else ""),
        )
        add("MACD histogram", vote, _round(histogram, 2), reading)

    ema20, ema50, ema200 = view.get("ema_20"), view.get("ema_50"), view.get("ema_200")
    if ema20 is None or ema50 is None or close is None:
        add("EMA stack", None, None, "unavailable")
    else:
        if close > ema20 > ema50:
            vote, reading = 1, "close above EMA20 above EMA50"
        elif close < ema20 < ema50:
            vote, reading = -1, "close below EMA20 below EMA50"
        else:
            vote, reading = 0, "EMAs are not stacked"
        add("EMA stack", vote, {"ema20": _round(ema20), "ema50": _round(ema50), "ema200": _round(ema200)}, reading)

    if ema200 is None or close is None:
        add("EMA200", None, None, "unavailable")
    else:
        vote = 1 if close > ema200 else -1 if close < ema200 else 0
        add("EMA200", vote, _round(ema200), "close %s the 200 EMA" % ("above" if vote > 0 else "below" if vote < 0 else "at"))

    dm = view.get("adx")
    if not dm:
        add("ADX / DI", None, None, "unavailable")
    else:
        adx = dm.get("adx")
        plus, minus = dm.get("plus_di"), dm.get("minus_di")
        if adx is None or plus is None or minus is None:
            add("ADX / DI", None, None, "unavailable")
        elif adx < ADX_TREND:
            add("ADX / DI", 0, _round(adx, 1), "ADX under %.0f: no trend strength" % ADX_TREND)
        else:
            vote = 1 if plus > minus else -1 if minus > plus else 0
            add("ADX / DI", vote, _round(adx, 1), "ADX %.1f with %s leading" % (adx, "+DI" if vote > 0 else "-DI" if vote < 0 else "neither"))

    bands = view.get("bollinger")
    if not bands or close is None:
        add("Bollinger position", None, None, "unavailable")
    else:
        middle, upper, lower = bands.get("middle"), bands.get("upper"), bands.get("lower")
        if middle is None or upper is None or lower is None:
            add("Bollinger position", None, None, "unavailable")
        elif close > upper:
            add("Bollinger position", -1 if direction > 0 else 0, _round(close), "close above the upper band: stretched for a new long")
        elif close < lower:
            add("Bollinger position", 1 if direction < 0 else 0, _round(close), "close below the lower band: stretched for a new short")
        else:
            vote = 1 if close > middle else -1 if close < middle else 0
            add("Bollinger position", vote, _round(close), "close %s the middle band" % ("above" if vote > 0 else "below" if vote < 0 else "at"))

    zscore = view.get("volume_zscore")
    if zscore is None:
        add("Volume z-score", None, None, "unavailable")
    else:
        add("Volume z-score", 0, _round(zscore, 2), "volume %s its 20-bar mean" % ("above" if zscore >= VOLUME_Z_CONFIRM else "near or below"))

    obv = view.get("obv_slope")
    if obv is None:
        add("OBV slope", None, None, "unavailable")
    else:
        vote = 1 if obv > 0 else -1 if obv < 0 else 0
        add("OBV slope", vote, _round(obv, 4), "on-balance volume %s" % ("rising" if vote > 0 else "falling" if vote < 0 else "flat"))

    agree = sum(1 for item in votes if item["state"] == "agree")
    disagree = sum(1 for item in votes if item["state"] == "disagree")
    neutral = sum(1 for item in votes if item["state"] == "neutral")
    available = agree + disagree + neutral
    agreement = None if available == 0 else round(agree / available, 4)
    return {"agree": agree, "disagree": disagree, "neutral": neutral, "available": available, "agreement": agreement, "votes": votes, "note": INDICATOR_RULES_NOTE}


def build_decision(result: dict, momentum_focus: dict | None, atr_percentile: float | None) -> dict:
    """Map the verdict, plan, and indicator state onto TAKE_TRADE / WAIT / HOLD / CONSIDER_EXIT."""
    verdict = result.get("verdict") or {}
    plan = result.get("trade_plan") or {}
    probabilities = result.get("probabilities") or {}
    sequence = result.get("sequence") or {}
    htf = result.get("htf_bias") or {}
    freshness = result.get("data_freshness") or {}
    close = result.get("close")
    code = verdict.get("verdict")
    confidence = float(result.get("confidence") or 0.0)
    bias = htf.get("bias") or "UNKNOWN"
    direction = 1 if bias == "BULLISH" else -1 if bias == "BEARISH" else 0
    p_dir = probabilities.get("p_bull") if direction > 0 else probabilities.get("p_bear") if direction < 0 else None
    rr = plan.get("risk_reward_first_target")
    stage_index = sequence.get("stage_index", -1) if sequence else -1
    stale = bool(freshness.get("stale"))
    votes = indicator_votes(momentum_focus, direction, close)

    checklist = [
        _check("Higher-timeframe bias is directional", direction != 0, bias, "BULLISH or BEARISH", "Daily and 4h structure must agree on a direction before any trade is considered."),
        _check("Daily and 4h structure agree", not bool(htf.get("conflict")), bool(htf.get("conflict")), False, "A conflict between the daily and 4h labels blocks entries."),
        _check("Market data is fresh", not stale, stale, False, "The latest closed candle must be inside the staleness window."),
        _check(
            "Volatility is not extreme",
            None if atr_percentile is None else atr_percentile < ATR_EXTREME_PERCENTILE,
            _round(atr_percentile, 1),
            "< %.0f percentile" % ATR_EXTREME_PERCENTILE,
            "ATR percentile at or above 95 means stops are more likely to be swept.",
        ),
        _check("Directional probability", None if p_dir is None else p_dir >= ENTER_P, _round(p_dir, 4), ">= %.2f" % ENTER_P, "Fused probability for the bias side."),
        _check("Confidence", confidence >= ENTER_CONFIDENCE, _round(confidence, 4), ">= %.2f" % ENTER_CONFIDENCE, "Agreement x layer sufficiency x freshness."),
        _check("Playbook reached the entry zone", stage_index >= 5, sequence.get("stage"), "IN_ENTRY_ZONE or later", "Sweep, structure shift, displacement, then price inside a fresh zone."),
        _check("Reward-to-risk to first target", None if rr is None else rr >= MIN_RR, _round(rr, 2), ">= %.1f" % MIN_RR, "Measured from the planned entry to the invalidation and the nearest mapped target."),
        _check(
            "Indicator agreement on the focus timeframe",
            None if votes["agreement"] is None else votes["agreement"] >= MIN_INDICATOR_AGREEMENT and votes["disagree"] <= votes["agree"],
            votes["agreement"],
            ">= %.0f%% of available votes, and not outvoted" % (MIN_INDICATOR_AGREEMENT * 100),
            "RSI, MACD, EMA stack, EMA200, ADX/DI, Bollinger position, OBV slope.",
        ),
    ]

    explanation: list[str] = []
    position = verdict.get("position")
    if code in ("HOLD", "CONSIDER_EXIT") and position:
        action = ACTION_HOLD if code == "HOLD" else ACTION_EXIT
        side = str(position.get("side", "")).lower()
        trade_direction = DIRECTION_BULL if side == "long" else DIRECTION_BEAR if side == "short" else DIRECTION_NONE
        explanation.extend(verdict.get("reasons") or [])
        return _payload(action, trade_direction, plan if plan.get("status") in ("OK", "NO_TARGET") else {}, confidence, explanation, checklist, votes, verdict, close, position)

    gates_failed = [item["rule"] for item in checklist if item["passed"] is False]
    gates_unknown = [item["rule"] for item in checklist if item["passed"] is None]
    if code == "ENTER_NOW" and not gates_failed:
        trade_direction = DIRECTION_BULL if direction > 0 else DIRECTION_BEAR
        explanation.append("All %d entry rules passed on the %s closed bars." % (len(checklist), result.get("focus_timeframe")))
        explanation.extend(verdict.get("reasons") or [])
        explanation.append("%d of %d available indicators agree with the %s direction." % (votes["agree"], votes["available"], trade_direction.lower()))
        if gates_unknown:
            explanation.append("Rules without a value were not counted as passed: %s." % ", ".join(gates_unknown))
        return _payload(ACTION_TAKE, trade_direction, plan, confidence, explanation, checklist, votes, verdict, close, None)

    if code == "ENTER_NOW" and gates_failed:
        explanation.append("The playbook verdict was ENTER_NOW, but the decision rules block it: %s." % "; ".join(gates_failed))
    elif code == "AVOID_HIGH_RISK":
        explanation.append("Risk filters are active, so no trade is taken.")
        explanation.extend(verdict.get("risks") or [])
    else:
        explanation.append("The setup is incomplete. Rules not yet satisfied: %s." % (", ".join(gates_failed) if gates_failed else "none failed, but the verdict is not ENTER_NOW"))
    explanation.extend(verdict.get("reasons") or [])
    if direction != 0 and p_dir is not None:
        explanation.append("Current lean is %s with p=%.0f%%; this is context, not a trade." % (bias.lower(), p_dir * 100))
    return _payload(ACTION_WAIT, DIRECTION_NONE, {}, confidence, explanation, checklist, votes, verdict, close, None, lean=bias if direction != 0 else None)


def _payload(action, direction, plan, confidence, explanation, checklist, votes, verdict, close, position, lean=None) -> dict:
    targets = [{"price": _round(item.get("price")), "r_multiple": item.get("r_multiple"), "synthetic": bool(item.get("synthetic"))} for item in (plan.get("targets") or [])]
    side_label = "BUY" if direction == DIRECTION_BULL else "SELL" if direction == DIRECTION_BEAR else None
    sizing = plan.get("sizing") or None
    if confidence >= 0.7:
        confidence_label = "HIGH"
    elif confidence >= ENTER_CONFIDENCE:
        confidence_label = "MEDIUM"
    elif confidence >= AVOID_CONFIDENCE:
        confidence_label = "LOW"
    else:
        confidence_label = "VERY_LOW"
    headline = {
        ACTION_TAKE: "%s (%s)" % (direction, side_label),
        ACTION_WAIT: "WAIT" + ("" if lean is None else " · lean %s" % lean.lower()),
        ACTION_HOLD: "HOLD %s" % direction.lower(),
        ACTION_EXIT: "CONSIDER EXIT %s" % direction.lower(),
    }[action]
    has_levels = action in (ACTION_TAKE, ACTION_HOLD, ACTION_EXIT) and plan.get("entry") is not None
    return {
        "action": action,
        "direction": direction,
        "side": side_label,
        "headline": headline,
        "entry": _round(plan.get("entry")) if has_levels else None,
        "entry_note": plan.get("entry_note") if has_levels else None,
        "stop_loss": _round(plan.get("invalidation")) if has_levels else None,
        "stop_loss_note": plan.get("invalidation_note") if has_levels else None,
        "take_profit": targets if has_levels else [],
        "risk_reward": plan.get("risk_reward_first_target") if has_levels else None,
        "expected_value_r": plan.get("expected_value_r") if has_levels else None,
        "time_stop": plan.get("time_stop") if has_levels else None,
        "sizing": sizing if has_levels else None,
        "confidence": round(confidence, 4),
        "confidence_label": confidence_label,
        "directional_probability": verdict.get("directional_probability"),
        "reference_close": close,
        "explanation": explanation,
        "checklist": checklist,
        "indicators": votes,
        "position": position,
        "risk_rules": [
            "No trade unless every rule in the checklist passes.",
            "Stop loss sits beyond the structural level plus 0.5 ATR, or 1.5 ATR from entry when no structure exists.",
            "Minimum reward-to-risk to the first target is %.1f." % MIN_RR,
            "Risk per trade is the configured percent of the account; the size is informational and must be rounded to the exchange lot.",
            "A close beyond the stop invalidates the setup. A target reached means take profit or trail; it is not a new entry.",
        ],
        "note": DECISION_NOTE,
        "disclaimer": DISCLAIMER,
    }
