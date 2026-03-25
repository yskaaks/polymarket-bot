"""Unified data models for backtesting ETL pipeline.

These models normalize data from various sources (Parquet, S3, CSV)
into a common format for loading into NautilusTrader's ParquetDataCatalog.
They are used only during ETL — at runtime the engine uses native Nautilus types.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class RawTrade:
    """A single trade normalized from any data source."""

    timestamp: datetime
    market_id: str
    token_id: str
    side: str  # "BUY" or "SELL"
    price: float
    size: float
    source: str  # "polymarket" or "kalshi"
    maker: Optional[str] = None
    taker: Optional[str] = None

    def __post_init__(self):
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got '{self.side}'")
        if not (0.0 < self.price <= 1.0):
            raise ValueError(
                f"price must be between 0 (exclusive) and 1 (inclusive), got {self.price}"
            )
        if self.size <= 0:
            raise ValueError(f"size must be positive, got {self.size}")


@dataclass(frozen=True)
class MarketInfo:
    """Static metadata for a prediction market."""

    market_id: str
    question: str
    outcomes: list[str]
    token_ids: list[str]
    created_at: datetime
    end_date: Optional[datetime]
    source: str  # "polymarket" or "kalshi"
    result: Optional[str] = None  # resolved outcome, None if unresolved


@dataclass
class MarketFilter:
    """Criteria for selecting which markets to load."""

    min_volume: Optional[float] = None
    min_trades: Optional[int] = None
    date_start: Optional[datetime] = None
    date_end: Optional[datetime] = None
    resolved_only: bool = False
    sources: Optional[list[str]] = None  # ["polymarket", "kalshi"]
    market_ids: Optional[list[str]] = None
