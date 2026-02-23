#!/usr/bin/env python3
"""
View Markets Script

Browse and filter active Polymarket markets.
"""

import sys
import argparse
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.layer0_ingestion.polymarket_gamma import MarketFetcher, Market
from src.utils import format_usd, format_percent


def print_market(market: Market, show_details: bool = False):
    """Print market information."""
    print(f"\n{'='*60}")
    print(f"📊 {market.question[:57]}...")
    print(f"{'='*60}")
    
    # Outcomes and prices
    yes_price = market.best_yes_price
    no_price = market.best_no_price
    
    print(f"  YES: {yes_price:.2%}  |  NO: {no_price:.2%}")
    print(f"  Spread: {market.spread:.2%}")
    
    # Volume and liquidity
    print(f"\n  Volume 24h: {format_usd(market.volume_24h)}")
    print(f"  Liquidity:  {format_usd(market.liquidity)}")
    print(f"  Total Vol:  {format_usd(market.volume)}")
    
    # Category
    if market.category:
        print(f"\n  Category: {market.category}")
    
    if show_details:
        print(f"\n  Slug: {market.slug}")
        print(f"  ID: {market.id}")
        
        if market.token_ids:
            print(f"\n  Token IDs:")
            for i, tid in enumerate(market.token_ids):
                label = market.outcomes[i] if i < len(market.outcomes) else f"Outcome {i}"
                print(f"    {label}: {tid[:20]}...")
        
        if market.end_date:
            print(f"\n  End Date: {market.end_date}")


def main():
    parser = argparse.ArgumentParser(description="Browse Polymarket markets")
    parser.add_argument("--limit", "-l", type=int, default=10, help="Number of markets to show")
    parser.add_argument("--min-volume", "-v", type=float, default=0, help="Minimum 24h volume")
    parser.add_argument("--min-liquidity", "-q", type=float, default=0, help="Minimum liquidity")
    parser.add_argument("--search", "-s", type=str, help="Search markets by question")
    parser.add_argument("--daily", "-d", action="store_true", help="Show only daily markets")
    parser.add_argument("--details", action="store_true", help="Show detailed info including token IDs")
    parser.add_argument("--slug", type=str, help="Get specific market by slug")
    
    args = parser.parse_args()
    
    fetcher = MarketFetcher()
    
    print("\n" + "#"*60)
    print("# Polymarket Markets")
    print("#"*60)
    
    # Handle specific market lookup
    if args.slug:
        market = fetcher.get_market_by_slug(args.slug)
        if market:
            print_market(market, show_details=True)
        else:
            print(f"\nMarket not found: {args.slug}")
        return
    
    # Fetch markets based on filters
    if args.search:
        print(f"\nSearching for: '{args.search}'")
        markets = fetcher.search_markets(args.search, limit=args.limit)
    elif args.daily:
        print("\nFetching daily (24h) markets...")
        markets = fetcher.get_daily_markets(limit=args.limit)
    elif args.min_volume > 0 or args.min_liquidity > 0:
        print(f"\nFetching high-volume markets...")
        print(f"  Min Volume: {format_usd(args.min_volume)}")
        print(f"  Min Liquidity: {format_usd(args.min_liquidity)}")
        markets = fetcher.get_high_volume_markets(
            min_volume_24h=args.min_volume,
            min_liquidity=args.min_liquidity,
            limit=args.limit
        )
    else:
        print(f"\nFetching top {args.limit} markets by 24h volume...")
        markets = fetcher.get_all_markets(limit=args.limit)
    
    # Filter by minimum volume/liquidity
    if args.min_volume > 0:
        markets = [m for m in markets if m.volume_24h >= args.min_volume]
    if args.min_liquidity > 0:
        markets = [m for m in markets if m.liquidity >= args.min_liquidity]
    
    # Display markets
    if not markets:
        print("\nNo markets found matching criteria.")
        return
    
    print(f"\nFound {len(markets)} markets:")
    
    for market in markets[:args.limit]:
        print_market(market, show_details=args.details)
    
    # Summary
    print(f"\n{'='*60}")
    print(f"Total markets shown: {min(len(markets), args.limit)}")
    total_volume = sum(m.volume_24h for m in markets[:args.limit])
    print(f"Combined 24h volume: {format_usd(total_volume)}")


if __name__ == "__main__":
    main()
