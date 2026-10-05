"""Portfolio simulator: replay a multi-asset allocation over real market data."""
from .config import load_config
from .engine import SimulationResult, simulate, step_dates
from .models import DEFAULT_ALLOCATION, DEFAULT_ASSETS, AssetSpec, SimulationConfig, parse_step

__all__ = [
    "AssetSpec", "DEFAULT_ALLOCATION", "DEFAULT_ASSETS", "SimulationConfig",
    "SimulationResult", "load_config", "parse_step", "simulate", "step_dates",
]
__version__ = "0.1.0"
