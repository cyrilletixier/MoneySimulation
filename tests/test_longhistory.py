import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from portfolio_sim import SimulationConfig, simulate
from portfolio_sim.export import export_prices
from portfolio_sim.longhistory import (
    LongHistoryBuilder,
    extend_backwards,
    parse_gold_monthly,
    parse_shiller,
    reinvest_dividends,
    treasury_index_from_yields,
)
from portfolio_sim.providers import DataError, SyntheticProvider


def shiller_csv(months=36, start="1990-01-01", price=100.0, dividend=4.0, rate=5.0, zero_tail=2):
    idx = pd.date_range(start, periods=months, freq="MS")
    df = pd.DataFrame({"Date": idx.strftime("%Y-%m-%d"), "SP500": price, "Dividend": dividend,
                       "Earnings": 5.0, "Consumer Price Index": 100.0, "Long Interest Rate": rate})
    if zero_tail:  # the real file ends with zero-filled rows
        df.loc[months - zero_tail:, ["Dividend", "Earnings", "Consumer Price Index", "Long Interest Rate"]] = 0.0
    return df.to_csv(index=False)


# ---------- parsing ----------
def test_parse_shiller_drops_zero_filled_tail_and_uses_month_start():
    sh = parse_shiller(shiller_csv(months=36, zero_tail=2))
    assert len(sh) == 34
    assert sh.index[0] == pd.Timestamp("1990-01-01")
    assert list(sh.columns) == ["price", "dividend", "rate"]


def test_parse_shiller_rejects_unexpected_format():
    with pytest.raises(DataError, match="columns"):
        parse_shiller("a,b\n1,2\n")


def test_parse_gold_monthly_dates_on_15th_and_from_1960():
    s = parse_gold_monthly("Date,Price\n1959-12,35.1\n1960-01,35.27\n1960-02,35.27\n")
    assert list(s.index) == [pd.Timestamp("1960-01-15"), pd.Timestamp("1960-02-15")]
    assert s.iloc[0] == 35.27


# ---------- reconstruction ----------
def test_reinvested_dividends_flat_price():
    sh = parse_shiller(shiller_csv(months=26, zero_tail=0, dividend=4.0, price=100.0))
    days = pd.bdate_range("1990-01-01", "1991-12-31")
    price = pd.Series(100.0, index=days)
    tr = reinvest_dividends(price, sh)
    # 4% a year paid out monthly and reinvested: (1 + 0.04/12)^12 - 1 = 4.07%
    one_year = tr[tr.index <= "1990-12-31"].iloc[-1] / tr.iloc[0]
    assert one_year == pytest.approx((1 + 0.04 / 12) ** 12, rel=2e-3)


def test_reinvested_dividends_follow_price_moves():
    sh = parse_shiller(shiller_csv(months=14, zero_tail=0, dividend=0.0001 + 3.0))
    days = pd.bdate_range("1990-01-01", "1990-12-31")
    price = pd.Series(np.linspace(100, 150, len(days)), index=days)
    tr = reinvest_dividends(price, sh)
    assert (tr / price).is_monotonic_increasing  # dividends only ever add to the price return
    assert tr.iloc[-1] / price.iloc[-1] > 1.025


def test_reinvest_requires_dividend_coverage():
    sh = parse_shiller(shiller_csv(months=12, zero_tail=0))
    price = pd.Series(100.0, index=pd.bdate_range("1989-06-01", "1989-12-29"))  # before Shiller's data
    with pytest.raises(DataError):
        reinvest_dividends(price, sh)


def test_treasury_index_constant_yield_earns_the_coupon():
    rate = pd.Series(5.0, index=pd.date_range("1990-01-01", periods=13, freq="MS"))
    idx = treasury_index_from_yields(rate)
    assert idx.iloc[-1] == pytest.approx((1 + 0.05 / 12) ** 12, rel=1e-9)
    assert idx.index[0] == pd.Timestamp("1990-01-15")


def test_treasury_index_loses_when_yields_rise_and_gains_when_they_fall():
    months = pd.date_range("1990-01-01", periods=3, freq="MS")
    up = treasury_index_from_yields(pd.Series([5.0, 6.0, 6.0], index=months))
    down = treasury_index_from_yields(pd.Series([5.0, 4.0, 4.0], index=months))
    assert -0.085 < up.iloc[1] / up.iloc[0] - 1 < -0.06  # ~ -duration (7.7) x 1% + coupon
    assert 0.06 < down.iloc[1] / down.iloc[0] - 1 < 0.09


# ---------- chaining ----------
def test_extend_backwards_rescales_and_cuts_at_the_join():
    prefix = pd.Series(np.arange(1.0, 11.0), index=pd.date_range("2000-01-03", periods=10, freq="B"))
    modern = pd.Series([200.0, 210.0], index=pd.to_datetime(["2000-01-10", "2000-01-11"]))
    before, join = extend_backwards(prefix, modern)
    assert join == pd.Timestamp("2000-01-10")
    assert before.index[-1] < join
    assert (before / before.iloc[0]).tolist() == pytest.approx((prefix[:5] / prefix.iloc[0]).tolist())
    # continuity: the value the prefix has on the join day equals the modern price
    assert prefix.asof(join) * (200.0 / prefix.asof(join)) == 200.0
    assert before.iloc[-1] == pytest.approx(prefix.iloc[4] * 200.0 / prefix.asof(join))


