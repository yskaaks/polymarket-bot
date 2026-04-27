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


def test_parse_usd_column():
    from src.layer1_research.backtesting.results import _parse_usd_series
    import pandas as pd
    s = pd.Series(["5.01 USD", "0.00 USD", "-2.50 USD", ""])
    out = _parse_usd_series(s)
    assert list(out) == [pytest.approx(5.01), pytest.approx(0.0),
                         pytest.approx(-2.50), pytest.approx(0.0)]


def test_backtest_result_construction_minimal():
    """BacktestResult can be built from minimal Nautilus-like reports."""
    from src.layer1_research.backtesting.results import BacktestResult
    from src.layer1_research.backtesting.config import BacktestConfig
    import pandas as pd

    config = BacktestConfig(
        catalog_path="data/catalog",
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 6, 2, tzinfo=timezone.utc),
        strategy_name="test", starting_capital=10_000.0, data_mode="trade",
    )
    account = pd.DataFrame({
        "total": ["10000.00 USD", "10050.00 USD", "9980.00 USD"],
    }, index=pd.to_datetime([
        "2024-06-01T00:00:00Z", "2024-06-01T12:00:00Z", "2024-06-02T00:00:00Z",
    ], utc=True))

    result = BacktestResult(
        config=config,
        fills=pd.DataFrame(),
        positions=pd.DataFrame(),
        account=account,
        instruments=[],
        signals=pd.DataFrame(),
        trades=pd.DataFrame(),
    )
    # equity_curve is built from account["total"], values are floats
    assert result.equity_curve.iloc[0] == pytest.approx(10_000.0)
    assert result.equity_curve.iloc[-1] == pytest.approx(9_980.0)
    # account["total"] is also cleaned to float
    assert result.account["total"].dtype.kind == "f"


def test_backtest_result_equity_curve_empty_account_raises():
    """Empty account report is a real error, not a silent zero."""
    from src.layer1_research.backtesting.results import BacktestResult
    from src.layer1_research.backtesting.config import BacktestConfig
    import pandas as pd

    config = BacktestConfig(
        catalog_path="data/catalog",
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 6, 2, tzinfo=timezone.utc),
        strategy_name="test", starting_capital=10_000.0, data_mode="trade",
    )
    with pytest.raises(ValueError, match="empty account"):
        BacktestResult(
            config=config,
            fills=pd.DataFrame(), positions=pd.DataFrame(),
            account=pd.DataFrame(),
            instruments=[],
            signals=pd.DataFrame(), trades=pd.DataFrame(),
        )


def test_plot_methods_return_figures():
    """Smoke: each plot_* returns a matplotlib Figure without raising."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: F401

    from src.layer1_research.backtesting.results import BacktestResult
    from src.layer1_research.backtesting.config import BacktestConfig

    config = BacktestConfig(
        catalog_path="data/catalog",
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 6, 30, tzinfo=timezone.utc),
        strategy_name="smoke", starting_capital=10_000.0, data_mode="trade",
    )
    import pandas as pd
    account = pd.DataFrame(
        {"total": ["10000.00 USD", "10200.00 USD", "9800.00 USD"]},
        index=pd.to_datetime(
            ["2024-06-01T00Z", "2024-06-15T00Z", "2024-06-30T00Z"], utc=True,
        ),
    )
    r = BacktestResult(
        config=config, fills=pd.DataFrame(), positions=pd.DataFrame(),
        account=account, instruments=[],
        signals=pd.DataFrame(), trades=pd.DataFrame(),
    )
    assert r.plot_equity_curve() is not None
    assert r.plot_drawdown() is not None
    assert r.plot_pnl_histogram() is not None
    assert r.plot_edge_calibration() is not None
    assert r.plot_per_market_pnl() is not None


def test_to_mlflow_roundtrip(tmp_path, monkeypatch):
    """BacktestResult.to_mlflow writes artifacts; from_mlflow reads them back."""
    import mlflow
    import pandas as pd
    from src.layer1_research.backtesting.results import BacktestResult
    from src.layer1_research.backtesting.config import BacktestConfig

    config = BacktestConfig(
        catalog_path="data/catalog",
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 6, 30, tzinfo=timezone.utc),
        strategy_name="mlflow_rt", starting_capital=10_000.0, data_mode="trade",
    )
    account = pd.DataFrame(
        {"total": ["10000.00 USD", "10500.00 USD"]},
        index=pd.to_datetime(["2024-06-01T00Z", "2024-06-30T00Z"], utc=True),
    )
    result = BacktestResult(
        config=config, fills=pd.DataFrame(), positions=pd.DataFrame(),
        account=account, instruments=[],
        signals=pd.DataFrame(), trades=pd.DataFrame(),
    )

    tracking_uri = f"sqlite:///{tmp_path}/mlflow.db"
    mlflow.set_tracking_uri(tracking_uri)
    run_id = result.to_mlflow(run_name="test-run", experiment="corr-test")
    assert run_id

    reloaded = BacktestResult.from_mlflow(run_id, tracking_uri=tracking_uri)
    assert reloaded.config.strategy_name == "mlflow_rt"
    assert float(reloaded.equity_curve.iloc[-1]) == pytest.approx(10_500.0)
