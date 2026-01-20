#!/usr/bin/env python3
"""
Limit Order Example

Simple example of placing a limit order.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import get_config
from src.client import PolymarketClient
from src.markets import MarketFetcher
from src.trading import TradingClient
from src.orderbook import OrderbookAnalyzer


def main():
    """Place a limit order example."""
    print("\n" + "#"*60)
    print("# Limit Order Example")
    print("#"*60)
    
    config = get_config()
    
    if not config.has_credentials:
        print("\n❌ Credentials required for trading")
        print("   Set PRIVATE_KEY and FUNDER_ADDRESS in .env")
        return
    
    print(f"\n⚙️  Dry Run: {config.dry_run}")
    
    # Connect
    client = PolymarketClient(config)
    if not client.connect():
        print("❌ Failed to connect")
        return
    
    # Get a market
    fetcher = MarketFetcher()
    markets = fetcher.get_all_markets(limit=1)
    
    if not markets:
        print("❌ No markets found")
        return
    
    market = markets[0]
    print(f"\n📊 Market: {market.question[:50]}...")
    
    if not market.token_ids:
        print("❌ No token IDs found")
        return
    
    token_id = market.token_ids[0]  # YES token
    
    # Get current price
    analyzer = OrderbookAnalyzer(client.clob)
    book = analyzer.get_orderbook(token_id)
    
    if book and book.midpoint:
        current_mid = book.midpoint
        print(f"  Current midpoint: {current_mid:.4f}")
        
        # Place a limit buy order below market (unlikely to fill immediately)
        buy_price = round(current_mid * 0.9, 2)  # 10% below midpoint
        size = 5.0  # 5 shares
        
        print(f"\n📝 Placing limit BUY order:")
        print(f"   Price: {buy_price}")
        print(f"   Size: {size}")
        
        trader = TradingClient(client.clob)
        result = trader.place_limit_order(
            token_id=token_id,
            side="BUY",
            price=buy_price,
            size=size,
            order_type="GTC"  # Good-til-cancelled
        )
        
        if result.success:
            print(f"\n✅ Order placed!")
            print(f"   Order ID: {result.order_id}")
        else:
            print(f"\n❌ Order failed: {result.error}")
        
        # Show open orders
        orders = trader.get_open_orders()
        print(f"\n📋 Open orders: {len(orders)}")


if __name__ == "__main__":
    main()
