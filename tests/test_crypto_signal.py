"""Tests for crypto price signal provider."""

import math
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from src.layer2_signals.crypto_price_signal import (
    CryptoPriceSignal,
    CryptoMarketMapping,
    _norm_cdf,
)


@pytest.fixture
def signal():
    """Create a CryptoPriceSignal without connecting to Binance."""
    with patch.object(CryptoPriceSignal, "_seed_prices"):
        s = CryptoPriceSignal()
    return s


class TestQuestionParsing:
    def test_parse_btc_above(self, signal):
        ok = signal.register_market(
            "tok1", "Will Bitcoin be above $80,000 on March 31, 2025?"
        )
        assert ok
        m = signal._mappings["tok1"]
        assert m.symbol == "BTCUSDT"
        assert m.strike == 80_000
        assert m.is_above is True

    def test_parse_eth_reach(self, signal):
        ok = signal.register_market(
            "tok2", "Will ETH reach $4,000 by December 31, 2025?"
        )
        assert ok
        m = signal._mappings["tok2"]
        assert m.symbol == "ETHUSDT"
        assert m.strike == 4_000
        assert m.is_above is True

    def test_parse_sol_below(self, signal):
        ok = signal.register_market(
            "tok3", "Will Solana drop below $100 by June 1, 2025?"
        )
        assert ok
        m = signal._mappings["tok3"]
        assert m.symbol == "SOLUSDT"
        assert m.strike == 100
        assert m.is_above is False

    def test_parse_k_suffix(self, signal):
        ok = signal.register_market(
            "tok4", "Will Bitcoin be above $100k by April 2025?"
        )
        assert ok
        assert signal._mappings["tok4"].strike == 100_000

    def test_non_crypto_question_skipped(self, signal):
        ok = signal.register_market(
            "tok5", "Will Trump win the 2024 election?"
        )
        assert not ok
        assert "tok5" not in signal._mappings

    def test_unregister(self, signal):
        signal.register_market("tok1", "Will BTC be above $80,000 on March 31?")
        signal.unregister_market("tok1")
        assert "tok1" not in signal._mappings


class TestPriceConversion:
    def test_parse_price_with_commas(self):
        assert CryptoPriceSignal._parse_price("80,000") == 80_000

    def test_parse_price_k(self):
        assert CryptoPriceSignal._parse_price("100k") == 100_000

    def test_parse_price_m(self):
        assert CryptoPriceSignal._parse_price("1.5M") == 1_500_000

    def test_parse_price_simple(self):
        assert CryptoPriceSignal._parse_price("4000") == 4_000


class TestProbabilityModel:
    def test_spot_well_above_strike_high_prob(self, signal):
        mapping = CryptoMarketMapping(
            token_id="t1", symbol="BTCUSDT", strike=80_000,
            expiry=datetime.now(timezone.utc) + timedelta(days=30),
            is_above=True, question="test",
        )
        prob = signal._compute_probability(100_000, mapping)
        assert prob is not None
        assert prob > 0.7  # 25% above strike, should be high prob

    def test_spot_well_below_strike_low_prob(self, signal):
        mapping = CryptoMarketMapping(
            token_id="t1", symbol="BTCUSDT", strike=80_000,
            expiry=datetime.now(timezone.utc) + timedelta(days=30),
            is_above=True, question="test",
        )
        prob = signal._compute_probability(60_000, mapping)
        assert prob is not None
        assert prob < 0.3  # 25% below strike

    def test_spot_at_strike_near_half(self, signal):
        mapping = CryptoMarketMapping(
            token_id="t1", symbol="BTCUSDT", strike=80_000,
            expiry=datetime.now(timezone.utc) + timedelta(days=30),
            is_above=True, question="test",
        )
        prob = signal._compute_probability(80_000, mapping)
        assert prob is not None
        # At the money should be near 0.5 (slightly below due to drift term)
        assert 0.3 < prob < 0.6

    def test_below_market_inverts(self, signal):
        mapping_above = CryptoMarketMapping(
            token_id="t1", symbol="BTCUSDT", strike=80_000,
            expiry=datetime.now(timezone.utc) + timedelta(days=30),
            is_above=True, question="test",
        )
        mapping_below = CryptoMarketMapping(
            token_id="t1", symbol="BTCUSDT", strike=80_000,
            expiry=datetime.now(timezone.utc) + timedelta(days=30),
            is_above=False, question="test",
        )
        p_above = signal._compute_probability(90_000, mapping_above)
        p_below = signal._compute_probability(90_000, mapping_below)
        assert p_above is not None and p_below is not None
        assert abs(p_above + p_below - 1.0) < 0.001

    def test_shorter_expiry_more_extreme(self, signal):
        mapping_long = CryptoMarketMapping(
            token_id="t1", symbol="BTCUSDT", strike=80_000,
            expiry=datetime.now(timezone.utc) + timedelta(days=90),
            is_above=True, question="test",
        )
        mapping_short = CryptoMarketMapping(
            token_id="t1", symbol="BTCUSDT", strike=80_000,
            expiry=datetime.now(timezone.utc) + timedelta(days=3),
            is_above=True, question="test",
        )
        # Spot well above strike: shorter expiry → higher probability
        p_long = signal._compute_probability(95_000, mapping_long)
        p_short = signal._compute_probability(95_000, mapping_short)
        assert p_short > p_long


class TestAdjustment:
    def test_no_mapping_returns_zero(self, signal):
        assert signal.get_adjustment("unknown_token", 0.5) == 0.0

    def test_no_price_returns_zero(self, signal):
        signal.register_market("tok1", "Will BTC be above $80,000 on March 31?")
        # No price seeded
        assert signal.get_adjustment("tok1", 0.5) == 0.0

    def test_returns_nonzero_on_divergence(self, signal):
        signal.register_market(
            "tok1", "Will Bitcoin be above $80,000 on March 31, 2025?",
            expiry=datetime.now(timezone.utc) + timedelta(days=30),
        )
        # Inject price: well above strike
        signal._prices["BTCUSDT"] = 100_000
        # Current FV is 0.50 but model says much higher → positive adjustment
        adj = signal.get_adjustment("tok1", 0.50)
        assert adj > 0

    def test_below_strike_negative_adjustment(self, signal):
        signal.register_market(
            "tok1", "Will Bitcoin be above $80,000 on March 31, 2025?",
            expiry=datetime.now(timezone.utc) + timedelta(days=30),
        )
        signal._prices["BTCUSDT"] = 60_000
        # Current FV is 0.50 but model says much lower → negative adjustment
        adj = signal.get_adjustment("tok1", 0.50)
        assert adj < 0


class TestNormCdf:
    def test_zero(self):
        assert abs(_norm_cdf(0) - 0.5) < 0.001

    def test_large_positive(self):
        assert _norm_cdf(5.0) > 0.999

    def test_large_negative(self):
        assert _norm_cdf(-5.0) < 0.001
