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


def test_becker_loader_raises_on_out_of_range_price(tmp_path):
    """Out-of-range raw prices must raise, not be silently clamped."""
    import os
    import duckdb

    d = str(tmp_path)
    os.makedirs(f"{d}/polymarket/markets", exist_ok=True)
    os.makedirs(f"{d}/polymarket/trades", exist_ok=True)
    os.makedirs(f"{d}/polymarket/blocks", exist_ok=True)

    con = duckdb.connect()
    con.execute(f"""
        COPY (
            SELECT 'cond_bad' as condition_id, 'q' as question, '["Yes","No"]' as outcomes,
                   '["tok_bad","tok_bad_no"]' as clob_token_ids, 1000.0 as volume,
                   1 as active, 0 as closed, '2024-12-31T00:00:00Z' as end_date,
                   '2024-01-01T00:00:00Z' as created_at
        ) TO '{d}/polymarket/markets/markets.parquet' (FORMAT PARQUET)
    """)
    # Create a trade that produces price > 1.0: maker=2_000_000, taker=1_000_000
    # maker_asset_id='0' means side='BUY', price = maker_amount/taker_amount = 2.0
    con.execute(f"""
        COPY (
            SELECT 50000000 as block_number, 'tx' as transaction_hash, 0 as log_index,
                   'ord' as order_hash, '0xm' as maker, '0xt' as taker,
                   '0' as maker_asset_id, 'tok_bad' as taker_asset_id,
                   2000000 as maker_amount, 1000000 as taker_amount, 0 as fee
        ) TO '{d}/polymarket/trades/trades.parquet' (FORMAT PARQUET)
    """)
    con.execute(f"""
        COPY (
            SELECT 50000000 as block_number, 1718448000 as timestamp
        ) TO '{d}/polymarket/blocks/blocks.parquet' (FORMAT PARQUET)
    """)
    con.close()

    from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
    loader = BeckerParquetLoader(d)
    with pytest.raises(ValueError, match="price must be between"):
        list(loader.get_trades("tok_bad"))