def test_extend_backwards_errors():
    modern = pd.Series([1.0, 1.1], index=pd.to_datetime(["2000-06-01", "2000-06-02"]))
    far = pd.Series([1.0, 1.1], index=pd.to_datetime(["1999-01-04", "1999-01-05"]))
    with pytest.raises(DataError, match="Gap"):
        extend_backwards(far, modern)
    late = pd.Series([1.0, 1.1], index=pd.to_datetime(["2001-01-04", "2001-01-05"]))
    with pytest.raises(DataError, match="no data before"):
        extend_backwards(late, modern)


# ---------- builder (offline, with stubbed sources) ----------
class StubPrices:
    name = "stub"

    def __init__(self):
        self.calls = []

    def get_prices(self, symbol, start, end):
        self.calls.append(symbol)
        days = pd.bdate_range("1990-01-01", "1993-12-31")
        return pd.Series(np.linspace(100, 130, len(days)), index=days, name=symbol)


def stub_fetch(url):
    return shiller_csv(months=60, zero_tail=0) if "s-and-p-500" in url else \
        "Date,Price\n" + "".join(f"{y}-{m:02d},{300 + y - 1990 + m / 12}\n" for y in (1990, 1991, 1992) for m in range(1, 13))


def modern_series(start="1992-06-01", periods=200, first=50.0):
    return pd.Series(np.linspace(first, first * 1.5, periods), index=pd.bdate_range(start, periods=periods), name="MOD")


@pytest.mark.parametrize("key,kind,label_part", [
    ("us_equity", "reconstructed", "S&P 500"),
    ("gold", "market", "Banque mondiale"),
    ("gov_debt", "reconstructed", "taux à 10 ans"),
    ("real_estate", "market", "VGSIX"),
])
def test_builder_extends_each_supported_asset(key, kind, label_part):
    builder = LongHistoryBuilder(StubPrices(), stub_fetch)
    modern = modern_series()
    full, payload = builder.extend(key, modern, "MOD", "Yahoo Finance")
    join = modern.index[0]
    assert payload["join"] == str(join.date())
    assert pd.Timestamp(payload["dates"][-1]) < join
    assert len(payload["dates"]) == len(payload["prices"]) > 10
    assert full.index.is_monotonic_increasing and full.index.is_unique
    assert full.iloc[len(payload["dates"])] == modern.iloc[0]  # modern part untouched
    first, second = payload["segments"]
    assert first["kind"] == kind and label_part in first["label"]
    assert second["kind"] == "market" and second["label"] == "MOD (Yahoo Finance)"
    assert first["to"] < second["from"]


def test_builder_unsupported_asset_and_download_failure():
    assert not LongHistoryBuilder.supports("bitcoin")
    assert not LongHistoryBuilder.supports("eu_equity")

    def broken(url):
        raise DataError("offline")

    with pytest.raises(DataError, match="offline"):
        LongHistoryBuilder(StubPrices(), broken).extend("us_equity", modern_series(), "MOD", "Yahoo Finance")


# ---------- engine ----------
def long_config(alloc, start, **kw):
    return SimulationConfig(initial_amount=10_000, start_date=start, end_date=date(2023, 1, 3),
                            allocation=alloc, long_history=True, **kw)


def test_long_history_lets_the_simulation_start_earlier():
    alloc = {"us_equity": 0.5, "gold": 0.25, "gov_debt": 0.25}
    cfg = long_config(alloc, date(1965, 1, 4))
    res = simulate(cfg, SyntheticProvider())
    assert res.table.index[0] == pd.Timestamp("1965-01-04")
    assert res.table["total"].iloc[0] == pytest.approx(10_000)
    short = SimulationConfig(initial_amount=10_000, start_date=date(1965, 1, 4), end_date=date(2023, 1, 3), allocation=alloc)
    with pytest.raises(DataError, match="no data before"):
        simulate(short, SyntheticProvider())


def test_long_history_is_still_bounded_by_assets_without_older_data():
    cfg = long_config({"us_equity": 0.5, "eu_equity": 0.5}, date(1990, 1, 2))
    with pytest.raises(DataError, match="Actions EU"):
        simulate(cfg, SyntheticProvider())


def test_long_history_leaves_recent_results_unchanged():
    alloc = {"us_equity": 0.6, "gold": 0.4}
    a = simulate(long_config(alloc, date(2010, 1, 4)), SyntheticProvider())
    b = simulate(SimulationConfig(initial_amount=10_000, start_date=date(2010, 1, 4), end_date=date(2023, 1, 3),
                                  allocation=alloc), SyntheticProvider())
    pd.testing.assert_frame_equal(a.table, b.table)


# ---------- export ----------
def test_export_carries_history_for_supported_assets_only(tmp_path):
    payload = export_prices(tmp_path / "p.json", provider_name="synthetic", end=date(2024, 6, 30))
    with_history = {k for k, a in payload["assets"].items() if "history" in a}
    assert with_history == {"us_equity", "gold", "gov_debt", "real_estate"}
    for key in with_history:
        a = payload["assets"][key]
        h = a["history"]
        assert h["dates"][-1] < h["join"] == a["dates"][0]
        assert h["segments"][0]["from"] == h["dates"][0]
    assert payload["warnings"] == []
    assert min(payload["inflation"]["us"]["dates"]) <= "1940-02-01"


def test_export_without_long_history(tmp_path):
    payload = export_prices(tmp_path / "p.json", provider_name="synthetic", end=date(2024, 6, 30), long_history=False)
    assert not any("history" in a for a in payload["assets"].values())
    json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))
