# Backtesting Engine Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a NautilusTrader-based backtesting engine for signal-based directional strategies on Polymarket prediction markets.

**Architecture:** Data flows through a pluggable loader layer (Parquet/S3/CSV → unified Nautilus types), into NautilusTrader's `BacktestEngine` which replays events through `PredictionMarketStrategy` subclasses. Strategies emit `Signal` objects that drive simulated order execution. Results flow to a reporting layer for CLI summaries and charts.

**Tech Stack:** NautilusTrader (backtesting engine), DuckDB (Parquet querying during ETL), matplotlib (charts), pytest (testing)

**Spec:** `docs/superpowers/specs/2026-03-11-backtesting-engine-design.md`

---

## Chunk 1: Project Setup & Data Models

### Task 1: Project Setup — Dependencies and Directory Structure

**Files:**
- Modify: `pyproject.toml:1-12`
- Modify: `.gitignore`
- Create: `src/layer1_research/backtesting/__init__.py`
- Create: `src/layer1_research/backtesting/data/__init__.py`
- Create: `src/layer1_research/backtesting/data/loaders/__init__.py`
- Create: `src/layer1_research/backtesting/strategies/__init__.py`
- Create: `src/layer1_research/backtesting/strategies/examples/__init__.py`
- Create: `src/layer1_research/backtesting/execution/__init__.py`
- Create: `src/layer1_research/backtesting/reporting/__init__.py`

- [ ] **Step 1: Add optional backtesting dependencies to pyproject.toml**

Add this section after the existing `dependencies` list in `pyproject.toml`:

```toml
[project.optional-dependencies]
backtesting = [
    "nautilus_trader>=1.207.0,<1.230.0",
    "duckdb>=1.0.0",
    "matplotlib>=3.8.0",
]
```

- [ ] **Step 2: Add data/catalog/ to .gitignore**

Append to `.gitignore`:

```
# Backtesting data catalog
data/catalog/
output/backtests/
```

- [ ] **Step 3: Create the directory structure**

```bash
mkdir -p src/layer1_research/backtesting/{data/loaders,strategies/examples,execution,reporting}
touch src/layer1_research/backtesting/__init__.py
touch src/layer1_research/backtesting/data/__init__.py
touch src/layer1_research/backtesting/data/loaders/__init__.py
touch src/layer1_research/backtesting/strategies/__init__.py
touch src/layer1_research/backtesting/strategies/examples/__init__.py
touch src/layer1_research/backtesting/execution/__init__.py
touch src/layer1_research/backtesting/reporting/__init__.py
mkdir -p tests/backtesting/fixtures
touch tests/backtesting/__init__.py
touch tests/backtesting/fixtures/__init__.py
```

- [ ] **Step 4: Install backtesting dependencies**

```bash
pip install -e ".[backtesting]"
```

- [ ] **Step 5: Verify NautilusTrader imports work**

```bash
python -c "from nautilus_trader.model.instruments import BinaryOption; print('OK')"
python -c "import duckdb; print('OK')"
```

- [ ] **Step 6: Add conftest.py to clear global state between tests**

```python
# tests/backtesting/conftest.py
"""Shared fixtures for backtesting tests."""
import pytest


@pytest.fixture(autouse=True)
def clear_instrument_pairs():
    """Clear the instrument pair registry between tests to prevent state leakage."""
    yield
    try:
        from src.layer1_research.backtesting.data.instruments import clear_pairs
        clear_pairs()
    except ImportError:
        pass
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore src/layer1_research/ tests/backtesting/
git commit -m "feat: scaffold backtesting engine directory structure and dependencies"
```

---

### Task 2: Data Models — Unified Trade and Market Types

These are the normalized data models that all loaders produce. They are lightweight dataclasses used only during ETL — once written to the Nautilus `ParquetDataCatalog`, the engine uses native Nautilus types (`TradeTick`, `BinaryOption`, `Bar`).

**Files:**
- Create: `src/layer1_research/backtesting/data/models.py`
- Create: `tests/backtesting/test_data_models.py`

- [ ] **Step 1: Write failing tests for data models**

