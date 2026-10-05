"""Synthetic provider: deterministic random-walk prices for offline demos and tests.

NOT real data. Each symbol always yields the same series (seeded by its name),
independently of the requested date range.
"""
from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

from .base import clean_series

# symbol -> (annual drift, annual volatility). Loosely inspired by long-run
# behaviour of each asset class; only meant to make demos look plausible.
PARAMS: dict[str, tuple[float, float]] = {
    "SPY": (0.10, 0.17),
    "EXSA.DE": (0.07, 0.18),
    "GC=F": (0.07, 0.16),
    "IEF": (0.04, 0.07),
    "LQD": (0.05, 0.08),
    "VNQ": (0.08, 0.22),
}
_DEFAULT = (0.06, 0.15)
_FIRST_DAY = "1990-01-01"
_LAST_DAY = "2035-12-31"


class SyntheticProvider:
    name = "synthetic"

    def get_prices(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
        mu, sigma = PARAMS.get(symbol, _DEFAULT)
        days = pd.bdate_range(_FIRST_DAY, _LAST_DAY)
        rng = np.random.default_rng(zlib.crc32(symbol.encode()))
        dt = 1 / 252
        shocks = rng.standard_normal(len(days))
        log_ret = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * shocks
        prices = 100 * np.exp(np.cumsum(log_ret))
        return clean_series(pd.Series(prices, index=days), symbol)
