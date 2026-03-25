"""Shared fixtures for backtesting tests."""
import pytest


@pytest.fixture(autouse=True)
def clear_instrument_pairs():
    """Clear the instrument pair registry between tests to prevent state leakage."""
    yield
    try:
        from src.layer1_research.backtesting.data.instruments import clear_pairs
        clear_pairs()
    except ImportError:
        pass
