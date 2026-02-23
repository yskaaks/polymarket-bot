"""
Trading Module

Handle order placement, cancellation, and management via CLOB API.
"""

import sys
from typing import Optional, Literal
from dataclasses import dataclass
from enum import Enum

from py_clob_client.clob_types import (
    OrderArgs,
    MarketOrderArgs,
    OrderType,
    OpenOrderParams
)
from py_clob_client.order_builder.constants import BUY, SELL

sys.path.insert(0, str(__file__).rsplit("src", 1)[0])
from config.settings import get_config


class Side(Enum):
    """Order side."""
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class OrderResult:
    """Result of an order operation."""
    success: bool
    order_id: Optional[str] = None
    error: Optional[str] = None
    data: Optional[dict] = None


class TradingClient:
    """
    Handles all trading operations on Polymarket.
    
    Supports:
    - Limit orders (GTC, GTD, FOK)
    - Market orders
    - Order cancellation
    - Order management
    """
    
    def __init__(self, clob_client):
        """
        Initialize trading client.
        
        Args:
            clob_client: Authenticated ClobClient instance
        """
        self.clob = clob_client
        self.config = get_config()
    
    def _check_dry_run(self, operation: str) -> Optional[OrderResult]:
        """Check if we're in dry run mode and return mock result."""
        if self.config.dry_run:
            return OrderResult(
                success=True,
                order_id="DRY_RUN_ORDER",
                error=None,
                data={"dry_run": True, "operation": operation}
            )
        return None
    
    def place_limit_order(
        self,
        token_id: str,
        side: Literal["BUY", "SELL"],
        price: float,
        size: float,
        order_type: str = "GTC",
        expiration: Optional[int] = None
    ) -> OrderResult:
        """
        Place a limit order.
        
        Args:
            token_id: CLOB token ID for the outcome
            side: "BUY" or "SELL"
            price: Price per share (0.01 to 0.99)
            size: Number of shares
            order_type: "GTC" (Good-Til-Cancelled), "GTD" (Good-Til-Date), "FOK" (Fill-or-Kill)
            expiration: Unix timestamp for GTD orders
        
        Returns:
            OrderResult with order ID if successful
        """
        # Check dry run
        dry_result = self._check_dry_run(f"limit_order_{side}_{price}x{size}")
        if dry_result:
            print(f"[DRY RUN] Would place limit {side} order: {size} @ {price}")
            return dry_result
        
        try:
            # Validate inputs
            if not 0.01 <= price <= 0.99:
                return OrderResult(success=False, error="Price must be between 0.01 and 0.99")
            
            if size <= 0:
                return OrderResult(success=False, error="Size must be positive")
            
            # Build order
            order_side = BUY if side == "BUY" else SELL
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=order_side
            )
            
            # Determine order type enum
            if order_type == "GTC":
                ot = OrderType.GTC
            elif order_type == "GTD":
                ot = OrderType.GTD
            elif order_type == "FOK":
                ot = OrderType.FOK
            else:
                return OrderResult(success=False, error=f"Unknown order type: {order_type}")
            
            # Create and sign order
            signed_order = self.clob.create_order(order_args)
            
            # Post order
            response = self.clob.post_order(signed_order, ot)
            
            return OrderResult(
                success=True,
                order_id=response.get("orderID") or response.get("id"),
                data=response
            )
            
        except Exception as e:
            return OrderResult(success=False, error=str(e))
    
    def place_market_order(
        self,
        token_id: str,
        side: Literal["BUY", "SELL"],
        amount: float
    ) -> OrderResult:
        """
        Place a market order (immediate execution).
        
        Args:
            token_id: CLOB token ID
            side: "BUY" or "SELL"
            amount: USDC amount to spend (for BUY) or shares to sell (for SELL)
        
        Returns:
            OrderResult with execution details
        """
        dry_result = self._check_dry_run(f"market_order_{side}_{amount}")
        if dry_result:
            print(f"[DRY RUN] Would place market {side} order: ${amount}")
            return dry_result
        
        try:
            order_side = BUY if side == "BUY" else SELL
            
            market_order = MarketOrderArgs(
                token_id=token_id,
                amount=amount,
                side=order_side
            )
            
            signed_order = self.clob.create_market_order(market_order)
            response = self.clob.post_order(signed_order, OrderType.FOK)
            
            return OrderResult(
                success=True,
                order_id=response.get("orderID"),
                data=response
            )
            
        except Exception as e:
            return OrderResult(success=False, error=str(e))
    
    def cancel_order(self, order_id: str) -> OrderResult:
        """
        Cancel a single order.
        
        Args:
            order_id: Order ID to cancel
        
        Returns:
            OrderResult indicating success/failure
        """
        dry_result = self._check_dry_run(f"cancel_{order_id}")
        if dry_result:
            print(f"[DRY RUN] Would cancel order: {order_id}")
            return dry_result
        
        try:
            response = self.clob.cancel(order_id)
            return OrderResult(success=True, data={"cancelled": order_id})
        except Exception as e:
            return OrderResult(success=False, error=str(e))
    
    def cancel_all_orders(self) -> OrderResult:
        """
        Cancel all open orders.
        
        Returns:
            OrderResult with cancellation details
        """
        dry_result = self._check_dry_run("cancel_all")
        if dry_result:
            print("[DRY RUN] Would cancel all orders")
            return dry_result
        
        try:
            response = self.clob.cancel_all()
            return OrderResult(success=True, data={"cancelled_all": True})
        except Exception as e:
            return OrderResult(success=False, error=str(e))
    
    def get_open_orders(self, market: Optional[str] = None) -> list[dict]:
        """
        Get all open orders.
        
        Args:
            market: Optional market filter
        
        Returns:
            List of open order objects
        """
        try:
            params = OpenOrderParams()
            if market:
                params.market = market
            
            orders = self.clob.get_orders(params)
            return orders if orders else []
        except Exception as e:
            print(f"Error fetching orders: {e}")
            return []
    
    def get_trades(self, limit: int = 100) -> list[dict]:
        """
        Get recent trades.
        
        Args:
            limit: Max trades to return
        
        Returns:
            List of trade objects
        """
        try:
            trades = self.clob.get_trades()
            return trades[:limit] if trades else []
        except Exception as e:
            print(f"Error fetching trades: {e}")
            return []
    
    def get_last_trade_price(self, token_id: str) -> Optional[float]:
        """
        Get last trade price for a token.
        
        Args:
            token_id: CLOB token ID
        
        Returns:
            Last trade price or None
        """
        try:
            return self.clob.get_last_trade_price(token_id)
        except Exception as e:
            print(f"Error fetching last trade: {e}")
            return None


# Two-sided order placement for market making
def place_two_sided_orders(
    trading_client: TradingClient,
    token_id: str,
    mid_price: float,
    spread: float,
    size: float
) -> tuple[OrderResult, OrderResult]:
    """
    Place buy and sell orders around a midpoint.
    
    Args:
        trading_client: TradingClient instance
        token_id: CLOB token ID
        mid_price: Midpoint price
        spread: Total spread (will be split on each side)
        size: Order size
    
    Returns:
        Tuple of (buy_result, sell_result)
    """
    half_spread = spread / 2
    buy_price = max(0.01, mid_price - half_spread)
    sell_price = min(0.99, mid_price + half_spread)
    
    buy_result = trading_client.place_limit_order(
        token_id=token_id,
        side="BUY",
        price=round(buy_price, 2),
        size=size
    )
    
    sell_result = trading_client.place_limit_order(
        token_id=token_id,
        side="SELL",
        price=round(sell_price, 2),
        size=size
    )
    
    return buy_result, sell_result
