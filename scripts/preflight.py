#!/usr/bin/env python3
"""
Preflight Check — verifies everything needed for live trading.

Run: python scripts/preflight.py
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import get_config, reload_config
from web3 import Web3


def check(label: str, passed: bool, detail: str = ""):
    icon = "PASS" if passed else "FAIL"
    msg = f"  [{icon}] {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return passed


def main():
    config = reload_config()
    all_ok = True

    print("\n" + "=" * 60)
    print("  PREFLIGHT CHECK")
    print("=" * 60)

    # --- 1. Credentials ---
    print("\n[1] Credentials")
    all_ok &= check("PRIVATE_KEY set", bool(config.private_key))
    all_ok &= check("FUNDER_ADDRESS set", bool(config.funder_address))
    issues = config.validate()
    all_ok &= check("Config validates", len(issues) == 0, "; ".join(issues) if issues else "")
    all_ok &= check("DRY_RUN mode", True, "ON (safe)" if config.dry_run else "OFF (live trading!)")

    # --- 2. CLOB API ---
    print("\n[2] Polymarket CLOB API")
    try:
        from src.layer0_ingestion.polymarket_clob import PolymarketClient
        pm = PolymarketClient(config)
        connected = pm.connect()
        all_ok &= check("CLOB connectivity", connected)
        all_ok &= check("Authentication", pm.is_authenticated)

        if pm.is_authenticated:
            from src.layer4_execution.trading import TradingClient
            tc = TradingClient(pm.clob)

            # Test order signing (dry run always)
            orders = tc.get_open_orders()
            all_ok &= check("Get open orders", True, f"{len(orders)} open")

            trades = tc.get_trades()
            all_ok &= check("Get trades", True, f"{len(trades)} recent")
        else:
            print("  [SKIP] Order tests (not authenticated)")
    except Exception as e:
        all_ok &= check("CLOB API", False, str(e))

    # --- 3. Gamma API ---
    print("\n[3] Gamma API")
    try:
        from src.layer0_ingestion.polymarket_gamma import MarketFetcher
        mf = MarketFetcher()
        markets = mf.get_all_markets(limit=3)
        all_ok &= check("Fetch markets", len(markets) > 0, f"{len(markets)} returned")
    except Exception as e:
        all_ok &= check("Gamma API", False, str(e))

    # --- 4. Web3 / Polygon RPC ---
    print("\n[4] Polygon RPC")
    try:
        w3 = Web3(Web3.HTTPProvider(config.polygon_rpc_url))
        connected = w3.is_connected()
        all_ok &= check("RPC connectivity", connected, config.polygon_rpc_url[:50] + "...")
        if connected:
            block = w3.eth.block_number
            all_ok &= check("Current block", True, str(block))

            chain_id = w3.eth.chain_id
            all_ok &= check("Chain ID = 137 (Polygon)", chain_id == 137, str(chain_id))
    except Exception as e:
        all_ok &= check("Polygon RPC", False, str(e))

    # --- 5. On-chain allowances (EOA only) ---
    print("\n[5] On-chain Allowances")
    if config.signature_type != 0:
        print(f"  [SKIP] Signature type {config.signature_type} (non-EOA, auto-approved)")
    elif not config.funder_address:
        print("  [SKIP] No FUNDER_ADDRESS")
    else:
        try:
            erc20_abi = [{"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}]
            usdc = w3.eth.contract(address=Web3.to_checksum_address(config.USDC_ADDRESS), abi=erc20_abi)
            owner = Web3.to_checksum_address(config.funder_address)

            for name, addr in [("Exchange", config.EXCHANGE_ADDRESS), ("NegRiskExchange", config.NEG_RISK_EXCHANGE), ("NegRiskAdapter", config.NEG_RISK_ADAPTER)]:
                spender = Web3.to_checksum_address(addr)
                allowance = usdc.functions.allowance(owner, spender).call()
                # 1e12 = 1M USDC (6 decimals) — a reasonable threshold
                ok = allowance > 1e12
                all_ok &= check(f"USDC -> {name}", ok, f"allowance={allowance / 1e6:.0f} USDC")
        except Exception as e:
            all_ok &= check("Allowance check", False, str(e))

    # --- 6. WebSocket (Alchemy WSS) ---
    print("\n[6] UMA WebSocket")
    ws_url = config.polygon_ws_url
    has_wss = "wss://" in ws_url
    all_ok &= check("WSS URL configured", has_wss, ws_url[:50] + "..." if has_wss else "no WSS URL")
    if has_wss:
        try:
            import asyncio
            import websockets
            import json

            async def test_wss():
                async with websockets.connect(ws_url) as ws:
                    msg = {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}
                    await ws.send(json.dumps(msg))
                    resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    return "result" in resp

            ok = asyncio.run(test_wss())
            all_ok &= check("WSS connectivity", ok)
        except Exception as e:
            all_ok &= check("WSS connectivity", False, str(e))

    # --- 7. Telegram (optional) ---
    print("\n[7] Telegram Notifications")
    tg_token = config.telegram_bot_token if hasattr(config, "telegram_bot_token") else None
    tg_chat = config.telegram_chat_id if hasattr(config, "telegram_chat_id") else None
    if tg_token and tg_chat:
        try:
            import requests
            resp = requests.get(f"https://api.telegram.org/bot{tg_token}/getMe", timeout=5)
            bot_info = resp.json()
            ok = bot_info.get("ok", False)
            bot_name = bot_info.get("result", {}).get("username", "?")
            all_ok &= check("Telegram bot", ok, f"@{bot_name}")
        except Exception as e:
            all_ok &= check("Telegram bot", False, str(e))
    else:
        print("  [SKIP] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")

    # --- Summary ---
    print("\n" + "=" * 60)
    if all_ok:
        print("  ALL CHECKS PASSED")
    else:
        print("  SOME CHECKS FAILED — review above")
    print("=" * 60 + "\n")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
