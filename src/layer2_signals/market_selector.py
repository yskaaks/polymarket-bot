"""
Market selector for market-making strategy.

Scans active markets, filters by spread/liquidity/volume criteria,
and scores candidates for quoting.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from config.settings import get_config
from src.layer0_ingestion.polymarket_gamma import MarketFetcher, Market
from src.orderbook import Orderbook, OrderbookAnalyzer

logger = logging.getLogger(__name__)


@dataclass
class MarketCandidate:
    """A market that passed screening with its score."""
    market: Market
    orderbook: Orderbook
    score: float
    spread: float
    midpoint: float
    bid_depth: float
    ask_depth: float

    @property
    def token_id(self) -> str:
        """Primary token ID (YES outcome)."""
        return self.market.token_ids[0] if self.market.token_ids else ""


class MarketSelector:
    """
    Scans and ranks markets suitable for market making.

    Filter criteria:
    - Spread between mm_min_spread and mm_max_spread
    - Liquidity > mm_min_liquidity
    - 24h volume > mm_min_volume_24h
    - Price in 0.10-0.90 (avoid extreme probabilities)
    - Expiry > 24h (avoid settlement risk)

    Scoring weights spread width, volume, depth, and price centrality.
    """

    def __init__(self, fetcher: MarketFetcher, analyzer: OrderbookAnalyzer):
        self.fetcher = fetcher
        self.analyzer = analyzer
        self.config = get_config()

    def scan(self, max_candidates: Optional[int] = None) -> list[MarketCandidate]:
        """
        Scan markets and return ranked candidates.

        Args:
            max_candidates: Max candidates to return (defaults to mm_max_markets)

        Returns:
            Sorted list of MarketCandidate (best first)
        """
        max_candidates = max_candidates or self.config.mm_max_markets

        markets = self.fetcher.get_high_volume_markets(
            min_volume_24h=self.config.mm_min_volume_24h,
            min_liquidity=self.config.mm_min_liquidity,
            limit=50,
        )

        logger.info(f"Scanned {len(markets)} markets from Gamma API")

        candidates = []
        for market in markets:
            candidate = self._evaluate(market)
            if candidate is not None:
                candidates.append(candidate)

        candidates.sort(key=lambda c: c.score, reverse=True)
        result = candidates[:max_candidates]

        logger.info(
            f"Selected {len(result)}/{len(candidates)} candidates "
            f"(from {len(markets)} markets)"
        )
        for c in result:
            logger.info(
                f"  {c.market.question[:60]} | spread={c.spread:.2%} "
                f"mid={c.midpoint:.4f} score={c.score:.2f}"
            )

        return result

    def _evaluate(self, market: Market) -> Optional[MarketCandidate]:
        """Evaluate a single market. Returns MarketCandidate if it passes filters."""
        if not market.token_ids:
            return None

        # Filter: price in 0.10-0.90
        yes_price = market.best_yes_price
        if yes_price < 0.10 or yes_price > 0.90:
            return None

        # Filter: expiry > 24h
        if market.end_date:
            now = datetime.now(timezone.utc)
            end = market.end_date if market.end_date.tzinfo else market.end_date.replace(tzinfo=timezone.utc)
            hours_until_expiry = (end - now).total_seconds() / 3600
            if hours_until_expiry < 24:
                return None

        # Fetch orderbook for YES token
        token_id = market.token_ids[0]
        orderbook = self.analyzer.get_orderbook(token_id)
        if orderbook is None or orderbook.midpoint is None:
            return None

        spread = orderbook.spread
        if spread is None:
            return None

        midpoint = orderbook.midpoint
        spread_pct = spread / midpoint if midpoint > 0 else 0

        # Filter: spread in [min_spread, max_spread]
        if spread_pct < self.config.mm_min_spread or spread_pct > self.config.mm_max_spread:
            return None

        bid_depth = orderbook.total_bid_depth(levels=5)
        ask_depth = orderbook.total_ask_depth(levels=5)

        # Score: weighted combination
        score = self._score(spread_pct, market.volume_24h, bid_depth + ask_depth, yes_price)

        return MarketCandidate(
            market=market,
            orderbook=orderbook,
            score=score,
            spread=spread_pct,
            midpoint=midpoint,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
        )

    def _score(
        self,
        spread_pct: float,
        volume_24h: float,
        depth: float,
        price: float,
    ) -> float:
        """
        Score a market candidate.
        Higher = better for market making.
        """
        # Wider spread = more profit opportunity (weight: 40%)
        spread_score = min(spread_pct / 0.20, 1.0) * 40

        # Higher volume = more fill opportunities (weight: 25%)
        volume_score = min(volume_24h / 50000, 1.0) * 25

        # Deeper book = easier to manage (weight: 20%)
        depth_score = min(depth / 2000, 1.0) * 20

        # Price centrality: closer to 0.50 = better (weight: 15%)
        centrality = 1.0 - abs(price - 0.50) * 2  # 0-1, peaks at 0.50
        centrality_score = centrality * 15

        return spread_score + volume_score + depth_score + centrality_score
