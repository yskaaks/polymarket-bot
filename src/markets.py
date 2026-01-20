"""
Market Discovery Module

Fetch and filter markets using the Gamma API.
Ideal for finding daily up/down markets for market making.
"""

import requests
from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

import sys
sys.path.insert(0, str(__file__).rsplit("src", 1)[0])
from config.settings import get_config


@dataclass
class Market:
    """Represents a Polymarket market."""
    id: str
    question: str
    slug: str
    condition_id: str
    token_ids: list[str]
    outcomes: list[str]
    outcome_prices: list[float]
    volume: float
    volume_24h: float
    liquidity: float
    end_date: Optional[datetime]
    active: bool
    closed: bool
    category: str
    
    @property
    def best_yes_price(self) -> float:
        """Get current YES price."""
        return self.outcome_prices[0] if self.outcome_prices else 0.0
    
    @property
    def best_no_price(self) -> float:
        """Get current NO price."""
        return self.outcome_prices[1] if len(self.outcome_prices) > 1 else 0.0
    
    @property
    def implied_probability(self) -> float:
        """Get implied probability (YES price)."""
        return self.best_yes_price
    
    @property
    def spread(self) -> float:
        """Calculate bid-ask spread approximation."""
        # Since prices sum to ~1, spread is related to gap from 1.0
        return 1.0 - (self.best_yes_price + self.best_no_price)


class MarketFetcher:
    """Fetches and filters markets from Gamma API."""
    
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or get_config().GAMMA_API_URL
    
    def _get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """Make GET request to Gamma API."""
        url = f"{self.base_url}{endpoint}"
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    
    def _parse_market(self, data: dict) -> Market:
        """Parse API response into Market object."""
        # Parse outcomes and prices (stored as JSON strings)
        outcomes = []
        outcome_prices = []
        
        if data.get("outcomes"):
            try:
                import json
                outcomes = json.loads(data["outcomes"]) if isinstance(data["outcomes"], str) else data["outcomes"]
            except:
                outcomes = ["Yes", "No"]
        
        if data.get("outcomePrices"):
            try:
                import json
                prices_raw = json.loads(data["outcomePrices"]) if isinstance(data["outcomePrices"], str) else data["outcomePrices"]
                outcome_prices = [float(p) for p in prices_raw]
            except:
                outcome_prices = [0.0, 0.0]
        
        # Parse token IDs
        token_ids = []
        if data.get("clobTokenIds"):
            try:
                import json
                token_ids = json.loads(data["clobTokenIds"]) if isinstance(data["clobTokenIds"], str) else data["clobTokenIds"]
            except:
                token_ids = []
        
        # Parse end date
        end_date = None
        if data.get("endDate"):
            try:
                end_date = datetime.fromisoformat(data["endDate"].replace("Z", "+00:00"))
            except:
                pass
        
        return Market(
            id=data.get("id", ""),
            question=data.get("question", ""),
            slug=data.get("slug", ""),
            condition_id=data.get("conditionId", ""),
            token_ids=token_ids,
            outcomes=outcomes,
            outcome_prices=outcome_prices,
            volume=float(data.get("volume", 0) or 0),
            volume_24h=float(data.get("volume24hr", 0) or 0),
            liquidity=float(data.get("liquidity", 0) or 0),
            end_date=end_date,
            active=data.get("active", False),
            closed=data.get("closed", False),
            category=data.get("category", "")
        )
    
    def get_all_markets(
        self,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
        order: str = "volume24hr",
        ascending: bool = False
    ) -> list[Market]:
        """
        Fetch all markets with filtering.
        
        Args:
            active: Only return active markets
            closed: Include closed markets
            limit: Max markets to return
            offset: Pagination offset
            order: Field to order by (volume24hr, liquidity, etc.)
            ascending: Order direction
        
        Returns:
            List of Market objects
        """
        params = {
            "limit": limit,
            "offset": offset,
            "order": order,
            "ascending": str(ascending).lower(),
            "closed": str(closed).lower()
        }
        
        if active:
            params["active"] = "true"
        
        data = self._get("/markets", params)
        
        # API returns list directly
        markets = []
        for item in data:
            try:
                markets.append(self._parse_market(item))
            except Exception as e:
                print(f"Error parsing market: {e}")
                continue
        
        return markets
    
    def get_market_by_slug(self, slug: str) -> Optional[Market]:
        """
        Get a specific market by its slug.
        
        Args:
            slug: Market slug (e.g., "will-btc-hit-100k-in-2024")
        
        Returns:
            Market object or None
        """
        params = {"slug": slug}
        data = self._get("/markets", params)
        
        if data and len(data) > 0:
            return self._parse_market(data[0])
        return None
    
    def get_market_by_id(self, market_id: str) -> Optional[Market]:
        """
        Get a specific market by ID.
        
        Args:
            market_id: Market ID
        
        Returns:
            Market object or None
        """
        params = {"id": market_id}
        data = self._get("/markets", params)
        
        if data and len(data) > 0:
            return self._parse_market(data[0])
        return None
    
    def get_daily_markets(self, limit: int = 50) -> list[Market]:
        """
        Get markets ending within 24 hours.
        Ideal for market making on short-term markets.
        
        Args:
            limit: Max markets to return
        
        Returns:
            List of markets ending soon
        """
        now = datetime.utcnow()
        tomorrow = now + timedelta(days=1)
        
        params = {
            "limit": limit,
            "active": "true",
            "closed": "false",
            "end_date_min": now.isoformat() + "Z",
            "end_date_max": tomorrow.isoformat() + "Z",
            "order": "volume24hr",
            "ascending": "false"
        }
        
        data = self._get("/markets", params)
        
        markets = []
        for item in data:
            try:
                markets.append(self._parse_market(item))
            except:
                continue
        
        return markets
    
    def get_high_volume_markets(
        self,
        min_volume_24h: float = 10000,
        min_liquidity: float = 5000,
        limit: int = 50
    ) -> list[Market]:
        """
        Get markets with high volume and liquidity.
        Better for market making due to more activity.
        
        Args:
            min_volume_24h: Minimum 24h volume in USDC
            min_liquidity: Minimum liquidity
            limit: Max markets
        
        Returns:
            High volume/liquidity markets
        """
        params = {
            "limit": limit,
            "active": "true",
            "closed": "false",
            "liquidity_num_min": min_liquidity,
            "order": "volume24hr",
            "ascending": "false"
        }
        
        data = self._get("/markets", params)
        
        markets = []
        for item in data:
            try:
                market = self._parse_market(item)
                if market.volume_24h >= min_volume_24h:
                    markets.append(market)
            except:
                continue
        
        return markets
    
    def search_markets(self, query: str, limit: int = 20) -> list[Market]:
        """
        Search markets by question text.
        
        Args:
            query: Search query
            limit: Max results
        
        Returns:
            Matching markets
        """
        # Gamma API doesn't have direct search, so we filter locally
        all_markets = self.get_all_markets(limit=200)
        query_lower = query.lower()
        
        matches = [
            m for m in all_markets
            if query_lower in m.question.lower()
        ]
        
        return matches[:limit]


# Convenience functions
def get_markets(limit: int = 50) -> list[Market]:
    """Quick function to get active markets."""
    fetcher = MarketFetcher()
    return fetcher.get_all_markets(limit=limit)


def get_market(slug_or_id: str) -> Optional[Market]:
    """Quick function to get a single market."""
    fetcher = MarketFetcher()
    
    # Try by slug first, then by ID
    market = fetcher.get_market_by_slug(slug_or_id)
    if not market:
        market = fetcher.get_market_by_id(slug_or_id)
    
    return market
