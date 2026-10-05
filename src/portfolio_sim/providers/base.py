"""Provider interface: anything that can return a price series for a symbol."""
from __future__ import annotations

from typing import Protocol

import pandas as pd


class DataError(RuntimeError):
    """Raised when market data cannot be fetched or is unusable."""


class PriceProvider(Protocol):
    name: str

    def get_prices(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
        """Return a price series (adjusted for dividends when possible).

        The result must be a float Series with a sorted, tz-naive DatetimeIndex,
        no NaN, covering at least [start, end] when the source has that history.
        """
        ...


def clean_series(s: pd.Series, symbol: str) -> pd.Series:
    s = pd.Series(s).astype(float).dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
    s.name = symbol
    if s.empty:
        raise DataError(f"No data returned for {symbol!r}.")
    return s