```python
# tests/backtesting/test_data_models.py
"""Tests for backtesting data models."""
import pytest
from datetime import datetime, timezone


def test_raw_trade_creation():
    from src.layer1_research.backtesting.data.models import RawTrade

    trade = RawTrade(
        timestamp=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        market_id="condition_abc123",
        token_id="token_yes_123",
        side="BUY",
        price=0.65,
        size=100.0,
        source="polymarket",
    )
    assert trade.price == 0.65
    assert trade.side == "BUY"
    assert trade.source == "polymarket"
    assert trade.maker is None
    assert trade.taker is None


def test_raw_trade_rejects_invalid_side():
    from src.layer1_research.backtesting.data.models import RawTrade

    with pytest.raises(ValueError, match="side must be BUY or SELL"):
        RawTrade(
            timestamp=datetime(2024, 6, 15, tzinfo=timezone.utc),
            market_id="abc",
            token_id="tok",
            side="HOLD",
            price=0.5,
            size=10.0,
            source="polymarket",
        )


def test_raw_trade_rejects_invalid_price():
    from src.layer1_research.backtesting.data.models import RawTrade

    with pytest.raises(ValueError, match="price must be between 0 and 1"):
        RawTrade(
            timestamp=datetime(2024, 6, 15, tzinfo=timezone.utc),
            market_id="abc",
            token_id="tok",
            side="BUY",
            price=1.5,
            size=10.0,
            source="polymarket",
        )


def test_market_info_creation():
    from src.layer1_research.backtesting.data.models import MarketInfo

    market = MarketInfo(
        market_id="condition_abc123",
        question="Will BTC hit $100k by Dec 2024?",
        outcomes=["Yes", "No"],
        token_ids=["token_yes_123", "token_no_456"],
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        source="polymarket",
        result=None,
    )
    assert market.question == "Will BTC hit $100k by Dec 2024?"
    assert len(market.token_ids) == 2
    assert market.result is None


def test_market_info_with_result():
    from src.layer1_research.backtesting.data.models import MarketInfo

    market = MarketInfo(
        market_id="condition_abc123",
        question="Will BTC hit $100k?",
        outcomes=["Yes", "No"],
        token_ids=["tok_y", "tok_n"],
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        source="polymarket",
        result="Yes",
    )
    assert market.result == "Yes"


def test_market_filter_defaults():
    from src.layer1_research.backtesting.data.models import MarketFilter

    f = MarketFilter()
    assert f.min_volume is None
    assert f.min_trades is None
    assert f.resolved_only is False
    assert f.sources is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/backtesting/test_data_models.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement data models**

```python
# src/layer1_research/backtesting/data/models.py
"""Unified data models for backtesting ETL pipeline.

These models normalize data from various sources (Parquet, S3, CSV)
into a common format for loading into NautilusTrader's ParquetDataCatalog.
They are used only during ETL — at runtime the engine uses native Nautilus types.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class RawTrade:
    """A single trade normalized from any data source."""

    timestamp: datetime
    market_id: str
    token_id: str
    side: str  # "BUY" or "SELL"
    price: float
    size: float
    source: str  # "polymarket" or "kalshi"
    maker: Optional[str] = None
    taker: Optional[str] = None

    def __post_init__(self):
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got '{self.side}'")
        if not (0.0 < self.price <= 1.0):
            raise ValueError(
                f"price must be between 0 (exclusive) and 1 (inclusive), got {self.price}"
            )
        if self.size <= 0:
            raise ValueError(f"size must be positive, got {self.size}")


@dataclass(frozen=True)
class MarketInfo:
    """Static metadata for a prediction market."""

    market_id: str
    question: str
    outcomes: list[str]
    token_ids: list[str]
    created_at: datetime
    end_date: Optional[datetime]
    source: str  # "polymarket" or "kalshi"
    result: Optional[str] = None  # resolved outcome, None if unresolved


@dataclass
class MarketFilter:
    """Criteria for selecting which markets to load."""

    min_volume: Optional[float] = None
    min_trades: Optional[int] = None
    date_start: Optional[datetime] = None
    date_end: Optional[datetime] = None
    resolved_only: bool = False
    sources: Optional[list[str]] = None  # ["polymarket", "kalshi"]
    market_ids: Optional[list[str]] = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/backtesting/test_data_models.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/data/models.py tests/backtesting/test_data_models.py
git commit -m "feat: add unified data models for backtesting ETL (RawTrade, MarketInfo, MarketFilter)"
```

---

### Task 3: Data Loader Base Class

**Files:**
- Create: `src/layer1_research/backtesting/data/loaders/base.py`
- Create: `tests/backtesting/test_data_loader_base.py`

- [ ] **Step 1: Write failing test**

```python
# tests/backtesting/test_data_loader_base.py
"""Tests for DataLoader ABC."""
import pytest
from datetime import datetime, timezone


def test_data_loader_is_abstract():
    from src.layer1_research.backtesting.data.loaders.base import DataLoader

    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        DataLoader()


def test_data_loader_concrete_implementation():
    from src.layer1_research.backtesting.data.loaders.base import DataLoader
    from src.layer1_research.backtesting.data.models import MarketInfo, RawTrade, MarketFilter

    class FakeLoader(DataLoader):
        def load_markets(self, filters=None):
            return []

        def get_trades(self, market_id, start=None, end=None):
            yield from []

    loader = FakeLoader()
    assert loader.load_markets() == []
    assert list(loader.get_trades("abc")) == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/backtesting/test_data_loader_base.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement DataLoader ABC**

```python
# src/layer1_research/backtesting/data/loaders/base.py
"""Abstract base class for data loaders."""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterator, Optional

from src.layer1_research.backtesting.data.models import MarketFilter, MarketInfo, RawTrade


class DataLoader(ABC):
    """Base class for loading prediction market data from external sources.

    Implementations normalize source-specific formats into unified
    RawTrade and MarketInfo types for ETL into NautilusTrader's catalog.
    """

    @abstractmethod
    def load_markets(
        self, filters: Optional[MarketFilter] = None
    ) -> list[MarketInfo]:
        """Load market metadata, optionally filtered."""
        ...

    @abstractmethod
    def get_trades(
        self,
        token_id: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Iterator[RawTrade]:
        """Yield trades for a specific token ID within a date range.

        Note: token_id is the CLOB token ID (e.g., "tok_yes_001"),
        not the market condition_id. Each YES/NO outcome has its own token.
        """
        ...
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/backtesting/test_data_loader_base.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/data/loaders/base.py tests/backtesting/test_data_loader_base.py
git commit -m "feat: add DataLoader ABC for pluggable data sources"
```

---

## Chunk 2: Becker Parquet Loader & Instrument Factory

### Task 4: Becker Parquet Loader

This loader reads the Jon-Becker `prediction-market-analysis` repo Parquet files via DuckDB and produces `RawTrade` and `MarketInfo` objects.

**Context:** The Becker repo stores data as chunked Parquet files:
- `data/polymarket/markets/markets_{start}_{end}.parquet` — columns include `id`, `condition_id`, `question`, `outcomes`, `outcome_prices`, `clob_token_ids`, `volume`, `active`, `closed`, `end_date`, `created_at`
- `data/polymarket/trades/trades_{start}_{end}.parquet` — columns include `block_number`, `transaction_hash`, `maker`, `taker`, `asset`, `side` (0=BUY, 1=SELL), `size`, `price`, `outcome`, `outcome_index`
- `data/polymarket/blocks/blocks_{start}_{end}.parquet` — columns include `block_number`, `timestamp` (for mapping block numbers to timestamps)

**Files:**
- Create: `src/layer1_research/backtesting/data/loaders/becker_parquet.py`
- Create: `tests/backtesting/test_becker_loader.py`
- Create: `tests/backtesting/fixtures/sample_data.py`

- [ ] **Step 1: Create test fixtures with synthetic Parquet data**

```python
# tests/backtesting/fixtures/sample_data.py
"""Synthetic data fixtures for testing data loaders."""
import tempfile
import os
from pathlib import Path

# Defer imports so tests fail clearly if duckdb missing
def create_becker_fixture_dir() -> str:
    """Create a temporary directory mimicking the Becker repo data layout.

    Returns the path to the temp directory.
    """
    import duckdb

    tmpdir = tempfile.mkdtemp(prefix="becker_test_")

    # Create directory structure
    os.makedirs(f"{tmpdir}/polymarket/markets", exist_ok=True)
    os.makedirs(f"{tmpdir}/polymarket/trades", exist_ok=True)
    os.makedirs(f"{tmpdir}/polymarket/blocks", exist_ok=True)

    con = duckdb.connect()

    # Markets
    con.execute(f"""
        COPY (
            SELECT
                'cond_001' as condition_id,
                'Will BTC hit 100k?' as question,
                'btc-100k' as slug,
                '["Yes","No"]' as outcomes,
                '[0.65, 0.35]' as outcome_prices,
                '["tok_yes_001","tok_no_001"]' as clob_token_ids,
                500000.0 as volume,
                1 as active,
                0 as closed,
                '2024-12-31T00:00:00Z' as end_date,
                '2024-01-01T00:00:00Z' as created_at
            UNION ALL
            SELECT
                'cond_002',
                'Will ETH hit 5k?',
                'eth-5k',
                '["Yes","No"]',
                '[0.30, 0.70]',
                '["tok_yes_002","tok_no_002"]',
                100000.0,
                0,
                1,
                '2024-06-30T00:00:00Z',
                '2024-01-15T00:00:00Z'
        ) TO '{tmpdir}/polymarket/markets/markets_0_1.parquet' (FORMAT PARQUET)
    """)

    # Trades
    con.execute(f"""
        COPY (
            SELECT
                50000000 as block_number,
                'tx_aaa' as transaction_hash,
                0 as log_index,
                '0xmaker1' as maker,
                '0xtaker1' as taker,
                'tok_yes_001' as asset,
                0 as side,
                100.0 as size,
                650000.0 as price,
                'Yes' as outcome,
                0 as outcome_index
            UNION ALL
            SELECT
                50000100,
                'tx_bbb',
                0,
                '0xmaker2',
                '0xtaker2',
                'tok_yes_001',
                1,
                50.0,
                680000.0,
                'Yes',
                0
            UNION ALL
            SELECT
                50000200,
                'tx_ccc',
                0,
                '0xmaker3',
                '0xtaker3',
                'tok_no_001',
                0,
                75.0,
                350000.0,
                'No',
                1
        ) TO '{tmpdir}/polymarket/trades/trades_0_1.parquet' (FORMAT PARQUET)
    """)

    # Blocks (for timestamp mapping)
    con.execute(f"""
        COPY (
            SELECT 50000000 as block_number, 1718448000 as timestamp
            UNION ALL
            SELECT 50000100, 1718448200
            UNION ALL
            SELECT 50000200, 1718448400
        ) TO '{tmpdir}/polymarket/blocks/blocks_0_1.parquet' (FORMAT PARQUET)
    """)

    con.close()
    return tmpdir
```

- [ ] **Step 2: Write failing tests for the loader**

```python
# tests/backtesting/test_becker_loader.py
"""Tests for BeckerParquetLoader."""
import pytest
import shutil
from datetime import datetime, timezone

from tests.backtesting.fixtures.sample_data import create_becker_fixture_dir


@pytest.fixture
def becker_data_dir():
    d = create_becker_fixture_dir()
    yield d
    shutil.rmtree(d)


def test_load_markets(becker_data_dir):
    from src.layer1_research.backtesting.data.loaders.becker_parquet import (
        BeckerParquetLoader,
    )

    loader = BeckerParquetLoader(becker_data_dir)
    markets = loader.load_markets()

    assert len(markets) == 2
    btc_market = next(m for m in markets if "BTC" in m.question)
    assert btc_market.market_id == "cond_001"
    assert btc_market.outcomes == ["Yes", "No"]
    assert btc_market.token_ids == ["tok_yes_001", "tok_no_001"]
    assert btc_market.source == "polymarket"


def test_load_markets_with_filter(becker_data_dir):
    from src.layer1_research.backtesting.data.loaders.becker_parquet import (
        BeckerParquetLoader,
    )
    from src.layer1_research.backtesting.data.models import MarketFilter

    loader = BeckerParquetLoader(becker_data_dir)
    markets = loader.load_markets(
        filters=MarketFilter(min_volume=200_000)
    )

    assert len(markets) == 1
    assert markets[0].market_id == "cond_001"


def test_get_trades(becker_data_dir):
    from src.layer1_research.backtesting.data.loaders.becker_parquet import (
        BeckerParquetLoader,
    )

    loader = BeckerParquetLoader(becker_data_dir)
    trades = list(loader.get_trades("tok_yes_001"))

    assert len(trades) == 2
    assert trades[0].side == "BUY"
    assert trades[0].price == pytest.approx(0.65, abs=0.01)
    assert trades[0].size == 100.0
    assert trades[0].source == "polymarket"
    assert trades[0].maker == "0xmaker1"
    assert trades[1].side == "SELL"


def test_get_trades_no_token(becker_data_dir):
    from src.layer1_research.backtesting.data.loaders.becker_parquet import (
        BeckerParquetLoader,
    )

    loader = BeckerParquetLoader(becker_data_dir)
    trades = list(loader.get_trades("nonexistent_token"))
    assert trades == []


def test_invalid_data_dir():
    from src.layer1_research.backtesting.data.loaders.becker_parquet import (
        BeckerParquetLoader,
    )

    with pytest.raises(FileNotFoundError):
        BeckerParquetLoader("/nonexistent/path")
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/backtesting/test_becker_loader.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement BeckerParquetLoader**

```python
# src/layer1_research/backtesting/data/loaders/becker_parquet.py
"""Data loader for Jon-Becker prediction-market-analysis Parquet files.

Reads chunked Parquet files via DuckDB, normalizes Polymarket trade data
(block-number-based timestamps, integer-encoded sides, scaled prices)
into unified RawTrade and MarketInfo types.

Expected directory layout:
    data_dir/
    ├── polymarket/
    │   ├── markets/markets_*.parquet
    │   ├── trades/trades_*.parquet
    │   └── blocks/blocks_*.parquet
    └── kalshi/  (future support)
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import duckdb

from src.layer1_research.backtesting.data.loaders.base import DataLoader
from src.layer1_research.backtesting.data.models import MarketFilter, MarketInfo, RawTrade

# Becker repo stores prices as integers (e.g., 650000 = 0.65)
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

    def load_markets(
        self, filters: Optional[MarketFilter] = None
    ) -> list[MarketInfo]:
        con = duckdb.connect()
        try:
            query = f"""
                SELECT
                    condition_id,
                    question,
                    outcomes,
                    clob_token_ids,
                    volume,
                    active,
                    closed,
                    end_date,
                    created_at
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
            (
                condition_id, question, outcomes_str, token_ids_str,
                volume, active, closed, end_date_str, created_at_str,
            ) = row

            outcomes = json.loads(outcomes_str) if isinstance(outcomes_str, str) else outcomes_str
            token_ids = json.loads(token_ids_str) if isinstance(token_ids_str, str) else token_ids_str

            end_date = self._parse_timestamp(end_date_str) if end_date_str else None
            created_at = self._parse_timestamp(created_at_str) if created_at_str else datetime(2020, 1, 1, tzinfo=timezone.utc)

            result = None
            if closed:
                # For resolved markets, we could determine the result
                # from settlement data. For now, leave as None.
                pass

            markets.append(
                MarketInfo(
                    market_id=condition_id,
                    question=question,
                    outcomes=outcomes,
                    token_ids=token_ids,
                    created_at=created_at,
                    end_date=end_date,
                    source="polymarket",
                    result=result,
                )
            )

        return markets

    def get_trades(
        self,
        token_id: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Iterator[RawTrade]:
        """Yield trades for a specific token_id.

        Uses parameterized queries to avoid SQL injection.
        """
        con = duckdb.connect()
        try:
            # Join trades with blocks to get timestamps
            query = f"""
                SELECT
                    t.asset,
                    t.side,
                    t.size,
                    t.price,
                    t.maker,
                    t.taker,
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
            price = price_int / PRICE_SCALE

            # Clamp price to valid range
            price = max(0.001, min(1.0, price))

            timestamp = datetime.fromtimestamp(block_ts, tz=timezone.utc)

            yield RawTrade(
                timestamp=timestamp,
                market_id=token_id,
                token_id=asset,
                side=side,
                price=price,
                size=size,
                source="polymarket",
                maker=maker,
                taker=taker,
            )

    @staticmethod
    def _parse_timestamp(ts_str: str) -> datetime:
        """Parse ISO timestamp string to datetime."""
        if isinstance(ts_str, datetime):
            return ts_str if ts_str.tzinfo else ts_str.replace(tzinfo=timezone.utc)
        ts_str = ts_str.rstrip("Z")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/backtesting/test_becker_loader.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/layer1_research/backtesting/data/loaders/becker_parquet.py tests/backtesting/test_becker_loader.py tests/backtesting/fixtures/sample_data.py
