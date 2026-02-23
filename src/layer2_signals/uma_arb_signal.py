import logging
import re
from typing import Optional, Dict, Any

from src.layer0_ingestion.polymarket_gamma import MarketFetcher
from src.layer0_ingestion.uma_oracle import UMAClient

logger = logging.getLogger(__name__)

class UmaArbSignalGenerator:
    """
    Layer 2: Signal Generation
    Detects if an UMA 'Settle' event presents a tradeable discrepancy 
    against the Polymarket orderbook.
    """
    def __init__(self, uma_client: UMAClient, market_fetcher: MarketFetcher):
        self.uma_client = uma_client
        self.market_fetcher = market_fetcher

    def generate_signal(self, settlement: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Takes a raw UMA settlement event and returns a trade signal if conditions are met.
        
        Returns a dict with signal details or None if no edge.
        """
        identifier = settlement.get('identifier')
        resolved_price = settlement.get('resolvedPrice')
        ancillary_data = settlement.get('ancillaryData', '')
        
        parsed_data = self.uma_client.parse_ancillary_data(ancillary_data)
        
        # 1. Match to Polymarket condition ID
        condition_id = self._match_to_polymarket(parsed_data)
        if not condition_id:
            logger.debug(f"Could not match UMA settlement to PM condition.")
            return None

        # 2. Check profitability edge
        edge_exists = self._check_profitability(condition_id, resolved_price)
        
        if edge_exists:
            logger.info(f"Signal Generated: Profitable discrepancy on Condition {condition_id}")
            return {
                "condition_id": condition_id,
                "resolved_price": resolved_price,
                "confidence": 0.99, # Highly confident since UMA resolved it
                "signal_type": "UMA_ARB"
            }
            
        return None

    def _match_to_polymarket(self, ancillary_data: str) -> Optional[str]:
        # Extract condition ID from ancillary data
        match = re.search(r'condition_id[^\w]*([a-fA-F0-9x]+)', ancillary_data.lower())
        condition_id = match.group(1) if match else None
        
        if condition_id:
            markets = self.market_fetcher.get_markets_by_condition_id(condition_id)
            if markets:
                return markets[0].condition_id
                
        return None
        
    def _check_profitability(self, condition_id: str, resolved_price: int) -> bool:
        # Placeholder for exact edge calculation
        return True
