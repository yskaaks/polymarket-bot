"""Tests for example strategies."""
import pytest
from unittest.mock import MagicMock


def test_fv_mean_reversion_buys_below_fair_value():
    from src.layer1_research.backtesting.strategies.examples.fair_value_mean_reversion import (
        FairValueMeanReversionStrategy, FairValueMRConfig,
    )
    config = FairValueMRConfig(lookback_trades=3, entry_threshold=0.05)
    strategy = FairValueMeanReversionStrategy(config=config)

    instrument = MagicMock()
    instrument.id = "test_id"
    strategy._price_history[str(instrument.id)] = [0.50, 0.52, 0.48]

    tick_low = MagicMock(spec=['price'])
    tick_low.price = MagicMock()
    tick_low.price.__float__ = MagicMock(return_value=0.40)

    signal = strategy.generate_signal(instrument, tick_low)
    assert signal is not None
    assert signal.direction == "BUY"


def test_fv_mean_reversion_flat_near_fair_value():
    from src.layer1_research.backtesting.strategies.examples.fair_value_mean_reversion import (
        FairValueMeanReversionStrategy, FairValueMRConfig,
    )
    config = FairValueMRConfig(lookback_trades=3, entry_threshold=0.05)
    strategy = FairValueMeanReversionStrategy(config=config)

    instrument = MagicMock()
    instrument.id = "test_id"
    strategy._price_history[str(instrument.id)] = [0.50, 0.52, 0.48]

    tick = MagicMock(spec=['price'])
    tick.price = MagicMock()
    tick.price.__float__ = MagicMock(return_value=0.50)

    signal = strategy.generate_signal(instrument, tick)
    assert signal is None
