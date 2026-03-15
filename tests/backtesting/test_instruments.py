"""Tests for BinaryOption instrument factory."""
import pytest
from datetime import datetime, timezone


def test_create_instruments_from_market():
    from src.layer1_research.backtesting.data.instruments import create_instruments
    from src.layer1_research.backtesting.data.models import MarketInfo

    market = MarketInfo(
        market_id="cond_001", question="Will BTC hit 100k?",
        outcomes=["Yes", "No"], token_ids=["tok_yes_001", "tok_no_001"],
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        source="polymarket",
    )
    instruments = create_instruments(market)
    assert len(instruments) == 2
    for inst in instruments:
        assert inst.price_precision == 2


def test_create_instruments_maps_token_ids():
    from src.layer1_research.backtesting.data.instruments import create_instruments
    from src.layer1_research.backtesting.data.models import MarketInfo

    market = MarketInfo(
        market_id="cond_001", question="Will BTC hit 100k?",
        outcomes=["Yes", "No"], token_ids=["tok_yes_001", "tok_no_001"],
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        source="polymarket",
    )
    instruments = create_instruments(market)
    instrument_ids = [str(inst.id) for inst in instruments]
    assert any("tok_yes_001" in iid for iid in instrument_ids)
    assert any("tok_no_001" in iid for iid in instrument_ids)


def test_get_token_pair():
    from src.layer1_research.backtesting.data.instruments import create_instruments, get_paired_token_id
    from src.layer1_research.backtesting.data.models import MarketInfo

    market = MarketInfo(
        market_id="cond_001", question="Test?",
        outcomes=["Yes", "No"], token_ids=["tok_yes", "tok_no"],
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        source="polymarket",
    )
    create_instruments(market)
    assert get_paired_token_id("tok_yes") == "tok_no"
    assert get_paired_token_id("tok_no") == "tok_yes"
