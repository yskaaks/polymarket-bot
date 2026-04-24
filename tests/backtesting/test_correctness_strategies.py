"""Invariant checks for HoldCash and RandomTrader — validate the engine itself."""
import pytest
import shutil
import tempfile
from datetime import datetime, timezone
from tests.backtesting.fixtures.sample_data import create_becker_fixture_dir


@pytest.fixture
def built_catalog():
    becker = create_becker_fixture_dir()
    cat = tempfile.mkdtemp(prefix="cat_corr_")
    from src.layer1_research.backtesting.data.catalog import build_catalog
    from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
    build_catalog(BeckerParquetLoader(becker), cat)
    yield cat
    shutil.rmtree(becker)
    shutil.rmtree(cat)


def test_hold_cash_preserves_capital(built_catalog):
    from src.layer1_research.backtesting.config import BacktestConfig
    from src.layer1_research.backtesting.runner import BacktestRunner
    from src.layer1_research.backtesting.strategies.examples.hold_cash import (
        HoldCashStrategy,
    )

    config = BacktestConfig(
        catalog_path=built_catalog,
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 7, 1, tzinfo=timezone.utc),
        strategy_name="hold_cash", starting_capital=10_000.0, data_mode="trade",
    )
    result = BacktestRunner(config).run(HoldCashStrategy)
    final_eq = float(result.equity_curve.iloc[-1])

    assert final_eq == pytest.approx(10_000.0, abs=1e-2), (
        f"HoldCash must not change capital, got {final_eq}"
    )
    assert len(result.trades) == 0
    assert len(result.signals) == 0
    m = result.metrics()
    assert m.total_fees == pytest.approx(0.0)
    assert m.total_trades == 0
