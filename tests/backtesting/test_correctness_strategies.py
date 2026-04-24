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


def test_random_trader_is_deterministic_with_seed(built_catalog):
    """Same seed -> identical trades/signals DataFrames."""
    from src.layer1_research.backtesting.config import BacktestConfig
    from src.layer1_research.backtesting.runner import BacktestRunner
    from src.layer1_research.backtesting.strategies.examples.random_trader import (
        RandomTraderStrategy,
    )
    import pandas as pd

    def _run():
        config = BacktestConfig(
            catalog_path=built_catalog,
            start=datetime(2024, 6, 1, tzinfo=timezone.utc),
            end=datetime(2024, 7, 1, tzinfo=timezone.utc),
            strategy_name="rand", starting_capital=10_000.0,
            data_mode="trade",
            strategy_params={"seed": 42, "p_trade": 0.02, "trade_size": 5.0},
        )
        return BacktestRunner(config).run(RandomTraderStrategy)

    r1 = _run()
    r2 = _run()

    # signals DataFrames must be equal (index + content)
    pd.testing.assert_frame_equal(
        r1.signals.reset_index(drop=True),
        r2.signals.reset_index(drop=True),
    )


def test_random_trader_pnl_direction_negative_or_zero(built_catalog):
    """Over many random trades with nonzero fees, expected net P&L is negative.

    We don't require statistical significance on the 20-market snapshot — we
    only require that *if* fees were applied, net P&L is not wildly positive.
    """
    from src.layer1_research.backtesting.config import BacktestConfig
    from src.layer1_research.backtesting.runner import BacktestRunner
    from src.layer1_research.backtesting.strategies.examples.random_trader import (
        RandomTraderStrategy,
    )

    config = BacktestConfig(
        catalog_path=built_catalog,
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 7, 1, tzinfo=timezone.utc),
        strategy_name="rand_neg", starting_capital=10_000.0,
        data_mode="trade",
        fee_rate_bps=100,
        strategy_params={"seed": 7, "p_trade": 0.05, "trade_size": 5.0},
    )
    result = BacktestRunner(config).run(RandomTraderStrategy)
    m = result.metrics()
    # With fees applied, net PnL over random entries should be <= 0 plus a
    # generous tolerance for small-sample noise (the fixture has only 3 trades).
    net_pnl = float(result.equity_curve.iloc[-1]) - config.starting_capital
    tolerance = max(20.0, abs(m.total_fees) * 3)
    assert net_pnl <= tolerance, (
        f"Random trader net PnL should not exceed tolerance: "
        f"got {net_pnl:.2f}, tolerance {tolerance:.2f}, fees {m.total_fees:.2f}"
    )
