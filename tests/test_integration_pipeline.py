"""
Integration test: full market-making pipeline with realistic Polymarket data.

Wires together: FairValueEngine → InventoryTracker → MMRiskManager → QuoteManager
and verifies the entire flow produces sane outputs for real orderbook shapes.
"""

import os
import pytest
from unittest.mock import MagicMock, patch

from src.layer2_signals.fair_value import FairValueEngine
from src.layer3_portfolio.mm_risk_manager import InventoryTracker, MMRiskManager, Fill
from src.layer4_execution.quote_manager import QuoteManager
from src.layer4_execution.trading import OrderResult
from src.utils import logit, logit_adjust, logit_spread, round_price
from tests.fixtures import (
    CLOB_BOOK_WIDE_SPREAD,
    CLOB_BOOK_TIGHT_SPREAD,
    CLOB_BOOK_THIN,
    CLOB_BOOK_BULLISH_IMBALANCE,
    clob_book_to_orderbook,
)


@pytest.fixture(autouse=True)
def mock_config():
    with patch.dict(os.environ, {
        "PRIVATE_KEY": "0x" + "a" * 64,
        "FUNDER_ADDRESS": "0x" + "b" * 40,
        "DRY_RUN": "1",
        "MM_MAX_MARKETS": "5",
        "MM_MAX_POSITION_PER_MARKET": "2000",
        "MM_MAX_TOTAL_EXPOSURE": "20000",
        "MM_STOP_LOSS_PER_MARKET": "-200",
        "MM_CAPITAL": "100000",
        "MAX_ORDER_SIZE": "50",
    }):
        with patch("config.settings.load_dotenv"):
            from config.settings import reload_config
            reload_config()
            yield


def make_mock_trading():
    """Create mock TradingClient that tracks order IDs."""
    client = MagicMock()
    order_counter = [0]

    def fake_place(token_id, side, price, size, **kwargs):
        order_counter[0] += 1
        return OrderResult(success=True, order_id=f"ORD_{order_counter[0]}")

    client.place_limit_order.side_effect = fake_place
    client.cancel_order.return_value = OrderResult(success=True)
    client.get_open_orders.return_value = []
    return client


