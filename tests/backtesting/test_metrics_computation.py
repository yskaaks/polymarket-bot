"""Tests for compute_metrics on hand-built BacktestResult fixtures."""
import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta


def _make_result(*, trades_rows, signals_rows=None, account_totals=None):
    from src.layer1_research.backtesting.results import BacktestResult
    from src.layer1_research.backtesting.config import BacktestConfig

    config = BacktestConfig(
        catalog_path="data/catalog",
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 6, 30, tzinfo=timezone.utc),
        strategy_name="test", starting_capital=10_000.0, data_mode="trade",
    )
    if account_totals is None:
        account_totals = [10_000.0, 10_000.0]
    account = pd.DataFrame(
        {"total": [f"{v:.2f} USD" for v in account_totals]},
        index=pd.to_datetime(
            ["2024-06-01T00:00:00Z", "2024-06-30T23:59:59Z"], utc=True,
        ),
    )
    trades = pd.DataFrame(trades_rows) if trades_rows else pd.DataFrame(columns=[
        "instrument_id", "direction", "entry_ts", "exit_ts",
        "entry_price", "exit_price", "size", "fees",
        "gross_pnl", "net_pnl", "edge_at_entry", "slippage_bps",
        "signal_confidence", "realized_edge",
    ])
    signals = pd.DataFrame(signals_rows) if signals_rows else pd.DataFrame()
    return BacktestResult(
        config=config, fills=pd.DataFrame(), positions=pd.DataFrame(),
        account=account, instruments=[],
        signals=signals, trades=trades,
    )


def test_win_rate_three_of_four_winners():
    from src.layer1_research.backtesting.reporting.metrics import compute_metrics
    trades = [
        {"instrument_id": "a", "direction": "LONG",
         "entry_ts": datetime(2024, 6, 1, tzinfo=timezone.utc),
         "exit_ts": datetime(2024, 6, 2, tzinfo=timezone.utc),
         "entry_price": 0.40, "exit_price": 0.55, "size": 100.0,
         "fees": 0.5, "gross_pnl": 15.0, "net_pnl": 14.5,
         "edge_at_entry": 0.25, "slippage_bps": 0.0,
         "signal_confidence": 0.65, "realized_edge": 0.15},
        {"instrument_id": "b", "direction": "LONG",
         "entry_ts": datetime(2024, 6, 3, tzinfo=timezone.utc),
         "exit_ts": datetime(2024, 6, 4, tzinfo=timezone.utc),
         "entry_price": 0.50, "exit_price": 0.60, "size": 50.0,
         "fees": 0.3, "gross_pnl": 5.0, "net_pnl": 4.7,
         "edge_at_entry": 0.15, "slippage_bps": 0.0,
         "signal_confidence": 0.65, "realized_edge": 0.10},
        {"instrument_id": "c", "direction": "LONG",
         "entry_ts": datetime(2024, 6, 5, tzinfo=timezone.utc),
         "exit_ts": datetime(2024, 6, 6, tzinfo=timezone.utc),
         "entry_price": 0.60, "exit_price": 0.65, "size": 20.0,
         "fees": 0.1, "gross_pnl": 1.0, "net_pnl": 0.9,
         "edge_at_entry": 0.05, "slippage_bps": 0.0,
         "signal_confidence": 0.65, "realized_edge": 0.05},
        {"instrument_id": "d", "direction": "LONG",
         "entry_ts": datetime(2024, 6, 7, tzinfo=timezone.utc),
         "exit_ts": datetime(2024, 6, 8, tzinfo=timezone.utc),
         "entry_price": 0.50, "exit_price": 0.30, "size": 100.0,
         "fees": 0.5, "gross_pnl": -20.0, "net_pnl": -20.5,
         "edge_at_entry": 0.15, "slippage_bps": 0.0,
         "signal_confidence": 0.65, "realized_edge": -0.20},
    ]
    r = _make_result(trades_rows=trades)
    m = compute_metrics(r)
    assert m.total_trades == 4
    assert m.win_rate == pytest.approx(0.75)
    assert m.avg_win == pytest.approx((14.5 + 4.7 + 0.9) / 3)
    assert m.avg_loss == pytest.approx(-20.5)
    # profit_factor = sum(wins) / |sum(losses)|
    assert m.profit_factor == pytest.approx((14.5 + 4.7 + 0.9) / 20.5)


def test_fee_drag_uses_equity_curve_gross_pnl():
    """Losers still show fee drag.

    fee_drag denominator is abs(gross_pnl_from_equity), where
    gross_pnl_from_equity = (final_equity - starting_equity) + total_fees.
    Using the equity curve as the P&L source captures unrealized P&L on
    open positions (trades.gross_pnl alone misses these).
    """
    from src.layer1_research.backtesting.reporting.metrics import compute_metrics
    trades = [
        {"instrument_id": "a", "direction": "LONG",
         "entry_ts": datetime(2024, 6, 1, tzinfo=timezone.utc),
         "exit_ts": datetime(2024, 6, 2, tzinfo=timezone.utc),
         "entry_price": 0.50, "exit_price": 0.40, "size": 100.0,
         "fees": 5.0, "gross_pnl": -10.0, "net_pnl": -15.0,
         "edge_at_entry": 0.0, "slippage_bps": 0.0,
         "signal_confidence": 0.50, "realized_edge": -0.10},
    ]
    # Equity 10000 -> 9985 reflects net_pnl = -15. With $5 fees on top,
    # gross_pnl_from_equity = -15 + 5 = -10. fee_drag = 5 / |-10| = 0.5 → 50%.
    r = _make_result(trades_rows=trades, account_totals=[10_000.0, 9_985.0])
    m = compute_metrics(r)
    assert m.total_fees == pytest.approx(5.0)
    assert m.fee_drag_pct == pytest.approx(50.0)


