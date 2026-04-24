"""Pretty-printer for BacktestMetrics."""
from src.layer1_research.backtesting.reporting.metrics import BacktestMetrics


def print_report(metrics: BacktestMetrics, top_n_markets: int = 5):
    border = "=" * 60
    print(f"\n{border}")
    print(f"  Backtest results")
    print(border)
    print(f"  Total Return:        {metrics.total_return_pct:>12.2f}%")
    print(f"  Sharpe Ratio:        {metrics.sharpe_ratio:>12.2f}")
    print(f"  Sortino Ratio:       {metrics.sortino_ratio:>12.2f}")
    print(f"  Max Drawdown:        {metrics.max_drawdown_pct:>12.2f}%")
    print(f"  Calmar Ratio:        {metrics.calmar_ratio:>12.2f}")
    print(border)
    print(f"  Total Trades:        {metrics.total_trades:>12}")
    print(f"  Win Rate:            {metrics.win_rate:>12.1%}")
    print(f"  Avg Win:             ${metrics.avg_win:>11,.2f}")
    print(f"  Avg Loss:            ${metrics.avg_loss:>11,.2f}")
    print(f"  Profit Factor:       {metrics.profit_factor:>12.2f}")
    if metrics.avg_hold_time is not None:
        print(f"  Avg Hold Time:       {str(metrics.avg_hold_time):>12}")
    print(border)
    print(f"  Total Fees:          ${metrics.total_fees:>11,.2f}")
    print(f"  Fee Drag:            {metrics.fee_drag_pct:>12.1%}")
    print(f"  Avg Slippage:        {metrics.avg_slippage_bps:>10.1f} bps")
    print(border)
    print(f"  Avg Edge @ Order:    {metrics.avg_edge_at_order:>12.4f}")
    print(f"  Edge Realization:    {metrics.edge_realization_rate:>12.2f}")
    print(border)

    if metrics.per_market:
        sorted_mkts = sorted(
            metrics.per_market.items(),
            key=lambda kv: kv[1].net_pnl, reverse=True,
        )
        top = sorted_mkts[:top_n_markets]
        bottom = sorted_mkts[-top_n_markets:]
        print(f"  Top {top_n_markets} markets by P&L:")
        for mkt, s in top:
            sign = "+" if s.net_pnl >= 0 else ""
            print(f"    {mkt[:40]:<40} {sign}${s.net_pnl:>9,.0f}  "
                  f"({s.trades} trades, win {s.win_rate:.0%})")
        print(f"  Bottom {top_n_markets} markets by P&L:")
        for mkt, s in bottom:
            sign = "+" if s.net_pnl >= 0 else ""
            print(f"    {mkt[:40]:<40} {sign}${s.net_pnl:>9,.0f}  "
                  f"({s.trades} trades, win {s.win_rate:.0%})")
        print(border)
    print()
