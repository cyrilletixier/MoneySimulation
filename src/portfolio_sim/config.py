"""Load a SimulationConfig from a YAML file."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import yaml

from .models import DEFAULT_ASSETS, AssetSpec, SimulationConfig, parse_step


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def load_config(path: str | Path) -> SimulationConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    assets = dict(DEFAULT_ASSETS)
    for key, spec in (raw.get("assets") or {}).items():
        base = assets.get(key)
        assets[key] = AssetSpec(
            key=key,
            label=spec.get("label", base.label if base else key),
            provider=spec.get("provider", base.provider if base else "yahoo"),
            symbol=spec.get("symbol", base.symbol if base else key),
        )
    kwargs = {}
    if "allocation" in raw:
        kwargs["allocation"] = raw["allocation"]
    return SimulationConfig(
        initial_amount=float(raw["initial_amount"]),
        start_date=_as_date(raw["start_date"]),
        end_date=_as_date(raw.get("end_date")),
        step_months=parse_step(raw.get("step", "1y")),
        rebalance=bool(raw.get("rebalance", True)),
        monthly_contribution=float(raw.get("monthly_contribution", 0) or 0),
        inflation=raw.get("inflation") or None,
        long_history=bool(raw.get("long_history", False)),
        assets=assets,
        **kwargs,
    )
