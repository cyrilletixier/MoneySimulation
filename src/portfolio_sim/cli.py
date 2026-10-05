"""Command line interface."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from .config import load_config
from .engine import SimulationResult, simulate
from .models import DEFAULT_ASSETS, SimulationConfig, parse_step
from .providers import DataError, build_provider


def _parse_alloc(items: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in items:
        if "=" not in item:
            raise argparse.ArgumentTypeError(f"Invalid allocation {item!r}, expected key=percent.")
        k, v = item.split("=", 1)
        out[k.strip()] = float(v)
    return out


def _fmt_money(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def render_report(result: SimulationResult) -> str:
    cfg, t = result.config, result.table.copy()
    labels = {k: cfg.assets[k].label for k in cfg.allocation}
    show = pd.DataFrame(index=t.index.strftime("%Y-%m-%d"))
    for k in cfg.allocation:
        show[labels[k]] = t[k].map(_fmt_money).values
    show["TOTAL"] = t["total"].map(_fmt_money).values
    show["Perf"] = t["return"].map(lambda r: "" if pd.isna(r) else f"{r:+.1%}").values
    m = result.metrics()
    lines = [
        f"Départ : {cfg.start_date}  |  Montant : {_fmt_money(cfg.initial_amount)}  |  "
        f"Pas : {cfg.step_months} mois  |  Rééquilibrage : {'oui' if cfg.rebalance else 'non'}",
        "Allocation cible : " + ", ".join(f"{labels[k]} {w:.0%}" for k, w in cfg.allocation.items()),
        "",
        show.to_string(),
        "",
        f"Valeur finale      : {_fmt_money(m['final_value'])}  ({m['total_return']:+.1%} sur {m['years']:.1f} ans)",
        f"Rendement annualisé: {m['cagr']:+.2%}",
        f"Volatilité annuelle: {m['volatility']:.2%}   (sur la base du pas choisi)",
        f"Perte max (drawdown): {m['max_drawdown']:.1%}   (mesurée aux dates du pas)",
        f"Meilleure / pire période : {m['best_period']:+.1%} / {m['worst_period']:+.1%}",
    ]
    return "\n".join(lines)


def _plot(result: SimulationResult, path: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib n'est pas installé : pip install 'portfolio-simulator[plot]'", file=sys.stderr)
        return
    cfg, t = result.config, result.table
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.stackplot(t.index, [t[k] for k in cfg.allocation],
                 labels=[cfg.assets[k].label for k in cfg.allocation], alpha=0.85)
    ax.plot(t.index, t["total"], color="black", linewidth=1.5, label="Total")
    ax.set_ylabel("Valeur")
    ax.set_title("Évolution du portefeuille")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140)
    print(f"Graphique écrit dans {path}")


def _build_config(args: argparse.Namespace) -> SimulationConfig:
    if args.config:
        cfg = load_config(args.config)
    else:
        if args.amount is None or args.start is None:
            raise SystemExit("Sans --config, --amount et --start sont obligatoires.")
        kwargs = {}
        if args.alloc:
            kwargs["allocation"] = _parse_alloc(args.alloc)
        cfg = SimulationConfig(
            initial_amount=args.amount, start_date=date.fromisoformat(args.start), **kwargs
        )
    # CLI flags override the config file
    if args.config:
        if args.amount is not None:
            cfg.initial_amount = args.amount
        if args.start:
            cfg.start_date = date.fromisoformat(args.start)
        if args.alloc:
            from .models import normalize_allocation
            cfg.allocation = normalize_allocation(_parse_alloc(args.alloc))
    if args.end:
        cfg.end_date = date.fromisoformat(args.end)
    if args.step:
        cfg.step_months = parse_step(args.step)
    if args.no_rebalance:
        cfg.rebalance = False
    return cfg


def cmd_run(args: argparse.Namespace) -> int:
    try:
        cfg = _build_config(args)
        provider = build_provider(args.provider, cache_dir=None if args.no_cache else args.cache_dir,
                                  csv_dir=args.csv_dir) if args.provider else None
        result = simulate(cfg, provider, cache_dir=None if args.no_cache else args.cache_dir,
                          csv_dir=args.csv_dir)
    except (DataError, ValueError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    print(render_report(result))
    if args.provider == "synthetic":
        print("\n⚠ Données SYNTHÉTIQUES (aléatoires) : résultat purement illustratif.")
    if args.csv:
        Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
        result.table.to_csv(args.csv)
        print(f"Tableau écrit dans {args.csv}")
    if args.plot:
        _plot(result, Path(args.plot))
    return 0


def cmd_assets(_: argparse.Namespace) -> int:
    print(f"{'clé':<13}{'libellé':<16}{'source':<9}symbole")
    for a in DEFAULT_ASSETS.values():
        print(f"{a.key:<13}{a.label:<16}{a.provider:<9}{a.symbol}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="portfolio-sim", description="Simulateur de portefeuille multi-actifs.")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="Lancer une simulation")
    r.add_argument("--config", help="Fichier YAML de configuration")
    r.add_argument("--amount", type=float, help="Somme de départ")
    r.add_argument("--start", help="Date de départ (AAAA-MM-JJ)")
    r.add_argument("--end", help="Date de fin (défaut : aujourd'hui)")
    r.add_argument("--step", help="Pas de calcul : 1y (défaut), 6m, 1q, 1m...")
    r.add_argument("--alloc", nargs="+", metavar="CLE=POURCENT",
                   help="Ex : us_equity=30 eu_equity=30 gold=10 gov_debt=10 bonds=10 real_estate=10")
    r.add_argument("--no-rebalance", action="store_true", help="Pas de rééquilibrage (buy & hold)")
    r.add_argument("--provider", choices=["yahoo", "fred", "csv", "synthetic"],
                   help="Force une source pour tous les actifs (ex : synthetic pour un test hors-ligne)")
    r.add_argument("--csv-dir", default="data", help="Dossier des CSV (source csv)")
    r.add_argument("--cache-dir", default=".cache", help="Dossier de cache des téléchargements")
    r.add_argument("--no-cache", action="store_true", help="Désactive le cache")
    r.add_argument("--csv", help="Exporte le tableau détaillé en CSV")
    r.add_argument("--plot", help="Écrit un graphique PNG (nécessite matplotlib)")
    r.set_defaults(func=cmd_run)

    a = sub.add_parser("assets", help="Afficher les sources de données par défaut")
    a.set_defaults(func=cmd_assets)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
