"""Tests for BacktestResult and its components."""
import pytest
from datetime import datetime, timezone


def test_signal_snapshot_buy_edge():
    from src.layer1_research.backtesting.results import SignalSnapshot
    snap = SignalSnapshot(
        ts=datetime(2024, 6, 1, tzinfo=timezone.utc),
        instrument_id="tok_yes_001.POLYMARKET",
        direction="BUY",
        market_price=0.40,
        confidence=0.70,
        target_price=0.55,
        size=100.0,
        client_order_id="O-1",
    )
    # BUY: edge = confidence - market_price = 0.70 - 0.40 = 0.30
    assert snap.edge_at_order == pytest.approx(0.30)


def test_signal_snapshot_sell_edge():
    from src.layer1_research.backtesting.results import SignalSnapshot
    snap = SignalSnapshot(
        ts=datetime(2024, 6, 1, tzinfo=timezone.utc),
        instrument_id="tok_yes_001.POLYMARKET",
        direction="SELL",
        market_price=0.70,
        confidence=0.65,  # 65% sure YES is overpriced, i.e. P(YES)=0.35
        target_price=0.55,
        size=100.0,
        client_order_id="O-2",
    )
    # SELL: edge = market_price - (1 - confidence) = 0.70 - 0.35 = 0.35
    assert snap.edge_at_order == pytest.approx(0.35)


def test_signal_snapshot_flat_edge():
    from src.layer1_research.backtesting.results import SignalSnapshot
    snap = SignalSnapshot(
        ts=datetime(2024, 6, 1, tzinfo=timezone.utc),
        instrument_id="tok.POLYMARKET",
        direction="FLAT",
        market_price=0.50, confidence=0.50, target_price=0.50, size=0.0,
        client_order_id=None,
    )
    assert snap.edge_at_order == 0.0


def test_signal_snapshot_rejects_bad_direction():
    from src.layer1_research.backtesting.results import SignalSnapshot
    with pytest.raises(ValueError, match="direction"):
        SignalSnapshot(
            ts=datetime(2024, 6, 1, tzinfo=timezone.utc),
            instrument_id="tok.POLYMARKET",
            direction="HOLD",
            market_price=0.50, confidence=0.50, target_price=0.50, size=0.0,
            client_order_id=None,
        )


def test_trade_long_realized_edge():
    from src.layer1_research.backtesting.results import Trade
    t = Trade(
        instrument_id="tok.POLYMARKET",
        direction="LONG",
        entry_ts=datetime(2024, 6, 1, tzinfo=timezone.utc),
        exit_ts=datetime(2024, 6, 2, tzinfo=timezone.utc),
        entry_price=0.40,
        exit_price=0.55,
        size=100.0,
        fees=1.50,
        gross_pnl=15.0,
        net_pnl=13.5,
        edge_at_entry=0.30,
        slippage_bps=0.0,
        signal_confidence=0.70,
    )
    # LONG realized_edge = exit - entry = 0.55 - 0.40 = 0.15
    assert t.realized_edge == pytest.approx(0.15)


def test_trade_short_realized_edge():
    from src.layer1_research.backtesting.results import Trade
    t = Trade(
        instrument_id="tok.POLYMARKET",
        direction="SHORT",
        entry_ts=datetime(2024, 6, 1, tzinfo=timezone.utc),
        exit_ts=datetime(2024, 6, 2, tzinfo=timezone.utc),
        entry_price=0.70,
        exit_price=0.55,
        size=100.0,
        fees=1.50,
        gross_pnl=15.0,
        net_pnl=13.5,
        edge_at_entry=0.35,
        slippage_bps=0.0,
        signal_confidence=0.65,
    )
    # SHORT realized_edge = entry - exit = 0.70 - 0.55 = 0.15
    assert t.realized_edge == pytest.approx(0.15)


def test_trade_open_position_realized_edge_none():
    from src.layer1_research.backtesting.results import Trade
    t = Trade(
        instrument_id="tok.POLYMARKET", direction="LONG",
        entry_ts=datetime(2024, 6, 1, tzinfo=timezone.utc),
        exit_ts=None,
        entry_price=0.40, exit_price=None, size=100.0,
        fees=1.50, gross_pnl=0.0, net_pnl=-1.5,
        edge_at_entry=0.30, slippage_bps=0.0, signal_confidence=0.70,
    )
    assert t.realized_edge is None
