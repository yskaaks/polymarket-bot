"""Tests for Kalshi cross-exchange signal provider."""

import time
from unittest.mock import patch, MagicMock

import pytest

from src.layer2_signals.kalshi_signal import KalshiSignal, KalshiMarketMatch


@pytest.fixture
def signal():
    """Create a KalshiSignal without connecting to API."""
    s = KalshiSignal()
    return s


class TestKeywordExtraction:
    def test_removes_stop_words(self):
        kw = KalshiSignal._extract_keywords("Will the price be above 100")
        assert "will" not in kw
        assert "the" not in kw
        assert "be" not in kw
        assert "100" in kw
        assert "price" in kw

    def test_removes_punctuation(self):
        kw = KalshiSignal._extract_keywords("Bitcoin's price? Yes!")
        assert "bitcoin" in kw or "bitcoins" in kw
        assert "price" in kw

    def test_removes_short_words(self):
        kw = KalshiSignal._extract_keywords("a b c hello world")
        assert "hello" in kw
        assert "world" in kw
        assert "a" not in kw


class TestMatching:
    def test_find_match_high_similarity(self, signal):
        signal._kalshi_cache = [
            {"ticker": "BTC-80K-MAR", "title": "Bitcoin above $80,000 March 2025", "subtitle": ""},
            {"ticker": "RAIN-NYC", "title": "Rain in New York City", "subtitle": ""},
        ]
        signal._cache_time = time.time()

        match = signal._find_match("Will Bitcoin be above $80,000 on March 31, 2025?")
        assert match is not None
        assert match["ticker"] == "BTC-80K-MAR"

    def test_no_match_low_similarity(self, signal):
        signal._kalshi_cache = [
            {"ticker": "RAIN-NYC", "title": "Rain in New York City", "subtitle": ""},
        ]
        signal._cache_time = time.time()

        match = signal._find_match("Will Bitcoin hit $100,000?")
        assert match is None

    def test_empty_cache(self, signal):
        signal._kalshi_cache = []
        signal._cache_time = time.time()
        match = signal._find_match("test question")
        assert match is None


class TestAdjustment:
    def test_no_match_returns_zero(self, signal):
        assert signal.get_adjustment("unknown", 0.5) == 0.0

    def test_returns_adjustment_on_divergence(self, signal):
        match = KalshiMarketMatch(
            polymarket_token_id="tok1",
            kalshi_ticker="TEST",
            kalshi_title="Test Market",
            polymarket_question="Test",
            last_kalshi_yes_bid=0.70,
            last_kalshi_yes_ask=0.75,
            last_kalshi_mid=0.725,
            last_updated=time.time(),
        )
        signal._matches["tok1"] = match

        # Polymarket FV at 0.50, Kalshi at 0.725 → positive adjustment
        adj = signal.get_adjustment("tok1", 0.50)
        assert adj > 0

    def test_small_divergence_returns_zero(self, signal):
        match = KalshiMarketMatch(
            polymarket_token_id="tok1",
            kalshi_ticker="TEST",
            kalshi_title="Test Market",
            polymarket_question="Test",
            last_kalshi_yes_bid=0.49,
            last_kalshi_yes_ask=0.51,
            last_kalshi_mid=0.50,
            last_updated=time.time(),
        )
        signal._matches["tok1"] = match

        # FV and Kalshi both ~0.50 → no adjustment
        adj = signal.get_adjustment("tok1", 0.50)
        assert adj == 0.0

    def test_extreme_kalshi_price_returns_zero(self, signal):
        match = KalshiMarketMatch(
            polymarket_token_id="tok1",
            kalshi_ticker="TEST",
            kalshi_title="Test Market",
            polymarket_question="Test",
            last_kalshi_mid=0.995,  # too extreme
            last_updated=time.time(),
        )
        signal._matches["tok1"] = match
        assert signal.get_adjustment("tok1", 0.50) == 0.0


class TestRegisterUnregister:
    def test_register_with_mock_api(self, signal):
        signal._kalshi_cache = [
            {"ticker": "BTC-80K", "title": "Bitcoin above $80,000", "subtitle": ""},
        ]
        signal._cache_time = time.time()

        with patch.object(signal, "_get_kalshi_price", return_value=(0.60, 0.65)):
            ok = signal.register_market("tok1", "Will Bitcoin be above $80,000?")

        assert ok
        assert "tok1" in signal._matches
        assert signal._matches["tok1"].last_kalshi_mid == 0.625

    def test_unregister(self, signal):
        signal._matches["tok1"] = KalshiMarketMatch(
            polymarket_token_id="tok1",
            kalshi_ticker="TEST",
            kalshi_title="Test",
            polymarket_question="Test",
        )
        signal.unregister_market("tok1")
        assert "tok1" not in signal._matches

    def test_num_matches(self, signal):
        assert signal.num_matches == 0
        signal._matches["tok1"] = KalshiMarketMatch(
            polymarket_token_id="tok1",
            kalshi_ticker="TEST",
            kalshi_title="Test",
            polymarket_question="Test",
        )
        assert signal.num_matches == 1
