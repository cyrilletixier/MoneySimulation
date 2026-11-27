"""Long history: extend recent fund series backwards with older data.

Each recent series (an ETF that exists since 1993-2004) is chained to an older
one, rescaled so that the two agree on the day the recent series starts:

    us_equity    S&P 500 price index (Yahoo ^GSPC, daily, 1928+) plus the monthly dividend
                 yield from Robert Shiller's dataset, reinvested -> total return index.
                 Chained to SPY.
    gold         World Bank "Pink Sheet" monthly average price (1960+). Chained to GC=F.
    gov_debt     Rebuilt from Shiller's 10-year yield: a 10-year par bond bought each month,
                 valued one month later at the new yield (coupon + price change).
                 Chained to IEF.
    real_estate  Vanguard REIT Index fund (VGSIX, 1996+), same portfolio as VNQ. Chained to VNQ.

Not covered (no free long series found): Europe equities, corporate bonds, Bitcoin.

Every extended series carries its segments (period, source, kind) so the front can show which
periods are market data and which are reconstructed. Monthly-average sources are dated on the
15th of the month.
"""
from __future__ import annotations

import io
import time
import urllib.error
import urllib.request
from typing import Callable

import numpy as np
import pandas as pd

from .providers.base import DataError, PriceProvider, clean_series

# Mirrors on GitHub of Robert Shiller's monthly dataset and of the World Bank gold series.
SHILLER_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv"
GOLD_URL = "https://raw.githubusercontent.com/datasets/gold-prices/main/data/monthly.csv"

GOLD_FIRST_YEAR = 1960
_MAX_GAP = pd.Timedelta(days=45)


def fetch_text(url: str, timeout: float = 30.0, retries: int = 3, pause: float = 2.0) -> str:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (portfolio-simulator)"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(pause * attempt)
    raise DataError(f"Cannot download {url}: {last}")


# ---------- parsing ----------
def parse_shiller(text: str) -> pd.DataFrame:
    """Monthly S&P 500 price, annualized dividend and 10-year rate (%), indexed by month start."""
    df = pd.read_csv(io.StringIO(text))
    needed = {"Date", "SP500", "Dividend", "Long Interest Rate"}
    if not needed <= set(df.columns):
        raise DataError(f"Unexpected Shiller CSV columns: {list(df.columns)}")
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.rename(columns={"SP500": "price", "Dividend": "dividend", "Long Interest Rate": "rate"})
    df = df.set_index("Date")[["price", "dividend", "rate"]]
    df = df[(df["dividend"] > 0) & (df["rate"] > 0)]  # recent rows beyond Shiller's coverage are zero-filled
    df.index = df.index.to_period("M").to_timestamp()
    if df.empty:
        raise DataError("Shiller CSV holds no usable rows.")
    return df


def parse_gold_monthly(text: str, first_year: int = GOLD_FIRST_YEAR) -> pd.Series:
    """Monthly average gold price in USD/oz, dated on the 15th of each month."""
    df = pd.read_csv(io.StringIO(text))
    if not {"Date", "Price"} <= set(df.columns):
        raise DataError(f"Unexpected gold CSV columns: {list(df.columns)}")
    dates = pd.to_datetime(df["Date"] + "-15")
    s = pd.Series(pd.to_numeric(df["Price"], errors="coerce").to_numpy(), index=dates)
    return clean_series(s[s.index.year >= first_year], "gold-monthly")


# ---------- reconstruction ----------
def reinvest_dividends(price: pd.Series, shiller: pd.DataFrame) -> pd.Series:
    """Total-return index from a daily price index and Shiller's monthly dividend yield.

    Each month's yield (annualized dividend / 12 / price) accrues evenly over that month's
    trading days and is reinvested. The result starts at the first price.
    """
    last_month_end = shiller.index[-1] + pd.offsets.MonthBegin(1)
    price = price[(price.index >= shiller.index[0]) & (price.index < last_month_end)]
    if price.empty:
        raise DataError("No price data within Shiller's dividend coverage.")
    months = price.index.to_period("M").to_timestamp()
    monthly_yield = (shiller["dividend"] / 12.0 / shiller["price"]).reindex(months).to_numpy()
    if np.isnan(monthly_yield).any():
        raise DataError("Shiller dividend data does not cover every month of the price series.")
    days_in_month = pd.Series(1.0, index=price.index).groupby(months).transform("sum").to_numpy()
    accrual = (1.0 + monthly_yield) ** (1.0 / days_in_month)
    ratio = (price / price.shift(1)).to_numpy(copy=True)
    ratio[0] = 1.0
    return pd.Series(float(price.iloc[0]) * np.cumprod(ratio * accrual), index=price.index, name=price.name)


def _bond_price(coupon: float, yld: float, years: float) -> float:
    """Price per 1 of face of a semiannual-coupon bond."""
    periods, r = 2.0 * years, yld / 2.0
    if abs(r) < 1e-12:
        return 1.0 + coupon * years
    discount = (1.0 + r) ** (-periods)
    return (coupon / 2.0) * (1.0 - discount) / r + discount


