#!/usr/bin/env python3
"""
Check Allowances Script

Verify token allowances are set for trading.
Provides instructions if allowances need to be set.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import get_config


def main():
    """Check and display allowance status."""
    config = get_config()
    
    print("\n" + "#"*60)
    print("# Token Allowance Check")
    print("#"*60)
    
    print(f"\n📋 Contract Addresses (Polygon)")
    print(f"{'='*60}")
    print(f"  USDC Token:         {config.USDC_ADDRESS}")
    print(f"  Conditional Token:  {config.CTF_ADDRESS}")
    print(f"\n  Exchange:           {config.EXCHANGE_ADDRESS}")
    print(f"  Neg Risk Exchange:  {config.NEG_RISK_EXCHANGE}")
    print(f"  Neg Risk Adapter:   {config.NEG_RISK_ADAPTER}")
    
    print(f"\n📝 Allowance Requirements")
    print(f"{'='*60}")
    print("""
  For EOA wallets (MetaMask, hardware wallets), you need to approve:
  
  1. USDC for all three contracts:
     - Exchange (main trading)
     - Neg Risk Exchange (for neg-risk markets)
     - Neg Risk Adapter
  
  2. Conditional Tokens (CTF) for all three contracts
  
  Note: Email/Magic wallets handle this automatically.
""")
    
    if not config.has_credentials:
        print(f"\n⚠️  No credentials configured")
        print(f"   Set PRIVATE_KEY and FUNDER_ADDRESS in .env to check allowances")
        return
    
    print(f"\n🔍 Checking wallet: {config.funder_address}")
    print(f"   Signature type: {config.signature_type}")
    
    if config.signature_type == 1:
        print(f"\n✅ Using email/Magic wallet - allowances should be automatic")
    elif config.signature_type == 2:
        print(f"\n✅ Using proxy wallet - allowances should be handled by proxy")
    else:
        print(f"\n⚠️  Using EOA wallet - manual allowances required")
        print(f"""
  To set allowances, you can:
  
  1. Use Polymarket's web interface (automatically prompts for approvals)
  
  2. Use the py-clob-client allowance helper:
     
     from py_clob_client.client import ClobClient
     client = ClobClient(...)
     # See: https://gist.github.com/poly-rodr/44313920481de58d5a3f6d1f8226bd5e
  
  3. Use a block explorer (PolygonScan) to call approve() directly:
     - Go to the token contract
     - Call approve(spender, amount) for each exchange contract
     - Set amount to max uint256 for unlimited
""")
    
    print(f"\n{'='*60}")
    print("Note: Actual on-chain allowance check requires web3 connection.")
    print("This script shows requirements and provides setup guidance.")


if __name__ == "__main__":
    main()
