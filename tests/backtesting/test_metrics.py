"""Tests for backtesting metrics."""
import pytest


def test_brier_score_perfect():
    from src.layer1_research.backtesting.reporting.metrics import brier_score
    assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == pytest.approx(0.0)


def test_brier_score_worst():
    from src.layer1_research.backtesting.reporting.metrics import brier_score
    assert brier_score([1.0, 0.0], [0, 1]) == pytest.approx(1.0)


def test_brier_score_random():
    from src.layer1_research.backtesting.reporting.metrics import brier_score
    assert brier_score([0.5, 0.5, 0.5, 0.5], [1, 0, 1, 0]) == pytest.approx(0.25)


def test_fee_drag():
    from src.layer1_research.backtesting.reporting.metrics import fee_drag
    assert fee_drag(total_fees=50.0, gross_pnl=500.0) == pytest.approx(0.10)
    assert fee_drag(total_fees=50.0, gross_pnl=0.0) == 0.0


def test_backtest_summary_creation():
    from src.layer1_research.backtesting.reporting.metrics import BacktestSummary
    summary = BacktestSummary(
        strategy_name="test", start="2024-01-01", end="2024-12-31",
        starting_capital=10_000.0, final_equity=11_500.0,
        total_return_pct=15.0, sharpe_ratio=1.5, max_drawdown_pct=-5.0,
        win_rate=0.60, total_trades=100, total_fees=50.0,
        brier=0.22, fee_drag_pct=0.03,
    )
    assert summary.total_return_pct == 15.0