git commit -m "feat: add BeckerParquetLoader for prediction-market-analysis repo data"
```

---

### Task 5: BinaryOption Instrument Factory

Builds NautilusTrader `BinaryOption` instruments from `MarketInfo` objects.

**Files:**
- Create: `src/layer1_research/backtesting/data/instruments.py`
- Create: `tests/backtesting/test_instruments.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/backtesting/test_instruments.py
"""Tests for BinaryOption instrument factory."""
import pytest
from datetime import datetime, timezone


def test_create_instruments_from_market():
    from src.layer1_research.backtesting.data.instruments import (
        create_instruments,
    )
    from src.layer1_research.backtesting.data.models import MarketInfo

    market = MarketInfo(
        market_id="cond_001",
        question="Will BTC hit 100k?",
        outcomes=["Yes", "No"],
        token_ids=["tok_yes_001", "tok_no_001"],
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        source="polymarket",
    )

    instruments = create_instruments(market)

    # Should create one BinaryOption per outcome
    assert len(instruments) == 2
    # Instruments should have correct price precision (2 decimal places for Polymarket)
    for inst in instruments:
        assert inst.price_precision == 2


def test_create_instruments_maps_token_ids():
    from src.layer1_research.backtesting.data.instruments import (
        create_instruments,
    )
    from src.layer1_research.backtesting.data.models import MarketInfo

    market = MarketInfo(
        market_id="cond_001",
        question="Will BTC hit 100k?",
        outcomes=["Yes", "No"],
        token_ids=["tok_yes_001", "tok_no_001"],
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        source="polymarket",
    )

    instruments = create_instruments(market)
    instrument_ids = [str(inst.id) for inst in instruments]

    # Each instrument should be identifiable by token_id
    assert any("tok_yes_001" in iid for iid in instrument_ids)
    assert any("tok_no_001" in iid for iid in instrument_ids)


def test_get_token_pair():
    from src.layer1_research.backtesting.data.instruments import (
        create_instruments,
        get_paired_token_id,
    )
    from src.layer1_research.backtesting.data.models import MarketInfo

    market = MarketInfo(
        market_id="cond_001",
        question="Test?",
        outcomes=["Yes", "No"],
        token_ids=["tok_yes", "tok_no"],
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        source="polymarket",
    )

    create_instruments(market)  # registers the pair

    assert get_paired_token_id("tok_yes") == "tok_no"
    assert get_paired_token_id("tok_no") == "tok_yes"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/backtesting/test_instruments.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement instrument factory**

```python
# src/layer1_research/backtesting/data/instruments.py
"""Factory for building NautilusTrader BinaryOption instruments from MarketInfo.

Polymarket markets have two outcomes (YES/NO), each with a distinct token ID.
This module creates a BinaryOption instrument per token and tracks the
YES/NO pairing so strategies can look up the counterpart.
"""
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AssetClass
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import BinaryOption
from nautilus_trader.model.objects import Price, Quantity

from src.layer1_research.backtesting.data.models import MarketInfo

POLYMARKET_VENUE = Venue("POLYMARKET")

# Module-level registry for YES/NO token pairs
_token_pairs: dict[str, str] = {}


def create_instruments(market: MarketInfo) -> list[BinaryOption]:
    """Create BinaryOption instruments for each outcome in a market.

    Also registers the YES/NO token pair for later lookup.

    Returns one BinaryOption per outcome (typically 2: YES and NO).

    Note: Uses USD as settlement currency. NautilusTrader's own Polymarket
    adapter uses USDC.e, but for backtesting purposes USD is sufficient
    since we only track P&L in dollar terms. If live trading integration
    is needed, switch to a custom USDC currency.
    """
    instruments = []

    # Register the pair
    if len(market.token_ids) == 2:
        _token_pairs[market.token_ids[0]] = market.token_ids[1]
        _token_pairs[market.token_ids[1]] = market.token_ids[0]

    for i, (outcome, token_id) in enumerate(
        zip(market.outcomes, market.token_ids)
    ):
        instrument_id = InstrumentId(
            symbol=Symbol(token_id),
            venue=POLYMARKET_VENUE,
        )

        # BinaryOption requires price_increment and size_increment.
        # Polymarket uses 2 decimal places for price (0.01 ticks)
        # and 1 decimal place for size.
        instrument = BinaryOption(
            instrument_id=instrument_id,
            raw_symbol=Symbol(token_id),
            asset_class=AssetClass.ALTERNATIVE,
            currency=USD,
            activation_ns=int(market.created_at.timestamp() * 1e9),
            expiration_ns=int(market.end_date.timestamp() * 1e9) if market.end_date else 0,
            price_precision=2,
            size_precision=1,
            price_increment=Price.from_str("0.01"),
            size_increment=Quantity.from_str("0.1"),
            maker_fee=0,
            taker_fee=0,
            outcome=outcome,
            ts_event=0,
            ts_init=0,
        )
        instruments.append(instrument)

    return instruments


def get_paired_token_id(token_id: str) -> str:
    """Get the paired token ID (YES→NO or NO→YES).

    Raises KeyError if the token pair has not been registered
    via create_instruments().
    """
    return _token_pairs[token_id]


def clear_pairs():
    """Clear the token pair registry. Used in tests."""
    _token_pairs.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/backtesting/test_instruments.py -v
```

Expected: all PASS

Note: The exact `BinaryOption` constructor may need adjustment based on the installed NautilusTrader version. The key fields are `instrument_id`, `currency`, `price_precision`, `size_precision`, and `outcome`. Consult `nautilus_trader.model.instruments.BinaryOption` if the constructor signature differs.

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/data/instruments.py tests/backtesting/test_instruments.py
git commit -m "feat: add BinaryOption instrument factory with YES/NO pair tracking"
```

---

### Task 6: Catalog Builder (ETL Orchestrator)

Takes a `DataLoader`, runs it, writes output to NautilusTrader's `ParquetDataCatalog`.

**Files:**
- Create: `src/layer1_research/backtesting/data/catalog.py`
- Create: `tests/backtesting/test_catalog.py`

- [ ] **Step 1: Write failing test**

```python
# tests/backtesting/test_catalog.py
"""Tests for catalog builder (ETL orchestrator)."""
import pytest
import shutil
import tempfile
from pathlib import Path

from tests.backtesting.fixtures.sample_data import create_becker_fixture_dir


@pytest.fixture
def becker_data_dir():
    d = create_becker_fixture_dir()
    yield d
    shutil.rmtree(d)


@pytest.fixture
def catalog_dir():
    d = tempfile.mkdtemp(prefix="catalog_test_")
    yield d
    shutil.rmtree(d)


def test_build_catalog_creates_output(becker_data_dir, catalog_dir):
    from src.layer1_research.backtesting.data.catalog import build_catalog
    from src.layer1_research.backtesting.data.loaders.becker_parquet import (
        BeckerParquetLoader,
    )

    loader = BeckerParquetLoader(becker_data_dir)
    result = build_catalog(loader, catalog_dir)

    assert result.markets_loaded > 0
    assert result.trades_loaded > 0
    assert result.instruments_created > 0
    # Catalog directory should have contents
    assert any(Path(catalog_dir).iterdir())


def test_build_catalog_with_market_filter(becker_data_dir, catalog_dir):
    from src.layer1_research.backtesting.data.catalog import build_catalog
    from src.layer1_research.backtesting.data.loaders.becker_parquet import (
        BeckerParquetLoader,
    )
    from src.layer1_research.backtesting.data.models import MarketFilter

    loader = BeckerParquetLoader(becker_data_dir)
    result = build_catalog(
        loader, catalog_dir, filters=MarketFilter(min_volume=200_000)
    )

    # Only the BTC market has volume > 200k
    assert result.markets_loaded == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/backtesting/test_catalog.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement catalog builder**

```python
# src/layer1_research/backtesting/data/catalog.py
"""ETL orchestrator: DataLoader → NautilusTrader ParquetDataCatalog.

Runs a DataLoader, converts output to Nautilus types, and writes to
a ParquetDataCatalog directory. This is a one-time ETL step — once
data is in the catalog, Nautilus reads it natively.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TradeId, Venue
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.layer1_research.backtesting.data.instruments import (
    POLYMARKET_VENUE,
    clear_pairs,
    create_instruments,
)
from src.layer1_research.backtesting.data.loaders.base import DataLoader
from src.layer1_research.backtesting.data.models import MarketFilter


@dataclass
class CatalogBuildResult:
    """Summary of a catalog build operation."""

    markets_loaded: int
    trades_loaded: int
    instruments_created: int
    catalog_path: str


def build_catalog(
    loader: DataLoader,
    catalog_path: str,
    filters: Optional[MarketFilter] = None,
) -> CatalogBuildResult:
    """Build a NautilusTrader ParquetDataCatalog from a DataLoader.

    1. Loads markets from the source
    2. Creates BinaryOption instruments
    3. Loads trades per token and converts to Nautilus TradeTick
    4. Writes everything to the catalog directory
    """
    Path(catalog_path).mkdir(parents=True, exist_ok=True)

    clear_pairs()

    # Load markets
    markets = loader.load_markets(filters=filters)

    # Create instruments
    all_instruments = []
    for market in markets:
        instruments = create_instruments(market)
        all_instruments.extend(instruments)

    # Write instruments to catalog
    catalog = ParquetDataCatalog(catalog_path)

    if all_instruments:
        catalog.write_data(all_instruments)

    # Load and write trades per token to avoid OOM on large datasets.
    # Each token's trades are loaded, converted, and written as a batch.
    trade_count = 0

    for market in markets:
        for token_id in market.token_ids:
            instrument_id = InstrumentId(
                symbol=Symbol(token_id),
                venue=POLYMARKET_VENUE,
            )

            batch: list[TradeTick] = []
            for i, raw_trade in enumerate(loader.get_trades(token_id)):
                ts_ns = int(raw_trade.timestamp.timestamp() * 1e9)

                aggressor_side = (
                    AggressorSide.BUYER
                    if raw_trade.side == "BUY"
                    else AggressorSide.SELLER
                )

                tick = TradeTick(
                    instrument_id=instrument_id,
                    price=Price(raw_trade.price, precision=2),
                    size=Quantity(raw_trade.size, precision=1),
                    aggressor_side=aggressor_side,
                    trade_id=TradeId(f"{token_id}_{i}"),
                    ts_event=ts_ns,
                    ts_init=ts_ns,
                )
                batch.append(tick)
                trade_count += 1

            if batch:
                catalog.write_data(batch)

    return CatalogBuildResult(
        markets_loaded=len(markets),
        trades_loaded=trade_count,
        instruments_created=len(all_instruments),
        catalog_path=catalog_path,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/backtesting/test_catalog.py -v
```

