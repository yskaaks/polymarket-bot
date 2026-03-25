"""Tests for backtesting data models."""
import pytest
from datetime import datetime, timezone


def test_raw_trade_creation():
    from src.layer1_research.backtesting.data.models import RawTrade

    trade = RawTrade(
        timestamp=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        market_id="condition_abc123",
        token_id="token_yes_123",
        side="BUY",
        price=0.65,
        size=100.0,
        source="polymarket",
    )
    assert trade.price == 0.65
    assert trade.side == "BUY"
    assert trade.source == "polymarket"
    assert trade.maker is None
    assert trade.taker is None


def test_raw_trade_rejects_invalid_side():
    from src.layer1_research.backtesting.data.models import RawTrade

    with pytest.raises(ValueError, match="side must be BUY or SELL"):
        RawTrade(
            timestamp=datetime(2024, 6, 15, tzinfo=timezone.utc),
            market_id="abc",
            token_id="tok",
            side="HOLD",
            price=0.5,
            size=10.0,
            source="polymarket",
        )


def test_raw_trade_rejects_invalid_price():
    from src.layer1_research.backtesting.data.models import RawTrade

    with pytest.raises(ValueError, match="price must be between 0"):
        RawTrade(
            timestamp=datetime(2024, 6, 15, tzinfo=timezone.utc),
            market_id="abc",
            token_id="tok",
            side="BUY",
            price=1.5,
            size=10.0,
            source="polymarket",
        )


def test_raw_trade_allows_price_at_one():
    from src.layer1_research.backtesting.data.models import RawTrade

    trade = RawTrade(
        timestamp=datetime(2024, 6, 15, tzinfo=timezone.utc),
        market_id="abc",
        token_id="tok",
        side="BUY",
        price=1.0,
        size=10.0,
        source="polymarket",
    )
    assert trade.price == 1.0


def test_raw_trade_rejects_zero_size():
    from src.layer1_research.backtesting.data.models import RawTrade

    with pytest.raises(ValueError, match="size must be positive"):
        RawTrade(
            timestamp=datetime(2024, 6, 15, tzinfo=timezone.utc),
            market_id="abc",
            token_id="tok",
            side="BUY",
            price=0.5,
            size=0.0,
            source="polymarket",
        )


def test_market_info_creation():
    from src.layer1_research.backtesting.data.models import MarketInfo

    market = MarketInfo(
        market_id="condition_abc123",
        question="Will BTC hit $100k by Dec 2024?",
        outcomes=["Yes", "No"],
        token_ids=["token_yes_123", "token_no_456"],
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        source="polymarket",
        result=None,
    )
    assert market.question == "Will BTC hit $100k by Dec 2024?"
    assert len(market.token_ids) == 2
    assert market.result is None


def test_market_info_with_result():
    from src.layer1_research.backtesting.data.models import MarketInfo

    market = MarketInfo(
        market_id="condition_abc123",
        question="Will BTC hit $100k?",
        outcomes=["Yes", "No"],
        token_ids=["tok_y", "tok_n"],
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        source="polymarket",
        result="Yes",
    )
    assert market.result == "Yes"


def test_market_filter_defaults():
    from src.layer1_research.backtesting.data.models import MarketFilter

    f = MarketFilter()
    assert f.min_volume is None
    assert f.min_trades is None
    assert f.resolved_only is False
    assert f.sources is None
