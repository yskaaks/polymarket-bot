"""
Tests for InventoryTracker and MMRiskManager.

Validates position tracking, quote skew direction/magnitude,
and risk limit enforcement.
"""

import os
import pytest
from unittest.mock import patch

from src.layer3_portfolio.mm_risk_manager import (
    InventoryTracker, MMRiskManager, Fill, RiskCheckResult,
)
from src.utils import logit_adjust


@pytest.fixture(autouse=True)
def mock_config():
    """Provide test config without needing .env."""
    with patch.dict(os.environ, {
        "PRIVATE_KEY": "0x" + "a" * 64,
        "FUNDER_ADDRESS": "0x" + "b" * 40,
        "DRY_RUN": "1",
        "MM_MAX_MARKETS": "3",
        "MM_MAX_POSITION_PER_MARKET": "1000",
        "MM_MAX_TOTAL_EXPOSURE": "5000",
        "MM_STOP_LOSS_PER_MARKET": "-100",
        "MM_CAPITAL": "50000",
        "MAX_ORDER_SIZE": "50",
    }):
        # Bypass load_dotenv so .env doesn't override our test values
        with patch("config.settings.load_dotenv"):
            from config.settings import reload_config
            reload_config()
            yield


@pytest.fixture
def tracker():
    return InventoryTracker()


@pytest.fixture
def risk(tracker):
    return MMRiskManager(tracker)


class TestInventoryTracker:
    """Position tracking and fill recording."""

    def test_empty_position(self, tracker):
        """New token has zero position."""
        pos = tracker.get_position("token_a")
        assert pos.net_quantity == 0
        assert pos.realized_pnl == 0

    def test_buy_increases_position(self, tracker):
        """BUY fill increases net quantity."""
        tracker.record_fill(Fill(
            token_id="token_a", side="BUY", price=0.50, size=100,
            timestamp=1000, order_id="ord1",
        ))
        pos = tracker.get_position("token_a")
        assert pos.net_quantity == 100
        assert pos.avg_entry_price == 0.50

    def test_sell_decreases_position(self, tracker):
        """SELL fill decreases net quantity."""
        tracker.record_fill(Fill(
            token_id="token_a", side="BUY", price=0.50, size=100,
            timestamp=1000, order_id="ord1",
        ))
        tracker.record_fill(Fill(
            token_id="token_a", side="SELL", price=0.55, size=50,
            timestamp=1001, order_id="ord2",
        ))
        pos = tracker.get_position("token_a")
        assert pos.net_quantity == 50

    def test_sell_realizes_pnl(self, tracker):
        """Selling at higher price → positive realized PnL."""
        tracker.record_fill(Fill(
            token_id="token_a", side="BUY", price=0.40, size=100,
            timestamp=1000, order_id="ord1",
        ))
        tracker.record_fill(Fill(
            token_id="token_a", side="SELL", price=0.45, size=100,
            timestamp=1001, order_id="ord2",
        ))
        pos = tracker.get_position("token_a")
        assert abs(pos.realized_pnl - 5.0) < 0.01  # 100 * (0.45 - 0.40) = 5.0

    def test_multiple_buys_avg_entry(self, tracker):
        """Multiple buys update average entry price."""
        tracker.record_fill(Fill(
            token_id="token_a", side="BUY", price=0.40, size=100,
            timestamp=1000, order_id="ord1",
        ))
        tracker.record_fill(Fill(
            token_id="token_a", side="BUY", price=0.50, size=100,
            timestamp=1001, order_id="ord2",
        ))
        pos = tracker.get_position("token_a")
        assert pos.net_quantity == 200
        assert abs(pos.avg_entry_price - 0.45) < 0.01  # (40 + 50) / 200

    def test_separate_tokens(self, tracker):
        """Different tokens tracked independently."""
        tracker.record_fill(Fill(
            token_id="token_a", side="BUY", price=0.50, size=100,
            timestamp=1000, order_id="ord1",
        ))
        tracker.record_fill(Fill(
            token_id="token_b", side="BUY", price=0.30, size=200,
            timestamp=1001, order_id="ord2",
        ))
        assert tracker.get_position("token_a").net_quantity == 100
        assert tracker.get_position("token_b").net_quantity == 200


