"""On-disk cache wrapper so repeated runs don't hit the network."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from .base import PriceProvider, clean_series


class CachedProvider:
    def __init__(self, inner: PriceProvider, cache_dir: str | Path) -> None:
        self.inner = inner
        self.name = inner.name
        self.cache_dir = Path(cache_dir)

    def _paths(self, symbol: str) -> tuple[Path, Path]:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", symbol)
        base = self.cache_dir / f"{self.inner.name}_{safe}"
        return base.with_suffix(".csv"), base.with_suffix(".json")

    def get_prices(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
        data_path, meta_path = self._paths(symbol)
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        if data_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if pd.Timestamp(meta["start"]) <= start and pd.Timestamp(meta["end"]) >= end:
                df = pd.read_csv(data_path, index_col=0, parse_dates=True)
                return clean_series(df.iloc[:, 0], symbol)
        series = self.inner.get_prices(symbol, start, end)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        series.to_csv(data_path, header=["price"])
        meta_path.write_text(json.dumps({"start": str(start.date()), "end": str(end.date())}))
        return series
