"""Simulation engine: step through time and track the portfolio value.

Model
-----
* The initial amount is invested on the start date according to the target allocation.
* With a monthly contribution, the simulation runs month by month: each month the
  contribution is invested according to the target allocation. Without one, it runs
  directly on the reporting step.
* With rebalancing, the portfolio is brought back to the target weights at every
  reporting step.
* Results are reported at each step date (start, start + step, ...).
* Performance is measured two ways: a money-weighted annualized return (IRR, which
  accounts for when money was added) and a time-weighted index (used for volatility,
  drawdown and per-period returns, unaffected by contributions).
* With an inflation index, each date also gets the purchasing-power value of the
  portfolio (in start-date money) and the "indexed capital": what the money paid in
  would be worth if it merely followed inflation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .models import SimulationConfig
from .providers import DataError, PriceProvider, build_provider

# A price series may end a few days before the requested date (weekends, holidays).
# Beyond this tolerance we consider the data stale.
_STALE_TOLERANCE = pd.Timedelta(days=10)
# A monthly index dated on the 1st of a month stays valid for the whole month.
_MONTHLY_TOLERANCE = pd.Timedelta(days=31)


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


def price_at(series: pd.Series, when: pd.Timestamp, label: str,
             tolerance: pd.Timedelta = _STALE_TOLERANCE) -> float:
    """Last known value on or before `when`; raises a clear error if unavailable."""
    if when < series.index[0]:
        raise DataError(
            f"{label}: no data before {series.index[0].date()} "
            f"(needed {when.date()}). Pick a later start date or another data source."
        )
    if when > series.index[-1] + tolerance:
        raise DataError(f"{label}: data ends on {series.index[-1].date()}, before {when.date()}.")
    return float(series.asof(when))


def irr(years: list[float], flows: list[float]) -> float:
    """Annualized money-weighted return: r such that sum(flow * (1+r)^-t) = 0 (bisection)."""
    def f(r: float) -> float:
        return sum(cf * (1.0 + r) ** (-t) for t, cf in zip(years, flows))

    lo, hi = -0.99, 10.0
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = f(mid)
        if flo * fm <= 0:
            hi = mid
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


@dataclass
class SimulationResult:
    config: SimulationConfig
    table: pd.DataFrame  # index = report dates; asset columns + total, return, invested (+ real columns)
    summary: dict = field(default_factory=dict)  # IRR etc., computed on the full internal path

    def metrics(self) -> dict[str, float]:
        t = self.table
        years = (t.index[-1] - t.index[0]).days / 365.25
        final, invested = float(t["total"].iloc[-1]), float(t["invested"].iloc[-1])
        rets = t["return"].dropna()
        periods_per_year = 12 / self.config.step_months
        vol = float(rets.std(ddof=1) * np.sqrt(periods_per_year)) if len(rets) >= 2 else float("nan")
        drawdown = t["twr_index"] / t["twr_index"].cummax() - 1
        out = {
            "initial_value": float(t["total"].iloc[0]),
            "final_value": final,
            "invested": invested,
            "gain": final - invested,
            "total_return": final / invested - 1,  # on the capital actually paid in
            "cagr": self.summary["irr"],  # money-weighted; equals the usual CAGR without contributions
            "volatility": vol,
            "max_drawdown": float(drawdown.min()),
            "best_period": float(rets.max()),
            "worst_period": float(rets.min()),
            "years": years,
        }
        if "real_total" in t:
            out.update(
                real_final_value=float(t["real_total"].iloc[-1]),
                real_cagr=self.summary["real_irr"],
                inflation_cagr=self.summary["inflation_cagr"],
                indexed_capital=float(t["indexed_capital"].iloc[-1]),
                vs_indexed=final / float(t["indexed_capital"].iloc[-1]) - 1,
            )
        return out


def simulate(
    config: SimulationConfig,
    provider: PriceProvider | None = None,
    *,
    cache_dir: str | None = ".cache",
    csv_dir: str = "data",
) -> SimulationResult:
    """Run the simulation.

    `provider` forces one data source for every series (e.g. a synthetic one for
    a dry-run); otherwise each asset uses the provider declared in its spec.
    """
    end = config.end_date or date.today()
    step = config.step_months
    contribution = config.monthly_contribution
    monthly = contribution > 0

    report_dates = step_dates(config.start_date, end, step)
    if len(report_dates) < 2:
        raise ValueError("The period is shorter than one step: nothing to simulate.")
    last_report = report_dates[-1]
    # internal grid: every month with contributions, otherwise just the steps
    dates = step_dates(config.start_date, last_report.date(), 1 if monthly else step)
    n = len(dates)
    is_step = [(i % step == 0) if monthly else True for i in range(n)]

    # 1. Fetch price histories and sample them on the internal grid.
    cache: dict[tuple[str, str], PriceProvider] = {}

    def source_for(provider_name: str) -> PriceProvider:
        if provider is not None:
            return provider
        return cache.setdefault((provider_name, ""), build_provider(provider_name, cache_dir=cache_dir, csv_dir=csv_dir))

    prices: dict[str, list[float]] = {}
    for key in config.allocation:
        spec = config.assets[key]
        series = source_for(spec.provider).get_prices(spec.symbol, dates[0], dates[-1])
        label = f"{spec.label} ({spec.symbol})"
        prices[key] = [price_at(series, d, label) for d in dates]

    cpi: list[float] | None = None
    if config.inflation:
        spec = config.inflation_series[config.inflation]
        series = source_for(spec.provider).get_prices(spec.symbol, dates[0], dates[-1])
        cpi = [price_at(series, d, f"{spec.label} ({spec.symbol})", _MONTHLY_TOLERANCE) for d in dates]

    # 2. Walk through time.
    keys = list(config.allocation)
    w = config.allocation
    units = {k: config.initial_amount * w[k] / prices[k][0] for k in keys}
    invested = config.initial_amount
    prev_total = config.initial_amount
    twr = 1.0
    indexed = config.initial_amount
    flows = [-config.initial_amount]
    real_flows = [-config.initial_amount]  # in start-date money
    rows = []
    for i, d in enumerate(dates):
        pre = sum(units[k] * prices[k][i] for k in keys)
        if i > 0:
            twr *= pre / prev_total
        added = contribution if (i > 0 and monthly) else 0.0
        if added:
            for k in keys:
                units[k] += added * w[k] / prices[k][i]
            invested += added
            flows.append(-added)
            real_flows.append(-added * cpi[0] / cpi[i] if cpi else -added)
        elif i > 0:
            flows.append(0.0)
            real_flows.append(0.0)
        total = pre + added
        if cpi and i > 0:
            indexed = indexed * cpi[i] / cpi[i - 1] + added
        if is_step[i]:
            row = {"date": d, **{k: units[k] * prices[k][i] for k in keys},
                   "total": total, "invested": invested, "twr_index": twr}
            if cpi:
                row["real_total"] = total * cpi[0] / cpi[i]
                row["indexed_capital"] = indexed
            rows.append(row)
            if config.rebalance:  # back to target weights, after recording the period-end values
                units = {k: total * w[k] / prices[k][i] for k in keys}
        prev_total = total

    # 3. Summary on the whole path (flows at every internal date).
    years = [(d - dates[0]).days / 365.25 for d in dates]
    flows[-1] += total
    real_flows[-1] += total * cpi[0] / cpi[-1] if cpi else total
    summary = {"irr": irr(years, flows)}
    if cpi:
        summary["real_irr"] = irr(years, real_flows)
        span = years[-1]
        summary["inflation_cagr"] = (cpi[-1] / cpi[0]) ** (1 / span) - 1 if span > 0 else float("nan")

    table = pd.DataFrame(rows).set_index("date")
    table["return"] = table["twr_index"].pct_change()
    return SimulationResult(config=config, table=table, summary=summary)
