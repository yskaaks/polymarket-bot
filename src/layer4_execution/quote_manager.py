"""
Quote manager for market-making strategy.

Manages active two-sided quotes: placement, cancellation, fill detection,
and requote logic. Accepts pre-computed bid/ask from the logit-space FV engine.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from config.settings import get_config
from src.layer4_execution.trading import TradingClient, OrderResult
from src.layer3_portfolio.mm_risk_manager import InventoryTracker
from src.utils import round_price, round_size, logit

logger = logging.getLogger(__name__)


@dataclass
class ActiveQuote:
    """Tracks a live two-sided quote."""
    token_id: str
    fair_value: float
    fair_value_logit: float  # for drift comparison in logit space
    buy_price: float
    sell_price: float
    size: float
    buy_order_id: Optional[str] = None
    sell_order_id: Optional[str] = None
    placed_at: float = 0.0


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

    Accepts pre-computed bid/ask prices (from logit-space FairValueEngine).
    Tracks quotes per token and detects fills by comparing open orders.
    """

    FV_REQUOTE_THRESHOLD_LOGIT: float = 0.08  # requote if FV moved >0.08 in logit space
    MAX_QUOTE_AGE: float = 30.0  # requote after 30 seconds (reduced for WS-driven mode)

    def __init__(self, trading_client: TradingClient, inventory: InventoryTracker):
        self.trading = trading_client
        self.config = get_config()
        self.inventory = inventory
        self._quotes: dict[str, ActiveQuote] = {}

    def place_quote(
        self,
        token_id: str,
        fair_value: float,
        bid_price: float,
        ask_price: float,
        size: float,
    ) -> bool:
        """
        Place a two-sided quote with pre-computed bid/ask.

        Bid/ask should already incorporate logit-space spread + inventory skew.
        Cancels any existing quote for this token first.

        Returns True if both sides placed successfully.
        """
        # Cancel existing quote first
        if token_id in self._quotes:
            self._cancel_quote(token_id)

        size = round_size(size)
        if size <= 0:
            logger.debug(f"Size too small for {token_id[:8]}..., skipping quote")
            return False

        bid = round_price(max(0.01, bid_price))
        ask = round_price(min(0.99, ask_price))

        if bid >= ask:
            logger.warning(f"Invalid quote {token_id[:8]}...: bid={bid:.4f} >= ask={ask:.4f}")
            return False

        # Only place SELL if we actually hold tokens to sell
        position = self.inventory.get_position(token_id)
        has_inventory = position.net_quantity > 0
        sell_size = min(size, position.net_quantity) if has_inventory else 0

        logger.info(
            f"Placing quote {token_id[:8]}...: "
            f"BUY {size:.1f}@{bid:.4f}"
            f"{f' / SELL {sell_size:.1f}@{ask:.4f}' if sell_size > 0 else ' / SELL skipped (no inventory)'}"
            f" (fv={fair_value:.4f})"
        )

        buy_result = self.trading.place_limit_order(
            token_id=token_id, side="BUY", price=bid, size=size
        )

        sell_result = None
        if sell_size > 0:
            sell_result = self.trading.place_limit_order(
                token_id=token_id, side="SELL", price=ask, size=round_size(sell_size)
            )

        now = time.time()
        quote = ActiveQuote(
            token_id=token_id,
            fair_value=fair_value,
            fair_value_logit=logit(fair_value),
            buy_price=bid,
            sell_price=ask,
            size=size,
            buy_order_id=buy_result.order_id if buy_result.success else None,
            sell_order_id=sell_result.order_id if sell_result and sell_result.success else None,
            placed_at=now,
        )
        self._quotes[token_id] = quote

        buy_ok = buy_result.success
        sell_ok = sell_result.success if sell_result else True  # skipped = ok

        if buy_ok and sell_ok:
            logger.info(f"Quote active {token_id[:8]}...")
        else:
            errors = []
            if not buy_ok:
                errors.append(f"buy: {buy_result.error}")
            if sell_result and not sell_ok:
                errors.append(f"sell: {sell_result.error}")
            logger.warning(f"Quote partially failed {token_id[:8]}...: {', '.join(errors)}")

        return buy_ok and sell_ok

    def needs_requote(self, token_id: str, current_fv: float) -> bool:
        """
        Check if a quote needs to be refreshed.

        Drift comparison is done in logit space — a 1% move at p=0.50 and
        p=0.90 map to very different logit distances.
        """
        quote = self._quotes.get(token_id)
        if quote is None:
            return True

        # FV drift check in logit space
        current_logit = logit(current_fv)
        logit_drift = abs(current_logit - quote.fair_value_logit)
        if logit_drift > self.FV_REQUOTE_THRESHOLD_LOGIT:
            logger.debug(
                f"Requote {token_id[:8]}...: logit drift {logit_drift:.3f} "
                f"> {self.FV_REQUOTE_THRESHOLD_LOGIT}"
            )
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

        try:
            open_orders = self.trading.get_open_orders()
        except Exception as e:
            logger.warning(f"Failed to fetch open orders, skipping fill detection: {e}")
            return []

        open_ids = set()
        for order in open_orders:
            oid = order.get("id") or order.get("orderID") or order.get("order_id")
            if oid:
                open_ids.add(oid)

        fills = []
        now = time.time()

        for token_id, quote in list(self._quotes.items()):
            # Skip dry run orders — they're never "open"
            if quote.buy_order_id == "DRY_RUN_ORDER":
                continue

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
