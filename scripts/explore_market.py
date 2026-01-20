#!/usr/bin/env python3
"""
Explore Market Script

Interactive exploration of a single market - orderbook, spread, prices.
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.client import PolymarketClient
from src.markets import MarketFetcher
from src.orderbook import OrderbookAnalyzer
from src.utils import format_usd, format_percent
from config.settings import get_config


def explore_market(market_index: int = 0):
    """Explore a market interactively."""
    
    print("\n" + "="*60)
    print("🔍 MARKET EXPLORER")
    print("="*60)
    
    # Fetch top markets
    print("\nFetching top markets...")
    fetcher = MarketFetcher()
    markets = fetcher.get_all_markets(limit=10)
    
    if not markets:
        print("No markets found!")
        return
    
    # Show options
    print(f"\n📋 Available Markets:")
    for i, m in enumerate(markets):
        print(f"  [{i}] {m.question[:55]}...")
        print(f"      Vol: {format_usd(m.volume_24h)} | Liq: {format_usd(m.liquidity)}")
    
    # Let user pick or use default
    try:
        choice = input(f"\nSelect market [0-{len(markets)-1}] (default 0): ").strip()
        market_index = int(choice) if choice else 0
    except:
        market_index = 0
    
    market = markets[market_index]
    
    print(f"\n" + "="*60)
    print(f"📊 {market.question}")
    print("="*60)
    
    # Market details
    print(f"\n📈 Market Info:")
    print(f"  Slug: {market.slug}")
    print(f"  Category: {market.category}")
    print(f"  24h Volume: {format_usd(market.volume_24h)}")
    print(f"  Liquidity: {format_usd(market.liquidity)}")
    
    # Outcomes and prices
    print(f"\n💰 Outcomes:")
    for i, outcome in enumerate(market.outcomes):
        price = market.outcome_prices[i] if i < len(market.outcome_prices) else 0
        token = market.token_ids[i] if i < len(market.token_ids) else "N/A"
        print(f"  {outcome}: {price:.2%}")
        print(f"    Token: {token[:30]}...")
    
    # Get orderbook
    if market.token_ids:
        print(f"\n📖 Orderbook Analysis (YES token):")
        
        config = get_config()
        client = PolymarketClient(config)
        client.connect()
        
        analyzer = OrderbookAnalyzer(client.clob)
        token_id = market.token_ids[0]
        
        book = analyzer.get_orderbook(token_id)
        
        if book:
            print(f"\n  Best Bid: {book.best_bid:.4f} ({book.best_bid_size:.2f} shares)")
            print(f"  Best Ask: {book.best_ask:.4f} ({book.best_ask_size:.2f} shares)")
            print(f"  Midpoint: {book.midpoint:.4f}")
            print(f"  Spread:   {book.spread:.4f} ({format_percent(book.spread_percent or 0)})")
            
            print(f"\n  📊 Order Book Depth (top 5 levels):")
            print(f"  {'BIDS':^25} | {'ASKS':^25}")
            print(f"  {'-'*25} | {'-'*25}")
            
            for i in range(5):
                bid_str = ""
                ask_str = ""
                
                if i < len(book.bids):
                    bid = book.bids[i]
                    bid_str = f"{bid.size:>8.2f} @ {bid.price:.4f}"
                
                if i < len(book.asks):
                    ask = book.asks[i]
                    ask_str = f"{ask.price:.4f} @ {ask.size:<8.2f}"
                
                print(f"  {bid_str:>25} | {ask_str:<25}")
            
            print(f"\n  Imbalance: {book.imbalance():.2f} ", end="")
            if book.imbalance() > 0.2:
                print("(more buyers)")
            elif book.imbalance() < -0.2:
                print("(more sellers)")
            else:
                print("(balanced)")
            
            # Slippage analysis
            print(f"\n  💸 Slippage Analysis:")
            for amount in [10, 50, 100]:
                slip = analyzer.calculate_slippage(token_id, "BUY", amount)
                if "error" not in slip:
                    print(f"    Buy ${amount}: avg {slip['avg_price']:.4f} (slippage: {slip['slippage_pct']:.2f}%)")
            
            # Check for arbitrage
            if len(market.token_ids) >= 2:
                print(f"\n  🎯 Arbitrage Check:")
                arb = analyzer.find_arbitrage_opportunity(
                    market.token_ids[0], 
                    market.token_ids[1]
                )
                if arb and arb.get("opportunity"):
                    print(f"    ⚠️ OPPORTUNITY! Buy both for {arb['total_cost']:.4f}")
                    print(f"    Profit: {arb['profit_per_share']:.4f} ({arb['profit_pct']:.2f}%)")
                else:
                    print(f"    No arbitrage (YES+NO = {arb.get('total_cost', 'N/A')})")
    
    print(f"\n" + "="*60)
    print("What you can do next:")
    print("  • Run market_making_demo.py to see order placement")
    print("  • Watch this market live via WebSocket")
    print("  • Place a test limit order far from market")
    print("="*60)


if __name__ == "__main__":
    explore_market()
