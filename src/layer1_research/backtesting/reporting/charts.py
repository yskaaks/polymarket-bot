"""Chart functions for BacktestResult.

Each function takes a BacktestResult and an optional matplotlib Axes, and
returns the Figure. No I/O — notebooks save/show as needed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import matplotlib.pyplot as plt
import pandas as pd

if TYPE_CHECKING:
    from src.layer1_research.backtesting.results import BacktestResult


def plot_equity_curve(result: "BacktestResult", ax=None):
    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 4))
    else:
        fig = ax.figure
    eq = result.equity_curve
    ax.plot(eq.index, eq.values, label="Equity (USD)", linewidth=1.4)
    ax.axhline(result.config.starting_capital, linestyle="--", linewidth=0.8,
               alpha=0.5, label="Starting capital")
    ax.set_title(f"Equity curve — {result.config.strategy_name}")
    ax.set_xlabel("Time")
    ax.set_ylabel("USD")
    ax.legend()
    ax.grid(alpha=0.3)
    return fig


def plot_drawdown(result: "BacktestResult", ax=None):
    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 3))
    else:
        fig = ax.figure
    eq = result.equity_curve
    running_max = eq.cummax()
    dd = (eq - running_max) / running_max * 100.0
    ax.fill_between(dd.index, dd.values, 0, alpha=0.4, color="red")
    ax.plot(dd.index, dd.values, linewidth=0.9, color="darkred")
    ax.set_title("Drawdown (%)")
    ax.set_xlabel("Time")
    ax.set_ylabel("%")
    ax.grid(alpha=0.3)
    return fig


def plot_pnl_histogram(result: "BacktestResult", ax=None, bins: int = 40):
    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 4))
    else:
        fig = ax.figure
    if result.trades.empty:
        ax.text(0.5, 0.5, "No trades", ha="center", va="center", transform=ax.transAxes)
        return fig
    pnl = result.trades["net_pnl"].dropna()
    ax.hist(pnl, bins=bins, alpha=0.7, edgecolor="black")
    ax.axvline(0, color="red", linestyle="--", linewidth=1.0)
    ax.set_title("Trade P&L distribution")
    ax.set_xlabel("Net P&L (USD)")
    ax.set_ylabel("Count")
    ax.grid(alpha=0.3)
    return fig


def plot_edge_calibration(result: "BacktestResult", ax=None):
    if ax is None:
        fig, ax = plt.subplots(figsize=(6, 6))
    else:
        fig = ax.figure
    if result.trades.empty or "exit_ts" not in result.trades.columns:
        ax.text(0.5, 0.5, "No closed trades", ha="center", va="center",
                transform=ax.transAxes)
        return fig
    closed = result.trades[result.trades["exit_ts"].notna()]
    if closed.empty:
        ax.text(0.5, 0.5, "No closed trades", ha="center", va="center",
                transform=ax.transAxes)
        return fig
    x = closed["edge_at_entry"]
    y = closed["realized_edge"]
    lo = float(min(x.min(), y.min(), 0.0))
    hi = float(max(x.max(), y.max(), 0.0))
    ax.scatter(x, y, alpha=0.5, s=18)
    ax.plot([lo, hi], [lo, hi], linestyle="--", color="black",
            linewidth=0.8, label="y=x (perfect calibration)")
    ax.axhline(0, color="gray", linewidth=0.5)
    ax.axvline(0, color="gray", linewidth=0.5)
    ax.set_title("Edge calibration")
    ax.set_xlabel("Edge at entry")
    ax.set_ylabel("Realized edge")
    ax.legend()
    ax.grid(alpha=0.3)
    return fig


def plot_per_market_pnl(result: "BacktestResult", ax=None, top_n: int = 20):
    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 6))
    else:
        fig = ax.figure
    if result.trades.empty:
        ax.text(0.5, 0.5, "No trades", ha="center", va="center", transform=ax.transAxes)
        return fig
    pnl_by_mkt = result.trades.groupby("instrument_id")["net_pnl"].sum()
    pnl_by_mkt = pnl_by_mkt.sort_values()
    pnl_by_mkt = pd.concat([pnl_by_mkt.head(top_n // 2), pnl_by_mkt.tail(top_n // 2)])
    colors = ["red" if v < 0 else "green" for v in pnl_by_mkt.values]
    labels = [str(i)[:18] for i in pnl_by_mkt.index]
    ax.barh(range(len(pnl_by_mkt)), pnl_by_mkt.values, color=colors)
    ax.set_yticks(range(len(pnl_by_mkt)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.axvline(0, color="black", linewidth=0.5)
    ax.set_title(f"Net P&L by market (top/bottom {top_n // 2})")
    ax.set_xlabel("USD")
    ax.grid(alpha=0.3, axis="x")
    return fig
