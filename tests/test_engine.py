from datetime import date

import pandas as pd
import pytest

from portfolio_sim import SimulationConfig, simulate, step_dates
from portfolio_sim.models import AssetSpec, normalize_allocation, parse_step
from portfolio_sim.providers import DataError, SyntheticProvider


class FakeProvider:
    """Serves prices from a dict {symbol: {date_str: price}}."""

    name = "fake"

    def __init__(self, data):
        self.data = data

    def get_prices(self, symbol, start, end):
        d = self.data[symbol]
        return pd.Series(list(d.values()), index=pd.to_datetime(list(d.keys())), dtype=float)


def make_config(allocation, **kw):
    assets = {k: AssetSpec(k, k, "fake", k.upper()) for k in allocation}
    return SimulationConfig(initial_amount=1000, start_date=date(2020, 1, 1),
                            end_date=kw.pop("end_date", date(2022, 1, 1)),
                            allocation=allocation, assets=assets, **kw)


# A doubles each year, B stays flat.
DATA = {
    "A": {"2019-12-31": 100, "2020-12-31": 200, "2021-12-31": 400, "2022-01-01": 400},
    "B": {"2019-12-31": 100, "2020-12-31": 100, "2021-12-31": 100, "2022-01-01": 100},
}


def test_step_dates_no_day_drift():
    ds = step_dates(date(2020, 1, 31), date(2021, 1, 31), 1)
    assert ds[1] == pd.Timestamp("2020-02-29")
    assert ds[2] == pd.Timestamp("2020-03-31")  # not 03-29
    assert len(ds) == 13


def test_yearly_steps_include_start_and_end_inclusive():
    ds = step_dates(date(2020, 1, 1), date(2022, 1, 1), 12)
    assert [d.year for d in ds] == [2020, 2021, 2022]


def test_buy_and_hold_drifts():
    cfg = make_config({"a": 0.5, "b": 0.5}, rebalance=False)
    cfg.assets = {"a": AssetSpec("a", "a", "fake", "A"), "b": AssetSpec("b", "b", "fake", "B")}
    res = simulate(cfg, FakeProvider(DATA))
    t = res.table
    assert t["total"].iloc[0] == pytest.approx(1000)
    # after 1 year: A 500 -> 1000, B 500 stays -> 1500
    assert t["total"].iloc[1] == pytest.approx(1500)
    # after 2 years: A 4x -> 2000, B 500 -> 2500
    assert t["total"].iloc[2] == pytest.approx(2500)


def test_rebalancing_resets_weights_each_step():
    cfg = make_config({"a": 0.5, "b": 0.5}, rebalance=True)
    cfg.assets = {"a": AssetSpec("a", "a", "fake", "A"), "b": AssetSpec("b", "b", "fake", "B")}
    res = simulate(cfg, FakeProvider(DATA))
    t = res.table
    assert t["total"].iloc[1] == pytest.approx(1500)  # same first year
    # rebalanced to 750/750 -> A doubles: 1500 + 750 = 2250
    assert t["total"].iloc[2] == pytest.approx(2250)
    assert t["return"].iloc[2] == pytest.approx(0.5)


def test_metrics():
    cfg = make_config({"a": 1.0})
    cfg.assets = {"a": AssetSpec("a", "a", "fake", "A")}
    res = simulate(cfg, FakeProvider(DATA))
    m = res.metrics()
    assert m["final_value"] == pytest.approx(4000)
    assert m["total_return"] == pytest.approx(3.0)
    assert m["cagr"] == pytest.approx(1.0, rel=0.01)  # doubles each year
    assert m["max_drawdown"] == pytest.approx(0.0)


def test_error_when_start_before_history():
    cfg = make_config({"a": 1.0})
    cfg.assets = {"a": AssetSpec("a", "a", "fake", "A")}
    cfg.start_date = date(2010, 1, 1)
    with pytest.raises(DataError, match="no data before"):
        simulate(cfg, FakeProvider(DATA))


def test_error_when_data_is_stale():
    cfg = make_config({"a": 1.0}, end_date=date(2025, 1, 1))
    cfg.assets = {"a": AssetSpec("a", "a", "fake", "A")}
    with pytest.raises(DataError, match="data ends"):
        simulate(cfg, FakeProvider(DATA))


def test_period_shorter_than_step():
    cfg = make_config({"a": 1.0}, end_date=date(2020, 6, 1))
    cfg.assets = {"a": AssetSpec("a", "a", "fake", "A")}
    with pytest.raises(ValueError, match="shorter than one step"):
        simulate(cfg, FakeProvider(DATA))


def test_allocation_percent_or_fraction():
    assert normalize_allocation({"a": 60, "b": 40}) == {"a": 0.6, "b": 0.4}
    assert normalize_allocation({"a": 0.6, "b": 0.4}) == {"a": 0.6, "b": 0.4}
    with pytest.raises(ValueError, match="sum to 100"):
        normalize_allocation({"a": 60, "b": 30})


def test_parse_step():
    assert parse_step("1y") == 12
    assert parse_step("1q") == 3
    assert parse_step("6m") == 6
    with pytest.raises(ValueError):
        parse_step("weekly")


