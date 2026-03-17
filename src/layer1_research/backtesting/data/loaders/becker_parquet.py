"""Data loader for Jon-Becker prediction-market-analysis Parquet files.

Reads chunked Parquet files via DuckDB, normalizes Polymarket trade data
into unified RawTrade and MarketInfo types.

Expected layout:
    data_dir/polymarket/markets/markets_*.parquet
    data_dir/polymarket/trades/trades_*.parquet
    data_dir/polymarket/blocks/blocks_*.parquet

Trade schema (from CTF Exchange logs):
    maker_asset_id: "0" = USDC (maker buying tokens) or token ID (maker selling)
    taker_asset_id: token ID (when maker buys) or "0" = USDC (when maker sells)
    maker_amount / taker_amount: raw amounts (6 decimal scaling)
    fee: raw fee amount
    block_number: joined to blocks table for timestamp
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import duckdb

from src.layer1_research.backtesting.data.loaders.base import DataLoader
from src.layer1_research.backtesting.data.models import MarketFilter, MarketInfo, RawTrade

# Both USDC and CTF tokens use 6 decimal places
AMOUNT_SCALE = 1_000_000.0


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
                FROM read_parquet('{self._markets_glob}', union_by_name=true)
            """
            conditions = []
            if filters:
                if filters.min_volume is not None:
                    conditions.append(f"volume >= {filters.min_volume}")
                if filters.resolved_only:
                    conditions.append("closed = true")
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
             volume, active, closed, end_date_val, created_at_val) = row

            outcomes = json.loads(outcomes_str) if isinstance(outcomes_str, str) else outcomes_str
            token_ids = json.loads(token_ids_str) if isinstance(token_ids_str, str) else token_ids_str
            end_date = self._normalize_timestamp(end_date_val) if end_date_val else None
            created_at = self._normalize_timestamp(created_at_val) if created_at_val else datetime(2020, 1, 1, tzinfo=timezone.utc)

            markets.append(MarketInfo(
                market_id=condition_id, question=question, outcomes=outcomes,
                token_ids=token_ids, created_at=created_at, end_date=end_date,
                source="polymarket", result=None,
            ))
        return markets

    def get_trades(self, token_id: str, start: Optional[datetime] = None,
                   end: Optional[datetime] = None) -> Iterator[RawTrade]:
        """Load trades for a specific token ID.

        In the CTF Exchange, each trade has a maker and taker side:
        - maker_asset_id = "0" means maker pays USDC → maker is BUYING the token
        - taker_asset_id = "0" means taker pays USDC → maker is SELLING the token

        The token_id can appear as either maker_asset_id or taker_asset_id.
        """
        con = duckdb.connect()
        try:
            query = f"""
                SELECT t.maker_asset_id, t.taker_asset_id,
                       t.maker_amount, t.taker_amount, t.fee,
                       t.maker, t.taker,
                       b.timestamp as block_timestamp
                FROM read_parquet('{self._trades_glob}', union_by_name=true) t
                LEFT JOIN read_parquet('{self._blocks_glob}', union_by_name=true) b
                    ON t.block_number = b.block_number
                WHERE t.maker_asset_id = $1 OR t.taker_asset_id = $1
                ORDER BY b.timestamp ASC, t.log_index ASC
            """
            rows = con.execute(query, [token_id]).fetchall()
        finally:
            con.close()

        for row in rows:
            (maker_asset_id, taker_asset_id, maker_amount, taker_amount,
             fee, maker, taker, block_ts) = row

            if block_ts is None:
                continue

            # Determine side and price from which side holds the token
            if maker_asset_id == "0":
                # Maker pays USDC, taker pays tokens → BUY
                side = "BUY"
                usdc_amount = maker_amount
                token_amount = taker_amount
            else:
                # Maker pays tokens, taker pays USDC → SELL
                side = "SELL"
                usdc_amount = taker_amount
                token_amount = maker_amount

            if token_amount == 0:
                continue

            price = float(usdc_amount) / float(token_amount)
            price = max(0.001, min(1.0, price))
            size = float(token_amount) / AMOUNT_SCALE

            # Parse block timestamp (VARCHAR in blocks table)
            timestamp = self._parse_block_timestamp(block_ts)

            yield RawTrade(
                timestamp=timestamp, market_id=token_id, token_id=token_id,
                side=side, price=price, size=size, source="polymarket",
                maker=maker, taker=taker,
            )

    @staticmethod
    def _parse_block_timestamp(ts) -> datetime:
        """Parse timestamp from blocks table (can be VARCHAR or int)."""
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        if isinstance(ts, datetime):
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        if isinstance(ts, str):
            try:
                return datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except ValueError:
                ts = ts.rstrip("Z")
                dt = datetime.fromisoformat(ts)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        raise ValueError(f"Cannot parse block timestamp: {ts!r}")

    @staticmethod
    def _normalize_timestamp(val) -> datetime:
        """Normalize a timestamp value from DuckDB (datetime or string)."""
        if isinstance(val, datetime):
            return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
        if isinstance(val, str):
            val = val.rstrip("Z")
            dt = datetime.fromisoformat(val)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        raise ValueError(f"Cannot parse timestamp: {val!r}")
