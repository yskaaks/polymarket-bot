"""ETL orchestrator: DataLoader -> NautilusTrader ParquetDataCatalog."""
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TradeId, Venue
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.layer1_research.backtesting.data.instruments import POLYMARKET_VENUE, clear_pairs, create_instruments
from src.layer1_research.backtesting.data.loaders.base import DataLoader
from src.layer1_research.backtesting.data.models import MarketFilter

# Smallest representable trade size given the instrument's size_precision=1
# (see data/instruments.py). Trades below this round to 0 at precision=1
# and would be rejected by Nautilus's Quantity constructor.
MIN_TRADE_SIZE_PRECISION = 0.05


@dataclass
class CatalogBuildResult:
    markets_loaded: int
    trades_loaded: int
    instruments_created: int
    catalog_path: str


def build_catalog(loader: DataLoader, catalog_path: str,
                  filters: Optional[MarketFilter] = None,
                  limit: Optional[int] = None) -> CatalogBuildResult:
    """Build a NautilusTrader ParquetDataCatalog from a DataLoader."""
    catalog_dir = Path(catalog_path)

    # Clean stale catalog to avoid "already exists, skipping" issues
    if catalog_dir.exists():
        print(f"Removing existing catalog at {catalog_path}")
        shutil.rmtree(catalog_dir)

    catalog_dir.mkdir(parents=True, exist_ok=True)
    clear_pairs()

    print("Loading markets...")
    markets = loader.load_markets(filters=filters)
    if limit is not None:
        print(f"Found {len(markets)} markets, limiting to {limit}")
        markets = markets[:limit]
    else:
        print(f"Found {len(markets)} markets")

    all_instruments = []
    for market in tqdm(markets, desc="Creating instruments"):
        all_instruments.extend(create_instruments(market))

    catalog = ParquetDataCatalog(catalog_path)
    if all_instruments:
        catalog.write_data(all_instruments)
        print(f"Wrote {len(all_instruments)} instruments")

    # Process trades per token
    trade_count = 0
    market_bar = tqdm(markets, desc="Markets", unit="mkt")
    for market in market_bar:
        market_bar.set_postfix_str(market.question[:40])
        for token_id in market.token_ids:
            instrument_id = InstrumentId(symbol=Symbol(token_id), venue=POLYMARKET_VENUE)
            batch = []
            for i, raw_trade in enumerate(loader.get_trades(token_id)):
                if raw_trade.size < MIN_TRADE_SIZE_PRECISION:
                    continue
                ts_ns = int(raw_trade.timestamp.timestamp() * 1e9)
                aggressor_side = AggressorSide.BUYER if raw_trade.side == "BUY" else AggressorSide.SELLER
                tick = TradeTick(
                    instrument_id=instrument_id,
                    price=Price(raw_trade.price, precision=2),
                    size=Quantity(raw_trade.size, precision=1),
                    aggressor_side=aggressor_side,
                    trade_id=TradeId(str(trade_count)),
                    ts_event=ts_ns, ts_init=ts_ns,
                )
                batch.append(tick)
                trade_count += 1
            if batch:
                catalog.write_data(batch)
        market_bar.set_postfix(trades=f"{trade_count:,}")

    market_bar.close()
    print(f"\nTotal: {trade_count:,} trades across {len(markets)} markets")

    return CatalogBuildResult(
        markets_loaded=len(markets), trades_loaded=trade_count,
        instruments_created=len(all_instruments), catalog_path=catalog_path,
    )
