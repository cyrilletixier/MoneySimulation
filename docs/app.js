/* Interface du simulateur : lit data/prices.json, appelle le moteur (engine.js) et affiche le résultat. */
(function () {
  "use strict";

  const E = window.PortfolioEngine;
  const $ = (id) => document.getElementById(id);

  // ---------- formats ----------
  const nf0 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
  const nfCompact = new Intl.NumberFormat("fr-FR", { notation: "compact", maximumFractionDigits: 1 });
  const fmtNum = (x) => nf0.format(Math.round(x));
  const fmtPct = (x, d = 1, sign = false) =>
    (x * 100).toLocaleString("fr-FR", {
      minimumFractionDigits: d,
      maximumFractionDigits: d,
      signDisplay: sign ? "exceptZero" : "auto",
    }) + " %";
  const fmtDate = (ms) => new Date(ms).toLocaleDateString("fr-FR", { timeZone: "UTC" });
  const signClass = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");

  const PROVIDER_NAMES = { yahoo: "Yahoo Finance", fred: "FRED", csv: "fichiers CSV", synthetic: "données synthétiques" };

  const PRESETS = [
    { name: "Par défaut", w: { us_equity: 30, eu_equity: 30, gold: 10, gov_debt: 10, bonds: 10, real_estate: 10 } },
    { name: "60 / 40", w: { us_equity: 30, eu_equity: 30, gov_debt: 20, bonds: 20 } },
    { name: "Prudent", w: { us_equity: 10, eu_equity: 10, gold: 10, gov_debt: 30, bonds: 30, real_estate: 10 } },
    { name: "Dynamique", w: { us_equity: 40, eu_equity: 40, gold: 10, real_estate: 10 } },
    { name: "Défaut + Bitcoin", w: { us_equity: 28, eu_equity: 27, gold: 10, gov_debt: 10, bonds: 10, real_estate: 10, bitcoin: 5 } },
    { name: "100 % actions US", w: { us_equity: 100 } },
  ];

  // ---------- état ----------
  let data = null;
  let series = null;
  let hasLong = false;
  let infl = {};
  let keys = [];
  let lastResult = null;
  let timer = null;
  const rows = {}; // key -> { num, rng }

  // ---------- démarrage ----------
  init();

  async function init() {
    try {
      const resp = await fetch("data/prices.json", { cache: "no-cache" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      data = await resp.json();
    } catch (err) {
      showStatus(
        "Impossible de charger les données de marché (data/prices.json). " +
          "En local, ouvrez le site via un serveur : python -m http.server --directory docs",
        false
      );
      $("status").hidden = false;
      return;
    }
    series = E.prepareSeries(data.assets);
    hasLong = Object.values(data.assets).some((a) => a.history);
    $("history-field").hidden = !hasLong;
    infl = E.prepareSeries(data.inflation);
    keys = Object.keys(series);

    if (data.synthetic) {
      const b = $("banner");
      b.textContent =
        "Données synthétiques (aléatoires) : les résultats sont purement illustratifs. " +
        "Les prix réels sont chargés au déploiement du site (voir le README).";
      b.hidden = false;
    }
    renderDataNote();
    buildInflationOptions();
    buildAllocationRows();
    buildPresets();
    applyInitialState();
    bindEvents();
    recalc();
  }

  function renderDataNote() {
    const providers = [...new Set(keys.map((k) => PROVIDER_NAMES[data.assets[k].provider] || data.assets[k].provider))];
    const proxies = keys.map((k) => `${data.assets[k].label} = ${data.assets[k].symbol}`).join(" · ");
    const gen = new Date(data.generated).toLocaleDateString("fr-FR");
    $("data-note").textContent =
      `Données : ${providers.join(", ")} (cours ajustés des dividendes), mises à jour le ${gen}. Proxies : ${proxies}.`;
  }

  // ---------- construction du formulaire ----------
  function buildInflationOptions() {
    const sel = $("inflation");
    Object.entries(infl).forEach(([k, s]) => {
      const o = document.createElement("option");
      o.value = k;
      o.textContent = s.label;
      sel.appendChild(o);
    });
    $("inflation-field").hidden = Object.keys(infl).length === 0;
  }

  function buildAllocationRows() {
    const host = $("alloc");
    keys.forEach((k, i) => {
      const a = data.assets[k];
      const row = document.createElement("div");
      row.className = "asset";
      row.innerHTML = `
        <label class="name" for="num-${k}"><span class="dot" style="background:var(--c${(i % 7) + 1})"></span>
          <span>${a.label} <span class="sym">${a.symbol}</span></span></label>
        <div class="pct"><input id="num-${k}" type="number" min="0" max="100" step="1" inputmode="decimal" aria-label="${a.label} en pourcentage"><span>%</span></div>
        <input id="rng-${k}" type="range" min="0" max="100" step="1" aria-label="${a.label}, curseur">
        <div class="avail">Historique depuis le ${fmtDate(E.parseISO(a.start))}</div>`;
      host.appendChild(row);
      rows[k] = { num: row.querySelector(`#num-${k}`), rng: row.querySelector(`#rng-${k}`) };
    });
  }

  function buildPresets() {
    const host = $("presets");
    PRESETS.forEach((p, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.textContent = p.name;
      b.dataset.index = i;
      b.setAttribute("aria-pressed", "false");
      host.appendChild(b);
    });
  }

  function setWeights(w) {
    keys.forEach((k) => {
      const v = w[k] || 0;
      rows[k].num.value = v;
      rows[k].rng.value = v;
    });
  }

  function readWeights() {
    const w = {};
    keys.forEach((k) => {
      const v = parseFloat(rows[k].num.value);
      w[k] = Number.isFinite(v) && v > 0 ? v : 0;
    });
    return w;
  }

  function setHistory(long) {
    series = E.prepareSeries(data.assets, { long });
    keys = Object.keys(series);
  }

  function renderSources(used) {
    const box = $("sources");
    const long = $("history").value === "long";
    const items = used.filter((k) => series[k].segments);
    box.hidden = !long || items.length === 0;
    if (box.hidden) return;
    const kinds = { market: "marché", reconstructed: "reconstitué", synthetic: "synthétique" };
    $("sources-body").innerHTML = items
      .map((k) => {
        const segs = series[k].segments
          .map(
            (g) =>
              `<li><span class="tag ${g.kind}">${kinds[g.kind] || g.kind}</span> ${esc(g.label)} <span class="muted">(${g.from.slice(0, 7)} → ${g.to.slice(0, 7)})</span></li>`
          )
          .join("");
        return `<div class="src"><strong>${esc(series[k].label)}</strong><ul>${segs}</ul></div>`;
      })
      .join("");
  }

  function esc(t) {
    return String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function applyInitialState() {
    const params = new URLSearchParams(location.hash.slice(1));
    const defaultKeys = keys.filter((k) => PRESETS[0].w[k] > 0);
    const defaultStart = Math.max(E.parseISO("2005-01-03"), E.earliestStart(series, defaultKeys));

    $("amount").value = params.get("a") || "100000";
    if (hasLong && params.get("h") === "long") {
      $("history").value = "long";
      setHistory(true);
    }
    $("start").value = params.get("s") || E.toISO(defaultStart);
    $("end").value = params.get("e") || "";
    if (["12", "6", "3", "1"].includes(params.get("p"))) $("step").value = params.get("p");
    $("rebalance").checked = params.get("r") !== "0";
    $("monthly").value = params.get("m") || "0";
    const iq = params.get("i");
    $("inflation").value = iq !== null && (iq === "" || infl[iq]) ? iq : Object.keys(infl)[0] || "";

    const wParam = params.get("w");
    if (wParam) {
      const vals = wParam.split(",").map(Number);
      const w = {};
      keys.forEach((k, i) => (w[k] = Number.isFinite(vals[i]) ? vals[i] : 0));
      setWeights(w);
    } else {
      setWeights(PRESETS[0].w);
    }
  }

  function saveHash() {
    const w = readWeights();
    const p = new URLSearchParams();
    p.set("a", $("amount").value);
    p.set("s", $("start").value);
    if ($("end").value) p.set("e", $("end").value);
    p.set("p", $("step").value);
    p.set("r", $("rebalance").checked ? "1" : "0");
    p.set("m", $("monthly").value || "0");
    if ($("history").value === "long") p.set("h", "long");
    p.set("i", $("inflation").value);
    p.set("w", keys.map((k) => w[k]).join(","));
    history.replaceState(null, "", "#" + p.toString());
  }

  // ---------- événements ----------
  function bindEvents() {
    $("form").addEventListener("submit", (e) => e.preventDefault());
    $("form").addEventListener("input", (e) => {
      const t = e.target;
      // synchronise champ numérique et curseur
      for (const k of keys) {
        if (t === rows[k].rng) rows[k].num.value = t.value;
        if (t === rows[k].num) rows[k].rng.value = Math.min(100, Math.max(0, parseFloat(t.value) || 0));
      }
      schedule();
    });
    $("form").addEventListener("change", schedule);
    $("history").addEventListener("change", () => {
      setHistory($("history").value === "long");
      recalc();
    });
    $("presets").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-index]");
      if (!b) return;
      setWeights(PRESETS[Number(b.dataset.index)].w);
      recalc();
    });
    $("normalize").addEventListener("click", () => {
      const w = readWeights();
      const sum = keys.reduce((s, k) => s + w[k], 0);
      if (sum <= 0) return;
      const scaled = {};
      keys.forEach((k) => (scaled[k] = Math.round((w[k] / sum) * 1000) / 10));
      // absorbe l'écart d'arrondi sur la plus grosse ligne
      const diff = 100 - keys.reduce((s, k) => s + scaled[k], 0);
      const biggest = keys.reduce((a, b) => (scaled[b] > scaled[a] ? b : a), keys[0]);
      scaled[biggest] = Math.round((scaled[biggest] + diff) * 10) / 10;
      setWeights(scaled);
      recalc();
    });
    $("export-csv").addEventListener("click", exportCsv);
    $("copy-link").addEventListener("click", copyLink);
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(recalc, 120);
  }

  // ---------- calcul ----------
  function showStatus(msg, isError = true) {
    const s = $("status");
    s.textContent = msg;
    s.className = "status" + (isError ? "" : " info");
    s.hidden = false;
    $("output").hidden = true;
  }

  function recalc() {
    clearTimeout(timer);
    const w = readWeights();
    const sum = keys.reduce((s, k) => s + w[k], 0);
    const sumOk = Math.abs(sum - 100) < 0.005;
    const sumEl = $("alloc-sum");
    sumEl.textContent = sum.toLocaleString("fr-FR", { maximumFractionDigits: 2 }) + " %";
    sumEl.classList.toggle("bad", !sumOk);
    $("normalize").hidden = sumOk || sum <= 0;
    updatePresets(w);
    saveHash();

    if (!sumOk) {
      return showStatus(
        `L'allocation doit totaliser 100 % (actuellement ${sum.toLocaleString("fr-FR", { maximumFractionDigits: 2 })} %).`
      );
    }
    const used = keys.filter((k) => w[k] > 0);
    const inflKey = $("inflation").value;
    const cpi = inflKey && infl[inflKey] ? infl[inflKey] : null;
    let earliest = E.earliestStart(series, used);
    let latest = E.latestEnd(series, used);
    if (cpi) {
      // l'indice d'inflation est publié avec ~1 à 2 mois de retard
      earliest = Math.max(earliest, cpi.dates[0]);
      latest = Math.min(latest, E.cpiLastUsable(cpi));
    }
    let limit = "";
    if (used.length) {
      const lim = used.reduce((a, b) => (series[b].dates[0] > series[a].dates[0] ? b : a), used[0]);
      if (!cpi || series[lim].dates[0] >= cpi.dates[0]) limit = ` (limité par : ${series[lim].label})`;
      else limit = " (limité par l'indice d'inflation)";
    }
    $("range-hint").textContent =
      `Données disponibles du ${fmtDate(earliest)} au ${fmtDate(latest)} pour cette allocation` +
      (cpi ? " et cet indice d'inflation" : "") + limit + ".";
    renderSources(used);
    for (const id of ["start", "end"]) {
      $(id).min = E.toISO(earliest);
      $(id).max = E.toISO(latest);
    }

    const amount = parseFloat($("amount").value);
    if (!(amount > 0)) return showStatus("Indiquez une somme de départ positive.");
    const monthly = parseFloat($("monthly").value) || 0;
    if (monthly < 0) return showStatus("Le versement mensuel ne peut pas être négatif.");
    if (!$("start").value) return showStatus("Choisissez une date de départ.", false);

    const cfg = {
      amount,
      start: E.parseISO($("start").value),
      end: $("end").value ? E.parseISO($("end").value) : latest,
      stepMonths: Number($("step").value),
      rebalance: $("rebalance").checked,
      monthlyContribution: monthly,
      cpi,
      allocation: Object.fromEntries(used.map((k) => [k, w[k] / 100])),
    };
    try {
      const res = E.simulate(cfg, series);
      const m = E.metrics(res, cfg.stepMonths);
      lastResult = { res, cfg, m };
      render(res, m, cfg);
    } catch (err) {
      if (err instanceof E.SimError) showStatus(err.message);
      else throw err;
    }
  }

  function updatePresets(w) {
    document.querySelectorAll("#presets .chip").forEach((b) => {
      const p = PRESETS[Number(b.dataset.index)].w;
      const same = keys.every((k) => Math.abs((p[k] || 0) - w[k]) < 1e-9);
      b.setAttribute("aria-pressed", same ? "true" : "false");
    });
  }

  // ---------- rendu ----------
  const colorOf = (k) => `var(--c${(keys.indexOf(k) % 7) + 1})`;

  const hasInvested = (res) => res.invested.some((v) => v !== res.invested[0]);
  const signedNum = (x) => (x < 0 ? "−" : "+") + fmtNum(Math.abs(x));

  function render(res, m, cfg) {
    $("status").hidden = true;
    $("output").hidden = false;
    renderKpis(res, m, cfg);
    renderLegend(res);
    drawChart(res);
    renderTable(res);
  }

  function renderKpis(res, m, cfg) {
    const kpi = (cls, k, v, s, vcls = "") =>
      `<div class="kpi ${cls}"><div class="k">${k}</div><div class="v ${vcls}">${v}</div><div class="s">${s}</div></div>`;
    const contrib = cfg.monthlyContribution > 0;
    const years = m.years.toLocaleString("fr-FR", { maximumFractionDigits: 1 });
    const parts = [
      kpi("main", "Valeur finale", fmtNum(m.final),
        contrib
          ? `pour ${fmtNum(m.invested)} versés · ${years} ans`
          : `pour ${fmtNum(m.initial)} au départ · ${years} ans`),
      kpi("", "Gain", signedNum(m.gain), `${fmtPct(m.totalReturn, 1, true)} du capital versé`, signClass(m.gain)),
      kpi("", "Rendement annualisé", fmtPct(m.cagr, 2, true),
        contrib ? "pondéré par les versements" : "moyenne géométrique", signClass(m.cagr)),
    ];
    if (contrib) parts.push(kpi("", "Capital versé", fmtNum(m.invested), "départ + versements mensuels"));
    if (m.realFinal !== undefined) {
      parts.push(
        kpi("", "Valeur finale réelle", fmtNum(m.realFinal), "pouvoir d'achat, en monnaie du départ"),
        kpi("", "Rendement réel annualisé", fmtPct(m.realCagr, 2, true),
          `inflation moyenne ${fmtPct(m.inflationCagr, 2)}`, signClass(m.realCagr)),
        kpi("", "Capital indexé inflation", fmtNum(m.indexedCapital), "l'argent versé, qui suit juste l'inflation"),
        kpi("", "Écart vs inflation", fmtPct(m.vsIndexed, 1, true), "portefeuille / capital indexé", signClass(m.vsIndexed))
      );
    }
    parts.push(
      kpi("", "Volatilité annuelle", fmtPct(m.volatility, 1), "d'après le pas choisi"),
      kpi("", "Perte maximale", fmtPct(m.maxDrawdown, 1), "mesurée aux dates du pas", "neg"),
      kpi("", "Meilleure / pire période",
        `<span class="pos">${fmtPct(m.best, 1, true)}</span> / <span class="neg">${fmtPct(m.worst, 1, true)}</span>`,
        "sur un pas")
    );
    $("kpis").innerHTML = parts.join("");
  }

  function renderLegend(res) {
    $("legend").innerHTML =
      res.keys.map((k) => `<span><i style="background:${colorOf(k)}"></i>${series[k].label}</span>`).join("") +
      `<span><i class="line"></i>Total</span>` +
      (hasInvested(res) ? `<span><i class="line dashed"></i>Capital versé</span>` : "") +
      (res.indexed ? `<span><i class="line dotted"></i>Capital indexé sur l'inflation</span>` : "");
  }

  function renderTable(res) {
    const inv = hasInvested(res);
    const head =
      "<thead><tr><th>Date</th>" +
      res.keys.map((k) => `<th>${series[k].label}</th>`).join("") +
      "<th>Total</th>" +
      (inv ? "<th>Versé</th>" : "") +
      (res.real ? "<th>Valeur réelle</th><th>Indexé inflation</th>" : "") +
      "<th>Variation</th></tr></thead>";
    const body = res.dates
      .map((d, i) => {
        const r = res.returns[i];
        return (
          `<tr><td>${fmtDate(d)}</td>` +
          res.keys.map((k) => `<td>${fmtNum(res.values[k][i])}</td>`).join("") +
          `<td class="total">${fmtNum(res.total[i])}</td>` +
          (inv ? `<td>${fmtNum(res.invested[i])}</td>` : "") +
          (res.real ? `<td>${fmtNum(res.real[i])}</td><td>${fmtNum(res.indexed[i])}</td>` : "") +
          `<td class="${signClass(r)}">${r === null ? "" : fmtPct(r, 1, true)}</td></tr>`
        );
      })
      .join("");
    $("table").innerHTML = head + "<tbody>" + body + "</tbody>";
  }

  // ---------- graphique (SVG, sans dépendance) ----------
  function niceTicks(max, count) {
    const raw = max / count;
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((f) => f * pow).find((s) => s >= raw);
    const out = [];
    for (let v = 0; v < max + step * 0.999; v += step) out.push(v);
    return out;
  }

  function drawChart(res) {
    const W = 820, H = 380;
    const m = { l: 56, r: 14, t: 12, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const x0 = res.dates[0], x1 = res.dates[res.dates.length - 1];
    const X = (t) => m.l + ((t - x0) / (x1 - x0)) * iw;
    const showInvested = hasInvested(res);
    const top = Math.max(...res.total, ...(showInvested ? res.invested : []), ...(res.indexed || []));
    const ticks = niceTicks(top, 5);
    const yMax = ticks[ticks.length - 1];
    const Y = (v) => m.t + ih - (v / yMax) * ih;

    // couches empilées
    let cum = res.dates.map(() => 0);
    const layers = res.keys.map((k) => {
      const lower = cum;
      const upper = cum.map((c, i) => c + res.values[k][i]);
      cum = upper;
      return { k, lower, upper };
    });
    const pt = (i, arr) => `${X(res.dates[i]).toFixed(1)},${Y(arr[i]).toFixed(1)}`;
    const areas = layers
      .map(({ k, lower, upper }) => {
        const top = res.dates.map((_, i) => pt(i, upper));
        const bottom = res.dates.map((_, i) => pt(i, lower)).reverse();
        return `<polygon points="${top.concat(bottom).join(" ")}" style="fill:${colorOf(k)}" fill-opacity="0.88"></polygon>`;
      })
      .join("");
    const line = (arr) => res.dates.map((_, i) => pt(i, arr)).join(" ");
    const totalLine = line(res.total);
    const extraLines =
      (showInvested ? `<polyline class="invested-line" points="${line(res.invested)}"></polyline>` : "") +
      (res.indexed ? `<polyline class="indexed-line" points="${line(res.indexed)}"></polyline>` : "");

    // axes
    const yGrid = ticks
      .map(
        (v) =>
          `<line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}"></line>` +
          `<text class="tick" x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${v === 0 ? "0" : nfCompact.format(v)}</text>`
      )
      .join("");
    const y0 = new Date(x0).getUTCFullYear(), y1 = new Date(x1).getUTCFullYear();
    const stride = Math.max(1, Math.ceil((y1 - y0 + 1) / 8));
    let xLabels = "";
    for (let y = y0; y <= y1; y++) {
      const t = Date.UTC(y, 0, 1);
      if (t < x0 || t > x1 || (y - y0) % stride !== 0) continue;
      xLabels += `<text class="tick" x="${X(t)}" y="${H - 6}" text-anchor="middle">${y}</text>`;
    }
    if (!xLabels) {
      xLabels =
        `<text class="tick" x="${m.l}" y="${H - 6}" text-anchor="start">${fmtDate(x0)}</text>` +
        `<text class="tick" x="${W - m.r}" y="${H - 6}" text-anchor="end">${fmtDate(x1)}</text>`;
    }

    const host = $("chart");
    host.innerHTML =
      `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Évolution de la valeur du portefeuille par classe d'actifs">` +
      yGrid + areas +
      `<polyline class="total-line" points="${totalLine}"></polyline>` + extraLines +
      xLabels +
      `<line class="hover-line" y1="${m.t}" y2="${m.t + ih}" x1="0" x2="0" visibility="hidden"></line>` +
      `<rect x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent"></rect></svg>` +
      `<div class="tooltip" hidden></div>`;

    const svg = host.querySelector("svg");
    const hover = host.querySelector(".hover-line");
    const tip = host.querySelector(".tooltip");

    function show(e) {
      const rect = svg.getBoundingClientRect();
      const px = ((e.clientX - rect.left) * W) / rect.width;
      let best = 0, bestD = Infinity;
      res.dates.forEach((d, i) => {
        const dist = Math.abs(X(d) - px);
        if (dist < bestD) { bestD = dist; best = i; }
      });
      const gx = X(res.dates[best]);
      hover.setAttribute("x1", gx);
      hover.setAttribute("x2", gx);
      hover.setAttribute("visibility", "visible");
      tip.innerHTML =
        `<div class="d">${fmtDate(res.dates[best])}</div>` +
        res.keys
          .slice()
          .reverse()
          .map((k) => `<div class="r"><span><i style="background:${colorOf(k)}"></i>${series[k].label}</span><span>${fmtNum(res.values[k][best])}</span></div>`)
          .join("") +
        `<div class="r tot"><span>Total</span><span>${fmtNum(res.total[best])}</span></div>` +
        (showInvested ? `<div class="r"><span>Capital versé</span><span>${fmtNum(res.invested[best])}</span></div>` : "") +
        (res.real ? `<div class="r"><span>Valeur réelle</span><span>${fmtNum(res.real[best])}</span></div>` : "") +
        (res.indexed ? `<div class="r"><span>Capital indexé</span><span>${fmtNum(res.indexed[best])}</span></div>` : "");
      tip.hidden = false;
      const leftPx = (gx * rect.width) / W;
      const flip = leftPx > rect.width * 0.55;
      tip.style.left = flip ? "auto" : leftPx + 12 + "px";
      tip.style.right = flip ? rect.width - leftPx + 12 + "px" : "auto";
      tip.style.top = "8px";
    }
    function hide() {
      hover.setAttribute("visibility", "hidden");
      tip.hidden = true;
    }
    svg.addEventListener("pointermove", show);
    svg.addEventListener("pointerdown", show);
    svg.addEventListener("pointerleave", hide);
  }

  // ---------- export / partage ----------
  function exportCsv() {
    if (!lastResult) return;
    const { res } = lastResult;
    const inv = hasInvested(res);
    const num = (x) => String(Math.round(x * 100) / 100).replace(".", ",");
    const header = ["Date", ...res.keys.map((k) => series[k].label), "Total"];
    if (inv) header.push("Capital versé");
    if (res.real) header.push("Valeur réelle", "Capital indexé inflation");
    header.push("Variation");
    const lines = [
      header.join(";"),
      ...res.dates.map((d, i) => {
        const row = [E.toISO(d), ...res.keys.map((k) => num(res.values[k][i])), num(res.total[i])];
        if (inv) row.push(num(res.invested[i]));
        if (res.real) row.push(num(res.real[i]), num(res.indexed[i]));
        row.push(res.returns[i] === null ? "" : String(Math.round(res.returns[i] * 10000) / 100).replace(".", ","));
        return row.join(";");
      }),
    ];
    const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "simulation-portefeuille.csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  async function copyLink() {
    const btn = $("copy-link");
    try {
      await navigator.clipboard.writeText(location.href);
      btn.textContent = "Lien copié";
    } catch (e) {
      btn.textContent = "Copiez l'adresse du navigateur";
    }
    setTimeout(() => (btn.textContent = "Copier le lien"), 2000);
  }
})();
