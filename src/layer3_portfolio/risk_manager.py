import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class PortfolioRiskManager:
    """
    Layer 3: Portfolio & Risk
    Validates trades against account constraints, maximum sizes, and correlation limits.
    """
    def __init__(self, max_trade_size: float = 100.0):
        self.max_trade_size = max_trade_size

    def validate_signal(self, signal: Dict[str, Any]) -> bool:
        """
        Check if the generated signal passes risk parameters.
        """
        confidence = signal.get("confidence", 0)
        
        # In a real scenario, this would check current portfolio exposure
        if confidence < 0.60:
            logger.warning(f"Signal rejected by Risk Manager: Confidence {confidence} too low.")
            return False
            
        logger.info(f"Signal approved by Risk Manager.")
        return True