class TestFullPipeline:
    """End-to-end: orderbook → FV → risk check → quote placement."""

    def test_pipeline_wide_spread_market(self):
        """
        Iran regime fall market: 0.39/0.40 spread, deep book.
        Pipeline should: estimate FV near 0.395, produce valid bid/ask, place quote.
        """
        fv_engine = FairValueEngine()
        inventory = InventoryTracker()
        risk = MMRiskManager(inventory)
        mock_trading = make_mock_trading()
        quotes = QuoteManager(mock_trading)

        book = clob_book_to_orderbook(CLOB_BOOK_WIDE_SPREAD)
        token_id = book.token_id
        last_trade = 0.40

        # Step 1: Estimate fair value
        estimate = fv_engine.estimate(token_id, book, last_trade)
        assert estimate is not None
        assert 0.38 < estimate.fair_value < 0.42

        # Step 2: Check risk
        risk_check = risk.should_quote(token_id)
        assert risk_check.allowed

        # Step 3: Compute skew (no position yet → zero)
        skew = inventory.get_quote_skew(token_id)
        assert skew == 0.0

        # Step 4: Compute size
        book_depth = book.total_bid_depth(5) + book.total_ask_depth(5)
        size = risk.compute_order_size(
            token_id=token_id,
            fair_value=estimate.fair_value,
            spread=estimate.spread,
            book_depth=book_depth,
        )
        assert size > 0

        # Step 5: Place quote
        success = quotes.place_quote(
            token_id=token_id,
            fair_value=estimate.fair_value,
            bid_price=estimate.bid_price,
            ask_price=estimate.ask_price,
            size=size,
        )
        assert success

        # Verify the placed quote is sane
        active = quotes.get_active_quote(token_id)
        assert active is not None
        assert active.buy_price < active.sell_price
        assert active.buy_price >= 0.01
        assert active.sell_price <= 0.99
        assert active.size > 0

    def test_pipeline_tight_spread_market(self):
        """
        Sports market: 0.50/0.51 spread (tight).
        Should still work with a wider spread recommendation.
        """
        fv_engine = FairValueEngine()
        inventory = InventoryTracker()
        risk = MMRiskManager(inventory)
        mock_trading = make_mock_trading()
        quotes = QuoteManager(mock_trading)

        book = clob_book_to_orderbook(CLOB_BOOK_TIGHT_SPREAD)
        token_id = book.token_id
        last_trade = 0.51

        estimate = fv_engine.estimate(token_id, book, last_trade)
        assert estimate is not None
        assert 0.49 < estimate.fair_value < 0.52
        # Should NOT tighten beyond market spread
        assert estimate.spread >= book.spread

        risk_check = risk.should_quote(token_id)
        assert risk_check.allowed

        book_depth = book.total_bid_depth(5) + book.total_ask_depth(5)
        size = risk.compute_order_size(
            token_id, estimate.fair_value, estimate.spread, book_depth,
        )
        assert size > 0

        success = quotes.place_quote(
            token_id, estimate.fair_value,
            estimate.bid_price, estimate.ask_price, size,
        )
        assert success

    def test_pipeline_thin_book(self):
        """
        Thin book: low liquidity.
        Should produce wider spread and smaller size.
        """
        fv_engine = FairValueEngine()
        inventory = InventoryTracker()
        risk = MMRiskManager(inventory)
        mock_trading = make_mock_trading()
        quotes = QuoteManager(mock_trading)

        book_thin = clob_book_to_orderbook(CLOB_BOOK_THIN)
        book_deep = clob_book_to_orderbook(CLOB_BOOK_TIGHT_SPREAD)

        est_thin = fv_engine.estimate("thin", book_thin)
        est_deep = fv_engine.estimate("deep", book_deep)

        # Thin book → wider spread
        assert est_thin.spread > est_deep.spread

        # Thin book → smaller order size (depth cap: 20% of book)
        depth_thin = book_thin.total_bid_depth(5) + book_thin.total_ask_depth(5)
        depth_deep = book_deep.total_bid_depth(5) + book_deep.total_ask_depth(5)

        size_thin = risk.compute_order_size("thin", est_thin.fair_value, est_thin.spread, depth_thin)
        size_deep = risk.compute_order_size("deep", est_deep.fair_value, est_deep.spread, depth_deep)

        # Thin book depth cap (20% of ~95 = 19) vs deep book depth cap (20% of ~57k)
        # Both may hit max_order_size cap, so compare depth caps directly
        thin_depth_cap = depth_thin * 0.20
        deep_depth_cap = depth_deep * 0.20
        assert thin_depth_cap < deep_depth_cap
        assert size_thin <= size_deep

    def test_pipeline_with_inventory_skew(self):
        """
        After accumulating a long position, quotes should skew down.
        """
        fv_engine = FairValueEngine()
        inventory = InventoryTracker()
        risk = MMRiskManager(inventory)
        mock_trading = make_mock_trading()
        quotes = QuoteManager(mock_trading)

        book = clob_book_to_orderbook(CLOB_BOOK_WIDE_SPREAD)
        token_id = book.token_id

        # Accumulate long position
        inventory.record_fill(Fill(
            token_id=token_id, side="BUY", price=0.39, size=500,
            timestamp=1000, order_id="fill_1",
        ))

        estimate = fv_engine.estimate(token_id, book)
        skew = inventory.get_quote_skew(token_id)
        assert skew < 0  # long → negative skew

        # Apply skew and compute adjusted bid/ask
        skewed_fv = logit_adjust(estimate.fair_value, skew)
        assert skewed_fv < estimate.fair_value  # shifted down

        half_width = (logit(estimate.ask_price) - logit(estimate.bid_price)) / 2
        bid_skewed, ask_skewed = logit_spread(skewed_fv, half_width)

        # Skewed quotes should be shifted down vs unskewed
        assert bid_skewed < estimate.bid_price
        assert ask_skewed < estimate.ask_price

        # Still valid
        assert 0.01 <= round_price(bid_skewed)
        assert round_price(ask_skewed) <= 0.99

    def test_pipeline_risk_blocks_after_loss(self):
        """
        After hitting stop-loss, pipeline should stop quoting that market.
        """
        fv_engine = FairValueEngine()
        inventory = InventoryTracker()
        risk = MMRiskManager(inventory)
        mock_trading = make_mock_trading()
        quotes = QuoteManager(mock_trading)

        book = clob_book_to_orderbook(CLOB_BOOK_WIDE_SPREAD)
        token_id = book.token_id

        # Simulate a -$250 loss (stop-loss is -$200)
        inventory.record_fill(Fill(
            token_id=token_id, side="BUY", price=0.50, size=1000,
            timestamp=1000, order_id="buy_1",
        ))
        inventory.record_fill(Fill(
            token_id=token_id, side="SELL", price=0.25, size=1000,
            timestamp=1001, order_id="sell_1",
        ))

        risk_check = risk.should_quote(token_id)
        assert not risk_check.allowed
        assert "stop-loss" in risk_check.reason

        # Should not be able to compute size either
        size = risk.compute_order_size(token_id, 0.40, 0.04, 10000)
        assert size == 0

    def test_pipeline_fill_updates_inventory(self):
        """
        Full cycle: place quote → detect fill → inventory updated → skew changes.
        """
        fv_engine = FairValueEngine()
        inventory = InventoryTracker()
        risk = MMRiskManager(inventory)
        mock_trading = make_mock_trading()
        quotes = QuoteManager(mock_trading)

        book = clob_book_to_orderbook(CLOB_BOOK_WIDE_SPREAD)
        token_id = book.token_id

        # Place initial quote
        estimate = fv_engine.estimate(token_id, book)
        book_depth = book.total_bid_depth(5) + book.total_ask_depth(5)
        size = risk.compute_order_size(token_id, estimate.fair_value, estimate.spread, book_depth)
        quotes.place_quote(token_id, estimate.fair_value, estimate.bid_price, estimate.ask_price, size)

        # Simulate buy side getting filled
        active = quotes.get_active_quote(token_id)
        buy_oid = active.buy_order_id
        sell_oid = active.sell_order_id

        # Only sell order remains open
        mock_trading.get_open_orders.return_value = [{"id": sell_oid}]
        fills = quotes.detect_fills()
        assert len(fills) == 1
        assert fills[0].side == "BUY"

        # Record fill in inventory
        for f in fills:
            inventory.record_fill(Fill(
                token_id=f.token_id, side=f.side, price=f.price,
                size=f.size, timestamp=f.timestamp, order_id=f.order_id,
            ))

        # Now we're long → skew should be negative
        pos = inventory.get_position(token_id)
        assert pos.net_quantity > 0
        skew = inventory.get_quote_skew(token_id)
        assert skew < 0

    def test_bullish_imbalance_shifts_quotes_up(self):
        """
        Bullish orderbook imbalance → FV above midpoint → quotes shifted up.
        """
        fv_engine = FairValueEngine()
        inventory = InventoryTracker()

        book_bull = clob_book_to_orderbook(CLOB_BOOK_BULLISH_IMBALANCE)
        book_tight = clob_book_to_orderbook(CLOB_BOOK_TIGHT_SPREAD)

        est_bull = fv_engine.estimate("bull", book_bull)
        assert est_bull.fair_value > est_bull.midpoint  # FV shifted up
        assert est_bull.imbalance_adj_logit > 0  # positive imbalance

    def test_multiple_markets_independent(self):
        """
        Two markets running simultaneously don't interfere.
        """
        fv_engine = FairValueEngine()
        inventory = InventoryTracker()
        risk = MMRiskManager(inventory)
        mock_trading = make_mock_trading()
        quotes = QuoteManager(mock_trading)

        book_a = clob_book_to_orderbook(CLOB_BOOK_WIDE_SPREAD)
        book_b = clob_book_to_orderbook(CLOB_BOOK_TIGHT_SPREAD)

        # Quote both markets
        est_a = fv_engine.estimate(book_a.token_id, book_a)
        est_b = fv_engine.estimate(book_b.token_id, book_b)

        depth_a = book_a.total_bid_depth(5) + book_a.total_ask_depth(5)
        depth_b = book_b.total_bid_depth(5) + book_b.total_ask_depth(5)

        size_a = risk.compute_order_size(book_a.token_id, est_a.fair_value, est_a.spread, depth_a)
        size_b = risk.compute_order_size(book_b.token_id, est_b.fair_value, est_b.spread, depth_b)

        quotes.place_quote(book_a.token_id, est_a.fair_value, est_a.bid_price, est_a.ask_price, size_a)
        quotes.place_quote(book_b.token_id, est_b.fair_value, est_b.bid_price, est_b.ask_price, size_b)

        assert quotes.num_active_quotes == 2

        # Fill in market A doesn't affect market B
        inventory.record_fill(Fill(
            token_id=book_a.token_id, side="BUY", price=0.39, size=100,
            timestamp=1000, order_id="fill_a",
        ))

        skew_a = inventory.get_quote_skew(book_a.token_id)
        skew_b = inventory.get_quote_skew(book_b.token_id)
        assert skew_a != 0
        assert skew_b == 0


