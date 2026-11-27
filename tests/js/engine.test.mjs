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

// ---------- versements mensuels ----------
const flat = E.prepareSeries({
  a: { label: "A", symbol: "A", dates: ["2019-12-31", "2022-01-01"], prices: [100, 100] },
});
const flatCfg = { amount: 1000, start: iso("2020-01-01"), end: iso("2022-01-01"), stepMonths: 12, rebalance: true, allocation: { a: 1 } };

test("versement mensuel : restitution au pas, investissement chaque mois", () => {
  const r = E.simulate({ ...flatCfg, monthlyContribution: 100 }, flat);
  assert.equal(r.dates.length, 3);
  [1000, 2200, 3400].forEach((v, i) => approx(r.invested[i], v));
  [1000, 2200, 3400].forEach((v, i) => approx(r.total[i], v));
  approx(E.metrics(r, 12).cagr, 0, 1e-9);
});

test("les versements ne comptent pas comme de la performance", () => {
  const s = E.prepareSeries({
    a: { label: "A", symbol: "A", dates: ["2019-12-31", "2020-01-31", "2020-02-01", "2022-01-01"], prices: [100, 100, 200, 200] },
  });
  const r = E.simulate({ ...flatCfg, monthlyContribution: 1000 }, s);
  approx(r.twr[r.twr.length - 1], 2);
  const m = E.metrics(r, 12);
  approx(m.maxDrawdown, 0, 1e-9);
  assert.ok(m.cagr < Math.sqrt(2) - 1);
});

// ---------- inflation ----------
const cpiEntries = { dates: [], prices: [], label: "CPI", symbol: "CPI" };
for (let m = 0; m <= 25; m++) {
  cpiEntries.dates.push(E.toISO(E.addMonths(iso("2019-12-01"), m)));
  cpiEntries.prices.push(100 * 2 ** ((m + 0) / 24)); // double en 2 ans
}
const cpi = E.prepareSeries({ x: cpiEntries }).x;

test("comparaison à l'inflation", () => {
  const r = E.simulate({ ...flatCfg, cpi }, flat);
  const m = E.metrics(r, 12);
  approx(m.final, 1000);
  approx(m.realFinal, 500, 1e-6);
  approx(m.indexedCapital, 2000, 1e-6);
  approx(m.vsIndexed, -0.5, 1e-9);
  assert.ok(Math.abs(m.inflationCagr - (Math.SQRT2 - 1)) < 1e-3);
  assert.ok(Math.abs(m.realCagr - (Math.SQRT1_2 - 1)) < 1e-3);
});

test("inflation : données trop courtes", () => {
  const short = { ...cpi, dates: cpi.dates.slice(0, 12), prices: cpi.prices.slice(0, 12) };
  assert.throws(() => E.simulate({ ...flatCfg, cpi: short }, flat), /s'arrêtent/);
});

test("cpiLastUsable : un mois de tolérance après la dernière valeur", () => {
  assert.equal(E.toISO(E.cpiLastUsable(cpi)), "2022-02-01");
});

test("prepareSeries long: prefix history before modern series", async () => {
  const E = (await import("../../docs/engine.js")).default ?? (await import("../../docs/engine.js"));
  const entries = {
    x: {
      label: "X", symbol: "X", dates: ["2000-01-03", "2000-01-04"], prices: [100, 101],
      history: { join: "2000-01-03", dates: ["1999-12-30", "1999-12-31"], prices: [98, 99],
                 segments: [{ from: "1999-12-30", to: "1999-12-31", kind: "reconstructed", label: "r" }] },
    },
  };
  const short = E.prepareSeries(entries);
  const long = E.prepareSeries(entries, { long: true });
  assert.equal(short.x.dates.length, 2);
  assert.equal(long.x.dates.length, 4);
  assert.deepEqual(long.x.prices, [98, 99, 100, 101]);
  assert.equal(long.x.segments[0].kind, "reconstructed");
});
