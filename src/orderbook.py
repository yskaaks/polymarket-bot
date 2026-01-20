"""
Orderbook Module

Analyze orderbook depth, spreads, and calculate execution prices.
"""

import sys
from typing import Optional
from dataclasses import dataclass

from py_clob_client.clob_types import BookParams

sys.path.insert(0, str(__file__).rsplit("src", 1)[0])
from config.settings import get_config


@dataclass
class OrderbookLevel:
    """Single price level in orderbook."""
    price: float
    size: float


@dataclass
class Orderbook:
    """Full orderbook for a token."""
    token_id: str
    bids: list[OrderbookLevel]  # Buy orders (sorted high to low)
    asks: list[OrderbookLevel]  # Sell orders (sorted low to high)
    timestamp: Optional[str] = None
    
    @property
    def best_bid(self) -> Optional[float]:
        """Highest bid price."""
        return self.bids[0].price if self.bids else None
    
    @property
    def best_ask(self) -> Optional[float]:
        """Lowest ask price."""
        return self.asks[0].price if self.asks else None
    
    @property
    def best_bid_size(self) -> Optional[float]:
        """Size at best bid."""
        return self.bids[0].size if self.bids else None
    
    @property
    def best_ask_size(self) -> Optional[float]:
        """Size at best ask."""
        return self.asks[0].size if self.asks else None
    
    @property
    def midpoint(self) -> Optional[float]:
        """Midpoint between best bid and ask."""
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None
    
    @property
    def spread(self) -> Optional[float]:
        """Absolute spread between best bid and ask."""
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None
    
    @property
    def spread_percent(self) -> Optional[float]:
        """Spread as percentage of midpoint."""
        mid = self.midpoint
        spread = self.spread
        if mid and spread and mid > 0:
            return (spread / mid) * 100
        return None
    
    def total_bid_depth(self, levels: int = 5) -> float:
        """Total size on bid side up to N levels."""
        return sum(level.size for level in self.bids[:levels])
    
    def total_ask_depth(self, levels: int = 5) -> float:
        """Total size on ask side up to N levels."""
        return sum(level.size for level in self.asks[:levels])
    
    def imbalance(self, levels: int = 5) -> float:
        """
        Order book imbalance ratio.
        
        Returns:
            Positive = more bids (bullish)
            Negative = more asks (bearish)
            Range: -1 to 1
        """
        bid_depth = self.total_bid_depth(levels)
        ask_depth = self.total_ask_depth(levels)
        total = bid_depth + ask_depth
        
        if total == 0:
            return 0
        
        return (bid_depth - ask_depth) / total


