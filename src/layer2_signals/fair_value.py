"""
Fair value estimation for market making.

All adjustments happen in logit (log-odds) space, which is the correct
transform for prediction market probabilities bounded in (0,1).
Extensible via SignalProvider interface for Phase 2/3 signal integration.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from src.orderbook import Orderbook
from src.utils import logit, expit, logit_adjust, logit_midpoint, logit_spread

logger = logging.getLogger(__name__)


@dataclass
class FairValueEstimate:
    """Result of fair value estimation."""
    fair_value: float
    bid_price: float  # recommended bid (from logit spread)
    ask_price: float  # recommended ask (from logit spread)
    spread: float  # ask - bid in probability space
    confidence: float  # 0-1, how confident we are in this estimate
    midpoint: float
    imbalance_adj_logit: float  # adjustment applied in logit space
    vwap_adj_logit: float
    signal_adj_logit: float = 0.0


class SignalProvider(ABC):
    """Interface for Phase 2/3 signal sources."""

    @abstractmethod
    def get_adjustment(self, token_id: str, current_fv: float) -> float:
        """Return adjustment in logit space. Positive = bullish."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


class FairValueEngine:
    """
    Estimates fair value and recommended spread for a token.

    All math is done in logit space:
    - p → logit(p) = log(p/(1-p))
    - adjustments are additive in logit space
    - expit(x) = 1/(1+exp(-x)) maps back to probability

    This ensures adjustments scale correctly near boundaries:
    a 0.1 logit shift at p=0.50 ≈ 2.5% move, at p=0.90 ≈ 0.9% move.
    """

    IMBALANCE_MAX_ADJ_LOGIT: float = 0.10  # max imbalance adjustment in logit space
    MIN_SPREAD_LOGIT: float = 0.15  # min half-spread in logit space (~2% at p=0.50)
    VWAP_WEIGHT: float = 0.3  # 30% weight on VWAP in logit-space blend
    DEPTH_THIN_THRESHOLD: float = 100  # shares
    DEPTH_MODERATE_THRESHOLD: float = 500

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
        if midpoint is None or midpoint <= 0 or midpoint >= 1:
            logger.warning(f"Cannot estimate FV for {token_id[:8]}...: invalid midpoint {midpoint}")
            return None

        # Work in logit space
        mid_logit = logit(midpoint)

        # 1. Imbalance adjustment in logit space
        imbalance = orderbook.imbalance(levels=5)  # -1 to 1
        imbalance_adj_logit = imbalance * self.IMBALANCE_MAX_ADJ_LOGIT

        # 2. VWAP/last trade blend in logit space
        vwap_adj_logit = 0.0
        if last_trade_price is not None and 0 < last_trade_price < 1:
            blended_logit = logit(midpoint) * (1 - self.VWAP_WEIGHT) + logit(last_trade_price) * self.VWAP_WEIGHT
            vwap_adj_logit = blended_logit - mid_logit

        fv_logit = mid_logit + imbalance_adj_logit + vwap_adj_logit

        # 3. Signal provider adjustments (in logit space)
        signal_adj_logit = 0.0
        for provider in self._signals:
            try:
                adj = provider.get_adjustment(token_id, expit(fv_logit))
                signal_adj_logit += adj
            except Exception as e:
                logger.warning(f"Signal provider {provider.name} failed: {e}")
        fv_logit += signal_adj_logit

        fair_value = expit(fv_logit)

        # 4. Spread in logit space (symmetric in logit = asymmetric in prob)
        half_spread_logit = self._recommend_half_spread_logit(orderbook)
        bid_price, ask_price = logit_spread(fair_value, half_spread_logit)
        spread = ask_price - bid_price

        # 5. Confidence
        confidence = self._compute_confidence(orderbook)

        estimate = FairValueEstimate(
            fair_value=fair_value,
            bid_price=bid_price,
            ask_price=ask_price,
            spread=spread,
            confidence=confidence,
            midpoint=midpoint,
            imbalance_adj_logit=imbalance_adj_logit,
            vwap_adj_logit=vwap_adj_logit,
            signal_adj_logit=signal_adj_logit,
        )

        logger.debug(
            f"FV estimate {token_id[:8]}...: mid={midpoint:.4f} "
            f"imb_logit={imbalance_adj_logit:+.3f} vwap_logit={vwap_adj_logit:+.3f} "
            f"→ fv={fair_value:.4f} bid={bid_price:.4f} ask={ask_price:.4f} "
            f"spread={spread:.4f} conf={confidence:.2f}"
        )

        return estimate

    def _recommend_half_spread_logit(self, orderbook: Orderbook) -> float:
        """
        Recommend half-spread width in logit space.
        Wider for thin books, never below MIN_SPREAD_LOGIT.

        In logit space, a constant half-spread naturally produces
        tighter prob-space spreads near 0.50 and wider near extremes.
        """
        bid_depth = orderbook.total_bid_depth(levels=5)
        ask_depth = orderbook.total_ask_depth(levels=5)
        total_depth = bid_depth + ask_depth

        if total_depth < self.DEPTH_THIN_THRESHOLD:
            depth_premium = 0.15  # significant extra width for thin books
        elif total_depth < self.DEPTH_MODERATE_THRESHOLD:
            depth_premium = 0.07
        else:
            depth_premium = 0.0

        # Also respect current market spread: don't quote tighter than the book
        current_spread = orderbook.spread
        if current_spread is not None and orderbook.midpoint is not None and orderbook.midpoint > 0:
            # Convert current spread to approximate logit half-width
            mid = orderbook.midpoint
            current_ask = mid + current_spread / 2
            current_bid = mid - current_spread / 2
            if 0 < current_bid < current_ask < 1:
                current_half_logit = (logit(current_ask) - logit(current_bid)) / 2
                return max(current_half_logit, self.MIN_SPREAD_LOGIT + depth_premium)

        return self.MIN_SPREAD_LOGIT + depth_premium

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
        spread_score = max(0, 1.0 - spread / 10.0) * 0.5

        return depth_score + spread_score
