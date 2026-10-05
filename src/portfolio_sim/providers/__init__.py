from __future__ import annotations

from pathlib import Path

from .base import DataError, PriceProvider
from .cache import CachedProvider
from .csv_provider import CsvProvider
from .fred import FredProvider
from .synthetic import SyntheticProvider
from .yahoo import YahooProvider

__all__ = [
    "DataError", "PriceProvider", "CachedProvider", "CsvProvider",
    "FredProvider", "SyntheticProvider", "YahooProvider", "build_provider",
]


def build_provider(
    name: str,
    *,
    cache_dir: str | Path | None = ".cache",
    csv_dir: str | Path = "data",
) -> PriceProvider:
    """Create a provider by name. Network providers are wrapped in a disk cache."""
    name = name.lower()
    if name == "yahoo":
        inner: PriceProvider = YahooProvider()
    elif name == "fred":
        inner = FredProvider()
    elif name == "csv":
        return CsvProvider(csv_dir)
    elif name == "synthetic":
        return SyntheticProvider()
    else:
        raise ValueError(f"Unknown provider {name!r} (yahoo, fred, csv, synthetic).")
    return CachedProvider(inner, cache_dir) if cache_dir else inner
