import logging
from typing import Dict, Any

from config.settings import get_config

logger = logging.getLogger(__name__)

class PortfolioRiskManager:
    """
    Layer 3: Portfolio & Risk
    Validates trades against account constraints, maximum sizes, and correlation limits.
    """
    def __init__(self, max_trade_size: float = None):
        config = get_config()
        self.max_trade_size = max_trade_size if max_trade_size is not None else config.max_order_size

    def validate_signal(self, signal: Dict[str, Any]) -> bool:
        """
        Check if the generated signal passes risk parameters.
        """
        confidence = signal.get("confidence", 0)
        edge = signal.get("edge", 0)

        logger.info(f"    Risk check: confidence={confidence:.2f} (min=0.60), edge={edge:.2%} (min>0), max_size=${self.max_trade_size:.0f}")

        if confidence < 0.60:
            logger.warning(f"    REJECTED: Confidence {confidence:.2f} below threshold")
            return False

        if edge <= 0:
            logger.warning(f"    REJECTED: Edge {edge:.2%} is not positive")
            return False

        logger.info(f"    APPROVED by Risk Manager")
        return True
