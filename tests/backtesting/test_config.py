"""Tests for BacktestConfig."""
import pytest
from datetime import datetime, timedelta, timezone


def test_config_basic():
    from src.layer1_research.backtesting.config import BacktestConfig
    config = BacktestConfig(
        catalog_path="/tmp/catalog",
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        strategy_name="kalshi_divergence",
        starting_capital=10_000.0,
    )
    assert config.data_mode == "bar"
    assert config.bar_interval == timedelta(minutes=5)
    assert config.fee_rate_bps == 0
    assert config.position_sizer == "fixed_fractional"


def test_config_trade_mode_no_bar_interval():
    from src.layer1_research.backtesting.config import BacktestConfig
    config = BacktestConfig(
        catalog_path="/tmp/catalog",
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        strategy_name="test",
        starting_capital=10_000.0,
        data_mode="trade",
    )
    assert config.bar_interval is None


def test_config_validates_dates():
    from src.layer1_research.backtesting.config import BacktestConfig
    with pytest.raises(ValueError, match="start must be before end"):
        BacktestConfig(
            catalog_path="/tmp/catalog",
            start=datetime(2024, 12, 31, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, tzinfo=timezone.utc),
            strategy_name="test",
            starting_capital=10_000.0,
        )
