"""Tests for PredictionMarketStrategy base class."""
import pytest


def test_base_strategy_is_abstract():
    from src.layer1_research.backtesting.strategies.base import (
        PredictionMarketStrategy, PredictionMarketStrategyConfig,
    )
    config = PredictionMarketStrategyConfig()
    strategy = PredictionMarketStrategy(config=config)
    with pytest.raises(NotImplementedError):
        strategy.generate_signal(None, None)


def test_concrete_strategy_instantiation():
    from src.layer1_research.backtesting.strategies.base import (
        PredictionMarketStrategy, PredictionMarketStrategyConfig,
    )
    from src.layer1_research.backtesting.strategies.signal import Signal

    class AlwaysBuy(PredictionMarketStrategy):
        def generate_signal(self, instrument, data):
            return Signal(direction="BUY", confidence=0.70, target_price=0.60)

    config = PredictionMarketStrategyConfig()
    strategy = AlwaysBuy(config=config)
    assert strategy is not None
