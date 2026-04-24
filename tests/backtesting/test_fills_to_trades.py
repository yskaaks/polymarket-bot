"""Unit tests for BacktestRunner._fills_to_trades, especially partial-reduction logic."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from src.layer1_research.backtesting.runner import BacktestRunner
from src.layer1_research.backtesting.config import BacktestConfig


def _make_runner() -> BacktestRunner:
    config = BacktestConfig(
        catalog_path="data/catalog",
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 6, 30, tzinfo=timezone.utc),
        strategy_name="test",
        starting_capital=10_000.0,
        data_mode="trade",
    )
    return BacktestRunner(config)


def _ts(hour: int) -> int:
    """Return a nanosecond timestamp for 2024-06-01 at the given hour."""
    dt = datetime(2024, 6, 1, hour, 0, 0, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1e9)


def _make_fills(rows: list[dict]) -> pd.DataFrame:
    """Build a fills DataFrame compatible with _fills_to_trades."""
    return pd.DataFrame(rows)


def test_partial_reduction_emits_two_trades():
    """LONG 10 -> SELL 3 -> SELL 7 must emit two Trade rows, not one.

    Fill sequence:
      t0: BUY  10 @ 0.40   (open LONG 10)
      t1: SELL  3 @ 0.50   (partial close, LONG 7 remains)
      t2: SELL  7 @ 0.60   (full close, position = 0)

    Expected trades:
      trade[0]: LONG, size=3, entry=0.40, exit=0.50
      trade[1]: LONG, size=7, entry=0.40, exit=0.60
    """
    fills = _make_fills([
        {
            "instrument_id": "mkt_A",
            "ts_event": _ts(1),
            "order_side": "BUY",
            "last_qty": 10.0,
            "last_px": 0.40,
            "commission": 0.0,
            "client_order_id": "oid-1",
        },
        {
            "instrument_id": "mkt_A",
            "ts_event": _ts(2),
            "order_side": "SELL",
            "last_qty": 3.0,
            "last_px": 0.50,
            "commission": 0.0,
            "client_order_id": "oid-2",
        },
        {
            "instrument_id": "mkt_A",
            "ts_event": _ts(3),
            "order_side": "SELL",
            "last_qty": 7.0,
            "last_px": 0.60,
            "commission": 0.0,
            "client_order_id": "oid-3",
        },
    ])

    runner = _make_runner()
    trades = runner._fills_to_trades(fills, signals_df=pd.DataFrame())

    assert len(trades) == 2, f"expected 2 trades, got {len(trades)}: {trades}"

    # Both trades are LONG direction
    assert list(trades["direction"]) == ["LONG", "LONG"]

    # Trade 0: partial reduction, size=3, exit at 0.50
    t0 = trades.iloc[0]
    assert t0["size"] == pytest.approx(3.0)
    assert t0["entry_price"] == pytest.approx(0.40)
    assert t0["exit_price"] == pytest.approx(0.50)
    expected_gross_0 = (0.50 - 0.40) * 3.0  # = 0.30
    assert t0["gross_pnl"] == pytest.approx(expected_gross_0)
    assert t0["net_pnl"] == pytest.approx(expected_gross_0 - t0["fees"])

    # Trade 1: remaining 7 lots closed, exit at 0.60
    t1 = trades.iloc[1]
    assert t1["size"] == pytest.approx(7.0)
    assert t1["entry_price"] == pytest.approx(0.40)
    assert t1["exit_price"] == pytest.approx(0.60)
    expected_gross_1 = (0.60 - 0.40) * 7.0  # = 1.40
    assert t1["gross_pnl"] == pytest.approx(expected_gross_1)
    assert t1["net_pnl"] == pytest.approx(expected_gross_1 - t1["fees"])


def test_simple_round_trip_still_one_trade():
    """Basic LONG 10 -> SELL 10 emits exactly one trade."""
    fills = _make_fills([
        {
            "instrument_id": "mkt_B",
            "ts_event": _ts(1),
            "order_side": "BUY",
            "last_qty": 10.0,
            "last_px": 0.40,
            "commission": 0.0,
            "client_order_id": "oid-1",
        },
        {
            "instrument_id": "mkt_B",
            "ts_event": _ts(2),
            "order_side": "SELL",
            "last_qty": 10.0,
            "last_px": 0.55,
            "commission": 0.0,
            "client_order_id": "oid-2",
        },
    ])

    runner = _make_runner()
    trades = runner._fills_to_trades(fills, signals_df=pd.DataFrame())

    assert len(trades) == 1
    t = trades.iloc[0]
    assert t["direction"] == "LONG"
    assert t["size"] == pytest.approx(10.0)
    assert t["entry_price"] == pytest.approx(0.40)
    assert t["exit_price"] == pytest.approx(0.55)
    assert t["gross_pnl"] == pytest.approx((0.55 - 0.40) * 10.0)


def test_partial_reduction_preserves_entry_price():
    """After a partial reduction, the remaining portion keeps the original entry price."""
    fills = _make_fills([
        {
            "instrument_id": "mkt_C",
            "ts_event": _ts(1),
            "order_side": "BUY",
            "last_qty": 10.0,
            "last_px": 0.40,
            "commission": 0.1,
            "client_order_id": "oid-1",
        },
        {
            "instrument_id": "mkt_C",
            "ts_event": _ts(2),
            "order_side": "SELL",
            "last_qty": 3.0,
            "last_px": 0.50,
            "commission": 0.05,
            "client_order_id": "oid-2",
        },
        {
            "instrument_id": "mkt_C",
            "ts_event": _ts(3),
            "order_side": "SELL",
            "last_qty": 7.0,
            "last_px": 0.60,
            "commission": 0.05,
            "client_order_id": "oid-3",
        },
    ])

    runner = _make_runner()
    trades = runner._fills_to_trades(fills, signals_df=pd.DataFrame())

    assert len(trades) == 2

    # Both legs should reference the original entry price of 0.40
    assert trades.iloc[0]["entry_price"] == pytest.approx(0.40)
    assert trades.iloc[1]["entry_price"] == pytest.approx(0.40)

    # Fees: partial trade gets entry_fees (0.1) + its exit fee (0.05) = 0.15
    assert trades.iloc[0]["fees"] == pytest.approx(0.15)
    # Final close: entry_fees reset to 0 after partial, so only exit fee 0.05
    assert trades.iloc[1]["fees"] == pytest.approx(0.05)
