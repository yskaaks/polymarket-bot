"""Backtesting metrics — scalar summary + helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Optional

import pandas as pd

if TYPE_CHECKING:
    from src.layer1_research.backtesting.results import BacktestResult


# ---- small helpers --------------------------------------------------------

def brier_score(predictions: list[float], outcomes: list[int]) -> float:
    """Brier score: mean squared error of probability predictions. Lower is better."""
    if not predictions:
        return 0.0
    n = len(predictions)
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / n


def fee_drag(total_fees: float, gross_pnl: float) -> float:
    """Fees as a fraction of |gross_pnl|. Losers still show drag.

    Returns 0.0 iff gross_pnl is exactly zero (no trades moved the needle).
    """
    if gross_pnl == 0:
        return 0.0
    return total_fees / abs(gross_pnl)


# ---- scalar summary -------------------------------------------------------

@dataclass
class PerMarketStats:
    trades: int
    net_pnl: float
    win_rate: float
    avg_edge_at_entry: float
    avg_realized_edge: float


@dataclass
class BacktestMetrics:
    # Scalar perf (from analyzer_stats or equity curve)
    total_return_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    calmar_ratio: float
    # Trade-level
    total_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    avg_hold_time: Optional[timedelta]
    # Execution
    total_fees: float
    fee_drag_pct: float
    avg_slippage_bps: float
    # Signal quality
    avg_edge_at_order: float
    edge_realization_rate: float
    # Per-market
    per_market: dict[str, PerMarketStats] = field(default_factory=dict)


_ANALYZER_KEY_ALIASES = {
    "sharpe_ratio": ("Sharpe Ratio", "Sharpe Ratio (252 days)"),
    "sortino_ratio": ("Sortino Ratio", "Sortino Ratio (252 days)"),
    "max_drawdown": ("Max Drawdown",),
}


def _pick(stats: dict, aliases: tuple) -> Optional[float]:
    for key in aliases:
        if key in stats:
            return float(stats[key])
    # Fuzzy: match prefix
    for key, val in stats.items():
        for alias in aliases:
            if key.startswith(alias):
                return float(val)
    return None


def compute_metrics(result: "BacktestResult") -> BacktestMetrics:
    """Derive a BacktestMetrics from a BacktestResult.

    Pure function — does not mutate `result`.
    """
    trades = result.trades
    stats = result.analyzer_stats

    # Total return from equity curve (authoritative)
    start_eq = float(result.equity_curve.iloc[0])
    end_eq = float(result.equity_curve.iloc[-1])
    total_return_pct = (end_eq - start_eq) / start_eq * 100.0 if start_eq else 0.0

    sharpe = _pick(stats, _ANALYZER_KEY_ALIASES["sharpe_ratio"]) or 0.0
    sortino = _pick(stats, _ANALYZER_KEY_ALIASES["sortino_ratio"]) or 0.0
    mdd_raw = _pick(stats, _ANALYZER_KEY_ALIASES["max_drawdown"])
    # Nautilus reports max_drawdown as a negative fraction (-0.12 = -12%).
    # We surface absolute percent for display (12.0).
    mdd_pct = abs(float(mdd_raw) * 100.0) if mdd_raw is not None else 0.0
    calmar = (total_return_pct / mdd_pct) if mdd_pct > 0 else 0.0

    total_trades = int(len(trades))

    if total_trades == 0:
        return BacktestMetrics(
            total_return_pct=total_return_pct,
            sharpe_ratio=sharpe, sortino_ratio=sortino,
            max_drawdown_pct=mdd_pct, calmar_ratio=calmar,
            total_trades=0, win_rate=0.0, avg_win=0.0, avg_loss=0.0,
            profit_factor=0.0, avg_hold_time=None,
            total_fees=0.0, fee_drag_pct=0.0,
            avg_slippage_bps=0.0, avg_edge_at_order=0.0,
            edge_realization_rate=0.0, per_market={},
        )

    closed = trades[trades["exit_ts"].notna()]
    wins = closed[closed["net_pnl"] > 0]
    losses = closed[closed["net_pnl"] < 0]
    win_rate = len(wins) / len(closed) if len(closed) else 0.0
    avg_win = float(wins["net_pnl"].mean()) if len(wins) else 0.0
    avg_loss = float(losses["net_pnl"].mean()) if len(losses) else 0.0
    sum_wins = float(wins["net_pnl"].sum())
    sum_losses_abs = abs(float(losses["net_pnl"].sum()))
    profit_factor = sum_wins / sum_losses_abs if sum_losses_abs > 0 else 0.0

    # Avg hold time (closed trades only)
    if len(closed):
        deltas = closed["exit_ts"] - closed["entry_ts"]
        avg_hold = pd.to_timedelta(deltas).mean()
        avg_hold_time = avg_hold if pd.notna(avg_hold) else None
    else:
        avg_hold_time = None

    total_fees = float(trades["fees"].sum())
    gross_pnl = float(trades["gross_pnl"].sum())
    fee_drag_pct = fee_drag(total_fees, gross_pnl)

    avg_slippage_bps = float(trades["slippage_bps"].mean()) if total_trades else 0.0

    # Signal quality
    if not result.signals.empty and "edge_at_order" in result.signals.columns:
        acted = result.signals[result.signals["direction"].isin(["BUY", "SELL"])]
        avg_edge_at_order = float(acted["edge_at_order"].mean()) if len(acted) else 0.0
    else:
        avg_edge_at_order = 0.0

    # Edge realization: mean(realized_edge / edge_at_entry) over closed trades
    # with a non-zero entry edge (avoid div-by-zero).
    eligible = closed[
        closed["edge_at_entry"].abs() > 1e-9
    ]
    if len(eligible):
        ratios = eligible["realized_edge"] / eligible["edge_at_entry"]
        edge_realization_rate = float(ratios.mean())
    else:
        edge_realization_rate = 0.0

    # Per-market
    per_market: dict[str, PerMarketStats] = {}
    for inst_id, group in trades.groupby("instrument_id"):
        closed_g = group[group["exit_ts"].notna()]
        wins_g = closed_g[closed_g["net_pnl"] > 0]
        per_market[str(inst_id)] = PerMarketStats(
            trades=int(len(group)),
            net_pnl=float(group["net_pnl"].sum()),
            win_rate=(len(wins_g) / len(closed_g)) if len(closed_g) else 0.0,
            avg_edge_at_entry=float(group["edge_at_entry"].mean()),
            avg_realized_edge=(
                float(closed_g["realized_edge"].mean()) if len(closed_g) else 0.0
            ),
        )

    return BacktestMetrics(
        total_return_pct=total_return_pct,
        sharpe_ratio=sharpe, sortino_ratio=sortino,
        max_drawdown_pct=mdd_pct, calmar_ratio=calmar,
        total_trades=total_trades,
        win_rate=win_rate, avg_win=avg_win, avg_loss=avg_loss,
        profit_factor=profit_factor, avg_hold_time=avg_hold_time,
        total_fees=total_fees, fee_drag_pct=fee_drag_pct,
        avg_slippage_bps=avg_slippage_bps,
        avg_edge_at_order=avg_edge_at_order,
        edge_realization_rate=edge_realization_rate,
        per_market=per_market,
    )
