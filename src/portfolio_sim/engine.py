"""Simulation engine: step through time and track the portfolio value."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from .models import SimulationConfig
from .providers import DataError, PriceProvider, build_provider

# A series may end a few days before the requested date (weekends, holidays,
# publication lag). Beyond this tolerance we consider the data stale.
_STALE_TOLERANCE = pd.Timedelta(days=10)


def step_dates(start: date, end: date, step_months: int) -> list[pd.Timestamp]:
    """Dates start, start+step, start+2*step, ... not after `end`.

    Each date is computed from `start` (not from the previous date) so that
    e.g. a 31st does not drift to the 28th after passing through February.
    """
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    dates: list[pd.Timestamp] = []
    k = 0
    while True:
        d = start_ts + pd.DateOffset(months=step_months * k)
        if d > end_ts:
            break
        dates.append(d)
        k += 1
    return dates


def price_at(series: pd.Series, when: pd.Timestamp, label: str) -> float:
    """Last known price on or before `when`; raises a clear error if unavailable."""
    if when < series.index[0]:
        raise DataError(
            f"{label}: no data before {series.index[0].date()} "
            f"(needed {when.date()}). Pick a later start date or another data source."
        )
    if when > series.index[-1] + _STALE_TOLERANCE:
        raise DataError(f"{label}: data ends on {series.index[-1].date()}, before {when.date()}.")
    return float(series.asof(when))


@dataclass
class SimulationResult:
    config: SimulationConfig
    table: pd.DataFrame  # index = dates; columns = asset keys + "total" + "return"

    def metrics(self) -> dict[str, float]:
        total = self.table["total"]
        years = (total.index[-1] - total.index[0]).days / 365.25
        final, initial = float(total.iloc[-1]), float(total.iloc[0])
        rets = self.table["return"].dropna()
        periods_per_year = 12 / self.config.step_months
        vol = float(rets.std(ddof=1) * np.sqrt(periods_per_year)) if len(rets) >= 2 else float("nan")
        drawdown = total / total.cummax() - 1
        return {
            "initial_value": initial,
            "final_value": final,
            "total_return": final / initial - 1,
            "cagr": (final / initial) ** (1 / years) - 1 if years > 0 else float("nan"),
            "volatility": vol,
            "max_drawdown": float(drawdown.min()),
            "best_period": float(rets.max()),
            "worst_period": float(rets.min()),
            "years": years,
        }


def simulate(
    config: SimulationConfig,
    provider: PriceProvider | None = None,
    *,
    cache_dir: str | None = ".cache",
    csv_dir: str = "data",
) -> SimulationResult:
    """Run the simulation.

    `provider` forces one data source for every asset (e.g. a synthetic one for
    a dry-run); otherwise each asset uses the provider declared in its spec.
    """
    end = config.end_date or date.today()
    dates = step_dates(config.start_date, end, config.step_months)
    if len(dates) < 2:
        raise ValueError("The period is shorter than one step: nothing to simulate.")

    # 1. Fetch price histories and sample them at each simulation date.
    prices: dict[str, list[float]] = {}
    cache: dict[str, PriceProvider] = {}
    for key in config.allocation:
        spec = config.assets[key]
        if provider is not None:
            src = provider
        else:
            src = cache.setdefault(spec.provider, build_provider(spec.provider, cache_dir=cache_dir, csv_dir=csv_dir))
        series = src.get_prices(spec.symbol, dates[0], dates[-1])
        label = f"{spec.label} ({spec.symbol})"
        prices[key] = [price_at(series, d, label) for d in dates]

    # 2. Walk through time.
    keys = list(config.allocation)
    units = {k: config.initial_amount * config.allocation[k] / prices[k][0] for k in keys}
    rows = []
    for i, d in enumerate(dates):
        values = {k: units[k] * prices[k][i] for k in keys}
        total = sum(values.values())
        rows.append({"date": d, **values, "total": total})
        if config.rebalance:  # back to target weights, after recording the period-end values
            units = {k: total * config.allocation[k] / prices[k][i] for k in keys}

    table = pd.DataFrame(rows).set_index("date")
    table["return"] = table["total"].pct_change()
    return SimulationResult(config=config, table=table)
