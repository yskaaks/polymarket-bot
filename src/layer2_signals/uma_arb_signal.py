import logging
import re
from typing import Optional, Dict, Any

from src.layer0_ingestion.polymarket_gamma import MarketFetcher
from src.layer0_ingestion.uma_client import UMAClient

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
        logger.info(f"    Ancillary data: {parsed_data[:120]}{'...' if len(parsed_data) > 120 else ''}")

        # 1. Match to Polymarket condition ID
        condition_id, market = self._match_to_polymarket(parsed_data)
        if not condition_id:
            logger.info(f"    No Polymarket match found in ancillary data")
            return None

        logger.info(f"    Matched market: \"{market.question}\"")
        logger.info(f"    Condition ID:   {condition_id}")
        logger.info(f"    PM YES price:   {market.best_yes_price:.4f}  |  NO price: {market.best_no_price:.4f}")
        logger.info(f"    Volume 24h:     ${market.volume_24h:,.0f}  |  Liquidity: ${market.liquidity:,.0f}")
        if market.token_ids:
            logger.info(f"    Token IDs:      YES={market.token_ids[0][:16]}...  NO={market.token_ids[1][:16] if len(market.token_ids) > 1 else 'N/A'}...")

        # UMA resolved price: 1e18 = YES, 0 = NO
        uma_outcome = "YES" if resolved_price > 0 else "NO"
        logger.info(f"    UMA outcome:    {uma_outcome} (raw: {resolved_price})")

        # 2. Check profitability edge
        edge_exists = self._check_profitability(condition_id, resolved_price)

        if edge_exists:
            pm_price = market.best_yes_price if uma_outcome == "YES" else market.best_no_price
            edge = (1.0 - pm_price) if pm_price > 0 else 0.0
            logger.info(f"    SIGNAL: Buy {uma_outcome} @ {pm_price:.4f} -> resolves to 1.00  |  Edge: {edge:.2%}")
            return {
                "condition_id": condition_id,
                "resolved_price": resolved_price,
                "confidence": 0.99,
                "signal_type": "UMA_ARB",
                "market_question": market.question,
                "uma_outcome": uma_outcome,
                "pm_price": pm_price,
                "edge": edge,
                "token_ids": market.token_ids,
            }

        logger.info(f"    No profitable edge found")
        return None

    def _match_to_polymarket(self, ancillary_data: str) -> tuple[Optional[str], Any]:
        # Extract condition ID from ancillary data
        match = re.search(r'condition_id[^\w]*([a-fA-F0-9x]+)', ancillary_data.lower())
        condition_id = match.group(1) if match else None

        if condition_id:
            logger.debug(f"    Extracted condition_id from ancillary: {condition_id}")
            markets = self.market_fetcher.get_markets_by_condition_id(condition_id)
            if markets:
                return markets[0].condition_id, markets[0]

        return None, None

    def _check_profitability(self, condition_id: str, resolved_price: int) -> bool:
        # Placeholder for exact edge calculation
        return True
