"""Abstract base class for data loaders."""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterator, Optional

from src.layer1_research.backtesting.data.models import MarketFilter, MarketInfo, RawTrade


class DataLoader(ABC):
    """Base class for loading prediction market data from external sources.

    Implementations normalize source-specific formats into unified
    RawTrade and MarketInfo types for ETL into NautilusTrader's catalog.
    """

    @abstractmethod
    def load_markets(
        self, filters: Optional[MarketFilter] = None
    ) -> list[MarketInfo]:
        """Load market metadata, optionally filtered."""
        ...

    @abstractmethod
    def get_trades(
        self,
        token_id: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Iterator[RawTrade]:
        """Yield trades for a specific token ID within a date range.

        Note: token_id is the CLOB token ID (e.g., "tok_yes_001"),
        not the market condition_id. Each YES/NO outcome has its own token.
        """
        ...
