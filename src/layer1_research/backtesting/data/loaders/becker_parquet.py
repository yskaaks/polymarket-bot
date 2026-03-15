"""Data loader for Jon-Becker prediction-market-analysis Parquet files.

Reads chunked Parquet files via DuckDB, normalizes Polymarket trade data
into unified RawTrade and MarketInfo types.

Expected layout:
    data_dir/polymarket/markets/markets_*.parquet
    data_dir/polymarket/trades/trades_*.parquet
    data_dir/polymarket/blocks/blocks_*.parquet
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import duckdb

from src.layer1_research.backtesting.data.loaders.base import DataLoader
from src.layer1_research.backtesting.data.models import MarketFilter, MarketInfo, RawTrade

PRICE_SCALE = 1_000_000.0


class BeckerParquetLoader(DataLoader):
    """Loads Polymarket data from Jon-Becker's prediction-market-analysis repo."""

    def __init__(self, data_dir: str):
        self._data_dir = Path(data_dir)
        if not self._data_dir.exists():
            raise FileNotFoundError(f"Data directory not found: {data_dir}")

        self._poly_dir = self._data_dir / "polymarket"
        self._markets_glob = str(self._poly_dir / "markets" / "*.parquet")
        self._trades_glob = str(self._poly_dir / "trades" / "*.parquet")
        self._blocks_glob = str(self._poly_dir / "blocks" / "*.parquet")

    def load_markets(self, filters: Optional[MarketFilter] = None) -> list[MarketInfo]:
        con = duckdb.connect()
        try:
            query = f"""
                SELECT condition_id, question, outcomes, clob_token_ids,
                       volume, active, closed, end_date, created_at
                FROM read_parquet('{self._markets_glob}')
            """
            conditions = []
            if filters:
                if filters.min_volume is not None:
                    conditions.append(f"volume >= {filters.min_volume}")
                if filters.resolved_only:
                    conditions.append("closed = 1")
                if filters.market_ids is not None:
                    ids = ",".join(f"'{m}'" for m in filters.market_ids)
                    conditions.append(f"condition_id IN ({ids})")
            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            rows = con.execute(query).fetchall()
        finally:
            con.close()

        markets = []
        for row in rows:
            (condition_id, question, outcomes_str, token_ids_str,
             volume, active, closed, end_date_str, created_at_str) = row

            outcomes = json.loads(outcomes_str) if isinstance(outcomes_str, str) else outcomes_str
            token_ids = json.loads(token_ids_str) if isinstance(token_ids_str, str) else token_ids_str
            end_date = self._parse_timestamp(end_date_str) if end_date_str else None
            created_at = self._parse_timestamp(created_at_str) if created_at_str else datetime(2020, 1, 1, tzinfo=timezone.utc)

            markets.append(MarketInfo(
                market_id=condition_id, question=question, outcomes=outcomes,
                token_ids=token_ids, created_at=created_at, end_date=end_date,
                source="polymarket", result=None,
            ))
        return markets

    def get_trades(self, token_id: str, start: Optional[datetime] = None,
                   end: Optional[datetime] = None) -> Iterator[RawTrade]:
        con = duckdb.connect()
        try:
            query = f"""
                SELECT t.asset, t.side, t.size, t.price, t.maker, t.taker,
                       b.timestamp as block_timestamp
                FROM read_parquet('{self._trades_glob}') t
                LEFT JOIN read_parquet('{self._blocks_glob}') b
                    ON t.block_number = b.block_number
                WHERE t.asset = $1
                ORDER BY b.timestamp ASC, t.log_index ASC
            """
            rows = con.execute(query, [token_id]).fetchall()
        finally:
            con.close()

        for row in rows:
            asset, side_int, size, price_int, maker, taker, block_ts = row
            side = "BUY" if side_int == 0 else "SELL"
            price = max(0.001, min(1.0, float(price_int) / PRICE_SCALE))
            timestamp = datetime.fromtimestamp(block_ts, tz=timezone.utc)

            yield RawTrade(
                timestamp=timestamp, market_id=token_id, token_id=asset,
                side=side, price=price, size=size, source="polymarket",
                maker=maker, taker=taker,
            )

    @staticmethod
    def _parse_timestamp(ts_str: str) -> datetime:
        if isinstance(ts_str, datetime):
            return ts_str if ts_str.tzinfo else ts_str.replace(tzinfo=timezone.utc)
        ts_str = ts_str.rstrip("Z")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
