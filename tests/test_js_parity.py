"""Parity check: the JS engine used by the website must give the same numbers as the Python engine."""
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from portfolio_sim import SimulationConfig, simulate
from portfolio_sim.export import export_prices
from portfolio_sim.providers import SyntheticProvider

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = """
const E = require(process.argv[1]);
const data = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
const cfg = JSON.parse(process.argv[3]);
const series = E.prepareSeries(data.assets);
const infl = E.prepareSeries(data.inflation);
const run = {
  ...cfg, start: E.parseISO(cfg.start), end: E.parseISO(cfg.end),
  cpi: cfg.inflation ? infl[cfg.inflation] : null,
};
const r = E.simulate(run, series);
console.log(JSON.stringify({ r, m: E.metrics(r, cfg.stepMonths) }));
"""

BASE = {"us_equity": 0.30, "eu_equity": 0.30, "gold": 0.10, "gov_debt": 0.10, "bonds": 0.10, "real_estate": 0.10}
WITH_BTC = {"us_equity": 0.5, "eu_equity": 0.3, "bitcoin": 0.2}


@pytest.fixture(scope="module")
def data_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("data") / "prices.json"
    export_prices(path, provider_name="synthetic", end=date(2024, 12, 31))
    return path


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize(
    "alloc,step,rebalance,monthly,inflation",
    [
        (BASE, 12, True, 0, None),
        (BASE, 12, False, 0, None),
        (BASE, 6, True, 0, None),
        (BASE, 1, True, 0, None),
        (BASE, 3, False, 0, None),
        (BASE, 12, True, 500, None),
        (BASE, 12, False, 500, None),
        (BASE, 6, True, 250, "eu"),
        (WITH_BTC, 12, True, 300, "us"),
        (WITH_BTC, 3, False, 0, "eu"),
    ],
)
def test_js_matches_python(data_file, alloc, step, rebalance, monthly, inflation):
    start, end = date(2016, 1, 31), date(2023, 7, 15)
    py = simulate(
        SimulationConfig(initial_amount=100_000, start_date=start, end_date=end, allocation=alloc,
                         step_months=step, rebalance=rebalance, monthly_contribution=monthly,
                         inflation=inflation),
        SyntheticProvider(),
    )
    cfg = {"amount": 100_000, "start": str(start), "end": str(end), "stepMonths": step,
           "rebalance": rebalance, "allocation": alloc, "monthlyContribution": monthly,
           "inflation": inflation}
    out = subprocess.run(
        ["node", "-e", SCRIPT, str(ROOT / "docs" / "engine.js"), str(data_file), json.dumps(cfg)],
        capture_output=True, text=True, check=True,
    )
    js = json.loads(out.stdout)
    r, jm, pm = js["r"], js["m"], py.metrics()

    assert len(r["total"]) == len(py.table)
    rel = dict(rel=1e-3)  # prices are rounded to 4 decimals in the JSON
    for a, b in zip(r["total"], py.table["total"]):
        assert a == pytest.approx(b, **rel)
    for a, b in zip(r["invested"], py.table["invested"]):
        assert a == pytest.approx(b, **rel)
    if inflation:
        for a, b in zip(r["real"], py.table["real_total"]):
            assert a == pytest.approx(b, **rel)
        for a, b in zip(r["indexed"], py.table["indexed_capital"]):
            assert a == pytest.approx(b, **rel)
        assert jm["realCagr"] == pytest.approx(pm["real_cagr"], abs=1e-4)
        assert jm["inflationCagr"] == pytest.approx(pm["inflation_cagr"], abs=1e-4)
        assert jm["vsIndexed"] == pytest.approx(pm["vs_indexed"], abs=2e-3)
    else:
        assert r["real"] is None
    assert jm["cagr"] == pytest.approx(pm["cagr"], abs=1e-4)
    assert jm["totalReturn"] == pytest.approx(pm["total_return"], abs=1e-3)
    assert jm["volatility"] == pytest.approx(pm["volatility"], abs=1e-3)
    assert jm["maxDrawdown"] == pytest.approx(pm["max_drawdown"], abs=1e-3)
