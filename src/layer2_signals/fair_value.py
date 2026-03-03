"""
Fair value estimation for market making.

Computes fair value from orderbook midpoint, imbalance adjustment, and VWAP blend.
Extensible via SignalProvider interface for Phase 2/3 signal integration.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from src.orderbook import Orderbook

logger = logging.getLogger(__name__)


@dataclass
class FairValueEstimate:
    """Result of fair value estimation."""
    fair_value: float
    spread: float  # recommended spread
    confidence: float  # 0-1, how confident we are in this estimate
    midpoint: float
    imbalance_adj: float
    vwap_adj: float
    signal_adj: float = 0.0  # from registered SignalProviders


class SignalProvider(ABC):
    """Interface for Phase 2/3 signal sources."""

    @abstractmethod
    def get_adjustment(self, token_id: str, current_fv: float) -> float:
        """Return price adjustment to apply to fair value. Positive = bullish."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


class FairValueEngine:
    """
    Estimates fair value and recommended spread for a token.

    Components:
    1. Orderbook midpoint (base)
    2. Imbalance adjustment (±0.5% max based on bid/ask depth ratio)
    3. VWAP midpoint blend (if last trade price available)
    4. Signal providers (Phase 2/3 extension point)
    """

    IMBALANCE_MAX_ADJ: float = 0.005  # ±0.5% max imbalance adjustment
    MIN_SPREAD: float = 0.02  # 2% floor on spread recommendation
    VWAP_WEIGHT: float = 0.3  # 30% weight on VWAP/last trade vs 70% midpoint

    def __init__(self):
        self._signals: list[SignalProvider] = []

    def register_signal(self, provider: SignalProvider) -> None:
        self._signals.append(provider)
        logger.info(f"Registered signal provider: {provider.name}")

    def estimate(
        self,
        token_id: str,
        orderbook: Orderbook,
        last_trade_price: Optional[float] = None,
    ) -> Optional[FairValueEstimate]:
        """
        Estimate fair value for a token.

        Args:
            token_id: CLOB token ID
            orderbook: Current orderbook snapshot
            last_trade_price: Most recent trade price (VWAP proxy)

        Returns:
            FairValueEstimate or None if orderbook is insufficient
        """
        midpoint = orderbook.midpoint
        if midpoint is None:
            logger.warning(f"Cannot estimate FV for {token_id[:8]}...: no midpoint")
            return None

        # 1. Imbalance adjustment
        imbalance = orderbook.imbalance(levels=5)  # -1 to 1
        imbalance_adj = imbalance * self.IMBALANCE_MAX_ADJ * midpoint

        # 2. VWAP/last trade blend
        vwap_adj = 0.0
        if last_trade_price is not None and last_trade_price > 0:
            vwap_mid = last_trade_price
            blended = (1 - self.VWAP_WEIGHT) * midpoint + self.VWAP_WEIGHT * vwap_mid
            vwap_adj = blended - midpoint

        fair_value = midpoint + imbalance_adj + vwap_adj

        # 3. Signal provider adjustments
        signal_adj = 0.0
        for provider in self._signals:
            try:
                adj = provider.get_adjustment(token_id, fair_value)
                signal_adj += adj
            except Exception as e:
                logger.warning(f"Signal provider {provider.name} failed: {e}")
        fair_value += signal_adj

        # Clamp to valid price range
        fair_value = max(0.01, min(0.99, fair_value))

        # 4. Spread recommendation
        spread = self._recommend_spread(orderbook)

        # 5. Confidence from depth + spread width
        confidence = self._compute_confidence(orderbook)

        estimate = FairValueEstimate(
            fair_value=fair_value,
            spread=spread,
            confidence=confidence,
            midpoint=midpoint,
            imbalance_adj=imbalance_adj,
            vwap_adj=vwap_adj,
            signal_adj=signal_adj,
        )

        logger.debug(
            f"FV estimate {token_id[:8]}...: mid={midpoint:.4f} "
            f"imb_adj={imbalance_adj:+.4f} vwap_adj={vwap_adj:+.4f} "
            f"→ fv={fair_value:.4f} spread={spread:.4f} conf={confidence:.2f}"
        )

        return estimate

    def _recommend_spread(self, orderbook: Orderbook) -> float:
        """
        Recommend a spread based on current market conditions.
        Never tighter than current spread. Wider for thin books.
        """
        current_spread = orderbook.spread or self.MIN_SPREAD

        # Thin book → wider spread
        bid_depth = orderbook.total_bid_depth(levels=5)
        ask_depth = orderbook.total_ask_depth(levels=5)
        total_depth = bid_depth + ask_depth

        if total_depth < 100:
            depth_premium = 0.02  # 2% extra for very thin books
        elif total_depth < 500:
            depth_premium = 0.01
        else:
            depth_premium = 0.0

        recommended = max(current_spread, self.MIN_SPREAD) + depth_premium

        return min(recommended, 0.20)  # cap at 20%

    def _compute_confidence(self, orderbook: Orderbook) -> float:
        """
        Confidence score 0-1 based on orderbook quality.
        Higher depth + tighter spread = higher confidence.
        """
        bid_depth = orderbook.total_bid_depth(levels=5)
        ask_depth = orderbook.total_ask_depth(levels=5)
        total_depth = bid_depth + ask_depth

        # Depth component: 0-0.5 (saturates at 1000 shares)
        depth_score = min(total_depth / 1000.0, 1.0) * 0.5

        # Spread component: 0-0.5 (tighter = better, saturates at 1%)
        spread = orderbook.spread_percent or 100.0
        spread_score = max(0, 1.0 - spread / 10.0) * 0.5  # 10% spread → 0 score

        return depth_score + spread_score
