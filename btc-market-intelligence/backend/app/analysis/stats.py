"""Sample summaries, base rates, and a seeded block bootstrap.

Resampling repeats observed historical returns. It is not a probability of a future move.
"""

from __future__ import annotations

import random

from app.analysis.definitions import BASE_RATE_SEPARATION, MIN_SAMPLE


def quantile(values: list[float], q: float) -> float | None:
    """Linear interpolation between sorted observations. q is between 0 and 1."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def summarize_sample(returns: list[float], favorable: list[float], adverse: list[float], min_sample: int = MIN_SAMPLE) -> dict:
    sample = len(returns)
    result = {
        "status": "INSUFFICIENT_SAMPLE" if sample < min_sample else "OK",
        "sample_size": sample,
        "minimum_sample": min_sample,
        "median_return": None,
        "p10_return": None,
        "p90_return": None,
        "positive_rate": None,
        "negative_rate": None,
        "median_mfe": None,
        "median_mae": None,
        "explanation": "More history is required before a frequency is shown." if sample < min_sample else None,
    }
    if sample < min_sample:
        return result
    positive = sum(1 for item in returns if item > 0) / sample
    negative = sum(1 for item in returns if item < 0) / sample
    result.update(
        {
            "median_return": quantile(returns, 0.5),
            "p10_return": quantile(returns, 0.1),
            "p90_return": quantile(returns, 0.9),
            "positive_rate": positive,
            "negative_rate": negative,
            "median_mfe": quantile(favorable, 0.5) if favorable else None,
            "median_mae": quantile(adverse, 0.5) if adverse else None,
        }
    )
    return result


def compare_base_rate(
    condition_rate: float | None,
    base_rate: float | None,
    sample: int,
    base_sample: int = MIN_SAMPLE,
    min_sample: int = MIN_SAMPLE,
) -> dict:
    """Publish a difference only when both samples meet the minimum and use the same sampling rules.

    The caller is responsible for aligning timeframe, horizon, and exclusion. This function refuses
    the difference when either sample is too small.
    """
    if condition_rate is None or base_rate is None or sample < min_sample or base_sample < min_sample:
        return {
            "status": "NOT_COMPARABLE",
            "condition_positive_rate": None,
            "base_positive_rate": None,
            "difference": None,
            "explanation": "NOT_COMPARABLE. A difference is withheld unless both the de-clustered condition sample and the same-timeframe base rate have at least %d observations."
            % min_sample,
        }
    difference = condition_rate - base_rate
    if abs(difference) < BASE_RATE_SEPARATION:
        text = "The positive-return frequency is within 5 percentage points of the unconditional base rate. That is not a meaningful separation in this sample."
    else:
        text = (
            "The positive-return frequency differs from the unconditional base rate by %.1f percentage points in this stored sample. "
            "That comparison is not a forecast, and a rate above 50%% is not by itself evidence that price is more likely to rise."
            % (difference * 100.0)
        )
    return {
        "status": "OK",
        "condition_positive_rate": condition_rate,
        "base_positive_rate": base_rate,
        "difference": difference,
        "explanation": text,
    }


def block_bootstrap(
    values: list[float],
    seed: int,
    block_length: int,
    draws: int,
    sampling_interval_seconds: int | None = None,
) -> dict | None:
    """Circular block bootstrap of one observed sample. The result is a distribution of resampled medians.

    `block_length` counts observations in this sample, not minutes. For a de-clustered horizon
    sample the observations are one horizon apart, so the block spans `block_length` horizons.
    """
    if len(values) < 2 or block_length < 1 or draws < 1 or block_length >= len(values):
        return None
    rng = random.Random(seed)
    size = len(values)
    medians = []
    for _ in range(draws):
        sample = []
        while len(sample) < size:
            start = rng.randrange(size)
            for step in range(block_length):
                sample.append(values[(start + step) % size])
                if len(sample) == size:
                    break
        medians.append(quantile(sample, 0.5))
    span = None if sampling_interval_seconds is None else block_length * sampling_interval_seconds
    return {
        "seed": seed,
        "block_length": block_length,
        "block_unit": "de-clustered observations",
        "sampling_interval_seconds": sampling_interval_seconds,
        "block_span_seconds": span,
        "resamples": draws,
        "historical_sample_size": size,
        "median_of_resampled_medians": quantile(medians, 0.5),
        "p10_of_resampled_medians": quantile(medians, 0.1),
        "p90_of_resampled_medians": quantile(medians, 0.9),
        "note": "Resampling reflects the observed historical sample. These quantiles are not a calibrated probability of future market movement.",
    }


def follow_through(median_return: float | None) -> str:
    if median_return is None:
        return "INSUFFICIENT_HISTORY"
    if median_return < 0:
        return "continued_downward"
    if median_return > 0:
        return "reversed"
    return "stabilized"
