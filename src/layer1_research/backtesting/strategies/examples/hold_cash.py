"""HoldCashStrategy — correctness test for the engine.

Subscribes to nothing, never emits a signal. If the engine's account balance
drifts off starting capital over the backtest, something is wrong upstream.
"""
from typing import Optional

from nautilus_trader.model.instruments import BinaryOption

from src.layer1_research.backtesting.strategies.base import (
    PredictionMarketStrategy, PredictionMarketStrategyConfig,
)
from src.layer1_research.backtesting.strategies.signal import Signal


class HoldCashStrategy(PredictionMarketStrategy):
    """Sits on starting capital for the entire run."""

    def __init__(self, config: PredictionMarketStrategyConfig):
        super().__init__(config)

    def on_start(self):
        # Intentionally do not subscribe to anything.
        pass

    def generate_signal(self, instrument: BinaryOption, data) -> Optional[Signal]:
        return None