def test_total_return_from_equity_curve():
    from src.layer1_research.backtesting.reporting.metrics import compute_metrics
    r = _make_result(trades_rows=[], account_totals=[10_000.0, 11_250.0])
    m = compute_metrics(r)
    assert m.total_return_pct == pytest.approx(12.5)


def test_per_market_breakdown():
    from src.layer1_research.backtesting.reporting.metrics import compute_metrics
    trades = [
        {"instrument_id": "mkt_a", "direction": "LONG",
         "entry_ts": datetime(2024, 6, 1, tzinfo=timezone.utc),
         "exit_ts": datetime(2024, 6, 2, tzinfo=timezone.utc),
         "entry_price": 0.40, "exit_price": 0.55, "size": 100.0,
         "fees": 0.5, "gross_pnl": 15.0, "net_pnl": 14.5,
         "edge_at_entry": 0.25, "slippage_bps": 0.0,
         "signal_confidence": 0.65, "realized_edge": 0.15},
        {"instrument_id": "mkt_b", "direction": "LONG",
         "entry_ts": datetime(2024, 6, 3, tzinfo=timezone.utc),
         "exit_ts": datetime(2024, 6, 4, tzinfo=timezone.utc),
         "entry_price": 0.50, "exit_price": 0.30, "size": 100.0,
         "fees": 0.5, "gross_pnl": -20.0, "net_pnl": -20.5,
         "edge_at_entry": 0.15, "slippage_bps": 0.0,
         "signal_confidence": 0.65, "realized_edge": -0.20},
    ]
    r = _make_result(trades_rows=trades)
    m = compute_metrics(r)
    assert set(m.per_market.keys()) == {"mkt_a", "mkt_b"}
    assert m.per_market["mkt_a"].net_pnl == pytest.approx(14.5)
    assert m.per_market["mkt_a"].win_rate == pytest.approx(1.0)
    assert m.per_market["mkt_b"].net_pnl == pytest.approx(-20.5)
    assert m.per_market["mkt_b"].win_rate == pytest.approx(0.0)


def test_sharpe_sortino_drawdown_from_equity_curve():
    """Sharpe / Sortino / Max DD are computed from the equity curve directly."""
    from src.layer1_research.backtesting.reporting.metrics import compute_metrics
    from src.layer1_research.backtesting.results import BacktestResult
    from src.layer1_research.backtesting.config import BacktestConfig

    config = BacktestConfig(
        catalog_path="data/catalog",
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 6, 30, tzinfo=timezone.utc),
        strategy_name="test", starting_capital=10_000.0, data_mode="trade",
    )
    # Equity timeline with multiple ups and downs so Sortino has >1 down sample.
    # 10000 -> 11000 -> 9500 -> 10500 -> 9000 -> 12000
    account = pd.DataFrame(
        {"total": [
            "10000.00 USD", "11000.00 USD", "9500.00 USD",
            "10500.00 USD", "9000.00 USD", "12000.00 USD",
        ]},
        index=pd.to_datetime([
            "2024-06-01T00Z", "2024-06-02T00Z", "2024-06-03T00Z",
            "2024-06-04T00Z", "2024-06-05T00Z", "2024-06-06T00Z",
        ], utc=True),
    )
    r = BacktestResult(
        config=config, fills=pd.DataFrame(), positions=pd.DataFrame(),
        account=account, instruments=[],
        signals=pd.DataFrame(), trades=pd.DataFrame(),
    )
    m = compute_metrics(r)
    assert m.total_return_pct == pytest.approx(20.0)         # 10k -> 12k
    # Largest peak-to-trough: 11000 -> 9000 = -18.18%
    assert m.max_drawdown_pct == pytest.approx(18.1818, abs=1e-2)
    assert m.sharpe_ratio != 0.0   # nonzero, derived from returns
    assert m.sortino_ratio != 0.0  # has >1 downside samples
    assert m.calmar_ratio == pytest.approx(20.0 / 18.1818, abs=1e-2)


def test_flat_equity_yields_zero_sharpe():
    """Flat equity curve = zero returns = Sharpe must be 0 (no division by 0)."""
    from src.layer1_research.backtesting.reporting.metrics import compute_metrics
    from src.layer1_research.backtesting.results import BacktestResult
    from src.layer1_research.backtesting.config import BacktestConfig

    config = BacktestConfig(
        catalog_path="data/catalog",
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 6, 30, tzinfo=timezone.utc),
        strategy_name="test", starting_capital=10_000.0, data_mode="trade",
    )
    account = pd.DataFrame(
        {"total": ["10000.00 USD", "10000.00 USD", "10000.00 USD"]},
        index=pd.to_datetime(
            ["2024-06-01T00Z", "2024-06-15T00Z", "2024-06-30T00Z"], utc=True,
        ),
    )
    r = BacktestResult(
        config=config, fills=pd.DataFrame(), positions=pd.DataFrame(),
        account=account, instruments=[],
        signals=pd.DataFrame(), trades=pd.DataFrame(),
    )
    m = compute_metrics(r)
    assert m.sharpe_ratio == 0.0
    assert m.sortino_ratio == 0.0
    assert m.max_drawdown_pct == 0.0
