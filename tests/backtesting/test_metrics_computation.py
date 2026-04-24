"""Tests for compute_metrics on hand-built BacktestResult fixtures."""
import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta


def _make_result(*, trades_rows, signals_rows=None, account_totals=None,
                 analyzer_stats=None):
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
        analyzer_stats=analyzer_stats or {},
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


def test_fee_drag_uses_abs_pnl():
    """Losers still show fee drag — abs(gross_pnl) in denominator."""
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
    r = _make_result(trades_rows=trades)
    m = compute_metrics(r)
    assert m.total_fees == pytest.approx(5.0)
    # fee_drag = fees / abs(gross_pnl) = 5 / 10 = 0.5 → stored as 50.0 (percent)
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


def test_sharpe_comes_from_analyzer_stats():
    from src.layer1_research.backtesting.reporting.metrics import compute_metrics
    r = _make_result(
        trades_rows=[],
        analyzer_stats={
            "Sharpe Ratio (252 days)": 1.85,
            "Sortino Ratio (252 days)": 2.20,
            "Max Drawdown": -0.12,
        },
    )
    m = compute_metrics(r)
    assert m.sharpe_ratio == pytest.approx(1.85)
    assert m.sortino_ratio == pytest.approx(2.20)
    # max drawdown reported as positive percent
    assert m.max_drawdown_pct == pytest.approx(12.0)
