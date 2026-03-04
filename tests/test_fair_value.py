"""
Tests for the FairValueEngine.

Uses real Polymarket orderbook formats from fixtures.
"""

import pytest
from src.layer2_signals.fair_value import FairValueEngine, FairValueEstimate, SignalProvider
from src.utils import logit
from tests.fixtures import (
    CLOB_BOOK_WIDE_SPREAD,
    CLOB_BOOK_TIGHT_SPREAD,
    CLOB_BOOK_THIN,
    CLOB_BOOK_BULLISH_IMBALANCE,
    clob_book_to_orderbook,
)


@pytest.fixture
def engine():
    return FairValueEngine()


@pytest.fixture
def book_wide():
    return clob_book_to_orderbook(CLOB_BOOK_WIDE_SPREAD)


@pytest.fixture
def book_tight():
    return clob_book_to_orderbook(CLOB_BOOK_TIGHT_SPREAD)


@pytest.fixture
def book_thin():
    return clob_book_to_orderbook(CLOB_BOOK_THIN)


@pytest.fixture
def book_bullish():
    return clob_book_to_orderbook(CLOB_BOOK_BULLISH_IMBALANCE)


class TestFairValueBasic:
    """Basic FV estimation from orderbook midpoint."""

    def test_fv_near_midpoint(self, engine, book_wide):
        """FV should be close to orderbook midpoint (0.395)."""
        est = engine.estimate("test_token", book_wide)
        assert est is not None
        # midpoint = (0.39 + 0.40) / 2 = 0.395
        assert abs(est.midpoint - 0.395) < 0.001
        # FV should be near midpoint (small adjustments)
        assert abs(est.fair_value - 0.395) < 0.02

    def test_fv_centered_market(self, engine, book_tight):
        """FV for a centered market (~0.505 mid)."""
        est = engine.estimate("test_token", book_tight)
        assert est is not None
        assert abs(est.midpoint - 0.505) < 0.001
        assert abs(est.fair_value - 0.505) < 0.02

    def test_fv_always_in_valid_range(self, engine, book_wide, book_tight, book_thin, book_bullish):
        """FV must always be in (0.01, 0.99)."""
        for book in [book_wide, book_tight, book_thin, book_bullish]:
            est = engine.estimate("test_token", book)
            assert est is not None
            assert 0.01 <= est.fair_value <= 0.99

    def test_returns_none_for_empty_book(self, engine):
        """No bids/asks → None."""
        from src.orderbook import Orderbook
        empty = Orderbook(token_id="empty", bids=[], asks=[])
        est = engine.estimate("test_token", empty)
        assert est is None


class TestFairValueSpread:
    """Spread recommendations."""

    def test_spread_bid_below_ask(self, engine, book_wide):
        """bid_price < ask_price always."""
        est = engine.estimate("test_token", book_wide)
        assert est.bid_price < est.ask_price

    def test_fv_between_bid_ask(self, engine, book_wide):
        """FV should be between our bid and ask."""
        est = engine.estimate("test_token", book_wide)
        assert est.bid_price < est.fair_value < est.ask_price

    def test_thin_book_wider_spread(self, engine, book_thin, book_tight):
        """Thin book should produce wider spread than deep book."""
        est_thin = engine.estimate("test_token", book_thin)
        est_deep = engine.estimate("test_token", book_tight)
        assert est_thin.spread > est_deep.spread

    def test_spread_positive(self, engine, book_wide):
        """Spread must be positive."""
        est = engine.estimate("test_token", book_wide)
        assert est.spread > 0

    def test_spread_not_insane(self, engine, book_wide):
        """Spread should be reasonable (< 30% of price)."""
        est = engine.estimate("test_token", book_wide)
        assert est.spread < 0.30


class TestFairValueImbalance:
    """Imbalance adjustment pushes FV in the right direction."""

    def test_bullish_imbalance_shifts_up(self, engine, book_bullish):
        """Heavy bid side → FV should shift above midpoint."""
        est = engine.estimate("test_token", book_bullish)
        # midpoint = (0.59 + 0.61) / 2 = 0.60
        # Heavy bids → imbalance > 0 → positive logit adjustment → FV > midpoint
        assert est.fair_value > est.midpoint
        assert est.imbalance_adj_logit > 0

    def test_symmetric_book_minimal_imbalance(self, engine, book_tight):
        """Roughly symmetric book → small imbalance adjustment."""
        est = engine.estimate("test_token", book_tight)
        assert abs(est.imbalance_adj_logit) < 0.05


class TestFairValueVWAP:
    """VWAP/last trade price blending."""

    def test_last_trade_above_mid_shifts_up(self, engine, book_wide):
        """Last trade above midpoint → FV shifts up."""
        # midpoint is 0.395, last trade at 0.42
        est = engine.estimate("test_token", book_wide, last_trade_price=0.42)
        est_no_vwap = engine.estimate("test_token", book_wide, last_trade_price=None)
        assert est.fair_value > est_no_vwap.fair_value

    def test_last_trade_below_mid_shifts_down(self, engine, book_wide):
        """Last trade below midpoint → FV shifts down."""
        est = engine.estimate("test_token", book_wide, last_trade_price=0.37)
        est_no_vwap = engine.estimate("test_token", book_wide, last_trade_price=None)
        assert est.fair_value < est_no_vwap.fair_value

    def test_last_trade_at_mid_no_change(self, engine, book_wide):
        """Last trade at midpoint → no VWAP adjustment."""
        mid = book_wide.midpoint
        est = engine.estimate("test_token", book_wide, last_trade_price=mid)
        assert abs(est.vwap_adj_logit) < 0.001

    def test_invalid_last_trade_ignored(self, engine, book_wide):
        """last_trade=0 or None → no VWAP adjustment."""
        est_none = engine.estimate("test_token", book_wide, last_trade_price=None)
        est_zero = engine.estimate("test_token", book_wide, last_trade_price=0)
        assert abs(est_none.fair_value - est_zero.fair_value) < 1e-10


class TestFairValueConfidence:
    """Confidence scoring."""

    def test_deep_book_higher_confidence(self, engine, book_tight, book_thin):
        """Deep book → higher confidence than thin book."""
        est_deep = engine.estimate("test_token", book_tight)
        est_thin = engine.estimate("test_token", book_thin)
        assert est_deep.confidence > est_thin.confidence

    def test_confidence_range(self, engine, book_wide):
        """Confidence always 0-1."""
        est = engine.estimate("test_token", book_wide)
        assert 0 <= est.confidence <= 1


class TestSignalProvider:
    """Signal provider extension point."""

    def test_signal_shifts_fv(self, engine, book_wide):
        """Registered signal provider shifts FV."""

        class BullishSignal(SignalProvider):
            def get_adjustment(self, token_id, current_fv):
                return 0.5  # strong bullish signal in logit space

            @property
            def name(self):
                return "test_bullish"

        engine.register_signal(BullishSignal())
        est = engine.estimate("test_token", book_wide)
        est_base_mid = book_wide.midpoint

        # FV should be shifted up significantly from midpoint
        assert est.fair_value > est_base_mid + 0.05

    def test_failing_signal_doesnt_crash(self, engine, book_wide):
        """Signal provider that throws → logged, not crash."""

        class BrokenSignal(SignalProvider):
            def get_adjustment(self, token_id, current_fv):
                raise ValueError("broken")

            @property
            def name(self):
                return "broken"

        engine.register_signal(BrokenSignal())
        est = engine.estimate("test_token", book_wide)
        assert est is not None  # should still produce estimate
