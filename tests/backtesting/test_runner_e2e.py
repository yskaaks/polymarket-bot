"""End-to-end test for BacktestRunner."""
import pytest
import shutil
import tempfile
from datetime import datetime, timezone
from tests.backtesting.fixtures.sample_data import create_becker_fixture_dir


@pytest.fixture
def becker_data_dir():
    d = create_becker_fixture_dir()
    yield d
    shutil.rmtree(d)


@pytest.fixture
def catalog_dir():
    d = tempfile.mkdtemp(prefix="catalog_e2e_")
    yield d
    shutil.rmtree(d)


def test_e2e_backtest_returns_result(becker_data_dir, catalog_dir):
    from src.layer1_research.backtesting.data.catalog import build_catalog
    from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
    from src.layer1_research.backtesting.config import BacktestConfig
    from src.layer1_research.backtesting.runner import BacktestRunner
    from src.layer1_research.backtesting.results import BacktestResult
    from src.layer1_research.backtesting.strategies.base import (
        PredictionMarketStrategy,
    )
    from src.layer1_research.backtesting.strategies.signal import Signal

    loader = BeckerParquetLoader(becker_data_dir)
    build_catalog(loader, catalog_dir)

    class BuyLowStrategy(PredictionMarketStrategy):
        def generate_signal(self, instrument, data):
            price = float(data.price) if hasattr(data, "price") else float(data.close)
            if price < 0.40:
                return Signal(direction="BUY", confidence=0.65,
                              target_price=price, size=10.0)
            return None

    config = BacktestConfig(
        catalog_path=catalog_dir,
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 7, 1, tzinfo=timezone.utc),
        strategy_name="buy_low",
        starting_capital=10_000.0,
        data_mode="trade",
    )
    runner = BacktestRunner(config)
    result = runner.run(BuyLowStrategy)

    assert isinstance(result, BacktestResult)
    assert result.config.strategy_name == "buy_low"
    assert result.config.starting_capital == 10_000.0
    # account report non-empty + equity curve populated
    assert not result.account.empty
    assert len(result.equity_curve) > 0
    # signals captured (even if 0 — should be a DataFrame, not None)
    assert result.signals is not None
    assert result.trades is not None
