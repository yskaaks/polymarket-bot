"""RandomTraderStrategy — correctness test for the engine.

On each trade tick, with probability p_trade emits a BUY or SELL at the
current price. Takes a seed for reproducibility. Expected net P&L over many
trades with fees: close to -total_fees.
"""
import random
from typing import Optional

from nautilus_trader.model.instruments import BinaryOption

from src.layer1_research.backtesting.strategies.base import (
    PredictionMarketStrategy, PredictionMarketStrategyConfig,
)
from src.layer1_research.backtesting.strategies.signal import Signal


class RandomTraderConfig(PredictionMarketStrategyConfig, frozen=True):
    seed: int = 0
    p_trade: float = 0.01
    trade_size: float = 5.0


class RandomTraderStrategy(PredictionMarketStrategy):
    """Emits random BUY/SELL signals with configurable probability."""

    def __init__(self, config: RandomTraderConfig):
        super().__init__(config)
        self._rng = random.Random(config.seed)

    def generate_signal(self, instrument: BinaryOption, data) -> Optional[Signal]:
        if self._rng.random() >= self.config.p_trade:
            return None

        if hasattr(data, "price"):
            price = float(data.price)
        elif hasattr(data, "close"):
            price = float(data.close)
        else:
            return None

        direction = "BUY" if self._rng.random() < 0.5 else "SELL"
        # Confidence 0.5 = zero expected edge; the strategy is a null hypothesis.
        return Signal(
            direction=direction,
            confidence=0.5,
            target_price=price,
            size=self.config.trade_size,
        )
