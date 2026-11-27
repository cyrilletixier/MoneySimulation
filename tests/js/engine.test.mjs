import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const E = require("../../docs/engine.js");

const iso = E.parseISO;
const approx = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

// A double chaque année, B reste stable (mêmes données que tests/test_engine.py)
const series = E.prepareSeries({
  a: { label: "A", symbol: "A", dates: ["2019-12-31", "2020-12-31", "2021-12-31", "2022-01-01"], prices: [100, 200, 400, 400] },
  b: { label: "B", symbol: "B", dates: ["2019-12-31", "2020-12-31", "2021-12-31", "2022-01-01"], prices: [100, 100, 100, 100] },
});
const base = {
  amount: 1000, start: iso("2020-01-01"), end: iso("2022-01-01"), stepMonths: 12,
  rebalance: false, allocation: { a: 0.5, b: 0.5 },
};

test("stepDates : pas de dérive du quantième", () => {
  const ds = E.stepDates(iso("2020-01-31"), iso("2021-01-31"), 1);
  assert.equal(E.toISO(ds[1]), "2020-02-29");
  assert.equal(E.toISO(ds[2]), "2020-03-31");
  assert.equal(ds.length, 13);
});

test("stepDates : bornes annuelles incluses", () => {
  const ds = E.stepDates(iso("2020-01-01"), iso("2022-01-01"), 12);
  assert.deepEqual(ds.map((d) => new Date(d).getUTCFullYear()), [2020, 2021, 2022]);
});

test("buy & hold : les positions dérivent", () => {
  const r = E.simulate(base, series);
  approx(r.total[0], 1000);
  approx(r.total[1], 1500);
  approx(r.total[2], 2500);
});

test("rééquilibrage : retour aux poids cibles à chaque pas", () => {
  const r = E.simulate({ ...base, rebalance: true }, series);
  approx(r.total[1], 1500);
  approx(r.total[2], 2250);
  approx(r.returns[2], 0.5);
});

test("métriques", () => {
  const r = E.simulate({ ...base, allocation: { a: 1 } }, series);
  const m = E.metrics(r, 12);
  approx(m.final, 4000);
  approx(m.totalReturn, 3);
  assert.ok(Math.abs(m.cagr - 1) < 0.01);
  approx(m.maxDrawdown, 0);
});

test("erreurs : départ avant l'historique, données périmées, période trop courte", () => {
  assert.throws(() => E.simulate({ ...base, start: iso("2010-01-01") }, series), /pas de données avant/);
  assert.throws(() => E.simulate({ ...base, end: iso("2025-01-01") }, series), /s'arrêtent/);
  assert.throws(() => E.simulate({ ...base, end: iso("2020-06-01") }, series), /plus courte qu'un pas/);
  assert.throws(() => E.simulate({ ...base, allocation: { a: 0.6, b: 0.3 } }, series), /100 %/);
});

test("earliestStart / latestEnd", () => {
  assert.equal(E.toISO(E.earliestStart(series, ["a", "b"])), "2019-12-31");
  assert.equal(E.toISO(E.latestEnd(series, ["a", "b"])), "2022-01-01");
});
