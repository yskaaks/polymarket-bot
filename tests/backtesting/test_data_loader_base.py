"""Tests for DataLoader ABC."""
import pytest
from datetime import datetime, timezone


def test_data_loader_is_abstract():
    from src.layer1_research.backtesting.data.loaders.base import DataLoader

    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        DataLoader()


def test_data_loader_concrete_implementation():
    from src.layer1_research.backtesting.data.loaders.base import DataLoader
    from src.layer1_research.backtesting.data.models import MarketInfo, RawTrade, MarketFilter

    class FakeLoader(DataLoader):
        def load_markets(self, filters=None):
            return []

        def get_trades(self, token_id, start=None, end=None):
            yield from []

    loader = FakeLoader()
    assert loader.load_markets() == []
    assert list(loader.get_trades("abc")) == []
