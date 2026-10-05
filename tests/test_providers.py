import pandas as pd
import pytest

from portfolio_sim.providers import CachedProvider, CsvProvider, DataError
from portfolio_sim.providers.fred import FredProvider
from portfolio_sim.providers.yahoo import YahooProvider


def test_yahoo_parse_uses_adjclose_and_local_date():
    payload = {"chart": {"result": [{
        "meta": {"gmtoffset": 3600},
        "timestamp": [1704067200, 1704153600],  # 2024-01-01 00:00 UTC, 2024-01-02 00:00 UTC
        "indicators": {"quote": [{"close": [10.0, 11.0]}],
                       "adjclose": [{"adjclose": [9.5, None]}]},
    }], "error": None}}
    s = YahooProvider.parse_payload(payload, "X")
    assert list(s.index) == [pd.Timestamp("2024-01-01")]
    assert s.iloc[0] == 9.5


def test_yahoo_parse_error():
    with pytest.raises(DataError):
        YahooProvider.parse_payload({"chart": {"result": None, "error": {"code": "Not Found"}}}, "X")


def test_fred_parse_handles_missing_values():
    text = "observation_date,CSUSHPINSA\n2020-01-01,200.5\n2020-02-01,.\n2020-03-01,201.0\n"
    s = FredProvider.parse_csv(text, "CSUSHPINSA")
    assert len(s) == 2
    assert s.iloc[-1] == 201.0


def test_csv_provider(tmp_path):
    (tmp_path / "FUND.csv").write_text("Date,Close\n2020-01-02,10\n2020-01-03,11\n")
    s = CsvProvider(tmp_path).get_prices("FUND", pd.Timestamp("2020-01-01"), pd.Timestamp("2020-02-01"))
    assert list(s) == [10.0, 11.0]
    with pytest.raises(DataError, match="not found"):
        CsvProvider(tmp_path).get_prices("NOPE", pd.Timestamp("2020-01-01"), pd.Timestamp("2020-02-01"))


def test_cache_avoids_second_fetch(tmp_path):
    calls = []

    class Inner:
        name = "inner"

        def get_prices(self, symbol, start, end):
            calls.append(symbol)
            return pd.Series([1.0, 2.0], index=pd.to_datetime(["2020-01-02", "2020-01-03"]))

    p = CachedProvider(Inner(), tmp_path)
    a = p.get_prices("S/Y", pd.Timestamp("2020-01-01"), pd.Timestamp("2020-02-01"))
    b = p.get_prices("S/Y", pd.Timestamp("2020-01-05"), pd.Timestamp("2020-01-20"))
    assert len(calls) == 1
    assert list(a) == list(b) == [1.0, 2.0]
    # wider range -> refetch
    p.get_prices("S/Y", pd.Timestamp("2019-01-01"), pd.Timestamp("2020-02-01"))
    assert len(calls) == 2
