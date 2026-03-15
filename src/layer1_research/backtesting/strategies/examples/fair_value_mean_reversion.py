"""Fair Value Mean Reversion Strategy.

Buys when price drops below rolling mean by a threshold, sells when above.
Template for building more sophisticated strategies.
"""
from collections import defaultdict
from typing import Optional

from nautilus_trader.model.instruments import BinaryOption

from src.layer1_research.backtesting.strategies.base import (
    PredictionMarketStrategy, PredictionMarketStrategyConfig,
)
from src.layer1_research.backtesting.strategies.signal import Signal


class FairValueMRConfig(PredictionMarketStrategyConfig, frozen=True):
    lookback_trades: int = 20
    entry_threshold: float = 0.05
    exit_threshold: Optional[float] = None


class FairValueMeanReversionStrategy(PredictionMarketStrategy):
    """Buy below fair value, sell above fair value."""

    def __init__(self, config: FairValueMRConfig):
        super().__init__(config)
        self._price_history: dict[str, list[float]] = defaultdict(list)
        self._exit_threshold = (
            config.exit_threshold if config.exit_threshold is not None
            else config.entry_threshold / 2
        )

    def generate_signal(self, instrument: BinaryOption, data) -> Optional[Signal]:
        # Use hasattr for compatibility with both real Nautilus types and mocks
        if hasattr(data, 'close'):
            current_price = float(data.close)
        elif hasattr(data, 'price'):
            current_price = float(data.price)
        else:
            return None

        inst_key = str(instrument.id)
        history = self._price_history[inst_key]
        history.append(current_price)

        lookback = self.config.lookback_trades
        if len(history) > lookback:
            history.pop(0)
        if len(history) < lookback:
            return None

        fair_value = sum(history) / len(history)
        deviation = current_price - fair_value
        entry_threshold = self.config.entry_threshold

        if deviation < -entry_threshold:
            return Signal(
                direction="BUY", confidence=min(0.5 + abs(deviation), 0.95),
                target_price=fair_value,
                metadata={"fair_value": fair_value, "deviation": deviation},
            )
        elif deviation > entry_threshold:
            return Signal(
                direction="SELL", confidence=min(0.5 + abs(deviation), 0.95),
                target_price=fair_value,
                metadata={"fair_value": fair_value, "deviation": deviation},
            )
        return None
