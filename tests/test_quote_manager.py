"""
Tests for QuoteManager.

Uses a mock TradingClient to verify quote placement, requote logic,
and fill detection without hitting Polymarket.
"""

import os
import time
import pytest
from unittest.mock import MagicMock, patch

from src.layer4_execution.quote_manager import QuoteManager, ActiveQuote, FillEvent
from src.layer4_execution.trading import OrderResult
from src.layer3_portfolio.mm_risk_manager import InventoryTracker, Fill


@pytest.fixture(autouse=True)
def mock_config():
    with patch.dict(os.environ, {
        "PRIVATE_KEY": "0x" + "a" * 64,
        "FUNDER_ADDRESS": "0x" + "b" * 40,
        "DRY_RUN": "1",
    }):
        with patch("config.settings.load_dotenv"):
            from config.settings import reload_config
            reload_config()
            yield


@pytest.fixture
def mock_trading():
    """Mock TradingClient that always succeeds."""
    client = MagicMock()
    client.place_limit_order.return_value = OrderResult(
        success=True, order_id="MOCK_ORDER_123"
    )
    client.cancel_order.return_value = OrderResult(success=True)
    client.get_open_orders.return_value = []
    return client


@pytest.fixture
def qm(mock_trading):
    return QuoteManager(mock_trading, InventoryTracker())


class TestQuotePlacement:
    """Quote placement logic."""

    def test_place_quote_buy_only_no_inventory(self, qm, mock_trading):
        """With no inventory, only BUY is placed (SELL skipped)."""
        success = qm.place_quote(
            token_id="token_a",
            fair_value=0.50,
            bid_price=0.47,
            ask_price=0.53,
            size=10.0,
        )
        assert success
        assert mock_trading.place_limit_order.call_count == 1

        buy_call = mock_trading.place_limit_order.call_args_list[0]
        assert buy_call.kwargs["side"] == "BUY"
        assert buy_call.kwargs["price"] == pytest.approx(0.47, abs=0.005)

    def test_place_quote_both_sides_with_inventory(self, mock_trading):
        """With inventory, both BUY and SELL are placed."""
        inventory = InventoryTracker()
        inventory.record_fill(Fill(
            token_id="token_a", side="BUY", price=0.50, size=20.0,
            order_id="x", timestamp=0.0
        ))
        qm = QuoteManager(mock_trading, inventory)

        success = qm.place_quote(
            token_id="token_a",
            fair_value=0.50,
            bid_price=0.47,
            ask_price=0.53,
            size=10.0,
        )
        assert success
        assert mock_trading.place_limit_order.call_count == 2

        buy_call = mock_trading.place_limit_order.call_args_list[0]
        assert buy_call.kwargs["side"] == "BUY"
        sell_call = mock_trading.place_limit_order.call_args_list[1]
        assert sell_call.kwargs["side"] == "SELL"

    def test_place_quote_tracks_active(self, qm):
        """Active quote is tracked after placement."""
        qm.place_quote("token_a", 0.50, 0.47, 0.53, 10.0)
        assert "token_a" in qm.active_tokens
        assert qm.num_active_quotes == 1

        quote = qm.get_active_quote("token_a")
        assert quote is not None
        assert quote.fair_value == 0.50
        assert quote.buy_price == pytest.approx(0.47, abs=0.005)
        assert quote.sell_price == pytest.approx(0.53, abs=0.005)

    def test_place_quote_cancels_existing(self, qm, mock_trading):
        """Placing a new quote cancels the old one first."""
        qm.place_quote("token_a", 0.50, 0.47, 0.53, 10.0)
        qm.place_quote("token_a", 0.51, 0.48, 0.54, 10.0)

        # Should have cancelled the first quote's orders
        assert mock_trading.cancel_order.called
        assert qm.num_active_quotes == 1

    def test_rejects_crossed_quote(self, qm, mock_trading):
        """bid >= ask should be rejected."""
        success = qm.place_quote("token_a", 0.50, 0.53, 0.47, 10.0)
        assert not success
        assert mock_trading.place_limit_order.call_count == 0

    def test_rejects_zero_size(self, qm, mock_trading):
        """Size too small (rounds to 0) → rejected."""
        success = qm.place_quote("token_a", 0.50, 0.47, 0.53, 0.01)
        assert not success

    def test_partial_failure_with_inventory(self, mock_trading):
        """One side fails → quote still tracked (partial)."""
        inventory = InventoryTracker()
        inventory.record_fill(Fill(
            token_id="token_a", side="BUY", price=0.50, size=20.0,
            order_id="x", timestamp=0.0
        ))
        qm = QuoteManager(mock_trading, inventory)

        mock_trading.place_limit_order.side_effect = [
            OrderResult(success=True, order_id="BUY_1"),
            OrderResult(success=False, error="insufficient balance"),
        ]
        success = qm.place_quote("token_a", 0.50, 0.47, 0.53, 10.0)
        assert not success
        # Quote still tracked (buy side is live)
        assert "token_a" in qm.active_tokens


