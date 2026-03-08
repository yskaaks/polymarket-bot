#!/usr/bin/env python3
"""
Check on-chain USDC balance and token allowances for the bot's wallet.
Reports exactly what's missing so you can fix the 'not enough balance / allowance' error.
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from web3 import Web3
from config.settings import get_config

# Minimal ERC20 ABI for balanceOf, allowance, decimals
ERC20_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# ERC1155 (CTF) uses isApprovedForAll instead of allowance
ERC1155_ABI = [
    {
        "inputs": [
            {"name": "account", "type": "address"},
            {"name": "operator", "type": "address"},
        ],
        "name": "isApprovedForAll",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

MAX_UINT256 = 2**256 - 1
# Anything above 1M USDC we consider "unlimited"
UNLIMITED_THRESHOLD = 1_000_000


def fmt_usdc(raw: int) -> str:
    amount = raw / 1e6
    if amount >= UNLIMITED_THRESHOLD:
        return "UNLIMITED"
    return f"${amount:,.2f}"


def main():
    config = get_config()

    if not config.has_credentials:
        print("ERROR: Set PRIVATE_KEY and FUNDER_ADDRESS in .env first")
        sys.exit(1)

    wallet = Web3.to_checksum_address(config.funder_address)
    rpc = config.polygon_rpc_url
    w3 = Web3(Web3.HTTPProvider(rpc))

    if not w3.is_connected():
        print(f"ERROR: Cannot connect to RPC: {rpc}")
        sys.exit(1)

    print(f"\nWallet: {wallet}")
    print(f"RPC:    {rpc}")

    # --- MATIC balance ---
    matic_bal = w3.eth.get_balance(wallet)
    matic_human = w3.from_wei(matic_bal, "ether")
    print(f"\nMATIC (for gas): {matic_human:.4f} POL")
    if matic_bal == 0:
        print("  WARNING: You need POL/MATIC to pay gas fees!")

    # --- USDC balance ---
    usdc = w3.eth.contract(address=Web3.to_checksum_address(config.USDC_ADDRESS), abi=ERC20_ABI)
    usdc_bal = usdc.functions.balanceOf(wallet).call()
    print(f"\nUSDC Balance: {fmt_usdc(usdc_bal)}")
    if usdc_bal == 0:
        print("  PROBLEM: No USDC in this wallet! Send USDC (Polygon) to this address.")

    # --- USDC Allowances ---
    spenders = {
        "Exchange": config.EXCHANGE_ADDRESS,
        "Neg Risk Exchange": config.NEG_RISK_EXCHANGE,
        "Neg Risk Adapter": config.NEG_RISK_ADAPTER,
    }

    print(f"\n{'='*60}")
    print("USDC ALLOWANCES")
    print(f"{'='*60}")
    usdc_ok = True
    for name, addr in spenders.items():
        allowance = usdc.functions.allowance(wallet, Web3.to_checksum_address(addr)).call()
        status = "OK" if allowance > 0 else "MISSING"
        if allowance == 0:
            usdc_ok = False
        print(f"  {name:25s} -> {fmt_usdc(allowance):>15s}  [{status}]")

    # --- CTF (Conditional Token) Approvals ---
    ctf = w3.eth.contract(address=Web3.to_checksum_address(config.CTF_ADDRESS), abi=ERC1155_ABI)

    print(f"\n{'='*60}")
    print("CTF (Conditional Token) APPROVALS")
    print(f"{'='*60}")
    ctf_ok = True
    for name, addr in spenders.items():
        approved = ctf.functions.isApprovedForAll(wallet, Web3.to_checksum_address(addr)).call()
        status = "OK" if approved else "MISSING"
        if not approved:
            ctf_ok = False
        print(f"  {name:25s} -> {'Approved':>15s}  [{status}]")

    # --- Summary ---
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    all_good = True

    if usdc_bal == 0:
        print("\n  [X] No USDC balance - send USDC on Polygon to your bot wallet")
        all_good = False
    else:
        print(f"\n  [OK] USDC balance: {fmt_usdc(usdc_bal)}")

    if not usdc_ok:
        print("  [X] Missing USDC allowances - need to approve exchange contracts")
        all_good = False
    else:
        print("  [OK] All USDC allowances set")

    if not ctf_ok:
        print("  [X] Missing CTF approvals - need to approve exchange contracts")
        all_good = False
    else:
        print("  [OK] All CTF approvals set")

    if matic_bal == 0:
        print("  [X] No MATIC/POL for gas fees")
        all_good = False
    else:
        print(f"  [OK] MATIC/POL balance: {matic_human:.4f}")

    if all_good:
        print("\n  All checks passed! You should be able to trade.")
    else:
        print(f"\n  Fix the issues above. Your bot wallet address is:")
        print(f"  {wallet}")
        print(f"\n  To set approvals, go to PolygonScan and call approve():")
        print(f"  USDC:  https://polygonscan.com/address/{config.USDC_ADDRESS}#writeContract")
        print(f"  CTF:   https://polygonscan.com/address/{config.CTF_ADDRESS}#writeContract")
        print(f"  (Connect your bot wallet, call approve/setApprovalForAll for each exchange)")


if __name__ == "__main__":
    main()
