"""Tests for Polymarket fee model."""
import pytest


def test_fee_at_midpoint_is_maximum():
    from src.layer1_research.backtesting.execution.fees import polymarket_fee
    fee_50 = polymarket_fee(0.50, fee_rate_bps=200)
    fee_30 = polymarket_fee(0.30, fee_rate_bps=200)
    fee_90 = polymarket_fee(0.90, fee_rate_bps=200)
    assert fee_50 > fee_30
    assert fee_50 > fee_90


def test_fee_at_extremes_near_zero():
    from src.layer1_research.backtesting.execution.fees import polymarket_fee
    fee = polymarket_fee(0.99, fee_rate_bps=200)
    assert fee < 0.001


def test_fee_zero_bps():
    from src.layer1_research.backtesting.execution.fees import polymarket_fee
    assert polymarket_fee(0.50, fee_rate_bps=0) == 0.0


def test_fee_known_values():
    from src.layer1_research.backtesting.execution.fees import polymarket_fee
    assert polymarket_fee(0.50, fee_rate_bps=200) == pytest.approx(0.005)
    assert polymarket_fee(0.30, fee_rate_bps=200) == pytest.approx(0.0042)
