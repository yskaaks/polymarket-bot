"""
WebSocket Feed Module

Real-time data streaming from Polymarket.
"""

import asyncio
import json
from typing import Callable, Optional
from dataclasses import dataclass
import websockets

import sys
sys.path.insert(0, str(__file__).rsplit("src", 1)[0])
from config.settings import get_config


@dataclass
class PriceUpdate:
    """Price update event."""
    token_id: str
    price: float
    timestamp: str


@dataclass
class TradeUpdate:
    """Trade event."""
    token_id: str
    price: float
    size: float
    side: str
    timestamp: str


@dataclass
class OrderbookUpdate:
    """Orderbook change event."""
    token_id: str
    bids: list[dict]
    asks: list[dict]
    timestamp: str


class WebSocketFeed:
    """
    Real-time WebSocket feed for Polymarket data.
    
    Supports:
    - Price updates
    - Trade notifications
    - Orderbook changes
    """
    
    def __init__(self, url: Optional[str] = None):
        """Initialize WebSocket feed."""
        self.url = url or get_config().WEBSOCKET_URL
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._callbacks: dict[str, list[Callable]] = {
            "price": [],
            "trade": [],
            "orderbook": [],
            "error": [],
        }
        self._subscriptions: set[str] = set()
    
    def on_price(self, callback: Callable[[PriceUpdate], None]):
        """Register callback for price updates."""
        self._callbacks["price"].append(callback)
    
    def on_trade(self, callback: Callable[[TradeUpdate], None]):
        """Register callback for trade events."""
        self._callbacks["trade"].append(callback)
    
    def on_orderbook(self, callback: Callable[[OrderbookUpdate], None]):
        """Register callback for orderbook changes."""
        self._callbacks["orderbook"].append(callback)
    
    def on_error(self, callback: Callable[[Exception], None]):
        """Register callback for errors."""
        self._callbacks["error"].append(callback)
    
    async def connect(self):
        """Establish WebSocket connection."""
        try:
            self._ws = await websockets.connect(self.url)
            self._running = True
            print(f"Connected to WebSocket: {self.url}")
        except Exception as e:
            print(f"WebSocket connection failed: {e}")
            raise
    
    async def disconnect(self):
        """Close WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        print("Disconnected from WebSocket")
    
    async def subscribe_market(self, token_id: str):
        """
        Subscribe to updates for a market.
        
        Args:
            token_id: CLOB token ID
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        # Polymarket WebSocket subscription format
        subscribe_msg = {
            "type": "subscribe",
            "channel": "market",
            "markets": [token_id]
        }
        
        await self._ws.send(json.dumps(subscribe_msg))
        self._subscriptions.add(token_id)
        print(f"Subscribed to market: {token_id}")
    
    async def unsubscribe_market(self, token_id: str):
        """
        Unsubscribe from market updates.
        
        Args:
            token_id: CLOB token ID
        """
        if not self._ws:
            return
        
        unsubscribe_msg = {
            "type": "unsubscribe", 
            "channel": "market",
            "markets": [token_id]
        }
        
        await self._ws.send(json.dumps(unsubscribe_msg))
        self._subscriptions.discard(token_id)
    
    async def _handle_message(self, data: dict):
        """Process incoming WebSocket message."""
        msg_type = data.get("type", "")
        
        if msg_type == "price_change":
            update = PriceUpdate(
                token_id=data.get("asset_id", ""),
                price=float(data.get("price", 0)),
                timestamp=data.get("timestamp", "")
            )
            for cb in self._callbacks["price"]:
                try:
                    cb(update)
                except Exception as e:
                    print(f"Price callback error: {e}")
        
        elif msg_type == "trade":
            update = TradeUpdate(
                token_id=data.get("asset_id", ""),
                price=float(data.get("price", 0)),
                size=float(data.get("size", 0)),
                side=data.get("side", ""),
                timestamp=data.get("timestamp", "")
            )
            for cb in self._callbacks["trade"]:
                try:
                    cb(update)
                except Exception as e:
                    print(f"Trade callback error: {e}")
        
        elif msg_type == "book":
            update = OrderbookUpdate(
                token_id=data.get("asset_id", ""),
                bids=data.get("bids", []),
                asks=data.get("asks", []),
                timestamp=data.get("timestamp", "")
            )
            for cb in self._callbacks["orderbook"]:
                try:
                    cb(update)
                except Exception as e:
                    print(f"Orderbook callback error: {e}")
    
    async def listen(self):
        """
        Start listening for messages.
        
        This is a blocking call - run in background task.
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        try:
            async for message in self._ws:
                if not self._running:
                    break
                
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    print(f"Invalid JSON: {message[:100]}")
                except Exception as e:
                    for cb in self._callbacks["error"]:
                        cb(e)
                    
        except websockets.exceptions.ConnectionClosed:
            print("WebSocket connection closed")
        except Exception as e:
            print(f"WebSocket error: {e}")
            for cb in self._callbacks["error"]:
                cb(e)
    
    async def run(self, token_ids: list[str]):
        """
        Connect, subscribe, and listen.
        
        Args:
            token_ids: List of token IDs to subscribe to
        """
        await self.connect()
        
        for token_id in token_ids:
            await self.subscribe_market(token_id)
        
        await self.listen()


# Synchronous wrapper for simpler usage
class SyncWebSocketFeed:
    """Synchronous wrapper around WebSocketFeed."""
    
    def __init__(self):
        self._feed = WebSocketFeed()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    def on_price(self, callback: Callable):
        """Register price callback."""
        self._feed.on_price(callback)
    
    def on_trade(self, callback: Callable):
        """Register trade callback."""
        self._feed.on_trade(callback)
    
    def on_orderbook(self, callback: Callable):
        """Register orderbook callback."""
        self._feed.on_orderbook(callback)
    
    def start(self, token_ids: list[str]):
        """
        Start listening (blocking).
        
        Args:
            token_ids: Token IDs to subscribe to
        """
        asyncio.run(self._feed.run(token_ids))
    
    def start_background(self, token_ids: list[str]):
        """
        Start in background thread.
        
        Args:
            token_ids: Token IDs to subscribe to
        
        Returns:
            Thread object
        """
        import threading
        
        def run():
            asyncio.run(self._feed.run(token_ids))
        
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread


# Example usage
async def example_websocket_usage():
    """Example of WebSocket feed usage."""
    feed = WebSocketFeed()
    
    # Register callbacks
    feed.on_price(lambda p: print(f"Price: {p.token_id} = {p.price}"))
    feed.on_trade(lambda t: print(f"Trade: {t.size} @ {t.price}"))
    
    # Connect and subscribe
    await feed.connect()
    await feed.subscribe_market("example_token_id")
    
    # Listen for 60 seconds
    try:
        await asyncio.wait_for(feed.listen(), timeout=60)
    except asyncio.TimeoutError:
        pass
    finally:
        await feed.disconnect()


if __name__ == "__main__":
    # For testing
    asyncio.run(example_websocket_usage())
