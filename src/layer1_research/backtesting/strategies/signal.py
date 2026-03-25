"""Signal dataclass emitted by backtesting strategies."""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Signal:
    """A trading signal emitted by a strategy's generate_signal() method."""

    direction: str  # "BUY", "SELL", or "FLAT"
    confidence: float
    target_price: float
    size: Optional[float] = None
    metadata: Optional[dict] = None

    def __post_init__(self):
        if self.direction not in ("BUY", "SELL", "FLAT"):
            raise ValueError(f"direction must be BUY, SELL, or FLAT, got '{self.direction}'")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be between 0 and 1, got {self.confidence}")
