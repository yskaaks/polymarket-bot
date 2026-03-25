"""Prediction market fill model for realistic backtesting.

Simulates thin, tiered liquidity characteristic of prediction markets.
Constructs a synthetic order book for each fill so the matching engine
applies realistic price impact — large orders walk through levels and
get progressively worse fills.

Usage:
    model = PredictionMarketFillModel(PredictionMarketFillConfig(
        base_spread_pct=0.04,
        depth_tiers=((100.0, 0.00), (500.0, 0.02), (2000.0, 0.05)),
    ))
    engine.add_venue(..., fill_model=model)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from nautilus_trader.backtest.models import FillModel
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.enums import BookType, OrderSide
from nautilus_trader.model.objects import Price, Quantity


# Liquidity thins at price extremes (near 0 or 1).
# At midpoint (0.50) depth is 100%, at 0.05/0.95 it drops to this floor.
_MIN_DEPTH_SCALE = 0.15

# Absolute price bounds for prediction markets.
_PRICE_FLOOR = 0.01
_PRICE_CEIL = 0.99


@dataclass(frozen=True)
class PredictionMarketFillConfig:
    """Immutable configuration for the prediction market fill model.

    Parameters
    ----------
    base_spread_pct : float
        Full bid-ask spread as a fraction of price (e.g., 0.04 = 4 cents
        around a 0.50 midpoint). Must be in (0, 1).
    depth_tiers : tuple of (size, offset) pairs
        Each tier specifies available quantity and the price offset (in
        absolute terms) from the best bid/ask. Tiers are cumulative:
        first tier is at best price, subsequent tiers are progressively
        worse. Must have at least one tier. Sizes must be positive,
        offsets non-negative and strictly increasing.
    depth_scale_by_price : bool
        If True, scale tier sizes by distance from midpoint — thinner
        liquidity at price extremes (0.05, 0.95). Default True.
    """
    base_spread_pct: float = 0.04
    depth_tiers: tuple[tuple[float, float], ...] = (
        (100.0, 0.00),
        (500.0, 0.02),
        (2000.0, 0.05),
    )
    depth_scale_by_price: bool = True

    def __post_init__(self):
        if not (0 < self.base_spread_pct < 1):
            raise ValueError(
                f"base_spread_pct must be in (0, 1), got {self.base_spread_pct}"
            )
        if not self.depth_tiers:
            raise ValueError("depth_tiers must have at least one tier")

        prev_offset = -1.0
        for i, (size, offset) in enumerate(self.depth_tiers):
            if size <= 0:
                raise ValueError(
                    f"depth_tiers[{i}] size must be positive, got {size}"
                )
            if offset < 0:
                raise ValueError(
                    f"depth_tiers[{i}] offset must be non-negative, got {offset}"
                )
            if offset <= prev_offset and i > 0:
                raise ValueError(
                    f"depth_tiers[{i}] offset {offset} must be strictly greater "
                    f"than previous offset {prev_offset}"
                )
            prev_offset = offset


class PredictionMarketFillModel(FillModel):
    """Fill model that simulates prediction market order book depth.

    On each fill attempt, constructs a synthetic L2 order book with
    tiered liquidity around the current best bid/ask. The NautilusTrader
    matching engine then walks this book to determine fill prices,
    producing realistic slippage for large orders.
    """

    def __init__(self, config: Optional[PredictionMarketFillConfig] = None):
        super().__init__(prob_fill_on_limit=1.0, prob_slippage=0.0)
        self._config = config or PredictionMarketFillConfig()
        # Pre-validate config is the right type
        if not isinstance(self._config, PredictionMarketFillConfig):
            raise TypeError(
                f"config must be PredictionMarketFillConfig, got {type(self._config)}"
            )

    @property
    def config(self) -> PredictionMarketFillConfig:
        return self._config

    def get_orderbook_for_fill_simulation(
        self, instrument, order, best_bid, best_ask,
    ) -> OrderBook:
        """Build a synthetic order book with tiered depth for fill simulation.

        The matching engine calls this before filling each market order.
        We return a book whose liquidity structure determines the fill
        price(s) and whether the order can be fully filled.
        """
        mid = self._compute_midpoint(best_bid, best_ask)
        half_spread = self._config.base_spread_pct / 2
        book_bid = max(_PRICE_FLOOR, mid - half_spread)
        book_ask = min(_PRICE_CEIL, mid + half_spread)

        depth_multiplier = self._depth_scale_for_price(mid)

        book = OrderBook(instrument.id, BookType.L2_MBP)
        order_id = 1

        for tier_size, tier_offset in self._config.depth_tiers:
            scaled_size = max(0.1, tier_size * depth_multiplier)

            bid_px = max(_PRICE_FLOOR, book_bid - tier_offset)
            ask_px = min(_PRICE_CEIL, book_ask + tier_offset)

            book.add(
                BookOrder(
                    OrderSide.BUY,
                    Price(bid_px, instrument.price_precision),
                    Quantity(scaled_size, instrument.size_precision),
                    order_id,
                ),
                ts_event=0,
            )
            order_id += 1

            book.add(
                BookOrder(
                    OrderSide.SELL,
                    Price(ask_px, instrument.price_precision),
                    Quantity(scaled_size, instrument.size_precision),
                    order_id,
                ),
                ts_event=0,
            )
            order_id += 1

        return book

    def _depth_scale_for_price(self, mid: float) -> float:
        """Scale liquidity depth based on distance from 0.50.

        Prediction markets are thinnest at extremes (price near 0 or 1)
        and deepest near the midpoint of the probability range.

        Returns a multiplier in [_MIN_DEPTH_SCALE, 1.0].
        """
        if not self._config.depth_scale_by_price:
            return 1.0

        # Distance from 0.50 on [0, 0.50] scale
        dist = abs(mid - 0.50)
        # Linear interpolation: 0.0 distance -> 1.0x, 0.50 distance -> _MIN_DEPTH_SCALE
        scale = 1.0 - dist * 2 * (1.0 - _MIN_DEPTH_SCALE)
        return max(_MIN_DEPTH_SCALE, min(1.0, scale))

    @staticmethod
    def _compute_midpoint(best_bid, best_ask) -> float:
        """Compute midpoint from best bid/ask, with defensive fallbacks."""
        bid = float(best_bid) if best_bid is not None else None
        ask = float(best_ask) if best_ask is not None else None

        if bid is not None and ask is not None:
            return (bid + ask) / 2
        if bid is not None:
            return bid
        if ask is not None:
            return ask
        # No price information at all — use midpoint of range
        return 0.50