Expected: all PASS

Note: The `ParquetDataCatalog` API may vary by NautilusTrader version. If `write_data()` doesn't accept a list directly, batch via `catalog.write_data(data)` per item. Check the installed version's API.

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/data/catalog.py tests/backtesting/test_catalog.py
git commit -m "feat: add catalog builder ETL orchestrator (DataLoader → ParquetDataCatalog)"
```

---

## Chunk 3: Fee Model, Position Sizing & Strategy Base

### Task 7: Polymarket Fee Model

**Files:**
- Create: `src/layer1_research/backtesting/execution/fees.py`
- Create: `tests/backtesting/test_fees.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/backtesting/test_fees.py
"""Tests for Polymarket fee model."""
import pytest


def test_fee_at_midpoint_is_maximum():
    """Fee is highest at p=0.50 (parabolic)."""
    from src.layer1_research.backtesting.execution.fees import polymarket_fee

    fee_50 = polymarket_fee(0.50, fee_rate_bps=200)
    fee_30 = polymarket_fee(0.30, fee_rate_bps=200)
    fee_90 = polymarket_fee(0.90, fee_rate_bps=200)

    assert fee_50 > fee_30
    assert fee_50 > fee_90


def test_fee_at_extremes_near_zero():
    """Fee approaches zero near p=0 and p=1."""
    from src.layer1_research.backtesting.execution.fees import polymarket_fee

    fee = polymarket_fee(0.99, fee_rate_bps=200)
    assert fee < 0.001


def test_fee_zero_bps():
    """Most Polymarket markets have 0 bps fees."""
    from src.layer1_research.backtesting.execution.fees import polymarket_fee

    assert polymarket_fee(0.50, fee_rate_bps=0) == 0.0


def test_fee_known_values():
    """Verify against known fee calculations from src/utils.py."""
    from src.layer1_research.backtesting.execution.fees import polymarket_fee

    # At p=0.50, 200bps: 0.50 * 0.50 * 0.02 = 0.005
    assert polymarket_fee(0.50, fee_rate_bps=200) == pytest.approx(0.005)

    # At p=0.30, 200bps: 0.30 * 0.70 * 0.02 = 0.0042
    assert polymarket_fee(0.30, fee_rate_bps=200) == pytest.approx(0.0042)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/backtesting/test_fees.py -v
```

- [ ] **Step 3: Implement fee model**

```python
# src/layer1_research/backtesting/execution/fees.py
"""Polymarket fee model for NautilusTrader backtesting.

Polymarket uses a parabolic taker fee structure:
    fee = price * (1 - price) * (fee_rate_bps / 10_000)

Most markets have 0 bps fees. Some (e.g., 15-minute crypto markets)
charge 20-50 bps. Fee is maximized at p=0.50 and approaches zero
at p=0 and p=1.

Provides both:
- polymarket_fee(): standalone function for manual calculation
- PolymarketFeeModel: NautilusTrader FeeModel subclass for engine integration
"""
from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money
from nautilus_trader.model.orders import Order

from src.utils import polymarket_taker_fee


def polymarket_fee(price: float, fee_rate_bps: int) -> float:
    """Calculate Polymarket taker fee for a given price and fee rate.

    Delegates to the canonical implementation in src/utils.py to
    ensure consistency between live trading and backtesting.
    """
    return polymarket_taker_fee(price, fee_rate_bps)


class PolymarketFeeModel(FeeModel):
    """NautilusTrader FeeModel implementing Polymarket's parabolic fee structure.

    Pass this to engine.add_venue() so fees are applied during simulation.
    """

    def __init__(self, fee_rate_bps: int = 0):
        self._fee_rate_bps = fee_rate_bps

    def get_commission(
        self,
        order: Order,
        fill_qty: "Quantity",
        fill_px: "Price",
        instrument: Instrument,
    ) -> Money:
        price = float(fill_px)
        qty = float(fill_qty)
        fee_per_unit = polymarket_taker_fee(price, self._fee_rate_bps)
        total_fee = fee_per_unit * qty
        return Money(total_fee, instrument.currency)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/backtesting/test_fees.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/execution/fees.py tests/backtesting/test_fees.py
git commit -m "feat: add Polymarket parabolic fee model for backtesting"
```

---

### Task 8: Position Sizer

**Files:**
- Create: `src/layer1_research/backtesting/execution/sizer.py`
- Create: `tests/backtesting/test_sizer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/backtesting/test_sizer.py
"""Tests for position sizer."""
import pytest


def test_fixed_fractional_basic():
    from src.layer1_research.backtesting.execution.sizer import (
        fixed_fractional_size,
    )

    # 2% of $10,000 capital at price 0.50
    size = fixed_fractional_size(
        capital=10_000.0, fraction=0.02, price=0.50
    )
    # $200 / $0.50 = 400 contracts
    assert size == pytest.approx(400.0)


def test_fixed_fractional_respects_max():
    from src.layer1_research.backtesting.execution.sizer import (
        fixed_fractional_size,
    )

    size = fixed_fractional_size(
        capital=10_000.0, fraction=0.02, price=0.50, max_size=100.0
    )
    assert size == 100.0


def test_kelly_size_basic():
    from src.layer1_research.backtesting.execution.sizer import kelly_size

    # 60% win probability, binary outcome (win 1, lose 1)
    size = kelly_size(
        capital=10_000.0,
        win_prob=0.60,
        price=0.50,
    )
    # Kelly fraction for 60/40 with even odds = 0.20
    # $2000 / $0.50 = 4000 contracts, but capped by max_fraction
    assert size > 0


def test_kelly_size_no_edge():
    from src.layer1_research.backtesting.execution.sizer import kelly_size

    # 50% win probability at price 0.50 = no edge
    size = kelly_size(
        capital=10_000.0,
        win_prob=0.50,
        price=0.50,
    )
    assert size == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/backtesting/test_sizer.py -v
```

- [ ] **Step 3: Implement position sizer**

```python
# src/layer1_research/backtesting/execution/sizer.py
"""Position sizing for backtesting strategies.

Two modes:
- Fixed fractional: risk a fixed % of portfolio per trade
- Kelly criterion: optimal sizing based on edge and win probability
"""
from src.utils import kelly_criterion


def fixed_fractional_size(
    capital: float,
    fraction: float,
    price: float,
    max_size: float = float("inf"),
) -> float:
    """Compute position size as a fixed fraction of capital.

    Args:
        capital: Current portfolio value in USDC.
        fraction: Fraction of capital to risk (e.g., 0.02 = 2%).
        price: Entry price per contract.
        max_size: Maximum number of contracts.

    Returns:
        Number of contracts to buy/sell.
    """
    if price <= 0:
        return 0.0

    dollar_amount = capital * fraction
    size = dollar_amount / price
    return min(size, max_size)


def kelly_size(
    capital: float,
    win_prob: float,
    price: float,
    max_fraction: float = 0.10,
) -> float:
    """Compute position size using Kelly criterion.

    For binary options: win_amount = (1 - price), loss_amount = price.
    If you buy at 0.40 and it resolves YES, you gain 0.60. If NO, you lose 0.40.

    Args:
        capital: Current portfolio value in USDC.
        win_prob: Estimated probability of the outcome.
        price: Entry price per contract.
        max_fraction: Maximum fraction of capital to risk (Kelly cap).

    Returns:
        Number of contracts to buy/sell. 0 if no edge.
    """
    win_amount = 1.0 - price
    loss_amount = price

    if win_amount <= 0 or loss_amount <= 0:
        return 0.0

    fraction = kelly_criterion(win_prob, win_amount, loss_amount)

    # Kelly can return negative (no edge) or very large values
    if fraction <= 0:
        return 0.0

    fraction = min(fraction, max_fraction)
    dollar_amount = capital * fraction
    return dollar_amount / price
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/backtesting/test_sizer.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/execution/sizer.py tests/backtesting/test_sizer.py
git commit -m "feat: add position sizer (fixed fractional + Kelly criterion)"
```

---

### Task 9: Signal Dataclass

**Files:**
- Create: `src/layer1_research/backtesting/strategies/signal.py`
- Create: `tests/backtesting/test_signal.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/backtesting/test_signal.py
"""Tests for Signal dataclass."""
import pytest


def test_signal_creation():
    from src.layer1_research.backtesting.strategies.signal import Signal

    s = Signal(
        direction="BUY",
        confidence=0.75,
        target_price=0.60,
    )
    assert s.direction == "BUY"
    assert s.confidence == 0.75
    assert s.size is None
    assert s.metadata is None


def test_signal_with_size():
    from src.layer1_research.backtesting.strategies.signal import Signal

    s = Signal(
        direction="SELL",
        confidence=0.80,
        target_price=0.40,
        size=100.0,
    )
    assert s.size == 100.0


def test_signal_rejects_invalid_direction():
    from src.layer1_research.backtesting.strategies.signal import Signal

    with pytest.raises(ValueError, match="direction must be"):
        Signal(direction="HOLD", confidence=0.5, target_price=0.5)


def test_signal_rejects_invalid_confidence():
    from src.layer1_research.backtesting.strategies.signal import Signal

    with pytest.raises(ValueError, match="confidence must be"):
        Signal(direction="BUY", confidence=1.5, target_price=0.5)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/backtesting/test_signal.py -v
