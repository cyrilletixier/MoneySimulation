/* Moteur de simulation (JavaScript) : même logique que src/portfolio_sim/engine.py.
 * Utilisable dans le navigateur (window.PortfolioEngine) et dans Node (require).
 *
 * Modèle : somme de départ investie selon l'allocation cible ; avec un versement mensuel, le calcul
 * se fait mois par mois (versement investi selon l'allocation cible), sinon directement au pas choisi ;
 * rééquilibrage éventuel à chaque pas ; résultats restitués à chaque date du pas.
 * Rendement annualisé = taux de rendement interne (tient compte de la date des versements) ;
 * volatilité, perte maximale et variations par période = indice « pondéré par le temps » (insensible aux versements).
 * Avec un indice d'inflation : valeur réelle (pouvoir d'achat, en monnaie de la date de départ) et
 * « capital indexé » = ce que vaudrait l'argent versé s'il suivait simplement l'inflation. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PortfolioEngine = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const DAY = 86400000;
  // Une série de prix peut se terminer quelques jours avant la date demandée (week-end,
  // jours fériés). Au-delà, les données sont considérées périmées.
  const STALE_DAYS = 10;
  // Un indice mensuel daté du 1er du mois reste valable tout le mois.
  const MONTHLY_STALE_DAYS = 31;

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

  /** Transforme une section du JSON exporté (assets ou inflation) en séries prêtes à interroger. */
  function prepareSeries(entries) {
    const out = {};
    for (const [key, a] of Object.entries(entries || {})) {
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

  /** Dernière valeur connue à la date `t` (recherche dichotomique). */
  function priceAt(series, t, label, staleDays = STALE_DAYS) {
    const { dates, prices } = series;
    if (t < dates[0]) {
      throw new SimError(
        `${label} : pas de données avant le ${toISO(dates[0])} (date nécessaire : ${toISO(t)}). ` +
          "Choisissez une date de départ plus tardive ou une autre allocation."
      );
    }
    const last = dates[dates.length - 1];
    if (t > last + staleDays * DAY) {
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

  /** Dernière date jusqu'à laquelle un indice mensuel est utilisable. */
  function cpiLastUsable(cpiSeries) {
    return cpiSeries.dates[cpiSeries.dates.length - 1] + MONTHLY_STALE_DAYS * DAY;
  }

  /** Taux annualisé r tel que somme(flux * (1+r)^-t) = 0 (dichotomie). */
  function irr(years, flows) {
    const f = (r) => years.reduce((s, t, i) => s + flows[i] * Math.pow(1 + r, -t), 0);
    let lo = -0.99, hi = 10;
    let flo = f(lo);
    const fhi = f(hi);
    if (flo * fhi > 0) return NaN;
    for (let i = 0; i < 200; i++) {
      const mid = (lo + hi) / 2;
      const fm = f(mid);
      if (flo * fm <= 0) hi = mid; else { lo = mid; flo = fm; }
    }
    return (lo + hi) / 2;
  }

  /**
   * cfg = { amount, start (ms), end (ms), stepMonths, rebalance, allocation: {key: fraction},
   *         monthlyContribution (optionnel), cpi (série préparée, optionnel) }
   * Renvoie { dates, keys, values, total, invested, twr, returns, real, indexed, summary }
   * (real / indexed : null sans indice d'inflation).
   */
  function simulate(cfg, series) {
    const keys = Object.keys(cfg.allocation).filter((k) => cfg.allocation[k] > 0);
    const sum = keys.reduce((s, k) => s + cfg.allocation[k], 0);
    const contribution = cfg.monthlyContribution || 0;
    const monthly = contribution > 0;
    const step = cfg.stepMonths;
    if (!(cfg.amount > 0)) throw new SimError("La somme de départ doit être positive.");
    if (contribution < 0) throw new SimError("Le versement mensuel ne peut pas être négatif.");
    if (Math.abs(sum - 1) > 1e-6) {
      throw new SimError(`L'allocation doit totaliser 100 % (actuellement ${(sum * 100).toFixed(2)} %).`);
    }
    if (!(cfg.end > cfg.start)) throw new SimError("La date de fin doit être postérieure à la date de départ.");

    const reportDates = stepDates(cfg.start, cfg.end, step);
    if (reportDates.length < 2) throw new SimError("La période est plus courte qu'un pas : rien à simuler.");
    const last = reportDates[reportDates.length - 1];
    // grille interne : tous les mois avec versements, sinon seulement les pas
    const dates = stepDates(cfg.start, last, monthly ? 1 : step);
    const n = dates.length;

    const prices = {};
    for (const k of keys) {
      const s = series[k];
      if (!s) throw new SimError(`Aucune donnée pour l'actif ${k}.`);
      const label = `${s.label} (${s.symbol})`;
      prices[k] = dates.map((d) => priceAt(s, d, label));
    }
    let cpi = null;
    if (cfg.cpi) {
      const label = `${cfg.cpi.label} (${cfg.cpi.symbol})`;
      cpi = dates.map((d) => priceAt(cfg.cpi, d, label, MONTHLY_STALE_DAYS));
    }

    const w = cfg.allocation;
    let units = {};
    for (const k of keys) units[k] = (cfg.amount * w[k]) / prices[k][0];
    let invested = cfg.amount;
    let prevTotal = cfg.amount;
    let twr = 1;
    let indexed = cfg.amount;
    const flows = [-cfg.amount];
    const realFlows = [-cfg.amount]; // en monnaie de la date de départ

    const out = {
      dates: [], keys, values: Object.fromEntries(keys.map((k) => [k, []])),
      total: [], invested: [], twr: [], returns: [],
      real: cpi ? [] : null, indexed: cpi ? [] : null, summary: {},
    };
    let total = 0;
    for (let i = 0; i < n; i++) {
      let pre = 0;
      for (const k of keys) pre += units[k] * prices[k][i];
      if (i > 0) twr *= pre / prevTotal;
      const added = i > 0 && monthly ? contribution : 0;
      if (added) {
        for (const k of keys) units[k] += (added * w[k]) / prices[k][i];
        invested += added;
        flows.push(-added);
        realFlows.push(cpi ? (-added * cpi[0]) / cpi[i] : -added);
      } else if (i > 0) {
        flows.push(0);
        realFlows.push(0);
      }
      total = pre + added;
      if (cpi && i > 0) indexed = (indexed * cpi[i]) / cpi[i - 1] + added;
      const isStep = monthly ? i % step === 0 : true;
      if (isStep) {
        out.dates.push(dates[i]);
        for (const k of keys) out.values[k].push(units[k] * prices[k][i]);
        out.total.push(total);
        out.invested.push(invested);
        out.twr.push(twr);
        if (cpi) {
          out.real.push((total * cpi[0]) / cpi[i]);
          out.indexed.push(indexed);
        }
        if (cfg.rebalance) {
          // retour à l'allocation cible, après avoir enregistré les valeurs de fin de période
          units = {};
          for (const k of keys) units[k] = (total * w[k]) / prices[k][i];
        }
      }
      prevTotal = total;
    }

    const years = dates.map((d) => (d - dates[0]) / DAY / 365.25);
    flows[n - 1] += total;
    realFlows[n - 1] += cpi ? (total * cpi[0]) / cpi[n - 1] : total;
    out.summary.irr = irr(years, flows);
    if (cpi) {
      out.summary.realIrr = irr(years, realFlows);
      const span = years[n - 1];
      out.summary.inflationCagr = span > 0 ? Math.pow(cpi[n - 1] / cpi[0], 1 / span) - 1 : NaN;
    }
    out.returns = out.twr.map((t, i) => (i === 0 ? null : t / out.twr[i - 1] - 1));
    return out;
  }

  function metrics(result, stepMonths) {
    const { total, invested, returns, dates, twr } = result;
    const years = (dates[dates.length - 1] - dates[0]) / DAY / 365.25;
    const final = total[total.length - 1];
    const paidIn = invested[invested.length - 1];
    const rets = returns.filter((r) => r !== null);
    let vol = NaN;
    if (rets.length >= 2) {
      const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
      const variance = rets.reduce((a, b) => a + (b - mean) ** 2, 0) / (rets.length - 1);
      vol = Math.sqrt(variance) * Math.sqrt(12 / stepMonths);
    }
    let peak = -Infinity, maxDrawdown = 0;
    for (const t of twr) {
      peak = Math.max(peak, t);
      maxDrawdown = Math.min(maxDrawdown, t / peak - 1);
    }
    const m = {
      initial: total[0],
      final,
      invested: paidIn,
      gain: final - paidIn,
      totalReturn: final / paidIn - 1, // sur le capital réellement versé
      cagr: result.summary.irr, // pondéré par les versements ; égal au CAGR classique sans versement
      volatility: vol,
      maxDrawdown,
      best: Math.max(...rets),
      worst: Math.min(...rets),
      years,
    };
    if (result.real) {
      const idx = result.indexed[result.indexed.length - 1];
      m.realFinal = result.real[result.real.length - 1];
      m.realCagr = result.summary.realIrr;
      m.inflationCagr = result.summary.inflationCagr;
      m.indexedCapital = idx;
      m.vsIndexed = final / idx - 1;
    }
    return m;
  }

  return {
    SimError, parseISO, toISO, addMonths, stepDates, prepareSeries, priceAt,
    earliestStart, latestEnd, cpiLastUsable, irr, simulate, metrics,
  };
});
