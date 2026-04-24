"""CLI report output for backtest results."""
from src.layer1_research.backtesting.reporting.metrics import BacktestMetrics


def print_report(summary: BacktestMetrics):
    """Print a formatted backtest summary to the terminal."""
    border = "=" * 55
    print(f"\n{border}")
    print(f"  Total Return:        {summary.total_return_pct:>12.1f}%")
    print(f"  Sharpe Ratio:        {summary.sharpe_ratio:>12.2f}")
    print(f"  Sortino Ratio:       {summary.sortino_ratio:>12.2f}")
    print(f"  Max Drawdown:        {summary.max_drawdown_pct:>12.1f}%")
    print(f"  Calmar Ratio:        {summary.calmar_ratio:>12.2f}")
    print(border)
    print(f"  Total Trades:        {summary.total_trades:>12}")
    print(f"  Win Rate:            {summary.win_rate:>12.1%}")
    print(f"  Avg Win:             ${summary.avg_win:>12,.2f}")
    print(f"  Avg Loss:            ${summary.avg_loss:>12,.2f}")
    print(f"  Profit Factor:       {summary.profit_factor:>12.2f}")
    print(border)
    print(f"  Fees Paid:           ${summary.total_fees:>12,.2f}")
    print(f"  Fee Drag:            {summary.fee_drag_pct:>12.1%}")
    print(f"  Avg Slippage (bps):  {summary.avg_slippage_bps:>12.2f}")
    print(border)
    if summary.per_market:
        print("  Top Markets:")
        sorted_markets = sorted(
            summary.per_market.items(), key=lambda x: x[1].net_pnl, reverse=True,
        )
        for name, stats in sorted_markets[:5]:
            pnl = stats.net_pnl
            trades = stats.trades
            sign = "+" if pnl >= 0 else ""
            print(f"    {name[:40]:<40} {sign}${pnl:>8,.0f}   ({trades} trades)")
        print(border)
    print()
