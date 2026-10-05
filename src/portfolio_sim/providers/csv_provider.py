"""CSV provider: read price history from local files, one file per symbol.

File name: <directory>/<symbol>.csv, with a date column (first column, or
'Date') and a price column ('Adj Close', 'Close' or 'Price'; else the second
column). Handy to plug in data you downloaded yourself (e.g. from a bank or
an index provider) and for fully offline runs.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .base import DataError, clean_series

_PRICE_COLUMNS = ("Adj Close", "Adj_Close", "Close", "Price", "Value")


class CsvProvider:
    name = "csv"

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def get_prices(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
        path = self.directory / f"{symbol}.csv"
        if not path.exists():
            raise DataError(f"CSV file not found: {path}")
        df = pd.read_csv(path)
        date_col = "Date" if "Date" in df.columns else df.columns[0]
        price_col = next((c for c in _PRICE_COLUMNS if c in df.columns), None)
        if price_col is None:
            if df.shape[1] < 2:
                raise DataError(f"{path} needs a date column and a price column.")
            price_col = df.columns[1]
        return clean_series(
            pd.Series(pd.to_numeric(df[price_col], errors="coerce").values,
                      index=pd.to_datetime(df[date_col])),
            symbol,
        )