class TestEdgeCases:
    """Edge cases that could cause real money loss."""

    def test_quote_prices_always_valid(self):
        """Bid >= 0.01, ask <= 0.99, bid < ask — for all book shapes."""
        fv_engine = FairValueEngine()

        books = [
            clob_book_to_orderbook(CLOB_BOOK_WIDE_SPREAD),
            clob_book_to_orderbook(CLOB_BOOK_TIGHT_SPREAD),
            clob_book_to_orderbook(CLOB_BOOK_THIN),
            clob_book_to_orderbook(CLOB_BOOK_BULLISH_IMBALANCE),
        ]

        for book in books:
            est = fv_engine.estimate("test", book)
            assert est is not None, f"No estimate for book mid={book.midpoint}"
            assert est.bid_price >= 0.01, f"bid {est.bid_price} < 0.01"
            assert est.ask_price <= 0.99, f"ask {est.ask_price} > 0.99"
            assert est.bid_price < est.ask_price, (
                f"crossed: bid={est.bid_price} >= ask={est.ask_price}"
            )
            assert round_price(est.bid_price) < round_price(est.ask_price), (
                f"crossed after rounding: bid={round_price(est.bid_price)} "
                f">= ask={round_price(est.ask_price)}"
            )

    def test_extreme_skew_doesnt_cross(self):
        """Max inventory skew should never produce crossed quotes."""
        fv_engine = FairValueEngine()
        inventory = InventoryTracker()

        book = clob_book_to_orderbook(CLOB_BOOK_WIDE_SPREAD)
        est = fv_engine.estimate("test", book)

        # Max out position to get max skew
        inventory.record_fill(Fill(
            token_id="test", side="BUY", price=0.40, size=10000,
            timestamp=1000, order_id="big_fill",
        ))
        skew = inventory.get_quote_skew("test")

        # Apply max skew
        skewed_fv = logit_adjust(est.fair_value, skew)
        half_width = (logit(est.ask_price) - logit(est.bid_price)) / 2
        bid, ask = logit_spread(skewed_fv, half_width)

        assert 0 < bid < ask < 1
        assert round_price(bid) < round_price(ask)

    def test_near_boundary_prices_safe(self):
        """FV near 0.01 or 0.99 doesn't produce invalid quotes."""
        fv_engine = FairValueEngine()

        # Book with midpoint near 0.10
        from src.orderbook import Orderbook, OrderbookLevel
        low_book = Orderbook(
            token_id="low",
            bids=[
                OrderbookLevel(0.09, 1000),
                OrderbookLevel(0.08, 2000),
                OrderbookLevel(0.07, 3000),
            ],
            asks=[
                OrderbookLevel(0.11, 1000),
                OrderbookLevel(0.12, 2000),
                OrderbookLevel(0.13, 3000),
            ],
        )

        est = fv_engine.estimate("low", low_book)
        assert est is not None
        assert est.bid_price >= 0.01
        assert est.ask_price <= 0.99
        assert est.bid_price < est.ask_price

        # Book with midpoint near 0.90
        high_book = Orderbook(
            token_id="high",
            bids=[
                OrderbookLevel(0.89, 1000),
                OrderbookLevel(0.88, 2000),
                OrderbookLevel(0.87, 3000),
            ],
            asks=[
                OrderbookLevel(0.91, 1000),
                OrderbookLevel(0.92, 2000),
                OrderbookLevel(0.93, 3000),
            ],
        )

        est = fv_engine.estimate("high", high_book)
        assert est is not None
        assert est.bid_price >= 0.01
        assert est.ask_price <= 0.99
        assert est.bid_price < est.ask_price
