"""
Utility Functions

Common helpers for logging, formatting, and calculations.
"""

import logging
from datetime import datetime
from typing import Optional
from decimal import Decimal, ROUND_DOWN


# Setup logging
def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    """
    Configure logging for the bot.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional file path for logs
    
    Returns:
        Configured logger
    """
    logger = logging.getLogger("polymarket")
    logger.setLevel(getattr(logging, level.upper()))
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    
    # Format
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler (optional)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


# Global logger
logger = setup_logging()


def log_trade(action: str, token_id: str, side: str, price: float, size: float):
    """Log a trade action."""
    logger.info(f"TRADE | {action} | {side} {size:.2f} @ {price:.4f} | {token_id[:16]}...")


def log_order(action: str, order_id: str, details: str = ""):
    """Log an order action."""
    logger.info(f"ORDER | {action} | {order_id} | {details}")


# Price formatting
def format_price(price: float, decimals: int = 4) -> str:
    """Format price for display."""
    return f"{price:.{decimals}f}"


def format_percent(value: float, decimals: int = 2) -> str:
    """Format percentage for display."""
    return f"{value:.{decimals}f}%"


def format_usd(amount: float) -> str:
    """Format USD amount for display."""
    return f"${amount:,.2f}"


# Price calculations
def round_price(price: float, tick_size: float = 0.01) -> float:
    """
    Round price to nearest tick size.
    
    Args:
        price: Raw price
        tick_size: Minimum price increment
    
    Returns:
        Rounded price
    """
    return round(price / tick_size) * tick_size


def round_size(size: float, min_size: float = 0.1) -> float:
    """
    Round size down to valid order size.
    
    Args:
        size: Raw size
        min_size: Minimum order size
    
    Returns:
        Valid order size
    """
    d = Decimal(str(size))
    step = Decimal(str(min_size))
    return float(d.quantize(step, rounding=ROUND_DOWN))


def calculate_effective_price(side: str, price: float, fee_bps: int = 0) -> float:
    """
    Calculate effective price after fees.
    
    Args:
        side: "BUY" or "SELL"
        price: Raw price
        fee_bps: Fee in basis points (1 bp = 0.01%)
    
    Returns:
        Effective price
    """
    fee_mult = fee_bps / 10000
    
    if side == "BUY":
        return price * (1 + fee_mult)
    else:
        return price * (1 - fee_mult)


def calculate_pnl(entry_price: float, exit_price: float, size: float, side: str) -> float:
    """
    Calculate profit/loss for a position.
    
    Args:
        entry_price: Entry price
        exit_price: Exit price
        size: Position size
        side: Original side ("BUY" or "SELL")
    
    Returns:
        P&L in USDC
    """
    if side == "BUY":
        return (exit_price - entry_price) * size
    else:
        return (entry_price - exit_price) * size


# Time utilities
def now_timestamp() -> str:
    """Get current UTC timestamp as ISO string."""
    return datetime.utcnow().isoformat() + "Z"


def parse_timestamp(ts: str) -> Optional[datetime]:
    """Parse ISO timestamp string to datetime."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except:
        return None


def time_until(target: datetime) -> float:
    """Seconds until target datetime."""
    now = datetime.utcnow()
    if target.tzinfo:
        from datetime import timezone
        now = now.replace(tzinfo=timezone.utc)
    delta = target - now
    return delta.total_seconds()


# Validation
def is_valid_token_id(token_id: str) -> bool:
    """Check if token ID looks valid."""
    if not token_id:
        return False
    # Token IDs are typically long numeric strings
    return len(token_id) > 10


def is_valid_price(price: float) -> bool:
    """Check if price is in valid range."""
    return 0.01 <= price <= 0.99


def is_valid_address(address: str) -> bool:
    """Check if Ethereum address format is valid."""
    if not address:
        return False
    if not address.startswith("0x"):
        return False
    if len(address) != 42:
        return False
    try:
        int(address, 16)
        return True
    except ValueError:
        return False


# Market analysis helpers
def kelly_criterion(win_prob: float, win_amount: float, loss_amount: float) -> float:
    """
    Calculate Kelly Criterion optimal bet fraction.
    
    Args:
        win_prob: Probability of winning
        win_amount: Amount won per unit bet
        loss_amount: Amount lost per unit bet
    
    Returns:
        Optimal fraction of bankroll to bet
    """
    if loss_amount == 0:
        return 0
    
    b = win_amount / loss_amount
    p = win_prob
    q = 1 - p
    
    kelly = (b * p - q) / b
    return max(0, kelly)  # Never recommend negative bet


def implied_probability_to_odds(prob: float) -> float:
    """Convert implied probability to decimal odds."""
    if prob <= 0 or prob >= 1:
        return 0
    return 1 / prob


def odds_to_implied_probability(odds: float) -> float:
    """Convert decimal odds to implied probability."""
    if odds <= 0:
        return 0
    return 1 / odds
