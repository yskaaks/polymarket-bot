"""Position sizing for backtesting strategies."""
from src.utils import kelly_criterion


def fixed_fractional_size(capital: float, fraction: float, price: float,
                          max_size: float = float("inf")) -> float:
    """Compute position size as a fixed fraction of capital.

    Args:
        capital: Current portfolio value in USDC.
        fraction: Fraction of capital to risk (e.g., 0.02 = 2%).
        price: Entry price per contract.
        max_size: Maximum number of contracts.
    Returns:
        Number of contracts to buy/sell.
    """
    if price <= 0:
        return 0.0
    dollar_amount = capital * fraction
    size = dollar_amount / price
    return min(size, max_size)


def kelly_size(capital: float, win_prob: float, price: float,
               max_fraction: float = 0.10) -> float:
    """Compute position size using Kelly criterion.

    For binary options: win_amount = (1 - price), loss_amount = price.
    """
    win_amount = 1.0 - price
    loss_amount = price
    if win_amount <= 0 or loss_amount <= 0:
        return 0.0

    fraction = kelly_criterion(win_prob, win_amount, loss_amount)
    if fraction <= 0:
        return 0.0

    fraction = min(fraction, max_fraction)
    dollar_amount = capital * fraction
    return dollar_amount / price
