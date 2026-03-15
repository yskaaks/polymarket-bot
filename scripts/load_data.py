"""One-time ETL: load external data into NautilusTrader ParquetDataCatalog.

Usage:
    python scripts/load_data.py --source becker --path ../prediction-market-analysis/data
    python scripts/load_data.py --source becker --path ../prediction-market-analysis/data --min-volume 100000
    python scripts/load_data.py --source becker --path ../prediction-market-analysis/data --dry-run
"""
import argparse
import sys
import time


def main():
    parser = argparse.ArgumentParser(description="Load prediction market data into backtesting catalog")
    parser.add_argument("--source", required=True, choices=["becker"], help="Data source format")
    parser.add_argument("--path", required=True, help="Path to source data directory")
    parser.add_argument("--catalog", default="data/catalog", help="Output catalog directory (default: data/catalog)")
    parser.add_argument("--min-volume", type=float, default=None, help="Minimum market volume filter")
    parser.add_argument("--resolved-only", action="store_true", help="Only load resolved markets")
    parser.add_argument("--dry-run", action="store_true", help="Preview markets without loading trades")
    args = parser.parse_args()

    from src.layer1_research.backtesting.data.models import MarketFilter
    filters = MarketFilter(min_volume=args.min_volume, resolved_only=args.resolved_only)

    if args.source == "becker":
        from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
        loader = BeckerParquetLoader(args.path)
    else:
        print(f"Unknown source: {args.source}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        markets = loader.load_markets(filters=filters)
        print(f"\nFound {len(markets)} markets matching filters:\n")
        for m in markets[:20]:
            print(f"  {m.market_id[:16]}  {m.question[:60]}")
        if len(markets) > 20:
            print(f"  ... and {len(markets) - 20} more")
        return

    from src.layer1_research.backtesting.data.catalog import build_catalog
    print(f"Loading data from {args.path} -> {args.catalog}")
    start = time.time()
    result = build_catalog(loader, args.catalog, filters=filters)
    elapsed = time.time() - start
    print(f"\nCatalog built in {elapsed:.1f}s:")
    print(f"  Markets:     {result.markets_loaded}")
    print(f"  Trades:      {result.trades_loaded:,}")
    print(f"  Instruments: {result.instruments_created}")
    print(f"  Path:        {result.catalog_path}")


if __name__ == "__main__":
    main()
