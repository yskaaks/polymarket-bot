"""
Market-making risk management: inventory tracking, quote skew, position limits.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from config.settings import get_config

logger = logging.getLogger(__name__)


@dataclass
class Fill:
    """A recorded fill from a quote being hit."""
    token_id: str
    side: str  # "BUY" or "SELL"
    price: float
    size: float
    timestamp: float
    order_id: str = ""


@dataclass
class InventoryPosition:
    """Net position state for a single token."""
    token_id: str
    net_quantity: float = 0.0  # positive = long, negative = short
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0
    total_bought: float = 0.0
    total_sold: float = 0.0
    fills: list = field(default_factory=list)


class InventoryTracker:
    """Tracks net position per token and computes quote skew in logit space."""

    MAX_SKEW_LOGIT: float = 0.30  # ±0.30 max skew in logit space
    # At p=0.50 this shifts ~7%, at p=0.90 this shifts ~3% — correct behavior

    def __init__(self):
        self._positions: dict[str, InventoryPosition] = {}

    def get_position(self, token_id: str) -> InventoryPosition:
        if token_id not in self._positions:
            self._positions[token_id] = InventoryPosition(token_id=token_id)
        return self._positions[token_id]

    def record_fill(self, fill: Fill) -> None:
        pos = self.get_position(fill.token_id)
        pos.fills.append(fill)

        if fill.side == "BUY":
            # Update average entry for buys
            old_cost = pos.avg_entry_price * max(pos.net_quantity, 0)
            new_cost = fill.price * fill.size
            pos.net_quantity += fill.size
            pos.total_bought += fill.size * fill.price
            if pos.net_quantity > 0:
                pos.avg_entry_price = (old_cost + new_cost) / pos.net_quantity
        elif fill.side == "SELL":
            # Realize P&L on sells if we had a long position
            if pos.net_quantity > 0:
                realized_qty = min(fill.size, pos.net_quantity)
                pos.realized_pnl += realized_qty * (fill.price - pos.avg_entry_price)
            pos.net_quantity -= fill.size
            pos.total_sold += fill.size * fill.price

        logger.info(
            f"Fill recorded: {fill.side} {fill.size:.2f} @ {fill.price:.4f} "
            f"token={fill.token_id[:8]}... | net={pos.net_quantity:.2f} "
            f"realized_pnl=${pos.realized_pnl:.2f}"
        )

    def get_quote_skew(self, token_id: str) -> float:
        """
        Compute quote skew in logit space based on inventory.

        Long position → negative logit skew (shift quotes down to sell more).
        Short position → positive logit skew (shift quotes up to buy more).
        Linear scaling up to ±MAX_SKEW_LOGIT.

        Returns a logit-space adjustment to be applied via logit_adjust() or
        added directly to the fair value's logit representation. This naturally
        produces smaller probability-space shifts near boundaries (correct
        behavior: pushing price from 0.90 to 0.93 is reckless, but logit-space
        prevents it).
        """
        config = get_config()
        pos = self.get_position(token_id)
        max_pos = config.mm_max_position_per_market

        if max_pos <= 0 or pos.net_quantity == 0:
            return 0.0

        inventory_ratio = pos.net_quantity / max_pos
        inventory_ratio = max(-1.0, min(1.0, inventory_ratio))
        skew_logit = -inventory_ratio * self.MAX_SKEW_LOGIT

        return skew_logit

    def get_net_exposure(self, token_id: str) -> float:
        """Absolute USDC exposure for a token."""
        pos = self.get_position(token_id)
        return abs(pos.net_quantity * pos.avg_entry_price) if pos.avg_entry_price > 0 else 0.0

    def get_total_exposure(self) -> float:
        """Total absolute USDC exposure across all tokens."""
        return sum(self.get_net_exposure(tid) for tid in self._positions)

    def get_market_pnl(self, token_id: str) -> float:
        """Realized P&L for a token."""
        return self.get_position(token_id).realized_pnl

    @property
    def active_tokens(self) -> list[str]:
        return [tid for tid, pos in self._positions.items() if pos.net_quantity != 0]

    def reset(self, token_id: str) -> None:
        if token_id in self._positions:
            del self._positions[token_id]


@dataclass
class RiskCheckResult:
    """Result of a risk check."""
    allowed: bool
    reason: str = ""
    max_size: float = 0.0


class MMRiskManager:
    """
    Enforces market-making risk limits:
    - Per-market position limit
    - Total exposure cap
    - Max active markets
    - Per-market stop-loss
    - Portfolio drawdown circuit breaker
    """

    PORTFOLIO_DRAWDOWN_LIMIT: float = -1000.0  # hard stop if total realized PnL drops below

    def __init__(self, inventory: InventoryTracker):
        self.inventory = inventory
        self._circuit_breaker_triggered = False

    @property
    def config(self):
        return get_config()

    def should_quote(self, token_id: str) -> RiskCheckResult:
        """Check if we should be quoting this token at all."""
        if self._circuit_breaker_triggered:
            return RiskCheckResult(allowed=False, reason="circuit breaker triggered")

        # Check portfolio drawdown
        total_pnl = sum(
            self.inventory.get_position(tid).realized_pnl
            for tid in self.inventory._positions
        )
        if total_pnl < self.PORTFOLIO_DRAWDOWN_LIMIT:
            self._circuit_breaker_triggered = True
            logger.error(
                f"CIRCUIT BREAKER: total realized PnL ${total_pnl:.2f} "
                f"< limit ${self.PORTFOLIO_DRAWDOWN_LIMIT:.2f}"
            )
            return RiskCheckResult(allowed=False, reason="portfolio drawdown circuit breaker")

        # Check per-market stop-loss
        market_pnl = self.inventory.get_market_pnl(token_id)
        if market_pnl < self.config.mm_stop_loss_per_market:
            logger.warning(
                f"Stop-loss hit for {token_id[:8]}...: "
                f"PnL ${market_pnl:.2f} < limit ${self.config.mm_stop_loss_per_market:.2f}"
            )
            return RiskCheckResult(allowed=False, reason="per-market stop-loss hit")

        # Check max active markets
        active_count = len(self.inventory.active_tokens)
        if active_count >= self.config.mm_max_markets:
            # Allow if this token is already active
            if token_id not in self.inventory.active_tokens:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"max active markets ({self.config.mm_max_markets}) reached"
                )

        return RiskCheckResult(allowed=True)

    def compute_order_size(
        self,
        token_id: str,
        fair_value: float,
        spread: float,
        book_depth: float,
        order_min_size: float = 5.0,
    ) -> float:
        """
        Compute order size respecting all limits.
        Uses 1/4 Kelly, capped by position limits, total exposure headroom,
        config max_order_size, and 20% of visible book depth.
        """
        if not self.should_quote(token_id).allowed:
            return 0.0

        # Size based on spread edge and capital allocation
        # Wider spread → more edge → can size larger
        # Allocate proportionally to spread/max_spread, capped at 1/max_markets of capital
        if spread <= 0:
            return 0.0
        spread_ratio = min(spread / 0.10, 1.0)  # normalize: 10% spread → full allocation
        per_market_capital = self.config.mm_capital / max(self.config.mm_max_markets, 1)
        base_size = spread_ratio * per_market_capital * 0.05  # 5% of per-market allocation

        # Cap by per-market position limit headroom
        current_exposure = self.inventory.get_net_exposure(token_id)
        position_headroom = max(0, self.config.mm_max_position_per_market - current_exposure)

        # Cap by total exposure headroom
        total_exposure = self.inventory.get_total_exposure()
        total_headroom = max(0, self.config.mm_max_total_exposure - total_exposure)

        # Cap by config max_order_size
        config_cap = self.config.max_order_size

        # Cap by 20% of visible book depth (don't be the book)
        depth_cap = book_depth * 0.20 if book_depth > 0 else config_cap

        size = min(base_size, position_headroom, total_headroom, config_cap, depth_cap)
        size = max(0, size)

        # Polymarket enforces a per-market minimum order size
        if 0 < size < order_min_size:
            logger.debug(
                f"Size {size:.2f} below Polymarket minimum ({order_min_size}), "
                f"bumping up for {token_id[:8]}..."
            )
            size = order_min_size

        # Re-check caps after bump — if min size exceeds limits, skip entirely
        if size > position_headroom or size > total_headroom:
            logger.debug(
                f"Min size {order_min_size} exceeds headroom for {token_id[:8]}..., skipping"
            )
            return 0.0

        if size > 0:
            logger.debug(
                f"Size calc for {token_id[:8]}...: base=${base_size:.2f} "
                f"pos_room=${position_headroom:.2f} total_room=${total_headroom:.2f} "
                f"depth_cap=${depth_cap:.2f} → ${size:.2f}"
            )

        return size

    def reset_circuit_breaker(self) -> None:
        self._circuit_breaker_triggered = False
        logger.info("Circuit breaker reset")
