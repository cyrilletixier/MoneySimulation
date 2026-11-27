"""Export price histories to a single JSON file consumed by the web front (docs/).

The web page runs the simulation in the browser, so it only needs the raw
price series. This module downloads them (with the same providers as the CLI)
and writes them in a compact format. It is run by the GitHub Actions workflow
to refresh the data, and can be run locally too.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from .models import DEFAULT_ASSETS, INFLATION_SERIES, AssetSpec
from .providers import DataError, PriceProvider, build_provider

DEFAULT_START = "1990-01-01"


def _fetch_with_retry(provider: PriceProvider, spec: AssetSpec, start: pd.Timestamp,
                      end: pd.Timestamp, retries: int, pause: float) -> pd.Series:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return provider.get_prices(spec.symbol, start, end)
        except DataError as exc:
            last = exc
            if attempt < retries:
                time.sleep(pause * attempt)
    raise DataError(f"{spec.label} ({spec.symbol}, {spec.provider}): {last}")


def _entry(spec: AssetSpec, provider_name: str, series: pd.Series) -> dict:
    return {
        "label": spec.label,
        "symbol": spec.symbol,
        "provider": provider_name,
        "start": series.index[0].strftime("%Y-%m-%d"),
        "end": series.index[-1].strftime("%Y-%m-%d"),
        "dates": list(series.index.strftime("%Y-%m-%d")),
        "prices": [round(float(p), 4) for p in series.to_numpy()],
    }


def export_prices(
    out: str | Path,
    *,
    provider_name: str | None = None,
    start: str = DEFAULT_START,
    end: date | None = None,
    assets: dict[str, AssetSpec] | None = None,
    inflation: dict[str, AssetSpec] | None = None,
    cache_dir: str | None = None,
    csv_dir: str = "data",
    retries: int = 3,
    pause: float = 2.0,
) -> dict:
    """Download every series and write it to `out`. Returns the payload.

    Raises DataError (and writes nothing) if any *asset* cannot be fetched, so a
    scheduled refresh never publishes a partial or silently degraded file.
    Inflation series are optional: one that fails is left out and reported in
    payload["warnings"], and the site simply hides that comparison.
    """
    assets = DEFAULT_ASSETS if assets is None else assets
    inflation = INFLATION_SERIES if inflation is None else inflation
    end = end or date.today()
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    providers: dict[str, PriceProvider] = {}

    def source(name: str) -> PriceProvider:
        return providers.setdefault(name, build_provider(name, cache_dir=cache_dir, csv_dir=csv_dir))

    payload_assets: dict[str, dict] = {}
    errors: list[str] = []
    for key, spec in assets.items():
        name = provider_name or spec.provider
        try:
            series = _fetch_with_retry(source(name), spec, start_ts, end_ts, retries, pause)
        except DataError as exc:
            errors.append(str(exc))
            continue
        series = series[series.index <= end_ts]  # synthetic data extends into the future
        if series.empty:
            errors.append(f"{spec.label} ({spec.symbol}): no data before {end}.")
            continue
        payload_assets[key] = _entry(spec, name, series)

    if errors:
        raise DataError("Export aborted, data unavailable for:\n  - " + "\n  - ".join(errors))

    payload_inflation: dict[str, dict] = {}
    warnings: list[str] = []
    for key, spec in inflation.items():
        name = provider_name or spec.provider
        try:
            series = _fetch_with_retry(source(name), spec, start_ts, end_ts, retries, pause)
        except DataError as exc:
            warnings.append(f"Inflation series skipped: {exc}")
            continue
        series = series[series.index <= end_ts]
        series = series.resample("MS").first().dropna()  # one value per month, dated on the 1st
        if series.empty:
            warnings.append(f"Inflation series skipped: {spec.label} has no data before {end}.")
            continue
        payload_inflation[key] = _entry(spec, name, series)

    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "synthetic": provider_name == "synthetic",
        "assets": payload_assets,
        "inflation": payload_inflation,
        "warnings": warnings,
    }
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return payload
