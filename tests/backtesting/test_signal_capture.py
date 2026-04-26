"""Tests that strategies capture SignalSnapshot on every emitted signal."""
import pytest
from datetime import datetime, timezone


def test_signal_log_initialized_empty():
    from src.layer1_research.backtesting.strategies.base import (
        PredictionMarketStrategy, PredictionMarketStrategyConfig,
    )
    cfg = PredictionMarketStrategyConfig(instrument_ids=[])
    strat = PredictionMarketStrategy(config=cfg)
    assert strat._signal_log == []


def test_signal_log_appends_on_act(monkeypatch):
    """Calling _act_on_signal appends a SignalSnapshot before order submission."""
    from src.layer1_research.backtesting.strategies.base import (
        PredictionMarketStrategy, PredictionMarketStrategyConfig,
    )
    from src.layer1_research.backtesting.strategies.signal import Signal

    cfg = PredictionMarketStrategyConfig(instrument_ids=[])
    strat = PredictionMarketStrategy(config=cfg)

    # Stub out submit_order and order_factory so we don't hit Nautilus.
    # Nautilus Strategy is a Cython extension type; instance-level attribute
    # assignment is not allowed, so we patch at the class level via monkeypatch
    # (which will restore the originals after the test).
    submitted = []

    class _FakeOrder:
        client_order_id = type("C", (), {"value": "O-1"})()

    class _FakeFactory:
        def market(self, **kw): return _FakeOrder()

    monkeypatch.setattr(PredictionMarketStrategy, "order_factory", _FakeFactory())
    monkeypatch.setattr(PredictionMarketStrategy, "submit_order", lambda self, o: submitted.append(o))

    # Stub account lookup to return a deterministic balance.
    class _FakeAccount:
        def balance_total(self, currency): return 10_000.0

    class _FakePortfolio:
        def account(self, venue): return _FakeAccount()

    monkeypatch.setattr(PredictionMarketStrategy, "portfolio", _FakePortfolio())

    # Fake instrument with a make_qty that passes size through.
    class _FakeInstrument:
        class id:
            class venue: pass
        quote_currency = None
        def make_qty(self, s): return s

    instr = _FakeInstrument()

    class _FakeTick:
        ts_event = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1e9)
        price = 0.40

    sig = Signal(direction="BUY", confidence=0.70, target_price=0.55, size=50.0)
    strat._act_on_signal(sig, instr, _FakeTick())

    assert len(strat._signal_log) == 1
    snap = strat._signal_log[0]
    assert snap.direction == "BUY"
    assert snap.confidence == pytest.approx(0.70)
    assert snap.market_price == pytest.approx(0.40)
    assert snap.client_order_id == "O-1"
    assert len(submitted) == 1


def test_act_on_signal_raises_when_account_missing(monkeypatch):
    """No silent capital=10_000 fallback: missing account should raise."""
    from src.layer1_research.backtesting.strategies.base import (
        PredictionMarketStrategy, PredictionMarketStrategyConfig,
    )
    from src.layer1_research.backtesting.strategies.signal import Signal

    cfg = PredictionMarketStrategyConfig(instrument_ids=[])
    strat = PredictionMarketStrategy(config=cfg)

    # Portfolio that raises on .account(...)
    class _BrokenPortfolio:
        def account(self, venue): raise RuntimeError("no venue account")

    monkeypatch.setattr(PredictionMarketStrategy, "portfolio", _BrokenPortfolio())

    class _FakeInstrument:
        class id:
            class venue: pass
        quote_currency = None

    class _FakeTick:
        ts_event = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1e9)
        price = 0.40

    # Signal that requires sizer (size=None)
    sig = Signal(direction="BUY", confidence=0.70, target_price=0.55)
    with pytest.raises(RuntimeError):
        strat._act_on_signal(sig, _FakeInstrument(), _FakeTick())


def test_sizer_branch_uses_quote_currency(monkeypatch):
    """When signal.size is None, the sizer must read balance_total(instrument.quote_currency).

    Regression: the old code read `instrument.currency` which doesn't exist on
    Nautilus BinaryOption. That AttributeError was hidden by a silent fallback
    (removed in Task 6) — this test keeps it from coming back.
    """
    from src.layer1_research.backtesting.strategies.base import (
        PredictionMarketStrategy, PredictionMarketStrategyConfig,
    )
    from src.layer1_research.backtesting.strategies.signal import Signal

    cfg = PredictionMarketStrategyConfig(instrument_ids=[])
    strat = PredictionMarketStrategy(config=cfg)

    submitted = []
    class _FakeOrder:
        client_order_id = type("C", (), {"value": "O-sizer"})()
    class _FakeFactory:
        def market(self, **kw): return _FakeOrder()
    monkeypatch.setattr(PredictionMarketStrategy, "order_factory", _FakeFactory())
    monkeypatch.setattr(PredictionMarketStrategy, "submit_order", lambda self, o: submitted.append(o))

    balance_calls = []
    class _FakeAccount:
        def balance_total(self, currency):
            balance_calls.append(currency)
            return 10_000.0
    class _FakePortfolio:
        def account(self, venue): return _FakeAccount()
    monkeypatch.setattr(PredictionMarketStrategy, "portfolio", _FakePortfolio())

    _SENTINEL = object()
    class _FakeInstrument:
        class id:
            class venue: pass
        quote_currency = _SENTINEL
        # intentionally do NOT define `currency` — catches regression that reads it
        def make_qty(self, s): return s
    instr = _FakeInstrument()

    class _FakeTick:
        ts_event = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1e9)
        price = 0.40

    # Signal with size=None → sizer branch must execute
    sig = Signal(direction="BUY", confidence=0.70, target_price=0.55)
    strat._act_on_signal(sig, instr, _FakeTick())

    # Sizer was invoked → balance_total was called with quote_currency
    assert balance_calls == [_SENTINEL], (
        f"balance_total should be called with instrument.quote_currency; "
        f"got {balance_calls}"
    )
    # And an order was submitted
    assert len(submitted) == 1
