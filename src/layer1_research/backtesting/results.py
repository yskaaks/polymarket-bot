"""Programmatic backtest result object.

BacktestRunner returns a single BacktestResult. All downstream consumers
(metrics, charts, MLflow logger, notebooks) read from it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class SignalSnapshot:
    """A single signal emitted by a strategy, captured at the moment of emission."""

    ts: datetime
    instrument_id: str
    direction: str                 # "BUY" / "SELL" / "FLAT"
    market_price: float            # observed price at signal time
    confidence: float              # strategy's P(signal is right), in [0, 1]
    target_price: float            # strategy's fair value
    size: float
    client_order_id: Optional[str]

    def __post_init__(self):
        if self.direction not in ("BUY", "SELL", "FLAT"):
            raise ValueError(
                f"direction must be BUY, SELL, or FLAT, got '{self.direction}'"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0, 1], got {self.confidence}"
            )

    @property
    def edge_at_order(self) -> float:
        """Expected edge per unit at signal time.

        BUY:  confidence - market_price
            (we think P(YES wins) = confidence; buying at market_price nets
             confidence - market_price in expectation)
        SELL: market_price - (1 - confidence)
            (we think P(YES wins) = 1 - confidence; selling at market_price nets
             market_price - (1 - confidence) in expectation)
        FLAT: 0.0 (no position taken)
        """
        if self.direction == "BUY":
            return self.confidence - self.market_price
        if self.direction == "SELL":
            return self.market_price - (1.0 - self.confidence)
        return 0.0
