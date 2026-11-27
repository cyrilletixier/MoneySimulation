/* Moteur de simulation (JavaScript) : même logique que src/portfolio_sim/engine.py.
 * Utilisable dans le navigateur (window.PortfolioEngine) et dans Node (require). */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PortfolioEngine = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const DAY = 86400000;
  // Une série peut se terminer quelques jours avant la date demandée (week-end,
  // jours fériés). Au-delà, les données sont considérées périmées.
  const STALE_DAYS = 10;

  class SimError extends Error {}

  function parseISO(s) {
    const [y, m, d] = s.split("-").map(Number);
    return Date.UTC(y, m - 1, d);
  }

  function toISO(ms) {
    return new Date(ms).toISOString().slice(0, 10);
  }

  /** start + n mois, en restant sur le même quantième (ramené en fin de mois si besoin). */
  function addMonths(ms, n) {
    const d = new Date(ms);
    const total = d.getUTCFullYear() * 12 + d.getUTCMonth() + n;
    const y = Math.floor(total / 12);
    const m = total - y * 12;
    const daysInMonth = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    return Date.UTC(y, m, Math.min(d.getUTCDate(), daysInMonth));
  }

  /** départ, départ + pas, départ + 2 pas... sans dépasser `end` (calculé depuis le départ : pas de dérive). */
  function stepDates(start, end, stepMonths) {
    const out = [];
    for (let k = 0; ; k++) {
      const t = addMonths(start, stepMonths * k);
      if (t > end) break;
      out.push(t);
    }
    return out;
  }

  /** Transforme le JSON exporté (prices.json) en séries prêtes à interroger. */
  function prepareSeries(assets) {
    const out = {};
    for (const [key, a] of Object.entries(assets)) {
      out[key] = {
        key,
        label: a.label,
        symbol: a.symbol,
        dates: a.dates.map(parseISO),
        prices: a.prices,
      };
    }
    return out;
  }

  /** Dernier cours connu à la date `t` (recherche dichotomique). */
  function priceAt(series, t, label) {
    const { dates, prices } = series;
    if (t < dates[0]) {
      throw new SimError(
        `${label} : pas de données avant le ${toISO(dates[0])} (date nécessaire : ${toISO(t)}). ` +
          "Choisissez une date de départ plus tardive ou une autre allocation."
      );
    }
    const last = dates[dates.length - 1];
    if (t > last + STALE_DAYS * DAY) {
      throw new SimError(`${label} : les données s'arrêtent le ${toISO(last)}, avant le ${toISO(t)}.`);
    }
    let lo = 0, hi = dates.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (dates[mid] <= t) lo = mid; else hi = mid - 1;
    }
    return prices[lo];
  }

  /** Première date où tous les actifs `keys` ont des données. */
  function earliestStart(series, keys) {
    return Math.max(...keys.map((k) => series[k].dates[0]));
  }

  /** Dernière date où tous les actifs `keys` ont des données. */
  function latestEnd(series, keys) {
    return Math.min(...keys.map((k) => series[k].dates[series[k].dates.length - 1]));
  }

  /**
   * cfg = { amount, start (ms), end (ms), stepMonths, rebalance, allocation: {key: fraction} }
   * Renvoie { dates, keys, values: {key: [...]}, total: [...], returns: [null, ...] }.
   */
  function simulate(cfg, series) {
    const keys = Object.keys(cfg.allocation).filter((k) => cfg.allocation[k] > 0);
    const sum = keys.reduce((s, k) => s + cfg.allocation[k], 0);
    if (!(cfg.amount > 0)) throw new SimError("La somme de départ doit être positive.");
    if (Math.abs(sum - 1) > 1e-6) {
      throw new SimError(`L'allocation doit totaliser 100 % (actuellement ${(sum * 100).toFixed(2)} %).`);
    }
    if (!(cfg.end > cfg.start)) throw new SimError("La date de fin doit être postérieure à la date de départ.");
    const dates = stepDates(cfg.start, cfg.end, cfg.stepMonths);
    if (dates.length < 2) throw new SimError("La période est plus courte qu'un pas : rien à simuler.");

    const prices = {};
    for (const k of keys) {
      const s = series[k];
      if (!s) throw new SimError(`Aucune donnée pour l'actif ${k}.`);
      const label = `${s.label} (${s.symbol})`;
      prices[k] = dates.map((d) => priceAt(s, d, label));
    }

    let units = {};
    for (const k of keys) units[k] = (cfg.amount * cfg.allocation[k]) / prices[k][0];
    const values = Object.fromEntries(keys.map((k) => [k, []]));
    const total = [];
    dates.forEach((_, i) => {
      let t = 0;
      for (const k of keys) {
        const v = units[k] * prices[k][i];
        values[k].push(v);
        t += v;
      }
      total.push(t);
      if (cfg.rebalance) {
        // retour à l'allocation cible, après avoir enregistré les valeurs de fin de période
        units = {};
        for (const k of keys) units[k] = (t * cfg.allocation[k]) / prices[k][i];
      }
    });
    const returns = total.map((t, i) => (i === 0 ? null : t / total[i - 1] - 1));
    return { dates, keys, values, total, returns };
  }

  function metrics(result, stepMonths) {
    const { total, returns, dates } = result;
    const years = (dates[dates.length - 1] - dates[0]) / DAY / 365.25;
    const initial = total[0];
    const final = total[total.length - 1];
    const rets = returns.filter((r) => r !== null);
    let vol = NaN;
    if (rets.length >= 2) {
      const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
      const variance = rets.reduce((a, b) => a + (b - mean) ** 2, 0) / (rets.length - 1);
      vol = Math.sqrt(variance) * Math.sqrt(12 / stepMonths);
    }
    let peak = -Infinity, maxDrawdown = 0;
    for (const t of total) {
      peak = Math.max(peak, t);
      maxDrawdown = Math.min(maxDrawdown, t / peak - 1);
    }
    return {
      initial,
      final,
      totalReturn: final / initial - 1,
      cagr: years > 0 ? Math.pow(final / initial, 1 / years) - 1 : NaN,
      volatility: vol,
      maxDrawdown,
      best: Math.max(...rets),
      worst: Math.min(...rets),
      years,
    };
  }

  return {
    SimError, parseISO, toISO, addMonths, stepDates, prepareSeries, priceAt,
    earliestStart, latestEnd, simulate, metrics,
  };
});
