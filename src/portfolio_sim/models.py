"""Data model: asset definitions, simulation configuration, parsing helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Mapping


@dataclass(frozen=True)
class AssetSpec:
    """Where to find the price history of one asset class."""

    key: str  # internal id, e.g. "us_equity"
    label: str  # human readable name
    provider: str  # "yahoo", "fred", "csv" or "synthetic"
    symbol: str  # ticker / series id / csv file stem


# Default proxies for each asset class. Everything can be overridden in the
# YAML config. All Yahoo series are total-return (dividends reinvested) or
# adjusted-close series. Note that most of them are expressed in USD.
DEFAULT_ASSETS: dict[str, AssetSpec] = {
    a.key: a
    for a in [
        AssetSpec("us_equity", "Actions US", "yahoo", "SPY"),  # S&P 500 ETF (1993+)
        AssetSpec("eu_equity", "Actions EU", "yahoo", "EXSA.DE"),  # STOXX Europe 600 UCITS ETF (2000+)
        AssetSpec("gold", "Or", "yahoo", "GC=F"),  # Gold futures (2000+)
        AssetSpec("gov_debt", "Dette d'Etat", "yahoo", "IEF"),  # US Treasuries 7-10y ETF (2002+)
        AssetSpec("bonds", "Obligations", "yahoo", "LQD"),  # US investment-grade corporate bonds ETF (2002+)
        AssetSpec("real_estate", "Immobilier", "yahoo", "VNQ"),  # US listed real estate (REITs) ETF (2004+)
    ]
}

DEFAULT_ALLOCATION: dict[str, float] = {
    "us_equity": 0.30,
    "eu_equity": 0.30,
    "gold": 0.10,
    "gov_debt": 0.10,
    "bonds": 0.10,
    "real_estate": 0.10,
}

_STEP_RE = re.compile(r"^\s*(\d+)\s*([ymq])\s*$", re.IGNORECASE)


def parse_step(value: str | int) -> int:
    """Convert a step like '1y', '6m', '1q', '12m' (or an int of months) to months."""
    if isinstance(value, int):
        months = value
    else:
        m = _STEP_RE.match(str(value))
        if not m:
            raise ValueError(f"Invalid step {value!r}: use e.g. '1y', '6m', '1q', '1m'.")
        n, unit = int(m.group(1)), m.group(2).lower()
        months = n * {"y": 12, "q": 3, "m": 1}[unit]
    if months < 1:
        raise ValueError("Step must be at least one month.")
    return months


def normalize_allocation(raw: Mapping[str, float]) -> dict[str, float]:
    """Accept weights as fractions (sum 1) or percentages (sum 100); return fractions."""
    weights = {k: float(v) for k, v in raw.items()}
    if any(w < 0 for w in weights.values()):
        raise ValueError("Allocation weights cannot be negative.")
    total = sum(weights.values())
    if abs(total - 100.0) < 1e-6:
        weights = {k: w / 100.0 for k, w in weights.items()}
        total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(
            f"Allocation must sum to 100% (got {total * 100:.2f}%)."
        )
    return {k: w for k, w in weights.items() if w > 0}


@dataclass
class SimulationConfig:
    initial_amount: float
    start_date: date
    allocation: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_ALLOCATION))
    end_date: date | None = None  # defaults to today
    step_months: int = 12
    rebalance: bool = True  # bring weights back to target at each step
    assets: dict[str, AssetSpec] = field(default_factory=lambda: dict(DEFAULT_ASSETS))

    def __post_init__(self) -> None:
        self.allocation = normalize_allocation(self.allocation)
        if self.initial_amount <= 0:
            raise ValueError("Initial amount must be positive.")
        if isinstance(self.step_months, str):
            self.step_months = parse_step(self.step_months)
        unknown = set(self.allocation) - set(self.assets)
        if unknown:
            raise ValueError(
                f"No data source defined for: {', '.join(sorted(unknown))}. "
                f"Known assets: {', '.join(sorted(self.assets))}."
            )
        if self.end_date is not None and self.end_date <= self.start_date:
            raise ValueError("End date must be after start date.")
