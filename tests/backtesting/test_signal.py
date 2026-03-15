"""Tests for Signal dataclass."""
import pytest


def test_signal_creation():
    from src.layer1_research.backtesting.strategies.signal import Signal
    s = Signal(direction="BUY", confidence=0.75, target_price=0.60)
    assert s.direction == "BUY"
    assert s.confidence == 0.75
    assert s.size is None
    assert s.metadata is None


def test_signal_with_size():
    from src.layer1_research.backtesting.strategies.signal import Signal
    s = Signal(direction="SELL", confidence=0.80, target_price=0.40, size=100.0)
    assert s.size == 100.0


def test_signal_rejects_invalid_direction():
    from src.layer1_research.backtesting.strategies.signal import Signal
    with pytest.raises(ValueError, match="direction must be"):
        Signal(direction="HOLD", confidence=0.5, target_price=0.5)


def test_signal_rejects_invalid_confidence():
    from src.layer1_research.backtesting.strategies.signal import Signal
    with pytest.raises(ValueError, match="confidence must be"):
        Signal(direction="BUY", confidence=1.5, target_price=0.5)
