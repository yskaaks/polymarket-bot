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


class TestTagBasedIdentification:
    def test_crypto_prices_tag_with_bitcoin(self, signal):
        ok = signal.register_market(
            "tok1",
            "Will Bitcoin be above $80,000 on March 31, 2025?",
            tags=["crypto", "crypto-prices", "bitcoin"],
        )
        assert ok
        m = signal._mappings["tok1"]
        assert m.symbol == "BTCUSDT"
        assert m.strike == 80_000
        assert m.is_above is True
        assert m.source == "tags"

    def test_crypto_prices_tag_with_solana(self, signal):
        ok = signal.register_market(
            "tok2",
            "Will Solana be above $200 by June 2025?",
            tags=["crypto-prices", "solana"],
        )
        assert ok
        m = signal._mappings["tok2"]
        assert m.symbol == "SOLUSDT"
        assert m.strike == 200
        assert m.source == "tags"

    def test_crypto_tag_with_price_in_question(self, signal):
        ok = signal.register_market(
            "tok3",
            "Will ETH reach $4,000 by December?",
            tags=["crypto", "ethereum"],
        )
        assert ok
        m = signal._mappings["tok3"]
        assert m.symbol == "ETHUSDT"
        assert m.source == "tags"

    def test_crypto_tag_without_price_skipped(self, signal):
        ok = signal.register_market(
            "tok4",
            "Will crypto regulation pass in 2025?",
            tags=["crypto"],
        )
        assert not ok

    def test_no_tags_falls_back_to_question(self, signal):
        ok = signal.register_market(
            "tok5",
            "Will Bitcoin be above $80,000 on March 31, 2025?",
            tags=[],
        )
        assert ok
        assert signal._mappings["tok5"].source == "question_parse"

    def test_non_crypto_tags_skipped(self, signal):
        ok = signal.register_market(
            "tok6",
            "Will Trump win the election?",
            tags=["politics", "elections"],
        )
        assert not ok


class TestQuestionParseFallback:
    def test_parse_btc_above(self, signal):
        ok = signal.register_market(
            "tok1", "Will Bitcoin be above $80,000 on March 31, 2025?"
        )
        assert ok
        m = signal._mappings["tok1"]
        assert m.symbol == "BTCUSDT"
        assert m.strike == 80_000
        assert m.is_above is True

    def test_parse_below(self, signal):
        ok = signal.register_market(
            "tok3", "Will Solana drop below $100 by June 1, 2025?"
        )
        assert ok
        m = signal._mappings["tok3"]
        assert m.symbol == "SOLUSDT"
        assert m.strike == 100
        assert m.is_above is False

    def test_non_crypto_question_skipped(self, signal):
        ok = signal.register_market(
            "tok5", "Will Trump win the 2024 election?"
        )
        assert not ok

    def test_unregister(self, signal):
        signal.register_market("tok1", "Will BTC be above $80,000 on March 31?")
        signal.unregister_market("tok1")
        assert "tok1" not in signal._mappings


class TestStrikeExtraction:
    def test_comma_separated(self, signal):
        assert CryptoPriceSignal._extract_strike("above $80,000") == 80_000

    def test_k_suffix(self, signal):
        assert CryptoPriceSignal._extract_strike("above $100k") == 100_000

    def test_m_suffix(self, signal):
        assert CryptoPriceSignal._extract_strike("above $1.5M") == 1_500_000

    def test_simple(self, signal):
        assert CryptoPriceSignal._extract_strike("above $4000") == 4_000

    def test_no_price(self, signal):
        assert CryptoPriceSignal._extract_strike("no price here") is None


