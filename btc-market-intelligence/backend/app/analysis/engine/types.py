"""Shared data types for the verdict engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.analysis.definitions import Bar

ENGINE_VERSION = "verdict-v1"

STATUS_OK = "OK"
STATUS_INSUFFICIENT = "INSUFFICIENT_DATA"
STATUS_UNAVAILABLE = "UNAVAILABLE"

# Evidence categories. Fusion caps the total contribution of each.
CAT_STRUCTURE = "structure"
CAT_LIQUIDITY = "liquidity_smc"
CAT_LEVELS = "support_resistance"
CAT_MOMENTUM = "momentum"
CAT_VOLUME = "volume_derivatives"
CAT_PATTERNS = "patterns"
CAT_SENTIMENT = "sentiment"
CAT_SESSIONS = "sessions"
CAT_EMPIRICAL = "empirical_prior"

# Regime gates. An evidence item tagged with a gate is suppressed in regimes that do not fit it.
GATE_ANY = "any"
GATE_TREND = "trend_continuation"
GATE_MEAN_REVERSION = "mean_reversion"

REGIME_TREND_UP = "TREND_UP"
REGIME_TREND_DOWN = "TREND_DOWN"
REGIME_RANGE = "RANGE"
REGIME_COMPRESSION = "COMPRESSION"
REGIME_HIGH_VOL = "HIGH_VOL"
REGIME_UNKNOWN = "UNKNOWN"

DISCLAIMER = (
    "Probabilities are derived from stored closed-candle history and the current market state. "
    "They are estimates, not guarantees, and this output is not financial advice."
)


@dataclass(frozen=True)
class Evidence:
    """One directional observation. direction is -1 (bearish) to +1 (bullish); strength is 0 to 1."""

    category: str
    name: str
    direction: float
    strength: float
    timeframe: str
    rationale: str
    regime_gate: str = GATE_ANY
    freshness: float = 1.0

    def signed(self) -> float:
        return max(-1.0, min(1.0, self.direction)) * max(0.0, min(1.0, self.strength)) * max(0.0, min(1.0, self.freshness))


@dataclass
class LayerResult:
    name: str
    status: str = STATUS_OK
    summary: str = ""
    findings: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_ms: float | None = None

    def add(self, item: Evidence) -> None:
        self.evidence.append(item)

    def public(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "findings": self.findings,
            "evidence": [
                {
                    "name": item.name,
                    "category": item.category,
                    "timeframe": item.timeframe,
                    "direction": round(item.direction, 4),
                    "strength": round(item.strength, 4),
                    "regime_gate": item.regime_gate,
                    "rationale": item.rationale,
                }
                for item in self.evidence
            ],
            "warnings": self.warnings,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class Position:
    side: str  # "long" or "short"
    entry_price: float
    size: float | None = None


@dataclass
class AnalyzeRequest:
    focus_timeframe: str = "15m"
    horizon: str = "4h"
    position: Position | None = None
    account_size: float | None = None
    risk_percent: float = 1.0


@dataclass
class MarketData:
    """Everything the layers are allowed to read. Built once per analysis."""

    bars: dict[str, list[Bar]]
    minute_bars: list[Bar]
    now: datetime
    derivatives: dict | None = None
    health: dict | None = None
    empirical: dict | None = None  # {condition: {horizon: {positive_rate, sample_size, base_positive_rate, base_sample_size}}}
    product: dict | None = None
    sentiment: dict | None = None
    correlation: dict | None = None

    def series(self, timeframe: str) -> list[Bar]:
        return self.bars.get(timeframe, [])

    def close(self) -> float | None:
        for name in ("1m", "5m", "15m", "1h", "4h", "1d"):
            bars = self.bars.get(name)
            if bars:
                return bars[-1].close
        return None


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
