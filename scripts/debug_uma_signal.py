import sys
import logging
import os
import time

# Ensure we can import src modules
sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config.settings import get_config
from src.layer0_ingestion.uma_client import UMAClient
from src.layer0_ingestion.polymarket_gamma import MarketFetcher
from src.layer2_signals.uma_arb_signal import UmaArbSignalGenerator

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("DebugUmaSignal")

def main():
    logger.info("Starting UMA Arbitrage Signal Debugger (Layer 0-2)")
    
    config = get_config()
    
    # We use a public RPC. No private keys required!
    # Providing a few alternatives in case one is rate limited.
    fallback_rpcs = [
        "https://rpc.ankr.com/polygon",
        "https://polygon-rpc.com",
        "https://rpc-mainnet.maticvigil.com"
    ]
    rpc_url = config.polygon_rpc_url or fallback_rpcs[0]
    
    is_connected = False
    for rpc in fallback_rpcs:
        uma_client = UMAClient(rpc)
        if uma_client.w3.is_connected():
            logger.info(f"Connected to Web3 via {rpc}")
            is_connected = True
            break
        else:
            logger.warning(f"Failed to connect to {rpc}")
            
    if not is_connected:
        logger.error("Could not connect to any Polygon RPCs. Web3 is down or rate limited.")
        sys.exit(1)
        
    latest_block = uma_client.w3.eth.block_number
    
    # Let's search the last ~10,000 blocks (~5.5 hours on Polygon) to ensure we find at least one event.
    blocks_to_search = 10000 
    from_block = max(0, latest_block - blocks_to_search)
    
    logger.info(f"Fetching UMA settlements from block {from_block} to {latest_block}...")
    
    try:
        settlements = uma_client.get_recent_settlements(from_block=from_block, to_block=latest_block)
        logger.info(f"Found {len(settlements)} settlements in the last {blocks_to_search} blocks.")
    except Exception as e:
        logger.error(f"Failed to fetch settlements (could be node range limit): {e}")
        logger.info("Tip: Try setting POLYGON_RPC_URL to a free Alchemy or Infura key in your .env")
        sys.exit(1)
    
    if not settlements:
        logger.info("No settlements found in this range. Try increasing 'blocks_to_search' in the script if you want to find older events.")
        sys.exit(0)
    
    # Instantiate MarketFetcher using the context manager (which handles the persistent requests Session we just added)
    with MarketFetcher(base_url=config.GAMMA_API_URL) as fetcher:
        signal_gen = UmaArbSignalGenerator(uma_client=uma_client, market_fetcher=fetcher)
        
        for i, settlement in enumerate(settlements, 1):
            identifier = settlement.get('identifier', b'Unknown')
            if isinstance(identifier, bytes):
                try: identifier = identifier.decode('utf-8').strip('\x00')
                except: identifier = identifier.hex()
                
            logger.info(f"\n--- Processing Settlement {i}/{len(settlements)}: {identifier} ---")
            logger.info(f"Resolved Price: {settlement.get('resolvedPrice')} | Block: {settlement.get('blockNumber')}")
            
            try:
                # generate_signal will do the heavy lifting: parsing ancillary data,
                # grabbing the condition ID, and fetching the polymarket market to check for an edge.
                signal = signal_gen.generate_signal(settlement)
                if signal:
                    logger.info(f"✅ SIGNAL GENERATED: {signal}")
                else:
                    logger.info("❌ No actionable signal generated (no polymarket market match or no edge).")
            except Exception as e:
                logger.error(f"Error generating signal: {e}")
                
if __name__ == "__main__":
    main()
