"""
Quote manager for market-making strategy.

Manages active two-sided quotes: placement, cancellation, fill detection,
and requote logic.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from config.settings import get_config
from src.layer4_execution.trading import TradingClient, place_two_sided_orders, OrderResult
from src.utils import round_price, round_size

logger = logging.getLogger(__name__)


@dataclass
class ActiveQuote:
    """Tracks a live two-sided quote."""
    token_id: str
    fair_value: float
    spread: float
    size: float
    buy_order_id: Optional[str] = None
    sell_order_id: Optional[str] = None
    buy_price: float = 0.0
    sell_price: float = 0.0
    placed_at: float = 0.0
    inventory_skew: float = 0.0


@dataclass
class FillEvent:
    """Detected fill on one side of a quote."""
    token_id: str
    side: str  # "BUY" or "SELL"
    price: float
    size: float
    order_id: str
    timestamp: float


class QuoteManager:
    """
    Manages active two-sided quotes for market making.

    Uses TradingClient and place_two_sided_orders for order lifecycle.
    Tracks quotes per token and detects fills by comparing open orders.
    """

    FV_REQUOTE_THRESHOLD: float = 0.01  # requote if FV moved > 1%
    MAX_QUOTE_AGE: float = 60.0  # requote after 60 seconds

    def __init__(self, trading_client: TradingClient):
        self.trading = trading_client
        self.config = get_config()
        self._quotes: dict[str, ActiveQuote] = {}

    def place_quote(
        self,
        token_id: str,
        fair_value: float,
        spread: float,
        size: float,
        inventory_skew: float = 0.0,
    ) -> bool:
        """
        Place a two-sided quote around fair value with inventory skew.

        Cancels any existing quote for this token first.

        Args:
            token_id: CLOB token ID
            fair_value: Estimated fair value
            spread: Total spread width
            size: Order size per side
            inventory_skew: Price adjustment from inventory (-0.03 to +0.03)

        Returns:
            True if both sides placed successfully
        """
        # Cancel existing quote first
        if token_id in self._quotes:
            self._cancel_quote(token_id)

        # Apply inventory skew to midpoint
        skewed_mid = fair_value + inventory_skew
        skewed_mid = max(0.01, min(0.99, skewed_mid))

        size = round_size(size)
        if size <= 0:
            logger.debug(f"Size too small for {token_id[:8]}..., skipping quote")
            return False

        buy_result, sell_result = place_two_sided_orders(
            self.trading, token_id, skewed_mid, spread, size
        )

        now = time.time()
        half_spread = spread / 2
        buy_price = round_price(max(0.01, skewed_mid - half_spread))
        sell_price = round_price(min(0.99, skewed_mid + half_spread))

        quote = ActiveQuote(
            token_id=token_id,
            fair_value=fair_value,
            spread=spread,
            size=size,
            buy_order_id=buy_result.order_id if buy_result.success else None,
            sell_order_id=sell_result.order_id if sell_result.success else None,
            buy_price=buy_price,
            sell_price=sell_price,
            placed_at=now,
            inventory_skew=inventory_skew,
        )
        self._quotes[token_id] = quote

        success = buy_result.success and sell_result.success
        if success:
            logger.info(
                f"Quote placed {token_id[:8]}...: "
                f"BUY {size:.1f}@{buy_price:.4f} / SELL {size:.1f}@{sell_price:.4f} "
                f"(fv={fair_value:.4f} skew={inventory_skew:+.4f})"
            )
        else:
            errors = []
            if not buy_result.success:
                errors.append(f"buy: {buy_result.error}")
            if not sell_result.success:
                errors.append(f"sell: {sell_result.error}")
            logger.warning(f"Quote partially failed {token_id[:8]}...: {', '.join(errors)}")

        return success

    def needs_requote(self, token_id: str, current_fv: float) -> bool:
        """
        Check if a quote needs to be refreshed.

        Requote if:
        - FV moved > 1% from quoted FV
        - Quote age > 60 seconds
        - One side was filled (detected by missing order)
        """
        quote = self._quotes.get(token_id)
        if quote is None:
            return True  # no quote exists

        # FV drift check
        if quote.fair_value > 0:
            fv_change = abs(current_fv - quote.fair_value) / quote.fair_value
            if fv_change > self.FV_REQUOTE_THRESHOLD:
                logger.debug(f"Requote {token_id[:8]}...: FV drift {fv_change:.2%}")
                return True

        # Age check
        age = time.time() - quote.placed_at
        if age > self.MAX_QUOTE_AGE:
            logger.debug(f"Requote {token_id[:8]}...: age {age:.0f}s > {self.MAX_QUOTE_AGE}s")
            return True

        return False

    def detect_fills(self) -> list[FillEvent]:
        """
        Detect fills by comparing tracked quotes against open orders.

        If a tracked order ID is no longer in open orders, it was filled.
        """
        if not self._quotes:
            return []

        open_orders = self.trading.get_open_orders()
        open_ids = set()
        for order in open_orders:
            oid = order.get("id") or order.get("orderID") or order.get("order_id")
            if oid:
                open_ids.add(oid)

        fills = []
        now = time.time()

        for token_id, quote in list(self._quotes.items()):
            # Skip dry run orders - they're never "open"
            if quote.buy_order_id == "DRY_RUN_ORDER":
                continue

            # Check buy side
            if quote.buy_order_id and quote.buy_order_id not in open_ids:
                fills.append(FillEvent(
                    token_id=token_id,
                    side="BUY",
                    price=quote.buy_price,
                    size=quote.size,
                    order_id=quote.buy_order_id,
                    timestamp=now,
                ))
                quote.buy_order_id = None

            # Check sell side
            if quote.sell_order_id and quote.sell_order_id not in open_ids:
                fills.append(FillEvent(
                    token_id=token_id,
                    side="SELL",
                    price=quote.sell_price,
                    size=quote.size,
                    order_id=quote.sell_order_id,
                    timestamp=now,
                ))
                quote.sell_order_id = None

        if fills:
            logger.info(f"Detected {len(fills)} fills")

        return fills

    def cancel_all_quotes(self) -> None:
        """Cancel all active quotes. Used for graceful shutdown."""
        for token_id in list(self._quotes.keys()):
            self._cancel_quote(token_id)
        logger.info("All quotes cancelled")

    def cancel_quote(self, token_id: str) -> None:
        """Cancel quote for a specific token."""
        self._cancel_quote(token_id)

    def _cancel_quote(self, token_id: str) -> None:
        """Cancel an existing quote for a token."""
        quote = self._quotes.get(token_id)
        if quote is None:
            return

        if quote.buy_order_id and quote.buy_order_id != "DRY_RUN_ORDER":
            result = self.trading.cancel_order(quote.buy_order_id)
            if not result.success:
                logger.warning(f"Failed to cancel buy order {quote.buy_order_id}: {result.error}")

        if quote.sell_order_id and quote.sell_order_id != "DRY_RUN_ORDER":
            result = self.trading.cancel_order(quote.sell_order_id)
            if not result.success:
                logger.warning(f"Failed to cancel sell order {quote.sell_order_id}: {result.error}")

        del self._quotes[token_id]

    def get_active_quote(self, token_id: str) -> Optional[ActiveQuote]:
        return self._quotes.get(token_id)

    @property
    def active_tokens(self) -> list[str]:
        return list(self._quotes.keys())

    @property
    def num_active_quotes(self) -> int:
        return len(self._quotes)
