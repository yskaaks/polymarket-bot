import logging
import time

from config.settings import Config
from src.layer0_ingestion.polymarket_clob import PolymarketClient
from src.layer0_ingestion.polymarket_gamma import MarketFetcher
from src.layer0_ingestion.uma_oracle import UMAClient

from src.layer2_signals.uma_arb_signal import UmaArbSignalGenerator
from src.layer3_portfolio.risk_manager import PortfolioRiskManager
from src.layer4_execution.execution_agent import ExecutionAgent

logger = logging.getLogger(__name__)

class UmaArbStrategy:
    """
    UMA Arbitrage Strategy Orchestrator
    
    Coordinates the 6-layer agentic architecture specifically for the 
    Polymarket <-> UMA Arbitrage opportunity.
    """
    def __init__(self, config: Config, pm_client: PolymarketClient, uma_client: UMAClient):
        self.config = config
        
        # Layer 0: Ingestion
        self.pm_client = pm_client
        self.uma_client = uma_client
        self.market_fetcher = MarketFetcher(
            base_url=config.GAMMA_API_URL if hasattr(config, 'GAMMA_API_URL') else "https://gamma-api.polymarket.com"
        )
        
        # Layer 1: Research (N/A for instant arb)
        
        # Layer 2: Signals
        self.signal_generator = UmaArbSignalGenerator(
            uma_client=self.uma_client, 
            market_fetcher=self.market_fetcher
        )
        
        # Layer 3: Portfolio & Risk
        self.risk_manager = PortfolioRiskManager()
        
        # Layer 4: Execution
        self.execution_agent = ExecutionAgent(pm_client=self.pm_client)
        
        # Layer 5: Monitoring (To Be Implemented)


    def run_loop(self, poll_interval: int = 15):
        """
        Main continuous execution loop for the strategy.
        """
        logger.info("Starting UMA Arb Strategy Orchestrator...")
        
        if not self.pm_client.is_authenticated:
            logger.warning("Polymarket client not authenticated. Running in DRY RUN mode.")
            
        last_block = self.uma_client.w3.eth.block_number - 100
        
        try:
            while True:
                current_block = self.uma_client.w3.eth.block_number
                logger.debug(f"Checking blocks {last_block} to {current_block}")
                
                # --- LAYER 0: Ingestion ---
                settlements = self.uma_client.get_recent_settlements(from_block=last_block, to_block=current_block)
                
                for settlement in settlements:
                    logger.info(f"Ingested UMA Settlement: {settlement.get('identifier')}")
                    
                    # --- LAYER 2: Signals ---
                    signal = self.signal_generator.generate_signal(settlement)
                    
                    if signal:
                        # --- LAYER 3: Portfolio & Risk ---
                        is_approved = self.risk_manager.validate_signal(signal)
                        
                        if is_approved:
                            # --- LAYER 4: Execution ---
                            self.execution_agent.execute_trade(signal)
                            
                            # --- LAYER 5: Monitoring ---
                            # e.g., self.monitoring_agent.record_trade(signal)
                
                last_block = current_block + 1
                time.sleep(poll_interval)
                
        except KeyboardInterrupt:
            logger.info("Stopping UMA Arb Strategy Orchestrator.")
        except Exception as e:
            logger.error(f"Strategy Loop Error: {e}")