```

- [ ] **Step 3: Implement Signal**

```python
# src/layer1_research/backtesting/strategies/signal.py
"""Signal dataclass emitted by backtesting strategies."""
from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class Signal:
    """A trading signal emitted by a strategy's generate_signal() method.

    Attributes:
        direction: BUY (long YES), SELL (short YES / long NO), or FLAT (close).
        confidence: Estimated probability of the predicted outcome (0 to 1).
        target_price: Estimated fair value of the token.
        size: Explicit size override. None = let position sizer decide.
        metadata: Strategy-specific context for reporting/debugging.
    """

    direction: str  # "BUY", "SELL", or "FLAT"
    confidence: float
    target_price: float
    size: Optional[float] = None
    metadata: Optional[dict] = None

    def __post_init__(self):
        if self.direction not in ("BUY", "SELL", "FLAT"):
            raise ValueError(
                f"direction must be BUY, SELL, or FLAT, got '{self.direction}'"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be between 0 and 1, got {self.confidence}"
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/backtesting/test_signal.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/strategies/signal.py tests/backtesting/test_signal.py
git commit -m "feat: add Signal dataclass for backtesting strategy output"
```

---

### Task 10: PredictionMarketStrategy Base Class

This is the core base class that strategy authors extend. It wraps NautilusTrader's `Strategy` with prediction-market-specific helpers.

**Files:**
- Create: `src/layer1_research/backtesting/strategies/base.py`
- Create: `tests/backtesting/test_strategy_base.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/backtesting/test_strategy_base.py
"""Tests for PredictionMarketStrategy base class."""
import pytest


def test_base_strategy_is_abstract():
    """Cannot instantiate directly — must implement generate_signal."""
    from src.layer1_research.backtesting.strategies.base import (
        PredictionMarketStrategy,
        PredictionMarketStrategyConfig,
    )

    with pytest.raises(TypeError):
        config = PredictionMarketStrategyConfig()
        PredictionMarketStrategy(config=config)


def test_concrete_strategy_instantiation():
    """A minimal concrete implementation should instantiate."""
    from src.layer1_research.backtesting.strategies.base import (
        PredictionMarketStrategy,
        PredictionMarketStrategyConfig,
    )
    from src.layer1_research.backtesting.strategies.signal import Signal

    class AlwaysBuy(PredictionMarketStrategy):
        def generate_signal(self, instrument, data) -> Signal | None:
            return Signal(direction="BUY", confidence=0.70, target_price=0.60)

    config = PredictionMarketStrategyConfig()
    strategy = AlwaysBuy(config=config)
    assert strategy is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/backtesting/test_strategy_base.py -v
```

- [ ] **Step 3: Implement base strategy**

```python
# src/layer1_research/backtesting/strategies/base.py
"""Base class for prediction market backtesting strategies.

Wraps NautilusTrader's Strategy with prediction-market-specific helpers:
- Binary outcome position management (BUY YES vs BUY NO)
- YES/NO token pair resolution
- Signal-driven order flow

Strategy authors implement generate_signal() and the base class
handles order submission, position management, and sizing.
"""
from abc import abstractmethod
from typing import Optional

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, TradeTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import BinaryOption
from nautilus_trader.trading.strategy import Strategy

from src.layer1_research.backtesting.data.instruments import get_paired_token_id
from src.layer1_research.backtesting.execution.fees import polymarket_fee
from src.layer1_research.backtesting.execution.sizer import (
    fixed_fractional_size,
    kelly_size,
)
from src.layer1_research.backtesting.strategies.signal import Signal


class PredictionMarketStrategyConfig(StrategyConfig, frozen=True):
    """Configuration for PredictionMarketStrategy.

    Attributes:
        instrument_ids: List of instrument IDs to trade (token IDs).
        fee_rate_bps: Polymarket fee rate in basis points.
        sizer_mode: Position sizing mode ("kelly" or "fixed_fractional").
        fixed_fraction: Fraction of capital per trade (if fixed_fractional).
        kelly_max_fraction: Max Kelly fraction cap.
        max_position_size: Max contracts per position.
    """

    instrument_ids: list[str] = []
    fee_rate_bps: int = 0
    sizer_mode: str = "fixed_fractional"
    fixed_fraction: float = 0.02
    kelly_max_fraction: float = 0.10
    max_position_size: float = 10_000.0


class PredictionMarketStrategy(Strategy):
    """Base class for prediction market backtesting strategies.

    Subclasses must implement generate_signal(). The base class handles
    event routing, order submission, and position sizing.
    """

    def __init__(self, config: PredictionMarketStrategyConfig):
        super().__init__(config)
        self.config = config
        self._instrument_map: dict[InstrumentId, BinaryOption] = {}

    @abstractmethod
    def generate_signal(
        self, instrument: BinaryOption, data: TradeTick | Bar
    ) -> Optional[Signal]:
        """Generate a trading signal from incoming market data.

        This is the single method strategy authors implement.

        Args:
            instrument: The BinaryOption instrument that received data.
            data: The market data event (TradeTick or Bar).

        Returns:
            A Signal to act on, or None to do nothing.
        """
        ...

    def on_start(self):
        """Subscribe to instruments on strategy start."""
        for inst_id_str in self.config.instrument_ids:
            instrument_id = InstrumentId.from_str(inst_id_str)
            instrument = self.cache.instrument(instrument_id)
            if instrument:
                self._instrument_map[instrument_id] = instrument
                self.subscribe_trade_ticks(instrument_id)

    def on_trade_tick(self, tick: TradeTick):
        """Route trade ticks to generate_signal and act on result."""
        instrument = self._instrument_map.get(tick.instrument_id)
        if not instrument:
            return

        signal = self.generate_signal(instrument, tick)
        if signal:
            self._act_on_signal(signal, instrument, tick)

    def on_bar(self, bar: Bar):
        """Route bars to generate_signal and act on result."""
        instrument = self._instrument_map.get(bar.bar_type.instrument_id)
        if not instrument:
            return

        signal = self.generate_signal(instrument, bar)
        if signal:
            self._act_on_signal(signal, instrument, bar)

    def _act_on_signal(
        self, signal: Signal, instrument: BinaryOption, data: TradeTick | Bar
    ):
        """Convert a Signal into an order submission."""
        if signal.direction == "FLAT":
            self._close_position(instrument)
            return

        # Determine size
        if signal.size is not None:
            size = signal.size
        else:
            # Get available capital from account balance, not exposure
            try:
                account = self.portfolio.account(instrument.id.venue)
                balance = account.balance_total(instrument.currency)
                capital = float(balance) if balance else 10_000.0
            except Exception:
                capital = 10_000.0  # fallback for initial capital

            if self.config.sizer_mode == "kelly":
                size = kelly_size(
                    capital=capital,
                    win_prob=signal.confidence,
                    price=signal.target_price,
                    max_fraction=self.config.kelly_max_fraction,
                )
            else:
                size = fixed_fractional_size(
                    capital=capital,
                    fraction=self.config.fixed_fraction,
                    price=signal.target_price,
                    max_size=self.config.max_position_size,
                )

        if size <= 0:
            return

        order_side = (
            OrderSide.BUY if signal.direction == "BUY" else OrderSide.SELL
        )

        order = self.order_factory.market(
            instrument_id=instrument.id,
            order_side=order_side,
            quantity=instrument.make_qty(size),
        )
        self.submit_order(order)

    def _close_position(self, instrument: BinaryOption):
        """Close any open position on the instrument."""
        positions = self.cache.positions(instrument_id=instrument.id)
        for position in positions:
            if position.is_open:
                self.close_position(position)

    def get_fee(self, price: float) -> float:
        """Calculate the Polymarket fee for a given price."""
        return polymarket_fee(price, self.config.fee_rate_bps)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/backtesting/test_strategy_base.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/strategies/base.py tests/backtesting/test_strategy_base.py
git commit -m "feat: add PredictionMarketStrategy base class with signal-driven order flow"
```

---

## Chunk 4: Backtest Config, Runner & Reporting

### Task 11: Backtest Configuration

**Files:**
- Create: `src/layer1_research/backtesting/config.py`
- Create: `tests/backtesting/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/backtesting/test_config.py
"""Tests for BacktestConfig."""
import pytest
from datetime import datetime, timedelta, timezone


def test_config_basic():
    from src.layer1_research.backtesting.config import BacktestConfig

    config = BacktestConfig(
        catalog_path="/tmp/catalog",
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        strategy_name="kalshi_divergence",
        starting_capital=10_000.0,
    )
    assert config.data_mode == "bar"
    assert config.bar_interval == timedelta(minutes=5)
    assert config.fee_rate_bps == 0
    assert config.position_sizer == "fixed_fractional"


def test_config_trade_mode_no_bar_interval():
    from src.layer1_research.backtesting.config import BacktestConfig

    config = BacktestConfig(
        catalog_path="/tmp/catalog",
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        strategy_name="test",
        starting_capital=10_000.0,
        data_mode="trade",
    )
    assert config.bar_interval is None


def test_config_validates_dates():
    from src.layer1_research.backtesting.config import BacktestConfig

    with pytest.raises(ValueError, match="start must be before end"):
        BacktestConfig(
            catalog_path="/tmp/catalog",
            start=datetime(2024, 12, 31, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, tzinfo=timezone.utc),
            strategy_name="test",
            starting_capital=10_000.0,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/backtesting/test_config.py -v
```

- [ ] **Step 3: Implement BacktestConfig**

```python
# src/layer1_research/backtesting/config.py
"""Backtest configuration."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Optional


@dataclass
class BacktestConfig:
    """Everything needed to define a backtest run.

    Attributes:
        catalog_path: Path to NautilusTrader ParquetDataCatalog directory.
        start: Backtest start datetime (inclusive).
        end: Backtest end datetime (inclusive).
        strategy_name: Name of the strategy to run (maps to registered strategies).
        starting_capital: Initial portfolio value in USDC.
        strategy_params: Additional parameters passed to the strategy config.
        markets: Specific market/token IDs to include. None = all in catalog.
        data_mode: "trade" for tick-by-tick, "bar" for aggregated bars.
        bar_interval: Bar aggregation interval. Required if data_mode="bar".
        fee_rate_bps: Default fee rate in basis points.
        position_sizer: Sizing mode ("kelly" or "fixed_fractional").
        max_position_pct: Max % of capital per position.
        max_total_exposure_pct: Max % of capital deployed across all positions.
        generate_charts: Whether to generate visual reports.
    """

    catalog_path: str
    start: datetime
    end: datetime
    strategy_name: str
    starting_capital: float

    strategy_params: dict = field(default_factory=dict)
    markets: Optional[list[str]] = None
    data_mode: Literal["trade", "bar"] = "bar"
    bar_interval: Optional[timedelta] = field(default_factory=lambda: timedelta(minutes=5))
    fee_rate_bps: int = 0
    position_sizer: Literal["kelly", "fixed_fractional"] = "fixed_fractional"
    max_position_pct: float = 0.10
    max_total_exposure_pct: float = 0.50
    generate_charts: bool = False

    def __post_init__(self):
        if self.start >= self.end:
            raise ValueError(f"start must be before end: {self.start} >= {self.end}")
        if self.data_mode == "trade":
            self.bar_interval = None
        if self.data_mode == "bar" and self.bar_interval is None:
            raise ValueError("bar_interval required when data_mode='bar'")
        if self.starting_capital <= 0:
            raise ValueError(f"starting_capital must be positive: {self.starting_capital}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/backtesting/test_config.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/config.py tests/backtesting/test_config.py
git commit -m "feat: add BacktestConfig with validation"
```

---

### Task 12: Reporting — Metrics & CLI Output

**Files:**
- Create: `src/layer1_research/backtesting/reporting/metrics.py`
- Create: `src/layer1_research/backtesting/reporting/cli_report.py`
- Create: `tests/backtesting/test_metrics.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/backtesting/test_metrics.py
"""Tests for backtesting metrics."""
import pytest


def test_brier_score_perfect():
    from src.layer1_research.backtesting.reporting.metrics import brier_score

    # Perfect predictions: predicted 1.0 for events that happened
    predictions = [1.0, 0.0, 1.0]
    outcomes = [1, 0, 1]
    assert brier_score(predictions, outcomes) == pytest.approx(0.0)


def test_brier_score_worst():
    from src.layer1_research.backtesting.reporting.metrics import brier_score

    # Worst predictions: predicted 1.0 for events that didn't happen
    predictions = [1.0, 0.0]
    outcomes = [0, 1]
    assert brier_score(predictions, outcomes) == pytest.approx(1.0)


def test_brier_score_random():
    from src.layer1_research.backtesting.reporting.metrics import brier_score

    # Naive 50/50 predictions
    predictions = [0.5, 0.5, 0.5, 0.5]
    outcomes = [1, 0, 1, 0]
    assert brier_score(predictions, outcomes) == pytest.approx(0.25)


def test_fee_drag():
    from src.layer1_research.backtesting.reporting.metrics import fee_drag

    assert fee_drag(total_fees=50.0, gross_pnl=500.0) == pytest.approx(0.10)
    assert fee_drag(total_fees=50.0, gross_pnl=0.0) == 0.0


def test_backtest_summary_creation():
    from src.layer1_research.backtesting.reporting.metrics import BacktestSummary

    summary = BacktestSummary(
        strategy_name="test",
        start="2024-01-01",
        end="2024-12-31",
        starting_capital=10_000.0,
        final_equity=11_500.0,
        total_return_pct=15.0,
        sharpe_ratio=1.5,
        max_drawdown_pct=-5.0,
        win_rate=0.60,
        total_trades=100,
        total_fees=50.0,
        brier=0.22,
        fee_drag_pct=0.03,
    )
    assert summary.total_return_pct == 15.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/backtesting/test_metrics.py -v
```

- [ ] **Step 3: Implement metrics**

```python
# src/layer1_research/backtesting/reporting/metrics.py
"""Backtesting performance metrics.

Built-in Nautilus metrics (Sharpe, drawdown, win rate) are supplemented
with prediction-market-specific metrics here.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BacktestSummary:
    """Aggregated backtest results for display and serialization."""

    strategy_name: str
    start: str
    end: str
    starting_capital: float
    final_equity: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate: float
    total_trades: int
    total_fees: float
    brier: Optional[float]
    fee_drag_pct: float
    per_market: dict = field(default_factory=dict)


def brier_score(predictions: list[float], outcomes: list[int]) -> float:
    """Compute Brier score: mean squared error of probability predictions.

    Lower is better. 0 = perfect, 1 = worst possible.

    Args:
        predictions: Predicted probabilities (0 to 1).
        outcomes: Actual outcomes (0 or 1).
    """
    if not predictions:
        return 0.0
    n = len(predictions)
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / n


def fee_drag(total_fees: float, gross_pnl: float) -> float:
    """Calculate fees as a fraction of gross P&L.

    Returns 0 if gross P&L is zero or negative (fees didn't eat into gains).
    """
    if gross_pnl <= 0:
        return 0.0
    return total_fees / gross_pnl
```

- [ ] **Step 4: Implement CLI reporter**

```python
# src/layer1_research/backtesting/reporting/cli_report.py
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
            summary.per_market.items(),
            key=lambda x: x[1].get("pnl", 0),
            reverse=True,
        )
        for name, stats in sorted_markets[:5]:
            pnl = stats.get("pnl", 0)
            trades = stats.get("trades", 0)
            sign = "+" if pnl >= 0 else ""
            print(f"    {name[:40]:<40} {sign}${pnl:>8,.0f}   ({trades} trades)")
        print(border)

    print()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/backtesting/test_metrics.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/layer1_research/backtesting/reporting/metrics.py src/layer1_research/backtesting/reporting/cli_report.py tests/backtesting/test_metrics.py
git commit -m "feat: add backtesting metrics (Brier score, fee drag) and CLI reporter"
```

---

### Task 13: Backtest Runner

The runner wires everything together: reads config, sets up the Nautilus engine, runs the backtest, collects results.

**Files:**
- Create: `src/layer1_research/backtesting/runner.py`
- Create: `tests/backtesting/test_runner_e2e.py`

- [ ] **Step 1: Write failing end-to-end test**

This test uses synthetic data to verify the entire pipeline works. It creates a trivial strategy, loads fake data, runs the engine, and checks that results are produced.

```python
# tests/backtesting/test_runner_e2e.py
"""End-to-end test for BacktestRunner with synthetic data."""
import pytest
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

from tests.backtesting.fixtures.sample_data import create_becker_fixture_dir


@pytest.fixture
def becker_data_dir():
    d = create_becker_fixture_dir()
    yield d
    shutil.rmtree(d)


@pytest.fixture
def catalog_dir():
    d = tempfile.mkdtemp(prefix="catalog_e2e_")
    yield d
    shutil.rmtree(d)


def test_e2e_backtest_produces_results(becker_data_dir, catalog_dir):
    """Smoke test: run a trivial strategy through the full pipeline."""
    from src.layer1_research.backtesting.data.catalog import build_catalog
    from src.layer1_research.backtesting.data.loaders.becker_parquet import (
        BeckerParquetLoader,
    )
    from src.layer1_research.backtesting.config import BacktestConfig
    from src.layer1_research.backtesting.runner import BacktestRunner
    from src.layer1_research.backtesting.strategies.base import (
        PredictionMarketStrategy,
        PredictionMarketStrategyConfig,
    )
    from src.layer1_research.backtesting.strategies.signal import Signal

    # 1. Build catalog from fixture data
    loader = BeckerParquetLoader(becker_data_dir)
    build_catalog(loader, catalog_dir)

    # 2. Define a trivial strategy
    class BuyLowStrategy(PredictionMarketStrategy):
        def generate_signal(self, instrument, data):
            price = float(data.price) if hasattr(data, 'price') else float(data.close)
            if price < 0.40:
                return Signal(
                    direction="BUY",
                    confidence=0.65,
                    target_price=price,
                    size=10.0,
                )
            return None

    # 3. Run backtest
    config = BacktestConfig(
        catalog_path=catalog_dir,
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 7, 1, tzinfo=timezone.utc),
        strategy_name="buy_low",
        starting_capital=10_000.0,
        data_mode="trade",
    )

    runner = BacktestRunner(config)
    summary = runner.run(BuyLowStrategy)

    # Should produce a result (even if no trades executed)
    assert summary is not None
    assert summary.strategy_name == "buy_low"
    assert summary.starting_capital == 10_000.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/backtesting/test_runner_e2e.py -v