def treasury_index_from_yields(rate_pct: pd.Series, maturity_years: float = 10.0) -> pd.Series:
    """Monthly total-return index of a constant-maturity government bond, from yields in %.

    Each month a bond is bought at par (coupon = last month's yield); one month later it is worth
    the price at the new yield plus one month of coupon. Dated on the 15th of each month.
    """
    y = rate_pct.to_numpy(dtype=float) / 100.0
    level = [1.0]
    for i in range(1, len(y)):
        coupon = y[i - 1]
        price = _bond_price(coupon, y[i], maturity_years - 1.0 / 12.0)
        level.append(level[-1] * (price + coupon / 12.0))
    return pd.Series(level, index=rate_pct.index + pd.Timedelta(days=14), name="gov-index")


def extend_backwards(prefix: pd.Series, modern: pd.Series) -> tuple[pd.Series, pd.Timestamp]:
    """Return `prefix` (before the day `modern` starts) rescaled to match `modern` on that day."""
    join = modern.index[0]
    before = prefix[prefix.index < join]
    if len(before) < 2:
        raise DataError(f"Older series has no data before {join.date()}.")
    if before.index[-1] < join - _MAX_GAP:
        raise DataError(
            f"Gap between the older series (ends {before.index[-1].date()}) and {join.date()}."
        )
    scale = float(modern.iloc[0]) / float(prefix.asof(join))
    return before * scale, join


# ---------- builder ----------
class LongHistoryBuilder:
    KEYS = ("us_equity", "gold", "gov_debt", "real_estate")

    def __init__(self, price_source: PriceProvider, text_fetcher: Callable[[str], str] = fetch_text) -> None:
        self.price_source = price_source
        self.text_fetcher = text_fetcher
        self._shiller: pd.DataFrame | None = None
        self._gold: pd.Series | None = None

    @classmethod
    def supports(cls, key: str) -> bool:
        return key in cls.KEYS

    def _shiller_df(self) -> pd.DataFrame:
        if self._shiller is None:
            self._shiller = parse_shiller(self.text_fetcher(SHILLER_URL))
        return self._shiller

    def _gold_series(self) -> pd.Series:
        if self._gold is None:
            self._gold = parse_gold_monthly(self.text_fetcher(GOLD_URL))
        return self._gold

    def raw_prefix(self, key: str, modern_symbol: str, end: pd.Timestamp) -> tuple[pd.Series, str, str]:
        """Older series (unscaled), a label describing it, and its kind ('market' / 'reconstructed')."""
        if getattr(self.price_source, "name", "") == "synthetic":
            s = self.price_source.get_prices(modern_symbol + "@long", pd.Timestamp("1900-01-01"), end)
            return s, "Synthétique (données aléatoires)", "synthetic"
        if key == "us_equity":
            gspc = self.price_source.get_prices("^GSPC", pd.Timestamp("1927-12-30"), end)
            tr = reinvest_dividends(gspc, self._shiller_df())
            return tr, "S&P 500 (Yahoo ^GSPC) + dividendes réinvestis (Shiller)", "reconstructed"
        if key == "gold":
            return self._gold_series(), "Moyenne mensuelle du prix de l'or (Banque mondiale)", "market"
        if key == "gov_debt":
            idx = treasury_index_from_yields(self._shiller_df()["rate"])
            return idx, "Obligation d'État 10 ans reconstituée depuis le taux à 10 ans (Shiller)", "reconstructed"
        if key == "real_estate":
            vg = self.price_source.get_prices("VGSIX", pd.Timestamp("1996-01-01"), end)
            return vg, "Vanguard REIT Index Fund (VGSIX, même portefeuille que VNQ)", "market"
        raise DataError(f"No long history for {key!r}.")

    def extend(self, key: str, modern: pd.Series, modern_symbol: str, modern_source: str,
               ) -> tuple[pd.Series, dict]:
        """Chain the older series before `modern`. Returns (full series, JSON-ready payload)."""
        end = modern.index[0] + pd.Timedelta(days=10)
        prefix, label, kind = self.raw_prefix(key, modern_symbol, end)
        before, join = extend_backwards(prefix, modern)
        day = lambda t: t.strftime("%Y-%m-%d")  # noqa: E731
        segments = [
            {"from": day(before.index[0]), "to": day(before.index[-1]), "kind": kind, "label": label},
            {"from": day(join), "to": day(modern.index[-1]),
             "kind": "synthetic" if kind == "synthetic" else "market",
             "label": f"{modern_symbol} ({modern_source})"},
        ]
        full = pd.concat([before, modern])
        full.name = modern.name
        payload = {
            "join": day(join),
            "dates": list(before.index.strftime("%Y-%m-%d")),
            "prices": [float(f"{p:.6g}") for p in before.to_numpy()],
            "segments": segments,
        }
        return full, payload
