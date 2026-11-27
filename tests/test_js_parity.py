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
const r = E.simulate({ ...cfg, start: E.parseISO(cfg.start), end: E.parseISO(cfg.end) }, series);
console.log(JSON.stringify({ total: r.total, m: E.metrics(r, cfg.stepMonths) }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("step,rebalance", [(12, True), (12, False), (6, True), (1, True), (3, False)])
def test_js_matches_python(tmp_path, step, rebalance):
    data = tmp_path / "prices.json"
    export_prices(data, provider_name="synthetic", end=date(2024, 12, 31))
    alloc = {"us_equity": 0.30, "eu_equity": 0.30, "gold": 0.10, "gov_debt": 0.10, "bonds": 0.10, "real_estate": 0.10}
    start, end = date(2008, 1, 31), date(2023, 7, 15)

    py = simulate(
        SimulationConfig(initial_amount=100_000, start_date=start, end_date=end,
                         allocation=alloc, step_months=step, rebalance=rebalance),
        SyntheticProvider(),
    )
    cfg = {"amount": 100_000, "start": str(start), "end": str(end), "stepMonths": step,
           "rebalance": rebalance, "allocation": alloc}
    out = subprocess.run(
        ["node", "-e", SCRIPT, str(ROOT / "docs" / "engine.js"), str(data), json.dumps(cfg)],
        capture_output=True, text=True, check=True,
    )
    js = json.loads(out.stdout)

    assert len(js["total"]) == len(py.table)
    for a, b in zip(js["total"], py.table["total"]):
        assert a == pytest.approx(b, rel=1e-3)  # prices are rounded to 4 decimals in the JSON
    pm = py.metrics()
    assert js["m"]["cagr"] == pytest.approx(pm["cagr"], abs=1e-4)
    assert js["m"]["volatility"] == pytest.approx(pm["volatility"], abs=1e-3)
    assert js["m"]["maxDrawdown"] == pytest.approx(pm["max_drawdown"], abs=1e-3)