```

- [ ] **Step 3: Implement BacktestRunner**

```python
# src/layer1_research/backtesting/runner.py
"""Backtest runner: wires config, engine, strategy, and reporting together.

Usage:
    config = BacktestConfig(...)
    runner = BacktestRunner(config)
    summary = runner.run(MyStrategy)
"""
from typing import Type

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.layer1_research.backtesting.config import BacktestConfig
from src.layer1_research.backtesting.execution.fees import PolymarketFeeModel
from src.layer1_research.backtesting.reporting.cli_report import print_report
from src.layer1_research.backtesting.reporting.metrics import BacktestSummary
from src.layer1_research.backtesting.strategies.base import (
    PredictionMarketStrategy,
    PredictionMarketStrategyConfig,
)

POLYMARKET_VENUE = Venue("POLYMARKET")


class BacktestRunner:
    """Orchestrates a backtest run from config to results."""

    def __init__(self, config: BacktestConfig):
        self._config = config

    def run(
        self,
        strategy_class: Type[PredictionMarketStrategy],
    ) -> BacktestSummary:
        """Execute the backtest and return a summary.

        Args:
            strategy_class: The strategy class to instantiate and run.

        Returns:
            BacktestSummary with performance metrics.
        """
        catalog = ParquetDataCatalog(self._config.catalog_path)

        # Configure engine
        engine_config = BacktestEngineConfig(
            logging=LoggingConfig(log_level="WARNING"),
        )
        engine = BacktestEngine(config=engine_config)

        # Add simulated venue with fee model
        fee_model = PolymarketFeeModel(self._config.fee_rate_bps)
        engine.add_venue(
            venue=POLYMARKET_VENUE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(self._config.starting_capital, USD)],
            fee_model=fee_model,
        )

        # Load instruments from catalog
        instruments = catalog.instruments()
        if self._config.markets:
            instruments = [
                inst for inst in instruments
                if any(m in str(inst.id) for m in self._config.markets)
            ]

        for instrument in instruments:
            engine.add_instrument(instrument)

        # Load trade data per instrument, filtered by date range
        start_ns = int(self._config.start.timestamp() * 1e9)
        end_ns = int(self._config.end.timestamp() * 1e9)

        for instrument in instruments:
            ticks = catalog.trade_ticks(
                instrument_ids=[instrument.id],
                start=start_ns,
                end=end_ns,
            )
            if ticks:
                engine.add_data(ticks)

        # Configure strategy
        instrument_ids = [str(inst.id) for inst in instruments]
        strategy_config = PredictionMarketStrategyConfig(
            instrument_ids=instrument_ids,
            fee_rate_bps=self._config.fee_rate_bps,
            sizer_mode=self._config.position_sizer,
        )

        strategy = strategy_class(config=strategy_config)
        engine.add_strategy(strategy)

        # Run
        engine.run()

        # Collect results
        summary = self._build_summary(engine, strategy)

        # Print CLI report
        print_report(summary)

        # Cleanup
        engine.dispose()

        return summary

    def _build_summary(
        self, engine: BacktestEngine, strategy: PredictionMarketStrategy
    ) -> BacktestSummary:
        """Extract metrics from the engine after a run."""
        # Get reports from trader
        fills_report = engine.trader.generate_fills_report()
        positions_report = engine.trader.generate_positions_report()

        total_trades = len(fills_report) if fills_report is not None else 0

        # Extract final equity from account
        final_equity = self._config.starting_capital
        total_fees = 0.0
        try:
            account_report = engine.trader.generate_account_report(POLYMARKET_VENUE)
            if account_report is not None and len(account_report) > 0:
                last_row = account_report.iloc[-1]
                balance = last_row.get("total", self._config.starting_capital)
                if isinstance(balance, (int, float)):
                    final_equity = float(balance)

            # Sum commissions from fills
            if fills_report is not None and "commission" in fills_report.columns:
                total_fees = float(fills_report["commission"].sum())
        except Exception:
            pass

        total_return_pct = (
            (final_equity - self._config.starting_capital)
            / self._config.starting_capital
            * 100
        )

        # Extract stats from positions report
        win_rate = 0.0
        sharpe_ratio = 0.0
        max_drawdown_pct = 0.0

        try:
            if positions_report is not None and len(positions_report) > 0:
                # Win rate from closed positions
                if "realized_pnl" in positions_report.columns:
                    closed = positions_report[positions_report["realized_pnl"] != 0]
                    if len(closed) > 0:
                        wins = (closed["realized_pnl"] > 0).sum()
                        win_rate = wins / len(closed)

            # Sharpe and drawdown from Nautilus analyzer if available
            if hasattr(engine, "analyzer"):
                stats = engine.analyzer.get_performance_stats_general()
                if stats:
                    sharpe_ratio = stats.get("sharpe_ratio", 0.0)
                    max_drawdown_pct = stats.get("max_drawdown", 0.0) * 100
        except Exception:
            pass

        gross_pnl = final_equity - self._config.starting_capital + total_fees

        from src.layer1_research.backtesting.reporting.metrics import fee_drag

        return BacktestSummary(
            strategy_name=self._config.strategy_name,
            start=self._config.start.strftime("%Y-%m-%d"),
            end=self._config.end.strftime("%Y-%m-%d"),
            starting_capital=self._config.starting_capital,
            final_equity=final_equity,
            total_return_pct=total_return_pct,
            sharpe_ratio=sharpe_ratio,
            max_drawdown_pct=max_drawdown_pct,
            win_rate=win_rate,
            total_trades=total_trades,
            total_fees=total_fees,
            brier=None,  # Requires strategy to track predictions vs outcomes
            fee_drag_pct=fee_drag(total_fees, gross_pnl),
        )