def test_default_strategy_end_to_end_with_synthetic_data():
    cfg = SimulationConfig(initial_amount=100_000, start_date=date(2010, 1, 4), end_date=date(2020, 1, 4))
    res = simulate(cfg, SyntheticProvider())
    assert len(res.table) == 11
    assert res.table["total"].iloc[0] == pytest.approx(100_000)
    first = res.table.iloc[0]
    assert first["us_equity"] == pytest.approx(30_000)
    assert first["real_estate"] == pytest.approx(10_000)
    # deterministic
    res2 = simulate(cfg, SyntheticProvider())
    pd.testing.assert_frame_equal(res.table, res2.table)


# ---------- monthly contributions ----------
FLAT = {"A": {"2019-12-31": 100, "2022-01-01": 100}}


def flat_config(**kw):
    cfg = make_config({"a": 1.0}, **kw)
    cfg.assets = {"a": AssetSpec("a", "a", "fake", "A")}
    return cfg


def test_monthly_contribution_reports_on_steps_but_invests_monthly():
    res = simulate(flat_config(monthly_contribution=100), FakeProvider(FLAT))
    t = res.table
    assert len(t) == 3  # yearly report dates
    assert list(t["invested"]) == pytest.approx([1000, 2200, 3400])  # 12 then 24 monthly payments
    assert list(t["total"]) == pytest.approx([1000, 2200, 3400])  # flat prices: no gain
    assert res.metrics()["cagr"] == pytest.approx(0.0, abs=1e-9)
    assert res.metrics()["gain"] == pytest.approx(0.0, abs=1e-9)


def test_contributions_do_not_count_as_performance():
    # A doubles in 2020 then stays flat: contributions made after the jump must not inflate returns
    data = {"A": {"2019-12-31": 100, "2020-01-31": 100, "2020-02-01": 200, "2022-01-01": 200}}
    res = simulate(flat_config(monthly_contribution=1000), FakeProvider(data))
    t = res.table
    assert t["twr_index"].iloc[-1] == pytest.approx(2.0)  # price doubled once, whatever was added
    assert t["return"].iloc[2] == pytest.approx(0.0, abs=1e-9)
    m = res.metrics()
    assert m["max_drawdown"] == pytest.approx(0.0, abs=1e-9)
    # money-weighted return is below the price-only return because most money came after the jump
    assert m["cagr"] < 2.0 ** (1 / 2) - 1


def test_contribution_amount_validated():
    with pytest.raises(ValueError, match="cannot be negative"):
        flat_config(monthly_contribution=-5)


# ---------- inflation ----------
CPI_SPEC = {"x": AssetSpec("x", "CPI", "fake", "CPI")}


def monthly_cpi_doubling_in_two_years():
    idx = pd.date_range("2019-12-01", "2022-01-01", freq="MS")
    return {str(d.date()): 100 * 2 ** (((d.year - 2019) * 12 + d.month - 12) / 24) for d in idx}


def test_inflation_comparison():
    cfg = flat_config(inflation="x", inflation_series=CPI_SPEC)
    res = simulate(cfg, FakeProvider({**FLAT, "CPI": monthly_cpi_doubling_in_two_years()}))
    m = res.metrics()
    assert m["final_value"] == pytest.approx(1000)  # flat asset
    assert m["real_final_value"] == pytest.approx(500)  # prices doubled: half the purchasing power
    assert m["indexed_capital"] == pytest.approx(2000)
    assert m["vs_indexed"] == pytest.approx(-0.5)
    assert m["inflation_cagr"] == pytest.approx(2 ** 0.5 - 1, rel=1e-3)
    assert m["real_cagr"] == pytest.approx(0.5 ** 0.5 - 1, rel=1e-3)


def test_inflation_with_contributions_indexes_each_payment():
    cfg = flat_config(inflation="x", monthly_contribution=100, inflation_series=CPI_SPEC)
    res = simulate(cfg, FakeProvider({**FLAT, "CPI": monthly_cpi_doubling_in_two_years()}))
    t = res.table
    assert t["indexed_capital"].iloc[0] == pytest.approx(1000)
    # every euro paid in keeps pace with prices, so indexed capital always exceeds the amount paid in
    assert (t["indexed_capital"].iloc[1:] > t["invested"].iloc[1:]).all()
    assert t["real_total"].iloc[-1] < t["total"].iloc[-1]


def test_unknown_inflation_series():
    with pytest.raises(ValueError, match="Unknown inflation"):
        flat_config(inflation="mars")


def test_inflation_data_too_short():
    cfg = flat_config(inflation="x", inflation_series=CPI_SPEC)
    short = {k: v for k, v in monthly_cpi_doubling_in_two_years().items() if k < "2021-01-01"}
    with pytest.raises(DataError, match="data ends"):
        simulate(cfg, FakeProvider({**FLAT, "CPI": short}))


def test_bitcoin_is_a_default_asset_and_works_with_synthetic_data():
    cfg = SimulationConfig(initial_amount=10_000, start_date=date(2016, 1, 4), end_date=date(2020, 1, 4),
                           allocation={"us_equity": 0.9, "bitcoin": 0.1}, monthly_contribution=200, inflation="eu")
    res = simulate(cfg, SyntheticProvider())
    assert {"us_equity", "bitcoin", "real_total", "indexed_capital"} <= set(res.table.columns)
    assert res.table["bitcoin"].iloc[0] == pytest.approx(1000)
