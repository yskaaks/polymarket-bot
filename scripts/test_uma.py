import sys
import logging
from src.layer0_ingestion.uma_oracle import UMAClient
from config.settings import get_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestUMA")

def main():
    config = get_config()
    rpc_url = getattr(config, 'POLYGON_RPC_URL', "https://rpc.ankr.com/polygon")
    
    logger.info(f"Connecting to Web3 at {rpc_url}")
    try:
        uma = UMAClient(rpc_url)
        if uma.w3.is_connected():
            logger.info("Successfully connected to Web3!")
            logger.info(f"Current Block: {uma.w3.eth.block_number}")
            
            # Try to get very recent events (last 100 blocks ~ 200 seconds)
            latest = uma.w3.eth.block_number
            events = uma.get_recent_settlements(from_block=latest - 100, to_block=latest)
            logger.info(f"Found {len(events)} recent Settle events.")
            
            for ev in events:
                logger.info(f"Event: {ev['identifier']}, Price: {ev['resolvedPrice']}")
                
        else:
            logger.error("Web3 connection failed.")
    except Exception as e:
        logger.error(f"Error during test: {e}")

if __name__ == "__main__":
    main()
