import json
from datetime import date

import pytest

from portfolio_sim.cli import main
from portfolio_sim.export import export_prices
from portfolio_sim.models import DEFAULT_ASSETS, AssetSpec
from portfolio_sim.providers import DataError


def test_export_synthetic_structure_and_no_future_dates(tmp_path):
    out = tmp_path / "prices.json"
    payload = export_prices(out, provider_name="synthetic", end=date(2024, 6, 30))
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk == payload
    assert on_disk["synthetic"] is True
    assert list(on_disk["assets"]) == list(DEFAULT_ASSETS)
    for a in on_disk["assets"].values():
        assert len(a["dates"]) == len(a["prices"]) > 1000
        assert a["dates"] == sorted(a["dates"])
        assert a["end"] <= "2024-06-30"
        assert all(p > 0 for p in a["prices"])


def test_export_aborts_and_writes_nothing_when_an_asset_fails(tmp_path):
    out = tmp_path / "prices.json"
    assets = {"x": AssetSpec("x", "Inconnu", "csv", "NOPE")}
    with pytest.raises(DataError, match="Export aborted"):
        export_prices(out, assets=assets, csv_dir=str(tmp_path), retries=1)
    assert not out.exists()


def test_cli_export_data(tmp_path, capsys):
    out = tmp_path / "p.json"
    assert main(["export-data", "--out", str(out), "--provider", "synthetic", "--no-cache"]) == 0
    assert out.exists()
    assert "SYNTHÉTIQUES" in capsys.readouterr().out
