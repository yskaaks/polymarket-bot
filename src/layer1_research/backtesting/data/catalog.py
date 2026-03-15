"""ETL orchestrator: DataLoader -> NautilusTrader ParquetDataCatalog."""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TradeId, Venue
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.layer1_research.backtesting.data.instruments import POLYMARKET_VENUE, clear_pairs, create_instruments
from src.layer1_research.backtesting.data.loaders.base import DataLoader
from src.layer1_research.backtesting.data.models import MarketFilter


@dataclass
class CatalogBuildResult:
    markets_loaded: int
    trades_loaded: int
    instruments_created: int
    catalog_path: str


def build_catalog(loader: DataLoader, catalog_path: str,
                  filters: Optional[MarketFilter] = None) -> CatalogBuildResult:
    """Build a NautilusTrader ParquetDataCatalog from a DataLoader."""
    Path(catalog_path).mkdir(parents=True, exist_ok=True)
    clear_pairs()

    markets = loader.load_markets(filters=filters)
    all_instruments = []
    for market in markets:
        all_instruments.extend(create_instruments(market))

    catalog = ParquetDataCatalog(catalog_path)
    if all_instruments:
        catalog.write_data(all_instruments)

    trade_count = 0
    for market in markets:
        for token_id in market.token_ids:
            instrument_id = InstrumentId(symbol=Symbol(token_id), venue=POLYMARKET_VENUE)
            batch = []
            for i, raw_trade in enumerate(loader.get_trades(token_id)):
                ts_ns = int(raw_trade.timestamp.timestamp() * 1e9)
                aggressor_side = AggressorSide.BUYER if raw_trade.side == "BUY" else AggressorSide.SELLER
                tick = TradeTick(
                    instrument_id=instrument_id,
                    price=Price(raw_trade.price, precision=2),
                    size=Quantity(raw_trade.size, precision=1),
                    aggressor_side=aggressor_side,
                    trade_id=TradeId(f"{token_id}_{i}"),
                    ts_event=ts_ns, ts_init=ts_ns,
                )
                batch.append(tick)
                trade_count += 1
            if batch:
                catalog.write_data(batch)

    return CatalogBuildResult(
        markets_loaded=len(markets), trades_loaded=trade_count,
        instruments_created=len(all_instruments), catalog_path=catalog_path,
    )
