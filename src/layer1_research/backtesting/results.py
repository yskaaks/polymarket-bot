"""Programmatic backtest result object.

BacktestRunner returns a single BacktestResult. All downstream consumers
(metrics, charts, MLflow logger, notebooks) read from it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import pandas as pd

if TYPE_CHECKING:
    from src.layer1_research.backtesting.config import BacktestConfig
    from src.layer1_research.backtesting.reporting.metrics import BacktestMetrics


@dataclass(frozen=True)
class SignalSnapshot:
    """A single signal emitted by a strategy, captured at the moment of emission."""

    ts: datetime
    instrument_id: str
    direction: str                 # "BUY" / "SELL" / "FLAT"
    market_price: float            # observed price at signal time
    confidence: float              # strategy's P(signal is right), in [0, 1]
    target_price: float            # strategy's fair value
    size: float
    client_order_id: Optional[str]

    def __post_init__(self):
        if self.direction not in ("BUY", "SELL", "FLAT"):
            raise ValueError(
                f"direction must be BUY, SELL, or FLAT, got '{self.direction}'"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0, 1], got {self.confidence}"
            )

    @property
    def edge_at_order(self) -> float:
        """Expected edge per unit at signal time.

        BUY:  confidence - market_price
            (we think P(YES wins) = confidence; buying at market_price nets
             confidence - market_price in expectation)
        SELL: market_price - (1 - confidence)
            (we think P(YES wins) = 1 - confidence; selling at market_price nets
             market_price - (1 - confidence) in expectation)
        FLAT: 0.0 (no position taken)
        """
        if self.direction == "BUY":
            return self.confidence - self.market_price
        if self.direction == "SELL":
            return self.market_price - (1.0 - self.confidence)
        return 0.0


@dataclass(frozen=True)
class Trade:
    """Round-trip trade: entry fill -> exit fill (or still-open at EOB)."""

    instrument_id: str
    direction: str                 # "LONG" or "SHORT"
    entry_ts: datetime
    exit_ts: Optional[datetime]    # None if still open at end of backtest
    entry_price: float
    exit_price: Optional[float]    # None if still open
    size: float
    fees: float
    gross_pnl: float
    net_pnl: float                 # gross_pnl - fees
    edge_at_entry: float           # from the SignalSnapshot that opened it
    slippage_bps: float            # fill price vs. signal market_price
    signal_confidence: float

    def __post_init__(self):
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError(
                f"direction must be LONG or SHORT, got '{self.direction}'"
            )

    @property
    def realized_edge(self) -> Optional[float]:
        """Actual edge captured at exit. None if position is still open."""
        if self.exit_price is None:
            return None
        if self.direction == "LONG":
            return self.exit_price - self.entry_price
        return self.entry_price - self.exit_price   # SHORT


def _parse_usd_series(s: pd.Series) -> pd.Series:
    """Convert a Nautilus money-string column ('5.01 USD') into a float column.

    Empty strings become 0.0 (Nautilus emits "" for zero commissions sometimes).
    """
    def _parse(v) -> float:
        if v is None:
            return 0.0
        s = str(v).strip()
        if not s:
            return 0.0
        return float(s.split()[0])
    return s.apply(_parse).astype(float)


_USD_COLUMNS = ("commission", "realized_pnl", "unrealized_pnl", "total", "free", "locked")


def _clean_usd_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of `df` with any known USD string columns parsed to floats."""
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    out = df.copy()
    for col in _USD_COLUMNS:
        if col in out.columns and out[col].dtype == object:
            out[col] = _parse_usd_series(out[col])
    return out


@dataclass
class BacktestResult:
    """Output of BacktestRunner.run(). Contains raw reports + derived views."""

    config: BacktestConfig
    fills: pd.DataFrame
    positions: pd.DataFrame
    account: pd.DataFrame
    instruments: list               # list[BinaryOption]
    analyzer_stats: dict            # from nautilus trader.analyzer
    signals: pd.DataFrame           # one row per SignalSnapshot
    trades: pd.DataFrame            # one row per Trade

    # Derived, filled by __post_init__
    equity_curve: pd.Series = None  # type: ignore[assignment]

    def __post_init__(self):
        # Clean string-money columns on the Nautilus frames
        self.fills = _clean_usd_columns(self.fills)
        self.positions = _clean_usd_columns(self.positions)
        self.account = _clean_usd_columns(self.account)

        if self.account is None or self.account.empty:
            raise ValueError(
                "BacktestResult constructed with empty account report — "
                "the engine did not record any balance snapshots"
            )
        if "total" not in self.account.columns:
            raise ValueError(
                f"account report missing 'total' column; got {list(self.account.columns)}"
            )

        # Equity curve: account['total'] series, indexed by the account df's index
        # (Nautilus uses a DatetimeIndex). Forward-fill gaps so flat periods carry
        # the previous balance.
        self.equity_curve = self.account["total"].astype(float).copy()
        self.equity_curve.name = "equity_usd"

        self._metrics_cache = None

    def metrics(self) -> "BacktestMetrics":
        """Return the BacktestMetrics derived from this result. Memoized."""
        if getattr(self, "_metrics_cache", None) is None:
            from src.layer1_research.backtesting.reporting.metrics import compute_metrics
            self._metrics_cache = compute_metrics(self)
        return self._metrics_cache

    def plot_equity_curve(self, ax=None):
        from src.layer1_research.backtesting.reporting.charts import plot_equity_curve
        return plot_equity_curve(self, ax=ax)

    def plot_drawdown(self, ax=None):
        from src.layer1_research.backtesting.reporting.charts import plot_drawdown
        return plot_drawdown(self, ax=ax)

    def plot_pnl_histogram(self, ax=None, bins: int = 40):
        from src.layer1_research.backtesting.reporting.charts import plot_pnl_histogram
        return plot_pnl_histogram(self, ax=ax, bins=bins)

    def plot_edge_calibration(self, ax=None):
        from src.layer1_research.backtesting.reporting.charts import plot_edge_calibration
        return plot_edge_calibration(self, ax=ax)

    def plot_per_market_pnl(self, ax=None, top_n: int = 20):
        from src.layer1_research.backtesting.reporting.charts import plot_per_market_pnl
        return plot_per_market_pnl(self, ax=ax, top_n=top_n)