class TestQuoteSkew:
    """Inventory skew in logit space."""

    def test_zero_position_zero_skew(self, tracker):
        """No position → no skew."""
        assert tracker.get_quote_skew("token_a") == 0.0

    def test_long_position_negative_skew(self, tracker):
        """Long → negative skew (shift quotes down to sell more)."""
        tracker.record_fill(Fill(
            token_id="token_a", side="BUY", price=0.50, size=500,
            timestamp=1000, order_id="ord1",
        ))
        skew = tracker.get_quote_skew("token_a")
        assert skew < 0  # negative in logit space → shifts price down

    def test_short_position_positive_skew(self, tracker):
        """Short → positive skew (shift quotes up to buy more)."""
        tracker.record_fill(Fill(
            token_id="token_a", side="SELL", price=0.50, size=500,
            timestamp=1000, order_id="ord1",
        ))
        skew = tracker.get_quote_skew("token_a")
        assert skew > 0  # positive in logit space → shifts price up

    def test_skew_bounded(self, tracker):
        """Skew never exceeds MAX_SKEW_LOGIT even with max position."""
        tracker.record_fill(Fill(
            token_id="token_a", side="BUY", price=0.50, size=5000,
            timestamp=1000, order_id="ord1",
        ))
        skew = tracker.get_quote_skew("token_a")
        assert abs(skew) <= tracker.MAX_SKEW_LOGIT + 1e-10

    def test_skew_proportional_to_position(self, tracker):
        """Larger position → larger skew magnitude."""
        t1, t2 = InventoryTracker(), InventoryTracker()
        t1.record_fill(Fill(token_id="a", side="BUY", price=0.50, size=100,
                            timestamp=1000, order_id="o1"))
        t2.record_fill(Fill(token_id="a", side="BUY", price=0.50, size=500,
                            timestamp=1000, order_id="o2"))
        assert abs(t2.get_quote_skew("a")) > abs(t1.get_quote_skew("a"))

    def test_skew_effect_smaller_at_extremes(self, tracker):
        """
        Key test: applying the same logit skew at p=0.90 should produce
        a smaller probability shift than at p=0.50.
        """
        tracker.record_fill(Fill(
            token_id="token_a", side="BUY", price=0.50, size=500,
            timestamp=1000, order_id="ord1",
        ))
        skew = tracker.get_quote_skew("token_a")

        # Apply skew at different price levels
        shift_at_50 = abs(logit_adjust(0.50, skew) - 0.50)
        shift_at_90 = abs(logit_adjust(0.90, skew) - 0.90)

        assert shift_at_50 > shift_at_90


