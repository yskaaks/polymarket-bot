#!/usr/bin/env python3
"""
Market Making Demo

Demonstrates a basic market making strategy:
1. Find a high-volume market
2. Get current orderbook
3. Place two-sided orders around midpoint
4. Monitor and adjust

⚠️ This is for educational purposes. Real market making requires:
- Proper risk management
- Position limits
- Faster execution (use WebSocket)
- Inventory management
"""

import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import get_config, reload_config
from src.layer0_ingestion.polymarket_clob import PolymarketClient
from src.layer0_ingestion.polymarket_gamma import MarketFetcher
from src.layer4_execution.trading import TradingClient, place_two_sided_orders
from src.orderbook import OrderbookAnalyzer
from src.utils import format_usd, format_percent, log_trade


def find_market_for_making(min_volume: float = 10000, min_liquidity: float = 5000):
    """Find a suitable market for market making."""
    print("\n🔍 Finding suitable market...")
    
    fetcher = MarketFetcher()
    markets = fetcher.get_high_volume_markets(
        min_volume_24h=min_volume,
        min_liquidity=min_liquidity,
        limit=10
    )
    
    # Filter for markets with good spreads (potential profit)
    good_markets = []
    for m in markets:
        if m.spread and m.spread > 0.01:  # At least 1% spread
            good_markets.append(m)
    
    if not good_markets:
        print("  No suitable markets found with good spreads")
        return None, None
    
    # Pick the first good one
    market = good_markets[0]
    print(f"\n  Selected: {market.question[:50]}...")
    print(f"  Volume 24h: {format_usd(market.volume_24h)}")
    print(f"  Current spread: {format_percent(market.spread * 100)}")
    
    # Return YES token for simplicity
    if market.token_ids:
        return market, market.token_ids[0]
    return market, None


def analyze_orderbook(analyzer: OrderbookAnalyzer, token_id: str):
    """Analyze orderbook and return trading parameters."""
    print("\n📊 Analyzing orderbook...")
    
    book = analyzer.get_orderbook(token_id)
    if not book:
        print("  Failed to get orderbook")
        return None
    
    print(f"  Best Bid: {book.best_bid:.4f} ({book.best_bid_size:.2f} shares)")
    print(f"  Best Ask: {book.best_ask:.4f} ({book.best_ask_size:.2f} shares)")
    print(f"  Midpoint: {book.midpoint:.4f}")
    print(f"  Spread: {book.spread:.4f} ({format_percent(book.spread_percent or 0)})")
    print(f"  Imbalance: {book.imbalance():.2f}")
    
    return {
        "midpoint": book.midpoint,
        "spread": book.spread,
        "best_bid": book.best_bid,
        "best_ask": book.best_ask,
    }


def run_market_making_demo():
    """Run the market making demonstration."""
    print("\n" + "#"*60)
    print("# Market Making Demo")
    print("#"*60)
    
    # Load config
    config = reload_config()
    
    print(f"\n⚙️  Configuration")
    print(f"  Dry Run: {config.dry_run}")
    print(f"  Has Credentials: {config.has_credentials}")
    
    if not config.has_credentials:
        print("\n⚠️  No credentials - running in analysis-only mode")
        print("   Set PRIVATE_KEY and FUNDER_ADDRESS in .env for trading")
    
    if not config.dry_run:
        print("\n⚠️  DRY_RUN is disabled - this will place REAL orders!")
        response = input("   Continue? (yes/no): ")
        if response.lower() != "yes":
            print("   Aborting.")
            return
    
    # Initialize client
    client = PolymarketClient(config)
    if not client.connect():
        print("❌ Failed to connect")
        return
    
    # Find a market
    market, token_id = find_market_for_making()
    if not token_id:
        print("❌ No suitable market found")
        return
    
    # Analyze orderbook
    analyzer = OrderbookAnalyzer(client.clob)
    params = analyze_orderbook(analyzer, token_id)
    if not params:
        return
    
    # Calculate our quotes
    midpoint = params["midpoint"]
    current_spread = params["spread"]
    
    # We'll try to capture half the spread (inside the current best bid/ask)
    our_spread = current_spread * 0.8  # Slightly tighter than market
    our_size = 10.0  # $10 per side for demo
    
    print(f"\n📝 Proposed Quotes")
    print(f"  Our Spread: {our_spread:.4f}")
    print(f"  Bid: {midpoint - our_spread/2:.4f}")
    print(f"  Ask: {midpoint + our_spread/2:.4f}")
    print(f"  Size: {our_size} shares each side")
    
    # Place orders (or simulate in dry run)
    if config.has_credentials:
        trader = TradingClient(client.clob)
        
        print(f"\n🚀 Placing two-sided orders...")
        buy_result, sell_result = place_two_sided_orders(
            trader,
            token_id=token_id,
            mid_price=midpoint,
            spread=our_spread,
            size=our_size
        )
        
        if buy_result.success:
            print(f"  ✓ BUY order: {buy_result.order_id}")
            log_trade("PLACED", token_id, "BUY", midpoint - our_spread/2, our_size)
        else:
            print(f"  ✗ BUY failed: {buy_result.error}")
        
        if sell_result.success:
            print(f"  ✓ SELL order: {sell_result.order_id}")
            log_trade("PLACED", token_id, "SELL", midpoint + our_spread/2, our_size)
        else:
            print(f"  ✗ SELL failed: {sell_result.error}")
        
        # Show open orders
        print(f"\n📋 Current Open Orders:")
        orders = trader.get_open_orders()
        for order in orders[:5]:
            print(f"  {order.get('side')} {order.get('size')} @ {order.get('price')}")
        
        # In a real bot, you would:
        # 1. Monitor fills via WebSocket
        # 2. Cancel and re-quote when price moves
        # 3. Manage inventory (don't get too long/short)
        # 4. Track P&L
        
        if config.dry_run:
            print("\n✅ Demo complete (DRY RUN - no real orders placed)")
        else:
            print("\n✅ Orders placed! Monitor on Polymarket.")
            print("   Remember to cancel orders when done testing.")
    else:
        print("\n⚠️  Skipping order placement (no credentials)")
    
    print(f"\n{'='*60}")
    print("Market Making Key Concepts:")
    print("  • Spread capture: Profit = Ask - Bid when both sides fill")
    print("  • Inventory risk: Getting stuck long/short if only one fills")
    print("  • Quote adjustment: Tighten spread when confident, widen when volatile")
    print("  • The $705k bot focuses on 1-day markets for cleaner risk")


if __name__ == "__main__":
    run_market_making_demo()