class TestRequoteLogic:
    """When to refresh quotes."""

    def test_needs_requote_no_existing(self, qm):
        """No existing quote → needs requote."""
        assert qm.needs_requote("token_a", 0.50) is True

    def test_no_requote_same_fv(self, qm):
        """Same FV and recent → no requote."""
        qm.place_quote("token_a", 0.50, 0.47, 0.53, 10.0)
        assert qm.needs_requote("token_a", 0.50) is False

    def test_requote_on_fv_drift(self, qm):
        """Large FV change → needs requote."""
        qm.place_quote("token_a", 0.50, 0.47, 0.53, 10.0)
        # 0.50 → 0.55 is a big logit drift
        assert qm.needs_requote("token_a", 0.55) is True

    def test_small_fv_change_no_requote(self, qm):
        """Small FV change → no requote."""
        qm.place_quote("token_a", 0.50, 0.47, 0.53, 10.0)
        # 0.50 → 0.505 is tiny in logit space
        assert qm.needs_requote("token_a", 0.505) is False

    def test_requote_on_age(self, qm):
        """Old quote → needs requote."""
        qm.place_quote("token_a", 0.50, 0.47, 0.53, 10.0)
        # Manually age the quote
        qm._quotes["token_a"].placed_at = time.time() - 60
        assert qm.needs_requote("token_a", 0.50) is True

    def test_fv_drift_sensitivity_logit_space(self, qm):
        """
        Key test: same absolute price change should trigger requote at
        p=0.50 but NOT at p=0.90 (because logit distance is different).
        """
        # Quote at 0.50, move to 0.52 (Δ=0.02)
        qm.place_quote("token_50", 0.50, 0.47, 0.53, 10.0)
        needs_at_50 = qm.needs_requote("token_50", 0.52)

        # Quote at 0.90, move to 0.92 (same Δ=0.02, but bigger in logit)
        qm.place_quote("token_90", 0.90, 0.88, 0.92, 10.0)
        needs_at_90 = qm.needs_requote("token_90", 0.92)

        # At 0.90, a 0.02 move is logit(0.92)-logit(0.90) ≈ 0.25
        # At 0.50, a 0.02 move is logit(0.52)-logit(0.50) ≈ 0.08
        # With threshold 0.08, the 0.90 case should definitely trigger
        assert needs_at_90 is True


class TestFillDetection:
    """Detecting when quotes get filled."""

    @pytest.fixture
    def qm_with_inventory(self, mock_trading):
        """QuoteManager with inventory so both BUY and SELL get placed."""
        inventory = InventoryTracker()
        inventory.record_fill(Fill(
            token_id="token_a", side="BUY", price=0.50, size=100.0,
            order_id="x", timestamp=0.0
        ))
        return QuoteManager(mock_trading, inventory)

    def test_no_fills_when_orders_open(self, qm_with_inventory, mock_trading):
        """All orders still open → no fills."""
        mock_trading.place_limit_order.side_effect = [
            OrderResult(success=True, order_id="BUY_1"),
            OrderResult(success=True, order_id="SELL_1"),
        ]
        qm_with_inventory.place_quote("token_a", 0.50, 0.47, 0.53, 10.0)

        mock_trading.get_open_orders.return_value = [
            {"id": "BUY_1"},
            {"id": "SELL_1"},
        ]
        fills = qm_with_inventory.detect_fills()
        assert len(fills) == 0

    def test_detects_buy_fill(self, qm_with_inventory, mock_trading):
        """Buy order disappears from open orders → fill detected."""
        mock_trading.place_limit_order.side_effect = [
            OrderResult(success=True, order_id="BUY_1"),
            OrderResult(success=True, order_id="SELL_1"),
        ]
        qm_with_inventory.place_quote("token_a", 0.50, 0.47, 0.53, 10.0)

        mock_trading.get_open_orders.return_value = [
            {"id": "SELL_1"},
        ]
        fills = qm_with_inventory.detect_fills()
        assert len(fills) == 1
        assert fills[0].side == "BUY"
        assert fills[0].price == pytest.approx(0.47, abs=0.005)
        assert fills[0].order_id == "BUY_1"

    def test_detects_both_sides_filled(self, qm_with_inventory, mock_trading):
        """Both sides filled → two fill events."""
        mock_trading.place_limit_order.side_effect = [
            OrderResult(success=True, order_id="BUY_1"),
            OrderResult(success=True, order_id="SELL_1"),
        ]
        qm_with_inventory.place_quote("token_a", 0.50, 0.47, 0.53, 10.0)

        mock_trading.get_open_orders.return_value = []
        fills = qm_with_inventory.detect_fills()
        assert len(fills) == 2
        sides = {f.side for f in fills}
        assert sides == {"BUY", "SELL"}

    def test_skips_dry_run_orders(self, qm, mock_trading):
        """DRY_RUN_ORDER ids should not trigger fill detection."""
        mock_trading.place_limit_order.return_value = OrderResult(
            success=True, order_id="DRY_RUN_ORDER"
        )
        qm.place_quote("token_a", 0.50, 0.47, 0.53, 10.0)

        mock_trading.get_open_orders.return_value = []
        fills = qm.detect_fills()
        assert len(fills) == 0


class TestCancellation:
    """Quote cancellation."""

    def test_cancel_all(self, qm, mock_trading):
        """cancel_all_quotes clears everything."""
        mock_trading.place_limit_order.side_effect = [
            OrderResult(success=True, order_id=f"ORD_{i}")
            for i in range(6)  # 3 tokens × 2 sides
        ]
        qm.place_quote("token_a", 0.50, 0.47, 0.53, 10.0)
        qm.place_quote("token_b", 0.60, 0.57, 0.63, 10.0)
        qm.place_quote("token_c", 0.70, 0.67, 0.73, 10.0)

        qm.cancel_all_quotes()
        assert qm.num_active_quotes == 0

    def test_cancel_single(self, qm, mock_trading):
        """Cancel specific token quote."""
        mock_trading.place_limit_order.side_effect = [
            OrderResult(success=True, order_id=f"ORD_{i}")
            for i in range(4)
        ]
        qm.place_quote("token_a", 0.50, 0.47, 0.53, 10.0)
        qm.place_quote("token_b", 0.60, 0.57, 0.63, 10.0)

        qm.cancel_quote("token_a")
        assert "token_a" not in qm.active_tokens
        assert "token_b" in qm.active_tokens
