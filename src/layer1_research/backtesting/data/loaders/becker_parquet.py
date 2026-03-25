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
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import duckdb
from tqdm import tqdm

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

        # Persistent connection + materialized trade table (lazy init)
        # Derive DB name from data dir so different sources don't collide
        import hashlib
        dir_hash = hashlib.md5(str(self._data_dir.resolve()).encode()).hexdigest()[:12]
        self._db_path = Path(tempfile.gettempdir()) / f"polymarket_etl_{dir_hash}.duckdb"
        self._con: Optional[duckdb.DuckDBPyConnection] = None
        self._db_ready = False

    def _ensure_trade_db(self):
        """One-time: scan all parquet files, join trades+blocks, materialize into
        an indexed DuckDB table. Subsequent per-token queries are instant."""
        if self._db_ready:
            return

        # Use file-backed DB so DuckDB can spill to disk
        self._con = duckdb.connect(str(self._db_path))

        # Check if table already exists (from a previous run)
        tables = [r[0] for r in self._con.execute("SHOW TABLES").fetchall()]
        if "trades" in tables:
            count = self._con.execute("SELECT count(*) FROM trades").fetchone()[0]
            print(f"Reusing existing trade DB: {count:,} trades ({self._db_path})")
            self._db_ready = True
            return

        print(f"Building trade database (one-time parquet scan)...")
        print(f"  DB location: {self._db_path}")

        self._con.execute(f"""
            CREATE TABLE trades AS
            SELECT
                CASE WHEN t.maker_asset_id = '0' THEN t.taker_asset_id
                     ELSE t.maker_asset_id END as token_id,
                CASE WHEN t.maker_asset_id = '0' THEN 'BUY' ELSE 'SELL' END as side,
                CASE WHEN t.maker_asset_id = '0'
                     THEN t.maker_amount::DOUBLE / t.taker_amount::DOUBLE
                     ELSE t.taker_amount::DOUBLE / t.maker_amount::DOUBLE
                END as price,
                CASE WHEN t.maker_asset_id = '0'
                     THEN t.taker_amount::DOUBLE / {AMOUNT_SCALE}
                     ELSE t.maker_amount::DOUBLE / {AMOUNT_SCALE}
                END as size,
                t.maker, t.taker,
                b.timestamp as block_timestamp
            FROM read_parquet('{self._trades_glob}', union_by_name=true) t
            JOIN read_parquet('{self._blocks_glob}', union_by_name=true) b
                ON t.block_number = b.block_number
            WHERE t.maker_amount > 0 AND t.taker_amount > 0
        """)

        count = self._con.execute("SELECT count(*) FROM trades").fetchone()[0]
        print(f"  Loaded {count:,} trades, creating index...")

        self._con.execute("CREATE INDEX idx_token ON trades(token_id)")
        print(f"  Trade database ready!")
        self._db_ready = True

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
                if filters.date_start is not None:
                    conditions.append(f"created_at >= '{filters.date_start.isoformat()}'")
                if filters.date_end is not None:
                    conditions.append(f"created_at <= '{filters.date_end.isoformat()}'")
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY volume DESC"

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
        """Load trades for a single token. Uses indexed DB for fast lookup."""
        self._ensure_trade_db()

        rows = self._con.execute("""
            SELECT side, price, size, maker, taker, block_timestamp
            FROM trades
            WHERE token_id = $1
            ORDER BY block_timestamp ASC
        """, [token_id]).fetchall()

        for row in rows:
            side, price, size, maker, taker, block_ts = row
            price = max(0.001, min(1.0, price))
            timestamp = self._parse_block_timestamp(block_ts)
            yield RawTrade(
                timestamp=timestamp, market_id=token_id, token_id=token_id,
                side=side, price=price, size=size, source="polymarket",
                maker=maker, taker=taker,
            )

    def get_trades_bulk(self, token_ids: set[str],
                        progress: bool = True) -> Iterator[tuple[str, RawTrade]]:
        """Iterate trades for many tokens using the indexed DB.

        Yields (token_id, RawTrade) pairs, grouped by token_id.
        """
        self._ensure_trade_db()

        tokens = sorted(token_ids)
        it = tqdm(tokens, desc="Querying tokens", unit="tok") if progress else tokens

        for token_id in it:
            rows = self._con.execute("""
                SELECT side, price, size, maker, taker, block_timestamp
                FROM trades
                WHERE token_id = $1
                ORDER BY block_timestamp ASC
            """, [token_id]).fetchall()

            for row in rows:
                side, price, size, maker, taker, block_ts = row
                price = max(0.001, min(1.0, price))
                timestamp = self._parse_block_timestamp(block_ts)
                yield (token_id, RawTrade(
                    timestamp=timestamp, market_id=token_id, token_id=token_id,
                    side=side, price=price, size=size, source="polymarket",
                    maker=maker, taker=taker,
                ))

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