```

- [ ] **Step 4: Run end-to-end test**

```bash
pytest tests/backtesting/test_runner_e2e.py -v
```

Expected: PASS

Note: The Nautilus API for extracting results (`generate_fills_report`, `generate_account_report`) may differ by version. Adjust the `_build_summary` method to match the installed version. The key goal is that the pipeline runs without errors — metric extraction can be refined iteratively.

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/runner.py tests/backtesting/test_runner_e2e.py
git commit -m "feat: add BacktestRunner with end-to-end pipeline"
```

---

## Chunk 5: CLI Scripts & Example Strategy

### Task 14: Data Loading Script

**Files:**
- Create: `scripts/load_data.py`

- [ ] **Step 1: Implement the ETL CLI script**

```python
# scripts/load_data.py
"""One-time ETL: load external data into NautilusTrader ParquetDataCatalog.

Usage:
    python scripts/load_data.py --source becker --path ../prediction-market-analysis/data
    python scripts/load_data.py --source becker --path ../prediction-market-analysis/data --min-volume 100000
"""
import argparse
import sys
import time


def main():
    parser = argparse.ArgumentParser(
        description="Load prediction market data into backtesting catalog"
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=["becker"],
        help="Data source format",
    )
    parser.add_argument(
        "--path",
        required=True,
        help="Path to source data directory",
    )
    parser.add_argument(
        "--catalog",
        default="data/catalog",
        help="Output catalog directory (default: data/catalog)",
    )
    parser.add_argument(
        "--min-volume",
        type=float,
        default=None,
        help="Minimum market volume filter",
    )
    parser.add_argument(
        "--resolved-only",
        action="store_true",
        help="Only load resolved (closed) markets",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview markets without loading trades",
    )

    args = parser.parse_args()

    from src.layer1_research.backtesting.data.models import MarketFilter

    filters = MarketFilter(
        min_volume=args.min_volume,
        resolved_only=args.resolved_only,
    )

    if args.source == "becker":
        from src.layer1_research.backtesting.data.loaders.becker_parquet import (
            BeckerParquetLoader,
        )

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
```

- [ ] **Step 2: Test script runs with --help**

```bash
python scripts/load_data.py --help
```

Expected: help text printed without errors

- [ ] **Step 3: Commit**

```bash
git add scripts/load_data.py
git commit -m "feat: add load_data.py ETL script for external data ingestion"
```

---

### Task 15: Backtest Runner Script

**Files:**
- Create: `scripts/run_backtest.py`

- [ ] **Step 1: Implement the backtest CLI script**

```python
# scripts/run_backtest.py
"""Run a backtest from the command line.

Usage:
    python scripts/run_backtest.py --strategy kalshi_divergence --start 2024-01-01 --end 2024-12-31
    python scripts/run_backtest.py --strategy fair_value_mr --start 2024-01-01 --end 2024-12-31 --data-mode trade
    python scripts/run_backtest.py --strategy kalshi_divergence --start 2024-01-01 --end 2024-12-31 --charts
    python scripts/run_backtest.py --strategy kalshi_divergence --dry-run
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

# Strategy registry — maps CLI names to strategy classes
STRATEGY_REGISTRY: dict = {}


def register_strategies():
    """Import and register available strategies."""
    import logging
    logger = logging.getLogger(__name__)

    try:
        from src.layer1_research.backtesting.strategies.examples.fair_value_mean_reversion import (
            FairValueMeanReversionStrategy,
        )
        STRATEGY_REGISTRY["fair_value_mr"] = FairValueMeanReversionStrategy
    except ImportError as e:
        logger.warning(f"Could not load fair_value_mr strategy: {e}")

    try:
        from src.layer1_research.backtesting.strategies.examples.kalshi_divergence import (
            KalshiDivergenceStrategy,
        )
        STRATEGY_REGISTRY["kalshi_divergence"] = KalshiDivergenceStrategy
    except ImportError as e:
        logger.warning(f"Could not load kalshi_divergence strategy: {e}")


def main():
    parser = argparse.ArgumentParser(description="Run a prediction market backtest")
    parser.add_argument(
        "--strategy",
        required=True,
        help="Strategy name (e.g., kalshi_divergence, fair_value_mr)",
    )
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--catalog",
        default="data/catalog",
        help="Catalog directory (default: data/catalog)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=10_000.0,
        help="Starting capital in USDC (default: 10000)",
    )
    parser.add_argument(
        "--data-mode",
        choices=["trade", "bar"],
        default="bar",
        help="Data replay mode (default: bar)",
    )
    parser.add_argument(
        "--bar-interval",
        type=int,
        default=5,
        help="Bar interval in minutes (default: 5)",
    )
    parser.add_argument(
        "--sizer",
        choices=["kelly", "fixed_fractional"],
        default="fixed_fractional",
        help="Position sizing mode (default: fixed_fractional)",
    )
    parser.add_argument(
        "--fee-bps",
        type=int,
        default=0,
        help="Fee rate in basis points (default: 0)",
    )
    parser.add_argument("--charts", action="store_true", help="Generate charts")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview config without running",
    )

    args = parser.parse_args()

    register_strategies()

    if args.strategy not in STRATEGY_REGISTRY:
        available = ", ".join(STRATEGY_REGISTRY.keys()) or "(none registered)"
        print(
            f"Unknown strategy: {args.strategy}. Available: {available}",
            file=sys.stderr,
        )
        sys.exit(1)

    from src.layer1_research.backtesting.config import BacktestConfig

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
    )

    if args.dry_run:
        print(f"\nBacktest config preview:")
        print(f"  Strategy:    {config.strategy_name}")
        print(f"  Period:      {config.start.date()} -> {config.end.date()}")
        print(f"  Capital:     ${config.starting_capital:,.2f}")
        print(f"  Data mode:   {config.data_mode}")
        if config.bar_interval:
            print(f"  Bar interval: {config.bar_interval}")
        print(f"  Sizer:       {config.position_sizer}")
        print(f"  Fee rate:    {config.fee_rate_bps} bps")
        print(f"  Catalog:     {config.catalog_path}")
        return

    from src.layer1_research.backtesting.runner import BacktestRunner

    strategy_class = STRATEGY_REGISTRY[args.strategy]
    runner = BacktestRunner(config)
    runner.run(strategy_class)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test script runs with --help**

```bash
python scripts/run_backtest.py --help
```

- [ ] **Step 3: Commit**

```bash
git add scripts/run_backtest.py
git commit -m "feat: add run_backtest.py CLI script"
```

---

### Task 16: Example Strategy — Fair Value Mean Reversion

A simple strategy that buys when price drops below estimated fair value and sells when above. Serves as a template for strategy development.

**Files:**
- Create: `src/layer1_research/backtesting/strategies/examples/fair_value_mean_reversion.py`
- Create: `tests/backtesting/test_example_strategies.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/backtesting/test_example_strategies.py
"""Tests for example strategies."""
import pytest
from unittest.mock import MagicMock


def test_fv_mean_reversion_buys_below_fair_value():
    from src.layer1_research.backtesting.strategies.examples.fair_value_mean_reversion import (
        FairValueMeanReversionStrategy,
        FairValueMRConfig,
    )

    config = FairValueMRConfig(
        lookback_trades=3,
        entry_threshold=0.05,
    )
    strategy = FairValueMeanReversionStrategy(config=config)

    # Simulate: feed prices to build fair value, then test signal
    instrument = MagicMock()
    instrument.id = "test_id"

    # Feed a few ticks to build history
    tick1 = MagicMock()
    tick1.price = MagicMock()
    tick1.price.as_double.return_value = 0.50
    tick1.instrument_id = instrument.id

    tick2 = MagicMock()
    tick2.price = MagicMock()
    tick2.price.as_double.return_value = 0.52
    tick2.instrument_id = instrument.id

    tick3 = MagicMock()
    tick3.price = MagicMock()
    tick3.price.as_double.return_value = 0.48
    tick3.instrument_id = instrument.id

    # Build price history
    strategy._price_history[str(instrument.id)] = [0.50, 0.52, 0.48]

    # Now test: price drops to 0.40, well below mean of 0.50
    tick_low = MagicMock()
    tick_low.price = MagicMock()
    tick_low.price.as_double.return_value = 0.40

    signal = strategy.generate_signal(instrument, tick_low)
    assert signal is not None
    assert signal.direction == "BUY"


