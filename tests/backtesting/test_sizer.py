"""Tests for position sizer."""
import pytest


def test_fixed_fractional_basic():
    from src.layer1_research.backtesting.execution.sizer import fixed_fractional_size
    size = fixed_fractional_size(capital=10_000.0, fraction=0.02, price=0.50)
    assert size == pytest.approx(400.0)


def test_fixed_fractional_respects_max():
    from src.layer1_research.backtesting.execution.sizer import fixed_fractional_size
    size = fixed_fractional_size(capital=10_000.0, fraction=0.02, price=0.50, max_size=100.0)
    assert size == 100.0


def test_kelly_size_basic():
    from src.layer1_research.backtesting.execution.sizer import kelly_size
    size = kelly_size(capital=10_000.0, win_prob=0.60, price=0.50)
    assert size > 0


def test_kelly_size_no_edge():
    from src.layer1_research.backtesting.execution.sizer import kelly_size
    size = kelly_size(capital=10_000.0, win_prob=0.50, price=0.50)
    assert size == 0.0
