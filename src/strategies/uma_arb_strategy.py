import asyncio
import logging
import sys
import threading
import time

from config.settings import Config, get_config
from src.layer0_ingestion.polymarket_clob import PolymarketClient
from src.layer0_ingestion.polymarket_gamma import MarketFetcher
from src.layer0_ingestion.uma_client import UMAClient, UMAWebSocketClient

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
            market_fetcher=self.market_fetcher,
            clob_client=self.pm_client.clob if self.pm_client._clob_client else None
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
        logger.info("=" * 60)
        logger.info("UMA Arb Strategy Orchestrator")
        logger.info(f"  Mode:           {'DRY RUN' if not self.pm_client.is_authenticated else 'LIVE'}")
        logger.info(f"  Poll interval:  {poll_interval}s")
        logger.info(f"  Oracle:         {self.uma_client.oov3_address}")
        logger.info(f"  Max order size: ${self.config.max_order_size:.2f}")
        logger.info(f"  Min edge:       {self.config.min_edge:.2%}")
        logger.info("=" * 60)

        if not self.pm_client.is_authenticated:
            logger.warning("Polymarket client not authenticated. Running in DRY RUN mode.")

        if not self.uma_client.w3.is_connected():
            logger.error("UMA Oracle Web3 connection is not active. Cannot start strategy loop.")
            return

        try:
            last_block = self.uma_client.w3.eth.block_number - 100
            scan_count = 0

            while True:
                current_block = self.uma_client.w3.eth.block_number
                block_range = current_block - last_block
                scan_count += 1
                logger.info(f"[Scan #{scan_count}] Blocks {last_block}..{current_block} ({block_range} blocks)")

                # --- LAYER 0: Ingestion ---
                settlements = self.uma_client.get_recent_settlements(from_block=last_block, to_block=current_block)

                if settlements:
                    logger.info(f"  Found {len(settlements)} settlement(s)")
                else:
                    logger.info(f"  No settlements found")

                for i, settlement in enumerate(settlements, 1):
                    logger.info("-" * 50)
                    logger.info(f"  Settlement {i}/{len(settlements)}")
                    logger.info(f"    Tx:             {settlement.get('transactionHash', '?')}")
                    logger.info(f"    Block:          {settlement.get('blockNumber', '?')}")
                    logger.info(f"    Identifier:     {settlement.get('identifier', '?')[:20]}...")
                    logger.info(f"    Resolved price: {settlement.get('resolvedPrice')}")
                    logger.info(f"    Settled price:  {settlement.get('settledPrice')}")
                    logger.info(f"    Expiration:     {settlement.get('expirationTimestamp')}")

                    # --- LAYER 2: Signals ---
                    signal = self.signal_generator.generate_signal(settlement)

                    if signal:
                        # --- LAYER 3: Portfolio & Risk ---
                        is_approved = self.risk_manager.validate_signal(signal)

                        if is_approved:
                            # --- LAYER 4: Execution ---
                            self.execution_agent.execute_trade(signal)
                    else:
                        logger.info(f"    -> No signal generated (no PM match or no edge)")

                last_block = current_block + 1
                logger.info(f"  Sleeping {poll_interval}s...")
                time.sleep(poll_interval)

        except KeyboardInterrupt:
            logger.info("Stopping UMA Arb Strategy Orchestrator.")
        except Exception as e:
            logger.error(f"Strategy Loop Error: {e}", exc_info=True)

    def _handle_settlement(self, settlement: dict):
        """Process a single settlement event (shared by both realtime and polling)."""
        logger.info(f"  Settlement detected:")
        logger.info(f"    Tx:             {settlement.get('transactionHash', '?')}")
        logger.info(f"    Block:          {settlement.get('blockNumber', '?')}")
        logger.info(f"    Resolved price: {settlement.get('resolvedPrice')}")
        logger.info(f"    Settled price:  {settlement.get('settledPrice')}")

        signal = self.signal_generator.generate_signal(settlement)
        if signal:
            is_approved = self.risk_manager.validate_signal(signal)
            if is_approved:
                self.execution_agent.execute_trade(signal)
        else:
            logger.info(f"    -> No signal generated (no PM match or no edge)")

    def run_realtime(self, fallback_poll_interval: int = 5):
        """
        Event-driven execution using WebSocket for real-time Settle detection.
        Falls back to polling as a safety net.
        """
        ws_url = self.config.polygon_ws_url
        logger.info("=" * 60)
        logger.info("UMA Arb Strategy Orchestrator (REALTIME MODE)")
        logger.info(f"  Mode:           {'DRY RUN' if not self.pm_client.is_authenticated else 'LIVE'}")
        logger.info(f"  WSS endpoint:   {ws_url[:60]}...")
        logger.info(f"  Fallback poll:  {fallback_poll_interval}s")
        logger.info(f"  Oracle:         {self.uma_client.oov3_address}")
        logger.info(f"  Max order size: ${self.config.max_order_size:.2f}")
        logger.info(f"  Min edge:       {self.config.min_edge:.2%}")
        logger.info("=" * 60)

        if not self.pm_client.is_authenticated:
            logger.warning("Polymarket client not authenticated. Running in DRY RUN mode.")

        # Create WebSocket client for real-time events
        ws_client = UMAWebSocketClient(ws_url=ws_url, oov3_address=self.uma_client.oov3_address)
        ws_client.on_settle(self._handle_settlement)

        # Run WebSocket listener in a background thread
        def run_ws():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(ws_client.listen())
            except Exception as e:
                logger.error(f"WebSocket listener crashed: {e}", exc_info=True)

        ws_thread = threading.Thread(target=run_ws, daemon=True)
        ws_thread.start()
        logger.info("WebSocket listener started in background thread")

        # Polling fallback loop (catches anything the WebSocket might miss)
        last_block = self.uma_client.w3.eth.block_number - 100
        scan_count = 0

        try:
            while True:
                try:
                    current_block = self.uma_client.w3.eth.block_number
                    block_range = current_block - last_block
                    scan_count += 1
                    logger.info(f"[Fallback scan #{scan_count}] Blocks {last_block}..{current_block} ({block_range} blocks)")

                    settlements = self.uma_client.get_recent_settlements(from_block=last_block, to_block=current_block)
                    if settlements:
                        logger.info(f"  Fallback found {len(settlements)} settlement(s)")
                        for settlement in settlements:
                            self._handle_settlement(settlement)

                    last_block = current_block + 1
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    logger.error(f"Fallback scan error (will retry): {e}", exc_info=True)

                time.sleep(fallback_poll_interval)

        except KeyboardInterrupt:
            logger.info("Stopping UMA Arb Strategy Orchestrator.")
            asyncio.run(ws_client.stop())


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S'
    )

    config = get_config()

    pm_client = PolymarketClient(config)
    if not pm_client.connect():
        logger.error("Failed to connect to Polymarket CLOB. Check credentials/network.")
        sys.exit(1)

    logger.info(f"Connected to Polymarket. Authenticated: {pm_client.is_authenticated}")

    uma_client = UMAClient(rpc_url=config.polygon_rpc_url)
    if uma_client.w3.is_connected():
        logger.info(f"Connected to UMA Oracle. Block: {uma_client.w3.eth.block_number}")
    else:
        logger.warning("UMA Oracle Web3 connection failed, continuing anyway...")

    strategy = UmaArbStrategy(config=config, pm_client=pm_client, uma_client=uma_client)

    # Use realtime mode if WSS URL looks valid (not a public RPC without WSS)
    ws_url = config.polygon_ws_url
    if "wss://" in ws_url and "alchemy.com" in ws_url:
        logger.info("Alchemy WSS detected, using realtime mode")
        strategy.run_realtime(fallback_poll_interval=5)
    else:
        logger.info("No Alchemy WSS detected, using polling mode")
        strategy.run_loop(poll_interval=15)