def test_fv_mean_reversion_flat_near_fair_value():
    from src.layer1_research.backtesting.strategies.examples.fair_value_mean_reversion import (
        FairValueMeanReversionStrategy,
        FairValueMRConfig,
    )

    config = FairValueMRConfig(
        lookback_trades=3,
        entry_threshold=0.05,
    )
    strategy = FairValueMeanReversionStrategy(config=config)

    instrument = MagicMock()
    instrument.id = "test_id"
    strategy._price_history[str(instrument.id)] = [0.50, 0.52, 0.48]

    # Price at 0.50 — right at fair value, no signal
    tick = MagicMock()
    tick.price = MagicMock()
    tick.price.as_double.return_value = 0.50

    signal = strategy.generate_signal(instrument, tick)
    assert signal is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/backtesting/test_example_strategies.py -v
```

- [ ] **Step 3: Implement the strategy**

```python
# src/layer1_research/backtesting/strategies/examples/fair_value_mean_reversion.py
"""Fair Value Mean Reversion Strategy.

A simple mean-reversion strategy for prediction markets:
- Estimates fair value as the rolling mean of recent trade prices
- Buys when current price drops below FV by a threshold
- Sells when current price rises above FV by a threshold

This serves as a template for building more sophisticated strategies.
"""
from collections import defaultdict
from typing import Optional

from nautilus_trader.model.data import Bar, TradeTick
from nautilus_trader.model.instruments import BinaryOption

from src.layer1_research.backtesting.strategies.base import (
    PredictionMarketStrategy,
    PredictionMarketStrategyConfig,
)
from src.layer1_research.backtesting.strategies.signal import Signal


class FairValueMRConfig(PredictionMarketStrategyConfig, frozen=True):
    """Config for Fair Value Mean Reversion strategy.

    Attributes:
        lookback_trades: Number of recent trades to average for FV estimate.
        entry_threshold: Minimum deviation from FV to trigger entry (e.g., 0.05 = 5%).
        exit_threshold: Deviation from FV to trigger exit. Default = half of entry.
    """

    lookback_trades: int = 20
    entry_threshold: float = 0.05
    exit_threshold: Optional[float] = None


class FairValueMeanReversionStrategy(PredictionMarketStrategy):
    """Buy below fair value, sell above fair value."""

    def __init__(self, config: FairValueMRConfig):
        super().__init__(config)
        self.config = config
        self._price_history: dict[str, list[float]] = defaultdict(list)
        self._exit_threshold = (
            config.exit_threshold
            if config.exit_threshold is not None
            else config.entry_threshold / 2
        )

    def generate_signal(
        self, instrument: BinaryOption, data: TradeTick | Bar
    ) -> Optional[Signal]:
        """Generate mean-reversion signal."""
        # Extract current price
        if isinstance(data, TradeTick):
            current_price = float(data.price)
        elif isinstance(data, Bar):
            current_price = float(data.close)
        else:
            return None

        inst_key = str(instrument.id)
        history = self._price_history[inst_key]

        # Update history
        history.append(current_price)
        if len(history) > self.config.lookback_trades:
            history.pop(0)

        # Need enough history to estimate FV
        if len(history) < self.config.lookback_trades:
            return None

        fair_value = sum(history) / len(history)
        deviation = current_price - fair_value

        if deviation < -self.config.entry_threshold:
            # Price is below FV — buy
            return Signal(
                direction="BUY",
                confidence=min(0.5 + abs(deviation), 0.95),
                target_price=fair_value,
                metadata={"fair_value": fair_value, "deviation": deviation},
            )
        elif deviation > self.config.entry_threshold:
            # Price is above FV — sell
            return Signal(
                direction="SELL",
                confidence=min(0.5 + abs(deviation), 0.95),
                target_price=fair_value,
                metadata={"fair_value": fair_value, "deviation": deviation},
            )

        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/backtesting/test_example_strategies.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/strategies/examples/fair_value_mean_reversion.py tests/backtesting/test_example_strategies.py
git commit -m "feat: add fair value mean reversion example strategy"
```

---

### Task 17: Charts Module (Optional Reporting)

**Files:**
- Create: `src/layer1_research/backtesting/reporting/charts.py`

- [ ] **Step 1: Implement chart generation**

```python
# src/layer1_research/backtesting/reporting/charts.py
"""Chart generation for backtest results.

Generates matplotlib charts saved to output/backtests/.
Only imported when --charts flag is used, so matplotlib
is not required for basic backtesting.
"""
import os
from pathlib import Path
from typing import Optional

from src.layer1_research.backtesting.reporting.metrics import BacktestSummary


def generate_charts(
    summary: BacktestSummary,
    equity_curve: Optional[list[tuple[str, float]]] = None,
    trade_returns: Optional[list[float]] = None,
    calibration_data: Optional[list[tuple[float, int]]] = None,
    exposure_over_time: Optional[list[tuple[str, float]]] = None,
    output_dir: str = "output/backtests",
):
    """Generate and save backtest charts.

    Args:
        summary: BacktestSummary with aggregate results.
        equity_curve: List of (date_str, equity_value) tuples.
        output_dir: Directory to save chart files.
    """
    import matplotlib.pyplot as plt

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    prefix = f"{output_dir}/{summary.strategy_name}"

    # Equity curve
    if equity_curve:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), height_ratios=[3, 1])

        dates = [e[0] for e in equity_curve]
        values = [e[1] for e in equity_curve]

        ax1.plot(dates, values, linewidth=1.5)
        ax1.set_title(f"{summary.strategy_name} — Equity Curve")
        ax1.set_ylabel("Portfolio Value (USDC)")
        ax1.axhline(y=summary.starting_capital, color="gray", linestyle="--", alpha=0.5)

        # Drawdown
        peak = summary.starting_capital
        drawdowns = []
        for v in values:
            peak = max(peak, v)
            dd = (v - peak) / peak * 100 if peak > 0 else 0
            drawdowns.append(dd)

        ax2.fill_between(range(len(drawdowns)), drawdowns, 0, alpha=0.3, color="red")
        ax2.set_ylabel("Drawdown %")
        ax2.set_xlabel("Time")

        plt.tight_layout()
        plt.savefig(f"{prefix}_equity.png", dpi=150)
        plt.close()
        print(f"  Saved: {prefix}_equity.png")

    # Per-market P&L bar chart
    if summary.per_market:
        fig, ax = plt.subplots(figsize=(10, max(4, len(summary.per_market) * 0.4)))

        markets = sorted(
            summary.per_market.items(),
            key=lambda x: x[1].get("pnl", 0),
        )
        names = [m[0][:40] for m in markets]
        pnls = [m[1].get("pnl", 0) for m in markets]
        colors = ["green" if p >= 0 else "red" for p in pnls]

        ax.barh(names, pnls, color=colors, alpha=0.7)
        ax.set_xlabel("P&L (USDC)")
        ax.set_title(f"{summary.strategy_name} — P&L by Market")
        plt.tight_layout()
        plt.savefig(f"{prefix}_per_market.png", dpi=150)
        plt.close()
        print(f"  Saved: {prefix}_per_market.png")

    # Returns distribution histogram
    if trade_returns:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.hist(trade_returns, bins=50, alpha=0.7, edgecolor="black")
        ax.axvline(x=0, color="red", linestyle="--", alpha=0.5)
        ax.set_xlabel("Return per Trade")
        ax.set_ylabel("Frequency")
        ax.set_title(f"{summary.strategy_name} — Returns Distribution")
        plt.tight_layout()
        plt.savefig(f"{prefix}_returns_dist.png", dpi=150)
        plt.close()
        print(f"  Saved: {prefix}_returns_dist.png")

    # Calibration plot (signal confidence vs actual win rate)
    if calibration_data:
        # calibration_data: list of (predicted_prob, actual_outcome 0/1)
        import numpy as np
        preds = [d[0] for d in calibration_data]
        actuals = [d[1] for d in calibration_data]

        # Bin predictions into deciles
        bins = np.linspace(0, 1, 11)
        bin_indices = np.digitize(preds, bins) - 1
        bin_means_pred = []
        bin_means_actual = []
        for i in range(10):
            mask = [j for j, b in enumerate(bin_indices) if b == i]
            if mask:
                bin_means_pred.append(np.mean([preds[j] for j in mask]))
                bin_means_actual.append(np.mean([actuals[j] for j in mask]))

        fig, ax = plt.subplots(figsize=(8, 8))
        ax.plot([0, 1], [0, 1], "k--", alpha=0.5, label="Perfect calibration")
        ax.scatter(bin_means_pred, bin_means_actual, s=80, zorder=5)
        ax.set_xlabel("Predicted Probability")
        ax.set_ylabel("Actual Win Rate")
        ax.set_title(f"{summary.strategy_name} — Calibration")
        ax.legend()
        plt.tight_layout()
        plt.savefig(f"{prefix}_calibration.png", dpi=150)
        plt.close()
        print(f"  Saved: {prefix}_calibration.png")

    # Exposure over time
    if exposure_over_time:
        fig, ax = plt.subplots(figsize=(12, 5))
        dates = [e[0] for e in exposure_over_time]
        values = [e[1] for e in exposure_over_time]
        ax.fill_between(range(len(values)), values, alpha=0.4)
        ax.set_ylabel("Capital Deployed (USDC)")
        ax.set_title(f"{summary.strategy_name} — Exposure Over Time")
        plt.tight_layout()
        plt.savefig(f"{prefix}_exposure.png", dpi=150)
        plt.close()
        print(f"  Saved: {prefix}_exposure.png")

    print(f"  Charts saved to {output_dir}/")
```

- [ ] **Step 2: Commit**

```bash
git add src/layer1_research/backtesting/reporting/charts.py
git commit -m "feat: add chart generation for backtest results (equity curve, per-market P&L)"
```

---

### Task 18: Final Wiring & Full Test Suite Run

- [ ] **Step 1: Run the full test suite**

```bash
pytest tests/backtesting/ -v
```

Expected: all tests PASS

- [ ] **Step 2: Verify the CLI scripts work end-to-end with fixture data**

```bash
# Create a temp catalog from fixture data (manual smoke test)
python -c "
from tests.backtesting.fixtures.sample_data import create_becker_fixture_dir
from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
from src.layer1_research.backtesting.data.catalog import build_catalog
d = create_becker_fixture_dir()
loader = BeckerParquetLoader(d)
result = build_catalog(loader, '/tmp/test_catalog')
print(f'Markets: {result.markets_loaded}, Trades: {result.trades_loaded}')
"
```

- [ ] **Step 3: Final commit with any adjustments**

```bash
git add -A
git commit -m "feat: complete backtesting engine v1 — data loaders, strategies, runner, reporting"
```
