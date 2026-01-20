#!/usr/bin/env python3
"""
Test Connection Script

Verifies API connectivity and authentication.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import get_config, reload_config
from src.client import PolymarketClient, get_readonly_client
from src.markets import MarketFetcher


def test_clob_api():
    """Test CLOB API connectivity."""
    print("\n" + "="*50)
    print("Testing CLOB API (Read-Only)")
    print("="*50)
    
    client = get_readonly_client()
    
    # Test basic connectivity
    try:
        result = client.test_connection()
        print(f"  ✓ Server OK: {result['ok']}")
        print(f"  ✓ Server Time: {result['server_time']}")
    except Exception as e:
        print(f"  ✗ Connection failed: {e}")
        return False
    
    # Test getting markets
    try:
        markets = client.clob.get_simplified_markets()
        market_count = len(markets.get("data", []))
        print(f"  ✓ Markets available: {market_count}")
    except Exception as e:
        print(f"  ✗ Failed to fetch markets: {e}")
    
    return True


def test_gamma_api():
    """Test Gamma API connectivity."""
    print("\n" + "="*50)
    print("Testing Gamma API")
    print("="*50)
    
    fetcher = MarketFetcher()
    
    try:
        markets = fetcher.get_all_markets(limit=5)
        print(f"  ✓ Fetched {len(markets)} markets")
        
        if markets:
            market = markets[0]
            print(f"\n  Sample Market:")
            print(f"    Question: {market.question[:60]}...")
            print(f"    Outcomes: {market.outcomes}")
            print(f"    Prices: {market.outcome_prices}")
            print(f"    Volume 24h: ${market.volume_24h:,.2f}")
            print(f"    Liquidity: ${market.liquidity:,.2f}")
            
            if market.token_ids:
                print(f"    Token IDs: {len(market.token_ids)} tokens")
    
    except Exception as e:
        print(f"  ✗ Gamma API failed: {e}")
        return False
    
    return True


def test_authenticated(config):
    """Test authenticated endpoints."""
    print("\n" + "="*50)
    print("Testing Authenticated Access")
    print("="*50)
    
    if not config.has_credentials:
        print("  ⚠ No credentials configured")
        print("    Set PRIVATE_KEY and FUNDER_ADDRESS in .env")
        return False
    
    issues = config.validate()
    if issues:
        print("  ✗ Configuration issues:")
        for issue in issues:
            print(f"    - {issue}")
        return False
    
    print(f"  ✓ Credentials found")
    print(f"  ✓ Signature type: {config.signature_type}")
    print(f"  ✓ Dry run mode: {config.dry_run}")
    
    try:
        client = PolymarketClient(config)
        connected = client.connect()
        
        if connected and client.is_authenticated:
            print(f"  ✓ Successfully authenticated!")
            
            # Try to get orders
            from src.trading import TradingClient
            trader = TradingClient(client.clob)
            orders = trader.get_open_orders()
            print(f"  ✓ Open orders: {len(orders)}")
            
        else:
            print(f"  ✗ Authentication failed")
            return False
            
    except Exception as e:
        print(f"  ✗ Auth error: {e}")
        return False
    
    return True


def main():
    """Run all connection tests."""
    print("\n" + "#"*50)
    print("# Polymarket Bot - Connection Test")
    print("#"*50)
    
    # Reload config to pick up any .env changes
    config = reload_config()
    
    # Run tests
    clob_ok = test_clob_api()
    gamma_ok = test_gamma_api()
    
    # Only test auth if --auth flag or credentials exist
    auth_ok = False
    if "--auth" in sys.argv or config.has_credentials:
        auth_ok = test_authenticated(config)
    else:
        print("\n" + "="*50)
        print("Skipping Auth Test (no credentials)")
        print("="*50)
        print("  Run with --auth flag or set credentials in .env")
    
    # Summary
    print("\n" + "#"*50)
    print("# Summary")
    print("#"*50)
    print(f"  CLOB API:  {'✓ OK' if clob_ok else '✗ FAILED'}")
    print(f"  Gamma API: {'✓ OK' if gamma_ok else '✗ FAILED'}")
    print(f"  Auth:      {'✓ OK' if auth_ok else '⚠ Not tested' if not config.has_credentials else '✗ FAILED'}")
    print()


if __name__ == "__main__":
    main()
