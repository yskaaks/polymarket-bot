"""Run a backtest from the command line.

Usage:
    python scripts/run_backtest.py --strategy fair_value_mr --start 2024-01-01 --end 2024-12-31
    python scripts/run_backtest.py --strategy fair_value_mr --start 2024-01-01 --end 2024-12-31 --charts
    python scripts/run_backtest.py --strategy fair_value_mr --dry-run --start 2024-01-01 --end 2024-12-31
"""
import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

STRATEGY_REGISTRY: dict = {}


def register_strategies():
    logger = logging.getLogger(__name__)
    try:
        from src.layer1_research.backtesting.strategies.examples.fair_value_mean_reversion import FairValueMeanReversionStrategy
        STRATEGY_REGISTRY["fair_value_mr"] = FairValueMeanReversionStrategy
    except ImportError as e:
        logger.warning(f"Could not load fair_value_mr strategy: {e}")


def main():
    parser = argparse.ArgumentParser(description="Run a prediction market backtest")
    parser.add_argument("--strategy", required=True, help="Strategy name")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--catalog", default="data/catalog", help="Catalog directory")
    parser.add_argument("--capital", type=float, default=10_000.0, help="Starting capital")
    parser.add_argument("--data-mode", choices=["trade", "bar"], default="bar", help="Data mode")
    parser.add_argument("--bar-interval", type=int, default=5, help="Bar interval in minutes")
    parser.add_argument("--sizer", choices=["kelly", "fixed_fractional"], default="fixed_fractional")
    parser.add_argument("--fee-bps", type=int, default=0, help="Fee rate in basis points")
    parser.add_argument("--slippage", action="store_true", help="Enable prediction market slippage model")
    parser.add_argument("--spread", type=float, default=0.04, help="Base bid-ask spread for slippage model (default: 0.04)")
    parser.add_argument("--charts", action="store_true", help="Generate charts")
    parser.add_argument("--dry-run", action="store_true", help="Preview config without running")
    args = parser.parse_args()

    register_strategies()

    if args.strategy not in STRATEGY_REGISTRY:
        available = ", ".join(STRATEGY_REGISTRY.keys()) or "(none registered)"
        print(f"Unknown strategy: {args.strategy}. Available: {available}", file=sys.stderr)
        sys.exit(1)

    from src.layer1_research.backtesting.config import BacktestConfig

    fill_model_config = None
    if args.slippage:
        from src.layer1_research.backtesting.execution.fill_model import PredictionMarketFillConfig
        fill_model_config = PredictionMarketFillConfig(base_spread_pct=args.spread)

    config = BacktestConfig(
        catalog_path=args.catalog,
        start=datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc),
        end=datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc),
        strategy_name=args.strategy,
        starting_capital=args.capital,
        data_mode=args.data_mode,
        bar_interval=timedelta(minutes=args.bar_interval) if args.data_mode == "bar" else None,
        fee_rate_bps=args.fee_bps,
        position_sizer=args.sizer,
        generate_charts=args.charts,
        fill_model=fill_model_config,
    )

    if args.dry_run:
        print(f"\nBacktest config preview:")
        print(f"  Strategy:     {config.strategy_name}")
        print(f"  Period:       {config.start.date()} -> {config.end.date()}")
        print(f"  Capital:      ${config.starting_capital:,.2f}")
        print(f"  Data mode:    {config.data_mode}")
        if config.bar_interval:
            print(f"  Bar interval: {config.bar_interval}")
        print(f"  Sizer:        {config.position_sizer}")
        print(f"  Fee rate:     {config.fee_rate_bps} bps")
        print(f"  Slippage:     {'ON (spread={:.0%})'.format(config.fill_model.base_spread_pct) if config.fill_model else 'OFF'}")
        print(f"  Catalog:      {config.catalog_path}")
        return

    from src.layer1_research.backtesting.runner import BacktestRunner
    strategy_class = STRATEGY_REGISTRY[args.strategy]
    runner = BacktestRunner(config)
    runner.run(strategy_class)


if __name__ == "__main__":
    main()
