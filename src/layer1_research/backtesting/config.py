"""Backtest configuration."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Optional

from src.layer1_research.backtesting.execution.fill_model import PredictionMarketFillConfig


@dataclass
class BacktestConfig:
    """Everything needed to define a backtest run."""

    catalog_path: str
    start: datetime
    end: datetime
    strategy_name: str
    starting_capital: float

    strategy_params: dict = field(default_factory=dict)
    markets: Optional[list[str]] = None
    data_mode: Literal["trade", "bar"] = "bar"
    bar_interval: Optional[timedelta] = field(default_factory=lambda: timedelta(minutes=5))
    fee_rate_bps: int = 0
    position_sizer: Literal["kelly", "fixed_fractional"] = "fixed_fractional"
    max_position_pct: float = 0.10
    max_total_exposure_pct: float = 0.50
    generate_charts: bool = False
    fill_model: Optional[PredictionMarketFillConfig] = None

    def __post_init__(self):
        if self.start >= self.end:
            raise ValueError(f"start must be before end: {self.start} >= {self.end}")
        if self.data_mode == "trade":
            self.bar_interval = None
        if self.data_mode == "bar" and self.bar_interval is None:
            raise ValueError("bar_interval required when data_mode='bar'")
        if self.starting_capital <= 0:
            raise ValueError(f"starting_capital must be positive: {self.starting_capital}")
