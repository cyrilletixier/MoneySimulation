"""FRED provider (St. Louis Fed). Uses the public CSV endpoint, no API key needed.

Useful for index-like series, e.g. Case-Shiller US house prices (CSUSHPINSA)
or any other economic series. The series is returned as-is (no total-return
adjustment), so pick level/index series, not yields.
"""
from __future__ import annotations

import io
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from .base import DataError, clean_series

_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


class FredProvider:
    name = "fred"

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def _fetch_text(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (portfolio-simulator)"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise DataError(f"FRED HTTP {exc.code} for series in {url}") from exc
        except urllib.error.URLError as exc:
            raise DataError(f"Cannot reach FRED: {exc.reason}") from exc

    def get_prices(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
        query = urllib.parse.urlencode(
            {"id": symbol,
             "cosd": (pd.Timestamp(start) - pd.Timedelta(days=100)).strftime("%Y-%m-%d"),
             "coed": (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")}
        )
        return self.parse_csv(self._fetch_text(f"{_URL}?{query}"), symbol)

    @staticmethod
    def parse_csv(text: str, symbol: str) -> pd.Series:
        df = pd.read_csv(io.StringIO(text))
        if df.shape[1] < 2:
            raise DataError(f"Unexpected FRED response for {symbol!r}.")
        dates = pd.to_datetime(df.iloc[:, 0])
        values = pd.to_numeric(df.iloc[:, 1], errors="coerce")  # '.' marks missing values
        return clean_series(pd.Series(values.values, index=dates), symbol)
