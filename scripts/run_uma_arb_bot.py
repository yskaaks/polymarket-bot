import sys
import logging
from config.settings import get_config
from src.layer0_ingestion.polymarket_clob import create_client
from src.layer0_ingestion.uma_oracle import UMAClient
from src.strategies.uma_arb_strategy import UmaArbStrategy

# Configure basic logging for the script
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('UmaArbBot')

def main():
    logger.info("Starting UMA Arbitrage Bot Initialization...")
    
    # 1. Load Configuration
    config = get_config()
    
    # 2. Initialize Polymarket Client
    pm_client = create_client(config)
    
    if not pm_client.connect():
        logger.error("Failed to connect to Polymarket CLOB. Check credentials/network.")
        sys.exit(1)
        
    logger.info(f"Connected to Polymarket. Authenticated: {pm_client.is_authenticated}")
    
    # 3. Initialize UMA Client
    # Expects ALCHEMY_RPC_URL or INFURA_RPC_URL in your environment or config
    # Fallback to a public polygon RPC for read-only if none provided
    rpc_url = getattr(config, 'POLYGON_RPC_URL', "https://polygon-rpc.com")
    uma_client = UMAClient(rpc_url=rpc_url)
    
    logger.info(f"Connected to UMA Oracle (Web3). Block: {uma_client.w3.eth.block_number if uma_client.w3.is_connected() else 'Disconnected'}")
    
    # 4. Initialize and Run Arbitrage Strategy Orchestrator
    strategy = UmaArbStrategy(config=config, pm_client=pm_client, uma_client=uma_client)
    
    # Optional API check 
    logger.info(f"Polymarket Server Time: {pm_client.get_server_time()}")
    
    # Start the continuous loop
    strategy.run_loop(poll_interval=15)

if __name__ == "__main__":
    main()
