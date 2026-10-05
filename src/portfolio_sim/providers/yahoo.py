"""Yahoo Finance provider (public chart endpoint, no API key, standard library only)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from .base import DataError, clean_series

_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


class YahooProvider:
    name = "yahoo"

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def _fetch_json(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (portfolio-simulator)"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raise DataError(f"Yahoo Finance HTTP {exc.code} for {url}") from exc
        except urllib.error.URLError as exc:
            raise DataError(f"Cannot reach Yahoo Finance: {exc.reason}") from exc

    def get_prices(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
        p1 = int((pd.Timestamp(start) - pd.Timedelta(days=15)).timestamp())
        p2 = int((pd.Timestamp(end) + pd.Timedelta(days=2)).timestamp())
        query = urllib.parse.urlencode(
            {"period1": p1, "period2": p2, "interval": "1d",
             "events": "div,splits", "includeAdjustedClose": "true"}
        )
        url = _URL.format(symbol=urllib.parse.quote(symbol)) + "?" + query
        payload = self._fetch_json(url)
        return self.parse_payload(payload, symbol)

    @staticmethod
    def parse_payload(payload: dict, symbol: str) -> pd.Series:
        chart = payload.get("chart", {})
        if chart.get("error") or not chart.get("result"):
            raise DataError(f"Yahoo Finance returned no data for {symbol!r}: {chart.get('error')}")
        result = chart["result"][0]
        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators", {})
        adj = (indicators.get("adjclose") or [{}])[0].get("adjclose")
        values = adj if adj is not None else (indicators.get("quote") or [{}])[0].get("close")
        if not timestamps or values is None:
            raise DataError(f"Yahoo Finance returned an empty series for {symbol!r}.")
        offset = int(result.get("meta", {}).get("gmtoffset", 0))
        index = pd.to_datetime(pd.Series(timestamps) + offset, unit="s")
        return clean_series(pd.Series(values, index=index), symbol)
