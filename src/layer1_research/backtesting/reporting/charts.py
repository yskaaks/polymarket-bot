"""Chart generation for backtest results. Only imported when --charts flag is used."""
from pathlib import Path
from typing import Optional
from src.layer1_research.backtesting.reporting.metrics import BacktestSummary


def generate_charts(
    summary: BacktestSummary,
    equity_curve: Optional[list[tuple[str, float]]] = None,
    trade_returns: Optional[list[float]] = None,
    calibration_data: Optional[list[tuple[float, int]]] = None,
    exposure_over_time: Optional[list[tuple[str, float]]] = None,
    output_dir: str = "output/backtests",
):
    import matplotlib.pyplot as plt
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    prefix = f"{output_dir}/{summary.strategy_name}"

    if equity_curve:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), height_ratios=[3, 1])
        dates = [e[0] for e in equity_curve]
        values = [e[1] for e in equity_curve]
        ax1.plot(dates, values, linewidth=1.5)
        ax1.set_title(f"{summary.strategy_name} — Equity Curve")
        ax1.set_ylabel("Portfolio Value (USDC)")
        ax1.axhline(y=summary.starting_capital, color="gray", linestyle="--", alpha=0.5)
        peak = summary.starting_capital
        drawdowns = []
        for v in values:
            peak = max(peak, v)
            drawdowns.append((v - peak) / peak * 100 if peak > 0 else 0)
        ax2.fill_between(range(len(drawdowns)), drawdowns, 0, alpha=0.3, color="red")
        ax2.set_ylabel("Drawdown %")
        ax2.set_xlabel("Time")
        plt.tight_layout()
        plt.savefig(f"{prefix}_equity.png", dpi=150)
        plt.close()
        print(f"  Saved: {prefix}_equity.png")

    if summary.per_market:
        fig, ax = plt.subplots(figsize=(10, max(4, len(summary.per_market) * 0.4)))
        markets = sorted(summary.per_market.items(), key=lambda x: x[1].get("pnl", 0))
        names = [m[0][:40] for m in markets]
        pnls = [m[1].get("pnl", 0) for m in markets]
        colors = ["green" if p >= 0 else "red" for p in pnls]
        ax.barh(names, pnls, color=colors, alpha=0.7)
        ax.set_xlabel("P&L (USDC)")
        ax.set_title(f"{summary.strategy_name} — P&L by Market")
        plt.tight_layout()
        plt.savefig(f"{prefix}_per_market.png", dpi=150)
        plt.close()
        print(f"  Saved: {prefix}_per_market.png")

    if trade_returns:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.hist(trade_returns, bins=50, alpha=0.7, edgecolor="black")
        ax.axvline(x=0, color="red", linestyle="--", alpha=0.5)
        ax.set_xlabel("Return per Trade")
        ax.set_ylabel("Frequency")
        ax.set_title(f"{summary.strategy_name} — Returns Distribution")
        plt.tight_layout()
        plt.savefig(f"{prefix}_returns_dist.png", dpi=150)
        plt.close()
        print(f"  Saved: {prefix}_returns_dist.png")

    if calibration_data:
        import numpy as np
        preds = [d[0] for d in calibration_data]
        actuals = [d[1] for d in calibration_data]
        bins = np.linspace(0, 1, 11)
        bin_indices = np.digitize(preds, bins) - 1
        bin_means_pred, bin_means_actual = [], []
        for i in range(10):
            mask = [j for j, b in enumerate(bin_indices) if b == i]
            if mask:
                bin_means_pred.append(np.mean([preds[j] for j in mask]))
                bin_means_actual.append(np.mean([actuals[j] for j in mask]))
        fig, ax = plt.subplots(figsize=(8, 8))
        ax.plot([0, 1], [0, 1], "k--", alpha=0.5, label="Perfect calibration")
        ax.scatter(bin_means_pred, bin_means_actual, s=80, zorder=5)
        ax.set_xlabel("Predicted Probability")
        ax.set_ylabel("Actual Win Rate")
        ax.set_title(f"{summary.strategy_name} — Calibration")
        ax.legend()
        plt.tight_layout()
        plt.savefig(f"{prefix}_calibration.png", dpi=150)
        plt.close()
        print(f"  Saved: {prefix}_calibration.png")

    if exposure_over_time:
        fig, ax = plt.subplots(figsize=(12, 5))
        values = [e[1] for e in exposure_over_time]
        ax.fill_between(range(len(values)), values, alpha=0.4)
        ax.set_ylabel("Capital Deployed (USDC)")
        ax.set_title(f"{summary.strategy_name} — Exposure Over Time")
        plt.tight_layout()
        plt.savefig(f"{prefix}_exposure.png", dpi=150)
        plt.close()
        print(f"  Saved: {prefix}_exposure.png")

    print(f"  Charts saved to {output_dir}/")
