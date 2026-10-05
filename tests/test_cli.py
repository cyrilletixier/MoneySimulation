from pathlib import Path

from portfolio_sim.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_cli_run_synthetic(capsys, tmp_path):
    out = tmp_path / "res.csv"
    rc = main(["run", "--amount", "100000", "--start", "2010-01-04", "--end", "2015-01-04",
               "--provider", "synthetic", "--csv", str(out)])
    text = capsys.readouterr().out
    assert rc == 0
    assert "Actions US 30%" in text
    assert "SYNTHÉTIQUES" in text
    assert out.exists()


def test_cli_with_example_config_and_override(capsys):
    rc = main(["run", "--config", str(ROOT / "config.example.yaml"), "--provider", "synthetic",
               "--step", "6m", "--end", "2010-01-03", "--no-rebalance"])
    text = capsys.readouterr().out
    assert rc == 0
    assert "Pas : 6 mois" in text
    assert "Rééquilibrage : non" in text


def test_cli_bad_allocation_reports_error(capsys):
    rc = main(["run", "--amount", "1000", "--start", "2010-01-04", "--provider", "synthetic",
               "--alloc", "us_equity=50", "gold=10"])
    assert rc == 1
    assert "sum to 100" in capsys.readouterr().err


def test_cli_assets(capsys):
    assert main(["assets"]) == 0
    assert "us_equity" in capsys.readouterr().out
