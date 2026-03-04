"""
WebSocket Feed Module

Real-time data streaming from Polymarket.
"""

import asyncio
import json
import logging
from typing import Callable, Optional
from dataclasses import dataclass
import websockets

logger = logging.getLogger(__name__)

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
    
    def __init__(self, url: Optional[str] = None, max_reconnect_delay: float = 60.0):
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
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = max_reconnect_delay
    
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
            logger.info(f"Connected to WebSocket: {self.url}")
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            raise
    
    async def disconnect(self):
        """Close WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("Disconnected from WebSocket")
    
    async def subscribe_market(self, token_id: str):
        """
        Subscribe to updates for a market.

        Args:
            token_id: CLOB token ID
        """
        if not self._ws:
            raise RuntimeError("Not connected")

        self._subscriptions.add(token_id)
        # Send a single subscription message with all current asset IDs
        await self._send_subscription()
        logger.info(f"Subscribed to market: {token_id}")

    async def _send_subscription(self):
        """Send subscription message with all tracked asset IDs."""
        if not self._ws or not self._subscriptions:
            return
        subscribe_msg = {
            "type": "market",
            "assets_ids": list(self._subscriptions),
        }
        await self._ws.send(json.dumps(subscribe_msg))
    
    async def unsubscribe_market(self, token_id: str):
        """
        Unsubscribe from market updates.

        Args:
            token_id: CLOB token ID
        """
        if not self._ws:
            return

        self._subscriptions.discard(token_id)
        # Re-send subscription with remaining asset IDs (replaces previous subscription)
        if self._subscriptions:
            await self._send_subscription()
        else:
            # No subscriptions left — close and let reconnect loop handle it
            await self._ws.close()
    
    async def _handle_message(self, raw: str):
        """Process incoming WebSocket message.

        Polymarket sends two kinds of messages:
        - Initial book snapshot: a JSON *array* with asset_id, bids, asks
        - Subsequent updates: a JSON object with ``price_changes`` list
        """
        data = json.loads(raw)

        # Initial orderbook snapshot comes as an array
        if isinstance(data, list):
            for entry in data:
                update = OrderbookUpdate(
                    token_id=entry.get("asset_id", ""),
                    bids=entry.get("bids", []),
                    asks=entry.get("asks", []),
                    timestamp=entry.get("timestamp", ""),
                )
                for cb in self._callbacks["orderbook"]:
                    try:
                        cb(update)
                    except Exception as e:
                        logger.error(f"Orderbook callback error: {e}")
            return

        # Price / trade change events
        if "price_changes" in data:
            for change in data["price_changes"]:
                asset_id = change.get("asset_id", "")
                if asset_id not in self._subscriptions:
                    continue

                price_update = PriceUpdate(
                    token_id=asset_id,
                    price=float(change.get("price", 0)),
                    timestamp=data.get("timestamp", change.get("timestamp", "")),
                )
                for cb in self._callbacks["price"]:
                    try:
                        cb(price_update)
                    except Exception as e:
                        logger.error(f"Price callback error: {e}")

                # Also emit as trade when size is present
                size = float(change.get("size", 0))
                if size > 0:
                    trade_update = TradeUpdate(
                        token_id=asset_id,
                        price=float(change.get("price", 0)),
                        size=size,
                        side=change.get("side", ""),
                        timestamp=data.get("timestamp", change.get("timestamp", "")),
                    )
                    for cb in self._callbacks["trade"]:
                        try:
                            cb(trade_update)
                        except Exception as e:
                            logger.error(f"Trade callback error: {e}")
    
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
                    await self._handle_message(message)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON: {message[:100]}")
                except Exception as e:
                    logger.error(f"Message handler error: {e}")
                    for cb in self._callbacks["error"]:
                        cb(e)

        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            for cb in self._callbacks["error"]:
                cb(e)

    async def run(self, token_ids: list[str]):
        """
        Connect, subscribe, and listen with auto-reconnect.

        Args:
            token_ids: List of token IDs to subscribe to
        """
        self._running = True
        self._subscriptions = set(token_ids)
        delay = self._reconnect_delay

        while self._running:
            try:
                await self.connect()
                delay = self._reconnect_delay  # reset on successful connect
                await self._send_subscription()
                await self.listen()
            except Exception as e:
                logger.error(f"WebSocket run error: {e}")

            if self._running:
                logger.info(f"Reconnecting in {delay:.1f}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._max_reconnect_delay)


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