class TestMMRiskManager:
    """Risk limit enforcement."""

    def test_allows_quote_initially(self, risk):
        """Fresh state → quoting allowed."""
        result = risk.should_quote("token_a")
        assert result.allowed

    def test_stop_loss_blocks_quoting(self, risk, tracker):
        """Per-market stop-loss blocks quoting."""
        # Simulate loss: buy high, sell low
        tracker.record_fill(Fill(
            token_id="token_a", side="BUY", price=0.50, size=1000,
            timestamp=1000, order_id="ord1",
        ))
        tracker.record_fill(Fill(
            token_id="token_a", side="SELL", price=0.38, size=1000,
            timestamp=1001, order_id="ord2",
        ))
        # PnL = 1000 * (0.38 - 0.50) = -120, stop-loss is -100
        result = risk.should_quote("token_a")
        assert not result.allowed
        assert "stop-loss" in result.reason

    def test_max_markets_blocks_new(self, risk, tracker):
        """Can't open position in 4th market when max is 3."""
        for i in range(3):
            tracker.record_fill(Fill(
                token_id=f"token_{i}", side="BUY", price=0.50, size=10,
                timestamp=1000 + i, order_id=f"ord_{i}",
            ))

        # 4th market should be blocked
        result = risk.should_quote("token_new")
        assert not result.allowed
        assert "max active markets" in result.reason

    def test_max_markets_allows_existing(self, risk, tracker):
        """Existing active market still allowed even at max."""
        for i in range(3):
            tracker.record_fill(Fill(
                token_id=f"token_{i}", side="BUY", price=0.50, size=10,
                timestamp=1000 + i, order_id=f"ord_{i}",
            ))

        # Existing market should still be allowed
        result = risk.should_quote("token_0")
        assert result.allowed

    def test_circuit_breaker(self, risk, tracker):
        """Portfolio drawdown triggers circuit breaker."""
        # Big loss across markets
        for i in range(3):
            tracker.record_fill(Fill(
                token_id=f"token_{i}", side="BUY", price=0.50, size=2000,
                timestamp=1000, order_id=f"buy_{i}",
            ))
            tracker.record_fill(Fill(
                token_id=f"token_{i}", side="SELL", price=0.30, size=2000,
                timestamp=1001, order_id=f"sell_{i}",
            ))
        # Total PnL = 3 * 2000 * (0.30 - 0.50) = -1200 < -1000 limit
        result = risk.should_quote("token_new")
        assert not result.allowed
        assert "circuit breaker" in result.reason

    def test_circuit_breaker_blocks_all(self, risk, tracker):
        """Once circuit breaker fires, ALL tokens blocked."""
        for i in range(3):
            tracker.record_fill(Fill(
                token_id=f"token_{i}", side="BUY", price=0.50, size=2000,
                timestamp=1000, order_id=f"buy_{i}",
            ))
            tracker.record_fill(Fill(
                token_id=f"token_{i}", side="SELL", price=0.30, size=2000,
                timestamp=1001, order_id=f"sell_{i}",
            ))
        risk.should_quote("token_0")  # triggers breaker
        result = risk.should_quote("token_0")
        assert not result.allowed


class TestOrderSizing:
    """Order size computation."""

    def test_size_positive_for_valid_market(self, risk):
        """Valid market → positive size."""
        size = risk.compute_order_size(
            token_id="token_a",
            fair_value=0.50,
            spread=0.04,
            book_depth=1000,
        )
        assert size > 0

    def test_size_capped_by_max_order_size(self, risk):
        """Size never exceeds config.max_order_size (50)."""
        size = risk.compute_order_size(
            token_id="token_a",
            fair_value=0.50,
            spread=0.10,
            book_depth=100000,
        )
        assert size <= 50

    def test_size_capped_by_book_depth(self, risk):
        """Size never exceeds 20% of book depth."""
        size = risk.compute_order_size(
            token_id="token_a",
            fair_value=0.50,
            spread=0.10,
            book_depth=100,
        )
        assert size <= 20  # 20% of 100

    def test_size_zero_when_risk_blocked(self, risk, tracker):
        """Blocked by risk → size 0."""
        # Trigger circuit breaker
        for i in range(3):
            tracker.record_fill(Fill(
                token_id=f"token_{i}", side="BUY", price=0.50, size=2000,
                timestamp=1000, order_id=f"buy_{i}",
            ))
            tracker.record_fill(Fill(
                token_id=f"token_{i}", side="SELL", price=0.30, size=2000,
                timestamp=1001, order_id=f"sell_{i}",
            ))
        risk.should_quote("any")  # triggers breaker

        size = risk.compute_order_size(
            token_id="token_a",
            fair_value=0.50,
            spread=0.04,
            book_depth=1000,
        )
        assert size == 0

    def test_size_decreases_with_exposure(self, risk, tracker):
        """Position headroom shrinks → smaller orders."""
        size_fresh = risk.compute_order_size(
            token_id="token_a",
            fair_value=0.50,
            spread=0.04,
            book_depth=10000,
        )

        # Build up some position
        tracker.record_fill(Fill(
            token_id="token_a", side="BUY", price=0.50, size=800,
            timestamp=1000, order_id="ord1",
        ))

        size_loaded = risk.compute_order_size(
            token_id="token_a",
            fair_value=0.50,
            spread=0.04,
            book_depth=10000,
        )

        assert size_loaded <= size_fresh
