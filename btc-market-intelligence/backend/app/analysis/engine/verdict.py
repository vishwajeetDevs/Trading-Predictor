"""L12. Deterministic verdict table.

Verdicts: ENTER_NOW, WAIT_FOR_BETTER_ENTRY, AVOID_HIGH_RISK, HOLD, CONSIDER_EXIT.
Every verdict carries reasons, risks, and what would change the view.
"""

from __future__ import annotations

from app.analysis.engine.types import DISCLAIMER, REGIME_HIGH_VOL, Position

ENTER_P = 0.60
ENTER_CONFIDENCE = 0.50
AVOID_CONFIDENCE = 0.30
EXIT_P = 0.45
MIN_RR = 2.0


def decide(
    *,
    bias: str,
    htf_conflict: bool,
    regime: str,
    atr_percentile: float | None,
    probabilities: dict,
    confidence: float,
    sequence: dict,
    plan: dict,
    dealing: dict | None,
    stale: bool,
    event_flag: bool | None,
    position: Position | None,
    close: float,
    latest_shift: dict | None,
    latest_sweep: dict | None,
) -> dict:
    direction = 1 if bias == "BULLISH" else -1 if bias == "BEARISH" else 0
    p_dir = probabilities["p_bull"] if direction > 0 else probabilities["p_bear"] if direction < 0 else max(probabilities["p_bull"], probabilities["p_bear"])
    risks: list[str] = []
    reasons: list[str] = []
    triggers: list[str] = []

    high_vol = regime == REGIME_HIGH_VOL or (atr_percentile is not None and atr_percentile >= 95)
    if high_vol:
        risks.append("Volatility regime is extreme (ATR percentile %s). Stops are more likely to be swept." % ("%.0f" % atr_percentile if atr_percentile is not None else "n/a"))
    if htf_conflict:
        risks.append("Daily and 4h structure labels disagree.")
    if stale:
        risks.append("Market data is stale; the view may lag the live tape.")
    if event_flag:
        risks.append("A high-impact scheduled event is within two hours.")
    if confidence < AVOID_CONFIDENCE:
        risks.append("Evidence agreement is low (confidence %.2f)." % confidence)
    rr = plan.get("risk_reward_first_target")
    if rr is not None and rr < MIN_RR:
        risks.append("Reward-to-risk to the first target is %.1f, under the %.0f minimum." % (rr, MIN_RR))

    # Position management first.
    if position is not None:
        side = 1 if position.side.lower() == "long" else -1
        p_side = probabilities["p_bull"] if side > 0 else probabilities["p_bear"]
        pnl_pct = (close - position.entry_price) / position.entry_price * 100.0 * side
        against_shift = latest_shift is not None and latest_shift.get("direction") == -side and latest_shift.get("bars_ago", 99) <= 10
        swept_protection = latest_sweep is not None and latest_sweep.get("direction") == -side and latest_sweep.get("bars_ago", 99) <= 5
        invalidated = False
        if plan.get("direction") == position.side.lower() and plan.get("invalidation") is not None:
            invalidated = close < plan["invalidation"] if side > 0 else close > plan["invalidation"]
        target_hit = False
        if plan.get("direction") == position.side.lower():
            for target in plan.get("targets") or []:
                if (side > 0 and close >= target["price"]) or (side < 0 and close <= target["price"]):
                    target_hit = True
        if against_shift or swept_protection or p_side < EXIT_P or invalidated or target_hit:
            if against_shift:
                reasons.append("A %s against the position printed %d bars ago." % (latest_shift["kind"], latest_shift["bars_ago"]))
            if swept_protection:
                reasons.append("Liquidity on the protective side was swept %d bars ago." % latest_sweep["bars_ago"])
            if p_side < EXIT_P:
                reasons.append("Probability for the position's side is %.0f%%, under %.0f%%." % (p_side * 100, EXIT_P * 100))
            if invalidated:
                reasons.append("Price is beyond the structural invalidation level.")
            if target_hit:
                reasons.append("A mapped target has been reached; consider taking profit or trailing.")
            triggers.append("A renewed %s structure break with displacement would re-arm the %s thesis." % ("bullish" if side > 0 else "bearish", position.side))
            return _payload("CONSIDER_EXIT", reasons, risks, triggers, p_dir, confidence, position, pnl_pct)
        reasons.append("Structure and bias still favour the %s (p=%.0f%%)." % (position.side, p_side * 100))
        reasons.append("No structure break against the position and no sweep of its protective liquidity in the last few bars.")
        triggers.append("Exit if a %s or sweep against the position appears, or if p drops below %.0f%%." % ("CHoCH/MSS", EXIT_P * 100))
        return _payload("HOLD", reasons, risks, triggers, p_dir, confidence, position, pnl_pct)

    if direction == 0 or bias == "RANGING" or bias == "UNKNOWN":
        reasons.append("Higher-timeframe bias is %s; the playbook is not armed in either direction." % bias.lower())
        triggers.append("A directional daily/4h structure label and a liquidity sweep would arm the playbook.")
        verdict = "AVOID_HIGH_RISK" if (high_vol or confidence < AVOID_CONFIDENCE or stale) else "WAIT_FOR_BETTER_ENTRY"
        return _payload(verdict, reasons, risks, triggers, p_dir, confidence, None, None)

    if high_vol or confidence < AVOID_CONFIDENCE or htf_conflict or event_flag or stale:
        reasons.append("Risk filters are active; the directional read is %s but conditions are not clean." % bias.lower())
        triggers.append("Volatility back inside its normal percentile, agreement across layers, and aligned daily/4h labels.")
        return _payload("AVOID_HIGH_RISK", reasons, risks, triggers, p_dir, confidence, None, None)

    stage_index = sequence.get("stage_index", -1) if sequence else -1
    in_zone = stage_index >= 5
    side_ok = bool(sequence.get("price_side_ok")) if sequence else False
    if in_zone and p_dir >= ENTER_P and confidence >= ENTER_CONFIDENCE and rr is not None and rr >= MIN_RR:
        reasons.append("Playbook reached %s in the %s direction." % (sequence["stage"], sequence["direction"]))
        reasons.append("Probability for the %s side is %.0f%% with confidence %.2f." % (bias.lower(), p_dir * 100, confidence))
        reasons.append("Reward-to-risk to the first target is %.1f." % rr)
        triggers.append("A close beyond %.1f invalidates the setup." % plan["invalidation"])
        return _payload("ENTER_NOW", reasons, risks, triggers, p_dir, confidence, None, None)

    if not in_zone:
        missing = ", ".join(sequence.get("missing", [])[:2]) if sequence else "sequence"
        reasons.append("Bias is %s but the playbook is at %s; still missing %s." % (bias.lower(), sequence.get("stage") if sequence else "none", missing))
    if not side_ok and dealing is not None:
        reasons.append("Price is in the %s of the dealing range; a %s wants %s." % (dealing["zone"], "long" if direction > 0 else "short", "discount" if direction > 0 else "premium"))
    if p_dir < ENTER_P:
        reasons.append("Directional probability %.0f%% is under the %.0f%% entry threshold." % (p_dir * 100, ENTER_P * 100))
    if confidence < ENTER_CONFIDENCE:
        reasons.append("Confidence %.2f is under the %.2f entry threshold." % (confidence, ENTER_CONFIDENCE))
    if rr is not None and rr < MIN_RR:
        reasons.append("First-target R:R %.1f is under %.0f." % (rr, MIN_RR))
    triggers.append("A retrace into a fresh %s FVG or order block in %s after a displacement break." % ("bullish" if direction > 0 else "bearish", "discount" if direction > 0 else "premium"))
    return _payload("WAIT_FOR_BETTER_ENTRY", reasons, risks, triggers, p_dir, confidence, None, None)


def _payload(verdict: str, reasons: list[str], risks: list[str], triggers: list[str], p_dir: float, confidence: float, position: Position | None, pnl_pct: float | None) -> dict:
    return {
        "verdict": verdict,
        "label": verdict.replace("_", " ").title(),
        "directional_probability": round(p_dir, 4),
        "confidence": round(confidence, 4),
        "reasons": reasons,
        "risks": risks,
        "what_changes_this_view": triggers,
        "position": None if position is None else {"side": position.side, "entry_price": position.entry_price, "size": position.size, "unrealized_percent": None if pnl_pct is None else round(pnl_pct, 3)},
        "disclaimer": DISCLAIMER,
    }
