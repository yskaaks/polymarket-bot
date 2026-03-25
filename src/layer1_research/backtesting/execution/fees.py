"""Polymarket fee model for NautilusTrader backtesting.

Provides both:
- polymarket_fee(): standalone function for manual calculation
- PolymarketFeeModel: NautilusTrader FeeModel subclass for engine integration
"""
from src.utils import polymarket_taker_fee


def polymarket_fee(price: float, fee_rate_bps: int) -> float:
    """Calculate Polymarket taker fee for a given price and fee rate."""
    return polymarket_taker_fee(price, fee_rate_bps)
