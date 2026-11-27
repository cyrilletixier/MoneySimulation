# MoneySimulation

Simulateur d'évolution d'un portefeuille multi-classes d'actifs, calculé sur des **données de marché réelles**.

Vous donnez : une somme de départ, une date de départ et une stratégie (répartition en %). Le simulateur récupère l'historique de prix, rejoue la stratégie pas à pas (1 an par défaut) et affiche la valeur du portefeuille, par actif et au total, avec quelques indicateurs (rendement annualisé, volatilité, perte maximale).

## Site web

Le dossier `docs/` contient un site statique : on choisit la **stratégie** (curseurs ou préréglages), la **somme de départ**, la **date de départ** (et de fin), le **pas** (annuel, semestriel, trimestriel, mensuel) et le rééquilibrage ; le résultat se recalcule en direct (indicateurs, graphique, tableau, export CSV, lien partageable).

Le calcul tourne dans le navigateur (`docs/engine.js`, même logique que le moteur Python, vérifiée par des tests de parité). Les prix viennent d'un fichier `docs/data/prices.json` généré par `portfolio-sim export-data` à partir de Yahoo Finance.

### Publier sur GitHub Pages (une fois)

1. Sur GitHub : **Settings → Pages → Build and deployment → Source : GitHub Actions**.
2. Le workflow `.github/workflows/pages.yml` télécharge les prix réels, puis déploie le site. Il tourne à chaque push sur `main`, chaque lundi (données à jour) et à la demande (onglet *Actions → Déployer le site → Run workflow*).
3. Le site est alors disponible sur `https://cyrilletixier.github.io/MoneySimulation/`.

Si le téléchargement échoue dans le workflow (Yahoo bloque parfois les serveurs GitHub), rien n'est publié. Solution de secours : générer le fichier chez soi et le committer.

```bash
portfolio-sim export-data           # écrit docs/data/prices.json avec les vrais prix
git add docs/data/prices.json && git commit -m "Mise à jour des prix" && git push
```

> Le `prices.json` versionné au départ contient des données **synthétiques** (aléatoires), signalées par un bandeau sur le site. Elles sont remplacées par les vraies à la première exécution du workflow (ou de la commande ci-dessus).

### Essayer en local

```bash
python -m http.server --directory docs     # puis ouvrir http://localhost:8000
```

## Installation

```bash
git clone https://github.com/cyrilletixier/MoneySimulation && cd MoneySimulation
python -m venv .venv && source .venv/bin/activate    # Windows : .venv\Scripts\activate
pip install -e ".[plot]"                              # ".[dev]" pour lancer les tests
```

## Utilisation

```bash
# Stratégie par défaut (30/30/10/10/10/10), 100 000 de départ, au 3 janvier 2005, pas d'un an
portfolio-sim run --amount 100000 --start 2005-01-03

# Via un fichier de configuration (voir config.example.yaml)
portfolio-sim run --config config.example.yaml

# Autre stratégie, pas semestriel, sans rééquilibrage, export CSV + graphique
portfolio-sim run --amount 50000 --start 2008-01-02 --step 6m --no-rebalance \
    --alloc us_equity=60 gov_debt=30 gold=10 --csv output/res.csv --plot output/res.png

# Voir les sources de données utilisées par défaut
portfolio-sim assets

# Essai 100 % hors-ligne avec des données aléatoires (illustratif uniquement)
portfolio-sim run --amount 100000 --start 2005-01-03 --provider synthetic
```

Depuis Python :

```python
from datetime import date
from portfolio_sim import SimulationConfig, simulate

cfg = SimulationConfig(initial_amount=100_000, start_date=date(2005, 1, 3), step_months=12)
result = simulate(cfg)
print(result.table)      # DataFrame : une ligne par date, une colonne par actif + total + return
print(result.metrics())  # rendement annualisé, volatilité, drawdown...
```

## Comment ça marche

1. Les dates de calcul sont `départ`, `départ + 1 pas`, `départ + 2 pas`… jusqu'à la date de fin (aujourd'hui par défaut).
2. À chaque date, le prix de chaque actif est le dernier cours connu à cette date.
3. Au départ, la somme est répartie selon l'allocation cible. Ensuite, chaque position évolue comme son actif.
4. Avec `rebalance: true` (défaut), le portefeuille est ramené à l'allocation cible après chaque pas ; avec `false`, les positions dérivent librement (buy & hold).

Le pas est configurable : `1y`, `6m`, `1q` (trimestre), `1m`… Le moteur est indépendant du pas.

## Sources de données

Chaque classe d'actifs est associée à une série de prix via un *provider*. Valeurs par défaut (modifiables dans le YAML) :

| Clé | Classe | Proxy par défaut | Historique depuis |
|---|---|---|---|
| `us_equity` | Actions US | `SPY` (ETF S&P 500) | 1993 |
| `eu_equity` | Actions EU | `EXSA.DE` (ETF STOXX Europe 600) | 2000 |
| `gold` | Or | `GC=F` (futures or) | 2000 |
| `gov_debt` | Dette d'État | `IEF` (Treasuries US 7-10 ans) | 2002 |
| `bonds` | Obligations | `LQD` (obligations d'entreprises US IG) | 2002 |
| `real_estate` | Immobilier | `VNQ` (REIT US cotés) | 2004 |

Providers disponibles :

- **`yahoo`** : Yahoo Finance (endpoint public, sans clé API), cours ajustés des dividendes.
- **`fred`** : FRED (Fed de St. Louis), sans clé API. Utile pour des indices, ex. l'immobilier résidentiel avec `CSUSHPINSA` (Case-Shiller).
- **`csv`** : vos propres fichiers `data/<symbole>.csv` (colonnes `Date` et `Close`/`Adj Close`).
- **`synthetic`** : prix aléatoires déterministes, pour tester hors-ligne. **Ce ne sont pas des données réelles.**

Les téléchargements sont mis en cache dans `.cache/` pour éviter de refaire des appels à chaque exécution. Pour brancher une nouvelle source, il suffit d'écrire une classe avec une méthode `get_prices(symbol, start, end)` qui renvoie une `pandas.Series` (voir `src/portfolio_sim/providers/`).

## Limites connues (à garder en tête)

- **Devises** : la plupart des proxies sont en USD, `EXSA.DE` est en EUR. Aucune conversion de change n'est faite : le résultat mélange les devises. Une conversion via `EURUSD=X` est une évolution naturelle.
- **Proxies imparfaits** : les ETF ne sont pas les indices ; l'immobilier par défaut est du REIT coté (volatil, corrélé aux actions), pas de l'immobilier physique. Les futures de l'or ne reflètent pas les coûts de détention.
- **Pas de frais ni de fiscalité** : ni frais de gestion/transaction, ni impôts, ni inflation.
- **Historique** : la date de départ ne peut pas précéder le plus ancien historique des actifs choisis (une erreur explicite est levée).
- Le passé ne préjuge pas de l'avenir ; ceci est un outil de simulation, pas un conseil en investissement.

## Tests

```bash
pip install -e ".[dev]" && pytest        # Python + parité Python/JS (nécessite node)
node --test tests/js/engine.test.mjs        # moteur JavaScript
```

Les connecteurs Yahoo et FRED sont testés sur des réponses simulées (pas d'appel réseau dans les tests).

## Idées pour la suite

- Conversion de devises, frais et fiscalité, apports/retraits périodiques (DCA).
- Pas mensuel avec métriques plus fines, benchmark de comparaison.
- Simulation de Monte-Carlo / scénarios futurs, interface web.
