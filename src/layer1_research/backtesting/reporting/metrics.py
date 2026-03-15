"""Backtesting performance metrics."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BacktestSummary:
    """Aggregated backtest results."""
    strategy_name: str
    start: str
    end: str
    starting_capital: float
    final_equity: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate: float
    total_trades: int
    total_fees: float
    brier: Optional[float]
    fee_drag_pct: float
    per_market: dict = field(default_factory=dict)


def brier_score(predictions: list[float], outcomes: list[int]) -> float:
    """Brier score: mean squared error of probability predictions. Lower is better."""
    if not predictions:
        return 0.0
    n = len(predictions)
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / n


def fee_drag(total_fees: float, gross_pnl: float) -> float:
    """Fees as a fraction of gross P&L. Returns 0 if no gains."""
    if gross_pnl <= 0:
        return 0.0
    return total_fees / gross_pnl
