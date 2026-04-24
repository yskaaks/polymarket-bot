"""Tests for small metric helpers."""
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


def test_fee_drag_abs_denominator():
    """fee_drag divides by |gross_pnl| so losers still show drag."""
    from src.layer1_research.backtesting.reporting.metrics import fee_drag
    assert fee_drag(total_fees=50.0, gross_pnl=500.0) == pytest.approx(0.10)
    assert fee_drag(total_fees=50.0, gross_pnl=-500.0) == pytest.approx(0.10)
    assert fee_drag(total_fees=50.0, gross_pnl=0.0) == 0.0
