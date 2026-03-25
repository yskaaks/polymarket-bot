"""Tests for BeckerParquetLoader."""
import pytest
import shutil
from tests.backtesting.fixtures.sample_data import create_becker_fixture_dir


@pytest.fixture
def becker_data_dir():
    d = create_becker_fixture_dir()
    yield d
    shutil.rmtree(d)


def test_load_markets(becker_data_dir):
    from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
    loader = BeckerParquetLoader(becker_data_dir)
    markets = loader.load_markets()
    assert len(markets) == 2
    btc_market = next(m for m in markets if "BTC" in m.question)
    assert btc_market.market_id == "cond_001"
    assert btc_market.outcomes == ["Yes", "No"]
    assert btc_market.token_ids == ["tok_yes_001", "tok_no_001"]
    assert btc_market.source == "polymarket"


def test_load_markets_with_filter(becker_data_dir):
    from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
    from src.layer1_research.backtesting.data.models import MarketFilter
    loader = BeckerParquetLoader(becker_data_dir)
    markets = loader.load_markets(filters=MarketFilter(min_volume=200_000))
    assert len(markets) == 1
    assert markets[0].market_id == "cond_001"


def test_get_trades(becker_data_dir):
    from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
    loader = BeckerParquetLoader(becker_data_dir)
    trades = list(loader.get_trades("tok_yes_001"))
    assert len(trades) == 2
    assert trades[0].side == "BUY"
    assert trades[0].price == pytest.approx(0.65)
    assert trades[0].size == pytest.approx(1.0)
    assert trades[0].source == "polymarket"
    assert trades[0].maker == "0xmaker1"
    assert trades[1].side == "SELL"


def test_get_trades_no_token(becker_data_dir):
    from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
    loader = BeckerParquetLoader(becker_data_dir)
    trades = list(loader.get_trades("nonexistent_token"))
    assert trades == []


def test_invalid_data_dir():
    from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
    with pytest.raises(FileNotFoundError):
        BeckerParquetLoader("/nonexistent/path")