class OrderbookAnalyzer:
    """Fetches and analyzes orderbooks."""
    
    def __init__(self, clob_client):
        """
        Initialize with CLOB client.
        
        Args:
            clob_client: ClobClient instance (can be read-only)
        """
        self.clob = clob_client
    
    def get_orderbook(self, token_id: str) -> Optional[Orderbook]:
        """
        Fetch orderbook for a token.
        
        Args:
            token_id: CLOB token ID
        
        Returns:
            Orderbook object or None on error
        """
        try:
            raw_book = self.clob.get_order_book(token_id)
            
            # Parse bids
            bids = []
            if raw_book.bids:
                for bid in raw_book.bids:
                    bids.append(OrderbookLevel(
                        price=float(bid.price),
                        size=float(bid.size)
                    ))
            
            # Parse asks
            asks = []
            if raw_book.asks:
                for ask in raw_book.asks:
                    asks.append(OrderbookLevel(
                        price=float(ask.price),
                        size=float(ask.size)
                    ))
            
            # Sort: bids high to low, asks low to high
            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price)
            
            return Orderbook(
                token_id=token_id,
                bids=bids,
                asks=asks,
                timestamp=getattr(raw_book, 'timestamp', None)
            )
            
        except Exception as e:
            print(f"Error fetching orderbook: {e}")
            return None
    
    def get_multiple_orderbooks(self, token_ids: list[str]) -> dict[str, Orderbook]:
        """
        Fetch orderbooks for multiple tokens.
        
        Args:
            token_ids: List of CLOB token IDs
        
        Returns:
            Dict mapping token_id to Orderbook
        """
        result = {}
        
        try:
            params = [BookParams(token_id=tid) for tid in token_ids]
            raw_books = self.clob.get_order_books(params)
            
            for i, raw_book in enumerate(raw_books):
                token_id = token_ids[i]
                
                bids = []
                if raw_book.bids:
                    for bid in raw_book.bids:
                        bids.append(OrderbookLevel(
                            price=float(bid.price),
                            size=float(bid.size)
                        ))
                
                asks = []
                if raw_book.asks:
                    for ask in raw_book.asks:
                        asks.append(OrderbookLevel(
                            price=float(ask.price),
                            size=float(ask.size)
                        ))
                
                bids.sort(key=lambda x: x.price, reverse=True)
                asks.sort(key=lambda x: x.price)
                
                result[token_id] = Orderbook(
                    token_id=token_id,
                    bids=bids,
                    asks=asks
                )
                
        except Exception as e:
            print(f"Error fetching orderbooks: {e}")
        
        return result
    
    def get_midpoint(self, token_id: str) -> Optional[float]:
        """Get midpoint price for a token."""
        try:
            return self.clob.get_midpoint(token_id)
        except:
            book = self.get_orderbook(token_id)
            return book.midpoint if book else None
    
    def get_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """Get executable price for a side."""
        try:
            return self.clob.get_price(token_id, side=side)
        except Exception as e:
            print(f"Error getting price: {e}")
            return None
    
    def calculate_slippage(
        self,
        token_id: str,
        side: str,
        amount: float
    ) -> dict:
        """
        Calculate expected slippage for an order.
        
        Args:
            token_id: CLOB token ID
            side: "BUY" or "SELL"
            amount: Order size in shares
        
        Returns:
            Dict with avg_price, slippage_pct, filled_amount
        """
        book = self.get_orderbook(token_id)
        if not book:
            return {"error": "Could not fetch orderbook"}
        
        levels = book.asks if side == "BUY" else book.bids
        
        if not levels:
            return {"error": "No liquidity on this side"}
        
        # Simulate walking the book
        remaining = amount
        total_cost = 0.0
        filled = 0.0
        
        for level in levels:
            if remaining <= 0:
                break
            
            fill_size = min(remaining, level.size)
            total_cost += fill_size * level.price
            filled += fill_size
            remaining -= fill_size
        
        if filled == 0:
            return {"error": "Could not fill any amount"}
        
        avg_price = total_cost / filled
        best_price = levels[0].price
        slippage = ((avg_price - best_price) / best_price) * 100 if side == "BUY" else ((best_price - avg_price) / best_price) * 100
        
        return {
            "avg_price": avg_price,
            "best_price": best_price,
            "slippage_pct": abs(slippage),
            "filled_amount": filled,
            "unfilled_amount": remaining,
            "total_cost": total_cost
        }
    
    def find_arbitrage_opportunity(
        self,
        yes_token_id: str,
        no_token_id: str
    ) -> Optional[dict]:
        """
        Check for arbitrage between YES/NO tokens.
        
        If YES + NO < 1, there's an arbitrage opportunity.
        
        Args:
            yes_token_id: Token ID for YES outcome
            no_token_id: Token ID for NO outcome
        
        Returns:
            Arbitrage details if opportunity exists
        """
        yes_book = self.get_orderbook(yes_token_id)
        no_book = self.get_orderbook(no_token_id)
        
        if not yes_book or not no_book:
            return None
        
        # Buy YES at ask, buy NO at ask
        yes_ask = yes_book.best_ask
        no_ask = no_book.best_ask
        
        if yes_ask is None or no_ask is None:
            return None
        
        total_cost = yes_ask + no_ask
        
        # If total cost < 1, we can buy both sides for less than guaranteed payout
        if total_cost < 1.0:
            profit = 1.0 - total_cost
            return {
                "opportunity": True,
                "yes_price": yes_ask,
                "no_price": no_ask,
                "total_cost": total_cost,
                "profit_per_share": profit,
                "profit_pct": (profit / total_cost) * 100
            }
        
        return {"opportunity": False, "total_cost": total_cost}
