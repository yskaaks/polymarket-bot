"""Tests for catalog builder."""
import pytest
import shutil
import tempfile
from pathlib import Path
from tests.backtesting.fixtures.sample_data import create_becker_fixture_dir


@pytest.fixture
def becker_data_dir():
    d = create_becker_fixture_dir()
    yield d
    shutil.rmtree(d)


@pytest.fixture
def catalog_dir():
    d = tempfile.mkdtemp(prefix="catalog_test_")
    yield d
    shutil.rmtree(d)


def test_build_catalog_creates_output(becker_data_dir, catalog_dir):
    from src.layer1_research.backtesting.data.catalog import build_catalog
    from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader

    loader = BeckerParquetLoader(becker_data_dir)
    result = build_catalog(loader, catalog_dir)
    assert result.markets_loaded > 0
    assert result.trades_loaded > 0
    assert result.instruments_created > 0
    assert any(Path(catalog_dir).iterdir())


def test_build_catalog_with_market_filter(becker_data_dir, catalog_dir):
    from src.layer1_research.backtesting.data.catalog import build_catalog
    from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
    from src.layer1_research.backtesting.data.models import MarketFilter

    loader = BeckerParquetLoader(becker_data_dir)
    result = build_catalog(loader, catalog_dir, filters=MarketFilter(min_volume=200_000))
    assert result.markets_loaded == 1
