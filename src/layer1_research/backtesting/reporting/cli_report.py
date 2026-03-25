"""CLI report output for backtest results."""
from src.layer1_research.backtesting.reporting.metrics import BacktestSummary


def print_report(summary: BacktestSummary):
    """Print a formatted backtest summary to the terminal."""
    border = "=" * 55
    print(f"\n{border}")
    print(f"  Backtest: {summary.strategy_name}")
    print(f"  Period: {summary.start} -> {summary.end}")
    print(border)
    print(f"  Starting Capital:    ${summary.starting_capital:>12,.2f}")
    print(f"  Final Equity:        ${summary.final_equity:>12,.2f}")
    print(f"  Total Return:        {summary.total_return_pct:>12.1f}%")
    print(f"  Sharpe Ratio:        {summary.sharpe_ratio:>12.2f}")
    print(f"  Max Drawdown:        {summary.max_drawdown_pct:>12.1f}%")
    print(f"  Win Rate:            {summary.win_rate:>12.1%}")
    print(f"  Total Trades:        {summary.total_trades:>12}")
    print(f"  Fees Paid:           ${summary.total_fees:>12,.2f}")
    if summary.brier is not None:
        print(f"  Brier Score:         {summary.brier:>12.3f}")
    print(f"  Fee Drag:            {summary.fee_drag_pct:>12.1%}")
    print(border)
    if summary.per_market:
        print("  Top Markets:")
        sorted_markets = sorted(
            summary.per_market.items(), key=lambda x: x[1].get("pnl", 0), reverse=True,
        )
        for name, stats in sorted_markets[:5]:
            pnl = stats.get("pnl", 0)
            trades = stats.get("trades", 0)
            sign = "+" if pnl >= 0 else ""
            print(f"    {name[:40]:<40} {sign}${pnl:>8,.0f}   ({trades} trades)")
        print(border)
    print()