class TestProbabilityModel:
    def test_spot_well_above_strike_high_prob(self, signal):
        mapping = CryptoMarketMapping(
            token_id="t1", symbol="BTCUSDT", strike=80_000,
            expiry=datetime.now(timezone.utc) + timedelta(days=30),
            is_above=True, source="tags", question="test",
        )
        prob = signal._compute_probability(100_000, mapping)
        assert prob is not None
        assert prob > 0.7

    def test_spot_well_below_strike_low_prob(self, signal):
        mapping = CryptoMarketMapping(
            token_id="t1", symbol="BTCUSDT", strike=80_000,
            expiry=datetime.now(timezone.utc) + timedelta(days=30),
            is_above=True, source="tags", question="test",
        )
        prob = signal._compute_probability(60_000, mapping)
        assert prob is not None
        assert prob < 0.3

    def test_spot_at_strike_near_half(self, signal):
        mapping = CryptoMarketMapping(
            token_id="t1", symbol="BTCUSDT", strike=80_000,
            expiry=datetime.now(timezone.utc) + timedelta(days=30),
            is_above=True, source="tags", question="test",
        )
        prob = signal._compute_probability(80_000, mapping)
        assert prob is not None
        assert 0.3 < prob < 0.6

    def test_below_market_inverts(self, signal):
        mapping_above = CryptoMarketMapping(
            token_id="t1", symbol="BTCUSDT", strike=80_000,
            expiry=datetime.now(timezone.utc) + timedelta(days=30),
            is_above=True, source="tags", question="test",
        )
        mapping_below = CryptoMarketMapping(
            token_id="t1", symbol="BTCUSDT", strike=80_000,
            expiry=datetime.now(timezone.utc) + timedelta(days=30),
            is_above=False, source="tags", question="test",
        )
        p_above = signal._compute_probability(90_000, mapping_above)
        p_below = signal._compute_probability(90_000, mapping_below)
        assert abs(p_above + p_below - 1.0) < 0.001

    def test_shorter_expiry_more_extreme(self, signal):
        mapping_long = CryptoMarketMapping(
            token_id="t1", symbol="BTCUSDT", strike=80_000,
            expiry=datetime.now(timezone.utc) + timedelta(days=90),
            is_above=True, source="tags", question="test",
        )
        mapping_short = CryptoMarketMapping(
            token_id="t1", symbol="BTCUSDT", strike=80_000,
            expiry=datetime.now(timezone.utc) + timedelta(days=3),
            is_above=True, source="tags", question="test",
        )
        p_long = signal._compute_probability(95_000, mapping_long)
        p_short = signal._compute_probability(95_000, mapping_short)
        assert p_short > p_long


class TestAdjustment:
    def test_no_mapping_returns_zero(self, signal):
        assert signal.get_adjustment("unknown_token", 0.5) == 0.0

    def test_no_price_returns_zero(self, signal):
        signal.register_market(
            "tok1", "Will BTC be above $80,000 on March 31?",
            tags=["crypto-prices", "bitcoin"],
        )
        assert signal.get_adjustment("tok1", 0.5) == 0.0

    def test_returns_nonzero_on_divergence(self, signal):
        signal.register_market(
            "tok1", "Will Bitcoin be above $80,000 on March 31, 2025?",
            tags=["crypto-prices", "bitcoin"],
            expiry=datetime.now(timezone.utc) + timedelta(days=30),
        )
        signal._prices["BTCUSDT"] = 100_000
        adj = signal.get_adjustment("tok1", 0.50)
        assert adj > 0

    def test_below_strike_negative_adjustment(self, signal):
        signal.register_market(
            "tok1", "Will Bitcoin be above $80,000 on March 31, 2025?",
            tags=["crypto-prices", "bitcoin"],
            expiry=datetime.now(timezone.utc) + timedelta(days=30),
        )
        signal._prices["BTCUSDT"] = 60_000
        adj = signal.get_adjustment("tok1", 0.50)
        assert adj < 0


class TestNormCdf:
    def test_zero(self):
        assert abs(_norm_cdf(0) - 0.5) < 0.001

    def test_large_positive(self):
        assert _norm_cdf(5.0) > 0.999

    def test_large_negative(self):
        assert _norm_cdf(-5.0) < 0.001
