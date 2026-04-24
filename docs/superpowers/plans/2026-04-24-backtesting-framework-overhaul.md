# Backtesting Framework Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing Nautilus-based backtest into a correct, trustworthy, notebook-driven framework by fixing silent failures, adding a programmatic `BacktestResult` with trade/signal analytics, computing real metrics, and migrating `scripts/` → `notebooks/` grouped by layer.

**Architecture:** Keep the current module layout under `src/layer1_research/backtesting/`. Strip metric computation out of the runner; introduce `results.py` with `SignalSnapshot`, `Trade`, and `BacktestResult`. Use Nautilus's `PortfolioAnalyzer` for standard perf stats (Sharpe/Sortino/DD/returns); compute everything else (win rate, edge, per-market) from our own `trades`/`signals` DataFrames. Add correctness-test strategies (`HoldCash`, `RandomTrader`) that validate the engine itself. Delete `scripts/`; move each to `notebooks/layer{0,1,4}_*/`.

**Tech Stack:** Python 3.12, `nautilus_trader`, `duckdb`, `pandas`, `matplotlib`, `mlflow`, `pytest`. Notebooks are `.ipynb` executed top-to-bottom.

**Working branch:** All tasks commit to `feat/backtesting-overhaul`. Never to `main`.

---

## File Structure

**Modified:**
- `src/layer1_research/backtesting/runner.py` — orchestration only; returns `BacktestResult`; no metrics; no MLflow
- `src/layer1_research/backtesting/config.py` — unchanged (verified)
- `src/layer1_research/backtesting/strategies/base.py` — remove `capital=10_000.0` fallback; add `_signal_log`
- `src/layer1_research/backtesting/strategies/signal.py` — unchanged
- `src/layer1_research/backtesting/data/loaders/becker_parquet.py` — remove price clamp (line 164)
- `src/layer1_research/backtesting/data/catalog.py` — replace magic `0.05` with named constant
- `src/layer1_research/backtesting/reporting/metrics.py` — replace `BacktestSummary` with `BacktestMetrics` + `compute_metrics()`; keep `brier_score` and `fee_drag` helpers
- `src/layer1_research/backtesting/reporting/cli_report.py` — print new `BacktestMetrics`
- `tests/backtesting/test_runner_e2e.py` — assert on `BacktestResult`
- `tests/backtesting/test_metrics.py` — absorb new cases
- `tests/backtesting/test_becker_loader.py` — assert raise on out-of-range prices

**Created:**
- `src/layer1_research/backtesting/results.py` — `SignalSnapshot`, `Trade`, `BacktestResult`
- `src/layer1_research/backtesting/reporting/charts.py` — equity, drawdown, calibration, per-market, histogram
- `src/layer1_research/backtesting/strategies/examples/hold_cash.py` — `HoldCashStrategy`
- `src/layer1_research/backtesting/strategies/examples/random_trader.py` — `RandomTraderStrategy`
- `tests/backtesting/test_results.py` — `BacktestResult`, `Trade`, `SignalSnapshot`
- `tests/backtesting/test_metrics_computation.py` — `compute_metrics` on fixtures
- `tests/backtesting/test_correctness_strategies.py` — `HoldCash`/`Random` invariants
- `tests/backtesting/test_signal_capture.py` — `_signal_log` population
- `notebooks/layer0_ingestion/check_allowances.ipynb`
- `notebooks/layer0_ingestion/explore_market.ipynb`
- `notebooks/layer0_ingestion/view_markets.ipynb`
- `notebooks/layer0_ingestion/test_connection.ipynb`
- `notebooks/layer0_ingestion/debug_uma_signal.ipynb`
- `notebooks/layer1_research/01_build_catalog.ipynb`
- `notebooks/layer1_research/03_run_backtest_fair_value_mr.ipynb`
- `notebooks/layer1_research/04_run_backtest_hold_cash.ipynb`
- `notebooks/layer1_research/05_run_backtest_random_trader.ipynb`
- `notebooks/layer1_research/99_analyze_trades.ipynb`
- `notebooks/layer4_execution/preflight.ipynb`

**Deleted:** the entire `scripts/` directory (after all notebooks are in place and verified).

---

## Task 0: Create the feature branch

**Files:** none.

- [ ] **Step 1: Create and check out the branch**

Run:
```bash
git checkout -b feat/backtesting-overhaul
git branch --show-current
```
Expected: prints `feat/backtesting-overhaul`.

- [ ] **Step 2: Verify test suite is green on the starting commit**

Run:
```bash
source .venv/bin/activate && pytest tests/backtesting -x -q
```
Expected: all tests pass. If any fail on the starting state, stop and report — the overhaul needs a green baseline.

---

## Task 1: Remove silent price clamp in Becker loader

**Files:**
- Modify: `src/layer1_research/backtesting/data/loaders/becker_parquet.py:164`
- Test: `tests/backtesting/test_becker_loader.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/backtesting/test_becker_loader.py`:

```python
def test_becker_loader_raises_on_zero_price(tmp_path):
    """Out-of-range raw prices must raise, not be silently clamped."""
    import os
    import duckdb

    d = str(tmp_path)
    os.makedirs(f"{d}/polymarket/markets", exist_ok=True)
    os.makedirs(f"{d}/polymarket/trades", exist_ok=True)
    os.makedirs(f"{d}/polymarket/blocks", exist_ok=True)

    con = duckdb.connect()
    con.execute(f"""
        COPY (
            SELECT 'cond_bad' as condition_id, 'q' as question, '[\"Yes\",\"No\"]' as outcomes,
                   '[\"tok_bad\",\"tok_bad_no\"]' as clob_token_ids, 1000.0 as volume,
                   1 as active, 0 as closed, '2024-12-31T00:00:00Z' as end_date,
                   '2024-01-01T00:00:00Z' as created_at
        ) TO '{d}/polymarket/markets/markets.parquet' (FORMAT PARQUET)
    """)
    # taker_amount=0 makes price = maker_amount/taker_amount division-by-zero or inf
    # We'll use a zero maker_amount instead -> price = 0.0 -> RawTrade should raise
    con.execute(f"""
        COPY (
            SELECT 50000000 as block_number, 'tx' as transaction_hash, 0 as log_index,
                   'ord' as order_hash, '0xm' as maker, '0xt' as taker,
                   '0' as maker_asset_id, 'tok_bad' as taker_asset_id,
                   0 as maker_amount, 1000000 as taker_amount, 0 as fee
        ) TO '{d}/polymarket/trades/trades.parquet' (FORMAT PARQUET)
    """)
    con.execute(f"""
        COPY (
            SELECT 50000000 as block_number, 1718448000 as timestamp
        ) TO '{d}/polymarket/blocks/blocks.parquet' (FORMAT PARQUET)
    """)
    con.close()

    from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
    loader = BeckerParquetLoader(d)
    with pytest.raises(ValueError, match="price must be between"):
        list(loader.get_trades("tok_bad"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/backtesting/test_becker_loader.py::test_becker_loader_raises_on_zero_price -v`
Expected: FAIL (the current code silently clamps 0.0 → 0.001, so no exception is raised).

- [ ] **Step 3: Remove the price clamp**

Edit `src/layer1_research/backtesting/data/loaders/becker_parquet.py`. In both `get_trades` (around line 164) and `get_trades_bulk` (around line 193), delete the line:

```python
price = max(0.001, min(1.0, price))
```

Leave the surrounding `yield RawTrade(...)` call unchanged — `RawTrade.__post_init__` now enforces the range.

- [ ] **Step 4: Run tests to confirm the raise**

Run: `pytest tests/backtesting/test_becker_loader.py -v`
Expected: the new test passes; existing tests still pass (the fixture prices are all in range).

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/data/loaders/becker_parquet.py tests/backtesting/test_becker_loader.py
git commit -m "backtest: raise on out-of-range raw trade prices instead of silent clamp"
```

---

## Task 2: Name the magic trade-size skip threshold

**Files:**
- Modify: `src/layer1_research/backtesting/data/catalog.py`

- [ ] **Step 1: Edit `catalog.py`**

Near the top of the file, add a module-level constant after the imports block:

```python
# Smallest representable trade size given the instrument's size_precision=1
# (see data/instruments.py). Trades below this round to 0 at precision=1
# and would be rejected by Nautilus's Quantity constructor.
MIN_TRADE_SIZE_PRECISION = 0.05
```

Then find the line (around line 68):
```python
if raw_trade.size < 0.05:
    continue  # Too small to represent at precision=1
```
Replace with:
```python
if raw_trade.size < MIN_TRADE_SIZE_PRECISION:
    continue
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/backtesting -x -q`
Expected: all pass (no behavioural change).

- [ ] **Step 3: Commit**

```bash
git add src/layer1_research/backtesting/data/catalog.py
git commit -m "backtest: name the magic trade-size skip threshold"
```

---

## Task 3: Add `SignalSnapshot` dataclass

**Files:**
- Create: `src/layer1_research/backtesting/results.py`
- Create: `tests/backtesting/test_results.py`

- [ ] **Step 1: Write the failing test**

Create `tests/backtesting/test_results.py`:

```python
"""Tests for BacktestResult and its components."""
import pytest
from datetime import datetime, timezone


def test_signal_snapshot_buy_edge():
    from src.layer1_research.backtesting.results import SignalSnapshot
    snap = SignalSnapshot(
        ts=datetime(2024, 6, 1, tzinfo=timezone.utc),
        instrument_id="tok_yes_001.POLYMARKET",
        direction="BUY",
        market_price=0.40,
        confidence=0.70,
        target_price=0.55,
        size=100.0,
        client_order_id="O-1",
    )
    # BUY: edge = confidence - market_price = 0.70 - 0.40 = 0.30
    assert snap.edge_at_order == pytest.approx(0.30)


def test_signal_snapshot_sell_edge():
    from src.layer1_research.backtesting.results import SignalSnapshot
    snap = SignalSnapshot(
        ts=datetime(2024, 6, 1, tzinfo=timezone.utc),
        instrument_id="tok_yes_001.POLYMARKET",
        direction="SELL",
        market_price=0.70,
        confidence=0.65,  # 65% sure YES is overpriced, i.e. P(YES)=0.35
        target_price=0.55,
        size=100.0,
        client_order_id="O-2",
    )
    # SELL: edge = market_price - (1 - confidence) = 0.70 - 0.35 = 0.35
    assert snap.edge_at_order == pytest.approx(0.35)


def test_signal_snapshot_flat_edge():
    from src.layer1_research.backtesting.results import SignalSnapshot
    snap = SignalSnapshot(
        ts=datetime(2024, 6, 1, tzinfo=timezone.utc),
        instrument_id="tok.POLYMARKET",
        direction="FLAT",
        market_price=0.50, confidence=0.50, target_price=0.50, size=0.0,
        client_order_id=None,
    )
    assert snap.edge_at_order == 0.0


def test_signal_snapshot_rejects_bad_direction():
    from src.layer1_research.backtesting.results import SignalSnapshot
    with pytest.raises(ValueError, match="direction"):
        SignalSnapshot(
            ts=datetime(2024, 6, 1, tzinfo=timezone.utc),
            instrument_id="tok.POLYMARKET",
            direction="HOLD",
            market_price=0.50, confidence=0.50, target_price=0.50, size=0.0,
            client_order_id=None,
        )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/backtesting/test_results.py -v`
Expected: FAIL — `results` module not yet created.

- [ ] **Step 3: Create the module with `SignalSnapshot`**

Create `src/layer1_research/backtesting/results.py`:

```python
"""Programmatic backtest result object.

BacktestRunner returns a single BacktestResult. All downstream consumers
(metrics, charts, MLflow logger, notebooks) read from it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class SignalSnapshot:
    """A single signal emitted by a strategy, captured at the moment of emission."""

    ts: datetime
    instrument_id: str
    direction: str                 # "BUY" / "SELL" / "FLAT"
    market_price: float            # observed price at signal time
    confidence: float              # strategy's P(signal is right), in [0, 1]
    target_price: float            # strategy's fair value
    size: float
    client_order_id: Optional[str]

    def __post_init__(self):
        if self.direction not in ("BUY", "SELL", "FLAT"):
            raise ValueError(
                f"direction must be BUY, SELL, or FLAT, got '{self.direction}'"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0, 1], got {self.confidence}"
            )

    @property
    def edge_at_order(self) -> float:
        """Expected edge per unit at signal time.

        BUY:  confidence - market_price
            (we think P(YES wins) = confidence; buying at market_price nets
             confidence - market_price in expectation)
        SELL: market_price - (1 - confidence)
            (we think P(YES wins) = 1 - confidence; selling at market_price nets
             market_price - (1 - confidence) in expectation)
        FLAT: 0.0 (no position taken)
        """
        if self.direction == "BUY":
            return self.confidence - self.market_price
        if self.direction == "SELL":
            return self.market_price - (1.0 - self.confidence)
        return 0.0
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/backtesting/test_results.py -v`
Expected: all three tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/results.py tests/backtesting/test_results.py
git commit -m "backtest: add SignalSnapshot dataclass with edge_at_order derivation"
```

---

## Task 4: Add `Trade` dataclass

**Files:**
- Modify: `src/layer1_research/backtesting/results.py`
- Modify: `tests/backtesting/test_results.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/backtesting/test_results.py`:

```python
def test_trade_long_realized_edge():
    from src.layer1_research.backtesting.results import Trade
    t = Trade(
        instrument_id="tok.POLYMARKET",
        direction="LONG",
        entry_ts=datetime(2024, 6, 1, tzinfo=timezone.utc),
        exit_ts=datetime(2024, 6, 2, tzinfo=timezone.utc),
        entry_price=0.40,
        exit_price=0.55,
        size=100.0,
        fees=1.50,
        gross_pnl=15.0,
        net_pnl=13.5,
        edge_at_entry=0.30,
        slippage_bps=0.0,
        signal_confidence=0.70,
    )
    # LONG realized_edge = exit - entry = 0.55 - 0.40 = 0.15
    assert t.realized_edge == pytest.approx(0.15)


def test_trade_short_realized_edge():
    from src.layer1_research.backtesting.results import Trade
    t = Trade(
        instrument_id="tok.POLYMARKET",
        direction="SHORT",
        entry_ts=datetime(2024, 6, 1, tzinfo=timezone.utc),
        exit_ts=datetime(2024, 6, 2, tzinfo=timezone.utc),
        entry_price=0.70,
        exit_price=0.55,
        size=100.0,
        fees=1.50,
        gross_pnl=15.0,
        net_pnl=13.5,
        edge_at_entry=0.35,
        slippage_bps=0.0,
        signal_confidence=0.65,
    )
    # SHORT realized_edge = entry - exit = 0.70 - 0.55 = 0.15
    assert t.realized_edge == pytest.approx(0.15)


def test_trade_open_position_realized_edge_none():
    from src.layer1_research.backtesting.results import Trade
    t = Trade(
        instrument_id="tok.POLYMARKET", direction="LONG",
        entry_ts=datetime(2024, 6, 1, tzinfo=timezone.utc),
        exit_ts=None,
        entry_price=0.40, exit_price=None, size=100.0,
        fees=1.50, gross_pnl=0.0, net_pnl=-1.5,
        edge_at_entry=0.30, slippage_bps=0.0, signal_confidence=0.70,
    )
    assert t.realized_edge is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/backtesting/test_results.py -v -k trade`
Expected: FAIL — `Trade` not defined.

- [ ] **Step 3: Add `Trade` to `results.py`**

Append to `src/layer1_research/backtesting/results.py`:

```python
@dataclass(frozen=True)
class Trade:
    """Round-trip trade: entry fill → exit fill (or still-open at EOB)."""

    instrument_id: str
    direction: str                 # "LONG" or "SHORT"
    entry_ts: datetime
    exit_ts: Optional[datetime]    # None if still open at end of backtest
    entry_price: float
    exit_price: Optional[float]    # None if still open
    size: float
    fees: float
    gross_pnl: float
    net_pnl: float                 # gross_pnl - fees
    edge_at_entry: float           # from the SignalSnapshot that opened it
    slippage_bps: float            # fill price vs. signal market_price
    signal_confidence: float

    def __post_init__(self):
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError(
                f"direction must be LONG or SHORT, got '{self.direction}'"
            )

    @property
    def realized_edge(self) -> Optional[float]:
        """Actual edge captured at exit. None if position is still open."""
        if self.exit_price is None:
            return None
        if self.direction == "LONG":
            return self.exit_price - self.entry_price
        return self.entry_price - self.exit_price   # SHORT
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/backtesting/test_results.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/results.py tests/backtesting/test_results.py
git commit -m "backtest: add Trade dataclass with realized_edge property"
```

---

## Task 5: `BacktestResult` — scaffold, column cleaning, equity curve

**Files:**
- Modify: `src/layer1_research/backtesting/results.py`
- Modify: `tests/backtesting/test_results.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/backtesting/test_results.py`:

```python
def test_parse_usd_column():
    from src.layer1_research.backtesting.results import _parse_usd_series
    import pandas as pd
    s = pd.Series(["5.01 USD", "0.00 USD", "-2.50 USD", ""])
    out = _parse_usd_series(s)
    assert list(out) == [pytest.approx(5.01), pytest.approx(0.0),
                         pytest.approx(-2.50), pytest.approx(0.0)]


def test_backtest_result_construction_minimal():
    """BacktestResult can be built from minimal Nautilus-like reports."""
    from src.layer1_research.backtesting.results import BacktestResult
    from src.layer1_research.backtesting.config import BacktestConfig
    import pandas as pd

    config = BacktestConfig(
        catalog_path="data/catalog",
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 6, 2, tzinfo=timezone.utc),
        strategy_name="test", starting_capital=10_000.0, data_mode="trade",
    )
    account = pd.DataFrame({
        "total": ["10000.00 USD", "10050.00 USD", "9980.00 USD"],
    }, index=pd.to_datetime([
        "2024-06-01T00:00:00Z", "2024-06-01T12:00:00Z", "2024-06-02T00:00:00Z",
    ], utc=True))

    result = BacktestResult(
        config=config,
        fills=pd.DataFrame(),
        positions=pd.DataFrame(),
        account=account,
        instruments=[],
        analyzer_stats={},
        signals=pd.DataFrame(),
        trades=pd.DataFrame(),
    )
    # equity_curve is built from account["total"], values are floats
    assert result.equity_curve.iloc[0] == pytest.approx(10_000.0)
    assert result.equity_curve.iloc[-1] == pytest.approx(9_980.0)
    # account["total"] is also cleaned to float
    assert result.account["total"].dtype.kind == "f"


def test_backtest_result_equity_curve_empty_account_raises():
    """Empty account report is a real error, not a silent zero."""
    from src.layer1_research.backtesting.results import BacktestResult
    from src.layer1_research.backtesting.config import BacktestConfig
    import pandas as pd

    config = BacktestConfig(
        catalog_path="data/catalog",
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 6, 2, tzinfo=timezone.utc),
        strategy_name="test", starting_capital=10_000.0, data_mode="trade",
    )
    with pytest.raises(ValueError, match="empty account"):
        BacktestResult(
            config=config,
            fills=pd.DataFrame(), positions=pd.DataFrame(),
            account=pd.DataFrame(),
            instruments=[], analyzer_stats={},
            signals=pd.DataFrame(), trades=pd.DataFrame(),
        )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/backtesting/test_results.py::test_parse_usd_column -v`
Expected: FAIL — `_parse_usd_series` not defined.

- [ ] **Step 3: Add the result class + helpers**

Append to `src/layer1_research/backtesting/results.py`:

```python
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from src.layer1_research.backtesting.config import BacktestConfig


def _parse_usd_series(s: pd.Series) -> pd.Series:
    """Convert a Nautilus money-string column ('5.01 USD') into a float column.

    Empty strings become 0.0 (Nautilus emits "" for zero commissions sometimes).
    """
    def _parse(v) -> float:
        if v is None:
            return 0.0
        s = str(v).strip()
        if not s:
            return 0.0
        return float(s.split()[0])
    return s.apply(_parse).astype(float)


_USD_COLUMNS = ("commission", "realized_pnl", "unrealized_pnl", "total", "free", "locked")


def _clean_usd_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of `df` with any known USD string columns parsed to floats."""
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    out = df.copy()
    for col in _USD_COLUMNS:
        if col in out.columns and out[col].dtype == object:
            out[col] = _parse_usd_series(out[col])
    return out


@dataclass
class BacktestResult:
    """Output of BacktestRunner.run(). Contains raw reports + derived views."""

    config: "BacktestConfig"
    fills: pd.DataFrame
    positions: pd.DataFrame
    account: pd.DataFrame
    instruments: list               # list[BinaryOption]
    analyzer_stats: dict            # from nautilus trader.analyzer
    signals: pd.DataFrame           # one row per SignalSnapshot
    trades: pd.DataFrame            # one row per Trade

    # Derived, filled by __post_init__
    equity_curve: pd.Series = None  # type: ignore[assignment]

    def __post_init__(self):
        # Clean string-money columns on the Nautilus frames
        self.fills = _clean_usd_columns(self.fills)
        self.positions = _clean_usd_columns(self.positions)
        self.account = _clean_usd_columns(self.account)

        if self.account is None or self.account.empty:
            raise ValueError(
                "BacktestResult constructed with empty account report — "
                "the engine did not record any balance snapshots"
            )
        if "total" not in self.account.columns:
            raise ValueError(
                f"account report missing 'total' column; got {list(self.account.columns)}"
            )

        # Equity curve: account['total'] series, indexed by the account df's index
        # (Nautilus uses a DatetimeIndex). Forward-fill gaps so flat periods carry
        # the previous balance.
        self.equity_curve = self.account["total"].astype(float).copy()
        self.equity_curve.name = "equity_usd"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/backtesting/test_results.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/results.py tests/backtesting/test_results.py
git commit -m "backtest: add BacktestResult with USD column cleaning and equity curve"
```

---

## Task 6: Strategy base — remove capital fallback + add signal log

**Files:**
- Modify: `src/layer1_research/backtesting/strategies/base.py`
- Create: `tests/backtesting/test_signal_capture.py`

- [ ] **Step 1: Write the failing test**

Create `tests/backtesting/test_signal_capture.py`:

```python
"""Tests that strategies capture SignalSnapshot on every emitted signal."""
import pytest
from datetime import datetime, timezone


def test_signal_log_initialized_empty():
    from src.layer1_research.backtesting.strategies.base import (
        PredictionMarketStrategy, PredictionMarketStrategyConfig,
    )
    cfg = PredictionMarketStrategyConfig(instrument_ids=[])
    strat = PredictionMarketStrategy(config=cfg)
    assert strat._signal_log == []


def test_signal_log_appends_on_act(monkeypatch):
    """Calling _act_on_signal appends a SignalSnapshot before order submission."""
    from src.layer1_research.backtesting.strategies.base import (
        PredictionMarketStrategy, PredictionMarketStrategyConfig,
    )
    from src.layer1_research.backtesting.strategies.signal import Signal

    cfg = PredictionMarketStrategyConfig(instrument_ids=[])
    strat = PredictionMarketStrategy(config=cfg)

    # Stub out submit_order and order_factory so we don't hit Nautilus
    submitted = []
    class _FakeOrder:
        client_order_id = type("C", (), {"value": "O-1"})()
    class _FakeFactory:
        def market(self, **kw): return _FakeOrder()
    strat.order_factory = _FakeFactory()
    strat.submit_order = lambda o: submitted.append(o)

    # Stub account lookup to return a deterministic balance
    class _FakeAccount:
        def balance_total(self, currency): return 10_000.0
    class _FakePortfolio:
        def account(self, venue): return _FakeAccount()
    strat.portfolio = _FakePortfolio()

    # Fake instrument with a make_qty that passes size through
    class _FakeInstrument:
        class id:
            class venue: pass
        currency = None
        def make_qty(self, s): return s
    instr = _FakeInstrument()

    class _FakeTick:
        ts_event = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1e9)
        price = 0.40

    sig = Signal(direction="BUY", confidence=0.70, target_price=0.55, size=50.0)
    strat._act_on_signal(sig, instr, _FakeTick())

    assert len(strat._signal_log) == 1
    snap = strat._signal_log[0]
    assert snap.direction == "BUY"
    assert snap.confidence == pytest.approx(0.70)
    assert snap.market_price == pytest.approx(0.40)
    assert snap.client_order_id == "O-1"
    assert len(submitted) == 1


def test_act_on_signal_raises_when_account_missing(monkeypatch):
    """No silent capital=10_000 fallback: missing account should raise."""
    from src.layer1_research.backtesting.strategies.base import (
        PredictionMarketStrategy, PredictionMarketStrategyConfig,
    )
    from src.layer1_research.backtesting.strategies.signal import Signal

    cfg = PredictionMarketStrategyConfig(instrument_ids=[])
    strat = PredictionMarketStrategy(config=cfg)

    # Portfolio that raises on .account(...)
    class _BrokenPortfolio:
        def account(self, venue): raise RuntimeError("no venue account")
    strat.portfolio = _BrokenPortfolio()

    class _FakeInstrument:
        class id:
            class venue: pass
        currency = None
    class _FakeTick:
        ts_event = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1e9)
        price = 0.40

    # Signal that requires sizer (size=None)
    sig = Signal(direction="BUY", confidence=0.70, target_price=0.55)
    with pytest.raises(RuntimeError):
        strat._act_on_signal(sig, _FakeInstrument(), _FakeTick())
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/backtesting/test_signal_capture.py -v`
Expected: FAILS — `_signal_log` not defined, and the fallback-raises test fails because the current code swallows `Exception`.

- [ ] **Step 3: Update `strategies/base.py`**

Replace the body of `_act_on_signal` and add `_signal_log` initialization. Edit `src/layer1_research/backtesting/strategies/base.py`:

Find:
```python
    def __init__(self, config: PredictionMarketStrategyConfig):
        super().__init__(config)
        self._instrument_map: dict[InstrumentId, BinaryOption] = {}
```
Replace with:
```python
    def __init__(self, config: PredictionMarketStrategyConfig):
        super().__init__(config)
        self._instrument_map: dict[InstrumentId, BinaryOption] = {}
        # Captured per emitted signal; pulled out by the runner post-run.
        self._signal_log: list = []
```

Find the whole `_act_on_signal` method and replace with:

```python
    def _act_on_signal(self, signal: Signal, instrument: BinaryOption, data):
        from datetime import datetime, timezone
        from src.layer1_research.backtesting.results import SignalSnapshot

        # Price at signal time — trade tick has .price; bar has .close.
        if hasattr(data, "price"):
            market_price = float(data.price)
        elif hasattr(data, "close"):
            market_price = float(data.close)
        else:
            raise ValueError(
                f"Cannot extract price from signal data {type(data).__name__}"
            )
        ts_ns = int(data.ts_event)
        ts = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)

        if signal.direction == "FLAT":
            # Log the flat signal for analysis, then close.
            self._signal_log.append(SignalSnapshot(
                ts=ts, instrument_id=str(instrument.id), direction="FLAT",
                market_price=market_price, confidence=signal.confidence,
                target_price=signal.target_price, size=0.0,
                client_order_id=None,
            ))
            self._close_position(instrument)
            return

        # Determine size: explicit on the signal, or computed via sizer.
        if signal.size is not None:
            size = signal.size
        else:
            account = self.portfolio.account(instrument.id.venue)
            balance = account.balance_total(instrument.currency)
            capital = float(balance)
            if self.config.sizer_mode == "kelly":
                size = kelly_size(
                    capital=capital, win_prob=signal.confidence,
                    price=signal.target_price,
                    max_fraction=self.config.kelly_max_fraction,
                )
            else:
                size = fixed_fractional_size(
                    capital=capital, fraction=self.config.fixed_fraction,
                    price=signal.target_price,
                    max_size=self.config.max_position_size,
                )

        if size <= 0:
            # Signal fired but sizer rejected — still log as FLAT-equivalent
            # so downstream metrics know the signal existed.
            self._signal_log.append(SignalSnapshot(
                ts=ts, instrument_id=str(instrument.id),
                direction=signal.direction,
                market_price=market_price, confidence=signal.confidence,
                target_price=signal.target_price, size=0.0,
                client_order_id=None,
            ))
            return

        order_side = OrderSide.BUY if signal.direction == "BUY" else OrderSide.SELL
        order = self.order_factory.market(
            instrument_id=instrument.id,
            order_side=order_side,
            quantity=instrument.make_qty(size),
        )
        client_oid = getattr(order.client_order_id, "value", str(order.client_order_id))
        self._signal_log.append(SignalSnapshot(
            ts=ts, instrument_id=str(instrument.id),
            direction=signal.direction,
            market_price=market_price, confidence=signal.confidence,
            target_price=signal.target_price, size=size,
            client_order_id=client_oid,
        ))
        self.submit_order(order)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/backtesting/test_signal_capture.py -v`
Expected: all three pass.

Run: `pytest tests/backtesting -x -q`
Expected: all pass, including existing `test_strategy_base.py` (if any test relied on the silent fallback, it will surface here — fix it by providing a real portfolio stub).

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/strategies/base.py tests/backtesting/test_signal_capture.py
git commit -m "backtest: capture SignalSnapshot per emitted signal; drop silent capital fallback"
```

---

## Task 7: Runner refactor — return `BacktestResult`, strip metrics, drop MLflow

**Files:**
- Modify: `src/layer1_research/backtesting/runner.py`
- Modify: `tests/backtesting/test_runner_e2e.py`

- [ ] **Step 1: Write the failing test**

Replace the body of `tests/backtesting/test_runner_e2e.py` with:

```python
"""End-to-end test for BacktestRunner."""
import pytest
import shutil
import tempfile
from datetime import datetime, timezone
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


def test_e2e_backtest_returns_result(becker_data_dir, catalog_dir):
    from src.layer1_research.backtesting.data.catalog import build_catalog
    from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
    from src.layer1_research.backtesting.config import BacktestConfig
    from src.layer1_research.backtesting.runner import BacktestRunner
    from src.layer1_research.backtesting.results import BacktestResult
    from src.layer1_research.backtesting.strategies.base import (
        PredictionMarketStrategy,
    )
    from src.layer1_research.backtesting.strategies.signal import Signal

    loader = BeckerParquetLoader(becker_data_dir)
    build_catalog(loader, catalog_dir)

    class BuyLowStrategy(PredictionMarketStrategy):
        def generate_signal(self, instrument, data):
            price = float(data.price) if hasattr(data, "price") else float(data.close)
            if price < 0.40:
                return Signal(direction="BUY", confidence=0.65,
                              target_price=price, size=10.0)
            return None

    config = BacktestConfig(
        catalog_path=catalog_dir,
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 7, 1, tzinfo=timezone.utc),
        strategy_name="buy_low",
        starting_capital=10_000.0,
        data_mode="trade",
    )
    runner = BacktestRunner(config)
    result = runner.run(BuyLowStrategy)

    assert isinstance(result, BacktestResult)
    assert result.config.strategy_name == "buy_low"
    assert result.config.starting_capital == 10_000.0
    # account report non-empty + equity curve populated
    assert not result.account.empty
    assert len(result.equity_curve) > 0
    # signals captured (even if 0 — should be a DataFrame, not None)
    assert result.signals is not None
    assert result.trades is not None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/backtesting/test_runner_e2e.py -v`
Expected: FAIL (runner still returns old `BacktestSummary`).

- [ ] **Step 3: Rewrite `runner.py`**

Replace the entire contents of `src/layer1_research/backtesting/runner.py` with:

```python
"""Backtest runner: orchestrates engine + strategy; returns BacktestResult.

No metric computation, no MLflow calls — those live in reporting/metrics.py
and on the BacktestResult object itself (result.to_mlflow()).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Type

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import MakerTakerFeeModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.risk.config import RiskEngineConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import BinaryOption
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.layer1_research.backtesting.config import BacktestConfig
from src.layer1_research.backtesting.execution.fill_model import PredictionMarketFillModel
from src.layer1_research.backtesting.results import BacktestResult
from src.layer1_research.backtesting.strategies.base import (
    PredictionMarketStrategy, PredictionMarketStrategyConfig,
)

POLYMARKET_VENUE = Venue("POLYMARKET")


def _to_datetime_utc(ts):
    """Normalize a timestamp to a tz-aware datetime in UTC.

    Nautilus fill rows come through with ts_event as either an int-ns value,
    a pandas Timestamp, or a python datetime depending on report version.
    """
    from datetime import datetime as _dt, timezone as _tz
    if isinstance(ts, _dt):
        return ts if ts.tzinfo else ts.replace(tzinfo=_tz.utc)
    if isinstance(ts, (int, float)):
        return _dt.fromtimestamp(ts / 1e9, tz=_tz.utc)
    # pandas Timestamp
    py = getattr(ts, "to_pydatetime", None)
    if callable(py):
        dt = py()
        return dt if dt.tzinfo else dt.replace(tzinfo=_tz.utc)
    raise TypeError(f"cannot convert ts to datetime: {ts!r} ({type(ts).__name__})")


def _parse_order_side(raw) -> str:
    """Return 'BUY' or 'SELL' from any reasonable Nautilus order_side cell."""
    s = str(raw).upper().split(".")[-1].strip()
    if s not in ("BUY", "SELL"):
        raise ValueError(f"unexpected order_side value: {raw!r}")
    return s


class BacktestRunner:
    """Orchestrates a backtest run: data -> engine -> result."""

    def __init__(self, config: BacktestConfig):
        self._config = config

    def run(self, strategy_class: Type[PredictionMarketStrategy]) -> BacktestResult:
        catalog = ParquetDataCatalog(self._config.catalog_path)

        engine_config = BacktestEngineConfig(
            logging=LoggingConfig(log_level="WARNING"),
            risk_engine=RiskEngineConfig(bypass=True),
        )
        engine = BacktestEngine(config=engine_config)

        fill_model = None
        if self._config.fill_model is not None:
            fill_model = PredictionMarketFillModel(self._config.fill_model)

        engine.add_venue(
            venue=POLYMARKET_VENUE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(self._config.starting_capital, USD)],
            fee_model=MakerTakerFeeModel(),
            fill_model=fill_model,
        )

        instruments = self._load_instruments(catalog)
        for inst in instruments:
            engine.add_instrument(inst)

        instrument_id_strs = [str(inst.id) for inst in instruments]
        ticks = catalog.trade_ticks(instrument_ids=instrument_id_strs)
        start_ns = int(self._config.start.timestamp() * 1e9)
        end_ns = int(self._config.end.timestamp() * 1e9)
        filtered_ticks = [t for t in ticks if start_ns <= t.ts_event <= end_ns]
        if filtered_ticks:
            engine.add_data(filtered_ticks)

        strategy = self._build_strategy(strategy_class, instrument_id_strs)
        engine.add_strategy(strategy)

        engine.run()

        fills = engine.trader.generate_fills_report()
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(POLYMARKET_VENUE)

        analyzer_stats = self._collect_analyzer_stats(engine)
        signals_df = self._signal_log_to_df(strategy._signal_log)
        trades_df = self._fills_to_trades(fills, signals_df)

        result = BacktestResult(
            config=self._config,
            fills=fills, positions=positions, account=account,
            instruments=instruments,
            analyzer_stats=analyzer_stats,
            signals=signals_df,
            trades=trades_df,
        )
        engine.dispose()
        return result

    # ---- helpers ------------------------------------------------------

    def _load_instruments(self, catalog: ParquetDataCatalog) -> list[BinaryOption]:
        fee_rate = Decimal(str(self._config.fee_rate_bps / 10_000))
        raw = catalog.instruments()
        if self._config.markets:
            raw = [
                inst for inst in raw
                if any(m in str(inst.id) for m in self._config.markets)
            ]
        rebuilt = []
        for inst in raw:
            rebuilt.append(BinaryOption(
                instrument_id=inst.id, raw_symbol=inst.raw_symbol,
                asset_class=inst.asset_class, currency=inst.quote_currency,
                price_precision=inst.price_precision,
                size_precision=inst.size_precision,
                price_increment=inst.price_increment,
                size_increment=inst.size_increment,
                activation_ns=inst.activation_ns,
                expiration_ns=inst.expiration_ns,
                ts_event=inst.ts_event, ts_init=inst.ts_init,
                maker_fee=fee_rate, taker_fee=fee_rate,
                outcome=inst.outcome,
            ))
        return rebuilt

    def _build_strategy(
        self, strategy_class: Type[PredictionMarketStrategy],
        instrument_id_strs: list[str],
    ) -> PredictionMarketStrategy:
        import inspect
        init_sig = inspect.signature(strategy_class.__init__)
        config_param = init_sig.parameters.get("config")
        if config_param and config_param.annotation is not inspect.Parameter.empty:
            config_cls = config_param.annotation
        else:
            config_cls = PredictionMarketStrategyConfig

        kwargs = dict(
            instrument_ids=instrument_id_strs,
            fee_rate_bps=self._config.fee_rate_bps,
            sizer_mode=self._config.position_sizer,
        )
        kwargs.update(self._config.strategy_params)
        return strategy_class(config=config_cls(**kwargs))

    def _collect_analyzer_stats(self, engine: BacktestEngine) -> dict:
        """Pull scalar perf stats from Nautilus's PortfolioAnalyzer.

        We grab the full dict; reporting.metrics decides which keys to surface.
        """
        analyzer = engine.trader.analyzer
        stats = {}
        try:
            stats.update(analyzer.get_performance_stats_pnls(USD))
        except Exception as e:
            raise RuntimeError(
                f"failed to read analyzer pnl stats: {e}"
            ) from e
        try:
            stats.update(analyzer.get_performance_stats_returns())
        except Exception as e:
            # Returns stats may be empty if no positions closed; that's fine,
            # but we still propagate if the call itself errors unexpectedly.
            # Empty returns -> analyzer raises; re-raise only if the error is
            # not about missing data.
            msg = str(e).lower()
            if "empty" not in msg and "no returns" not in msg:
                raise
        try:
            stats.update(analyzer.get_performance_stats_general())
        except AttributeError:
            # Older Nautilus versions may not expose this method; OK to skip.
            pass
        return stats

    def _signal_log_to_df(self, log: list) -> pd.DataFrame:
        if not log:
            return pd.DataFrame(columns=[
                "ts", "instrument_id", "direction", "market_price",
                "confidence", "target_price", "size", "client_order_id",
                "edge_at_order",
            ])
        rows = [{
            "ts": s.ts, "instrument_id": s.instrument_id,
            "direction": s.direction, "market_price": s.market_price,
            "confidence": s.confidence, "target_price": s.target_price,
            "size": s.size, "client_order_id": s.client_order_id,
            "edge_at_order": s.edge_at_order,
        } for s in log]
        return pd.DataFrame(rows).set_index("ts").sort_index()

    def _fills_to_trades(
        self, fills: pd.DataFrame, signals_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Pair entry/exit fills per instrument into round-trip Trade rows.

        Uses OmsType.NETTING: a position opens on the first fill and closes
        when quantity returns to zero. We walk fills in time order per
        instrument and emit one Trade per (entry, exit) cycle.
        """
        cols = [
            "instrument_id", "direction", "entry_ts", "exit_ts",
            "entry_price", "exit_price", "size", "fees",
            "gross_pnl", "net_pnl", "edge_at_entry", "slippage_bps",
            "signal_confidence", "realized_edge",
        ]
        if fills is None or fills.empty:
            return pd.DataFrame(columns=cols)

        # Nautilus fill columns we rely on: instrument_id, order_side,
        # last_px, last_qty, ts_event, commission, client_order_id
        f = fills.copy().sort_values("ts_event")
        trades_rows = []

        for inst_id, group in f.groupby("instrument_id"):
            position = 0.0          # signed (LONG +, SHORT -)
            entry_price = None
            entry_ts = None
            entry_fees = 0.0
            entry_client_oid = None
            entry_size_abs = 0.0

            for _, fill in group.iterrows():
                side = _parse_order_side(fill["order_side"])
                qty = float(fill["last_qty"])
                px = float(fill["last_px"])
                fee = float(fill.get("commission", 0.0) or 0.0)
                ts = _to_datetime_utc(fill["ts_event"])
                client_oid = fill.get("client_order_id")

                signed = qty if side == "BUY" else -qty
                new_position = position + signed

                if position == 0.0 and new_position != 0.0:
                    # opening fill
                    entry_price = px
                    entry_ts = ts
                    entry_fees = fee
                    entry_client_oid = client_oid
                    entry_size_abs = abs(signed)
                elif position != 0.0 and (position > 0) != (new_position > 0) and new_position != 0.0:
                    # flip: close existing, open opposite — emit close + start new
                    trades_rows.append(self._build_trade_row(
                        inst_id, position > 0, entry_ts, ts,
                        entry_price, px, entry_size_abs,
                        entry_fees + fee, entry_client_oid, signals_df,
                    ))
                    entry_price = px
                    entry_ts = ts
                    entry_fees = 0.0
                    entry_client_oid = client_oid
                    entry_size_abs = abs(new_position)
                elif new_position == 0.0 and position != 0.0:
                    # closing fill
                    trades_rows.append(self._build_trade_row(
                        inst_id, position > 0, entry_ts, ts,
                        entry_price, px, entry_size_abs,
                        entry_fees + fee, entry_client_oid, signals_df,
                    ))
                    entry_price = None
                    entry_ts = None
                    entry_fees = 0.0
                    entry_client_oid = None
                    entry_size_abs = 0.0
                else:
                    # same-direction add (accumulate) or same-direction partial
                    entry_fees += fee
                    # weighted-average entry price for adds
                    if (position > 0) == (signed > 0):
                        total = entry_size_abs + abs(signed)
                        entry_price = (
                            (entry_price * entry_size_abs + px * abs(signed)) / total
                        )
                        entry_size_abs = total

                position = new_position

            # Still-open position at end of fills
            if position != 0.0 and entry_ts is not None:
                trades_rows.append(self._build_trade_row(
                    inst_id, position > 0, entry_ts, None,
                    entry_price, None, entry_size_abs,
                    entry_fees, entry_client_oid, signals_df,
                ))

        if not trades_rows:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame(trades_rows)[cols]

    @staticmethod
    def _build_trade_row(
        inst_id, is_long, entry_ts, exit_ts, entry_px, exit_px, size,
        fees, entry_client_oid, signals_df,
    ) -> dict:
        direction = "LONG" if is_long else "SHORT"
        if exit_px is None:
            gross = 0.0
        elif is_long:
            gross = (exit_px - entry_px) * size
        else:
            gross = (entry_px - exit_px) * size
        net = gross - fees

        edge_at_entry = 0.0
        signal_confidence = 0.0
        slippage_bps = 0.0
        if signals_df is not None and not signals_df.empty and entry_client_oid is not None:
            match = signals_df[signals_df["client_order_id"] == entry_client_oid]
            if not match.empty:
                row = match.iloc[0]
                edge_at_entry = float(row["edge_at_order"])
                signal_confidence = float(row["confidence"])
                signal_price = float(row["market_price"])
                if signal_price > 0:
                    slippage_bps = (entry_px - signal_price) / signal_price * 10_000.0
                    if not is_long:
                        slippage_bps = -slippage_bps

        realized_edge = None
        if exit_px is not None:
            realized_edge = (exit_px - entry_px) if is_long else (entry_px - exit_px)

        return {
            "instrument_id": str(inst_id),
            "direction": direction,
            "entry_ts": entry_ts, "exit_ts": exit_ts,
            "entry_price": entry_px, "exit_price": exit_px,
            "size": size, "fees": fees,
            "gross_pnl": gross, "net_pnl": net,
            "edge_at_entry": edge_at_entry, "slippage_bps": slippage_bps,
            "signal_confidence": signal_confidence,
            "realized_edge": realized_edge,
        }
```

- [ ] **Step 4: Run the e2e test**

Run: `pytest tests/backtesting/test_runner_e2e.py -v`
Expected: PASS.

Run: `pytest tests/backtesting -x -q`
Expected: all pass (except `test_metrics.py` which will be rewritten in Task 8; check that the only failures there are the old `BacktestSummary` tests — leave them failing for now or temporarily skip with `pytest.skip`).

If `test_metrics.py` has failures unrelated to `BacktestSummary`, stop and investigate.

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/runner.py tests/backtesting/test_runner_e2e.py
git commit -m "backtest: runner returns BacktestResult; strip metrics and MLflow"
```

---

## Task 8: Compute real metrics — `BacktestMetrics` + `compute_metrics`

**Files:**
- Modify: `src/layer1_research/backtesting/reporting/metrics.py`
- Modify: `src/layer1_research/backtesting/results.py` — add `.metrics()` method
- Create: `tests/backtesting/test_metrics_computation.py`
- Modify: `tests/backtesting/test_metrics.py`

- [ ] **Step 1: Write failing tests**

Create `tests/backtesting/test_metrics_computation.py`:

```python
"""Tests for compute_metrics on hand-built BacktestResult fixtures."""
import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta


def _make_result(*, trades_rows, signals_rows=None, account_totals=None,
                 analyzer_stats=None):
    from src.layer1_research.backtesting.results import BacktestResult
    from src.layer1_research.backtesting.config import BacktestConfig

    config = BacktestConfig(
        catalog_path="data/catalog",
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 6, 30, tzinfo=timezone.utc),
        strategy_name="test", starting_capital=10_000.0, data_mode="trade",
    )
    if account_totals is None:
        account_totals = [10_000.0, 10_000.0]
    account = pd.DataFrame(
        {"total": [f"{v:.2f} USD" for v in account_totals]},
        index=pd.to_datetime(
            ["2024-06-01T00:00:00Z", "2024-06-30T23:59:59Z"], utc=True,
        ),
    )
    trades = pd.DataFrame(trades_rows) if trades_rows else pd.DataFrame(columns=[
        "instrument_id", "direction", "entry_ts", "exit_ts",
        "entry_price", "exit_price", "size", "fees",
        "gross_pnl", "net_pnl", "edge_at_entry", "slippage_bps",
        "signal_confidence", "realized_edge",
    ])
    signals = pd.DataFrame(signals_rows) if signals_rows else pd.DataFrame()
    return BacktestResult(
        config=config, fills=pd.DataFrame(), positions=pd.DataFrame(),
        account=account, instruments=[],
        analyzer_stats=analyzer_stats or {},
        signals=signals, trades=trades,
    )


def test_win_rate_three_of_four_winners():
    from src.layer1_research.backtesting.reporting.metrics import compute_metrics
    trades = [
        {"instrument_id": "a", "direction": "LONG",
         "entry_ts": datetime(2024, 6, 1, tzinfo=timezone.utc),
         "exit_ts": datetime(2024, 6, 2, tzinfo=timezone.utc),
         "entry_price": 0.40, "exit_price": 0.55, "size": 100.0,
         "fees": 0.5, "gross_pnl": 15.0, "net_pnl": 14.5,
         "edge_at_entry": 0.25, "slippage_bps": 0.0,
         "signal_confidence": 0.65, "realized_edge": 0.15},
        {"instrument_id": "b", "direction": "LONG",
         "entry_ts": datetime(2024, 6, 3, tzinfo=timezone.utc),
         "exit_ts": datetime(2024, 6, 4, tzinfo=timezone.utc),
         "entry_price": 0.50, "exit_price": 0.60, "size": 50.0,
         "fees": 0.3, "gross_pnl": 5.0, "net_pnl": 4.7,
         "edge_at_entry": 0.15, "slippage_bps": 0.0,
         "signal_confidence": 0.65, "realized_edge": 0.10},
        {"instrument_id": "c", "direction": "LONG",
         "entry_ts": datetime(2024, 6, 5, tzinfo=timezone.utc),
         "exit_ts": datetime(2024, 6, 6, tzinfo=timezone.utc),
         "entry_price": 0.60, "exit_price": 0.65, "size": 20.0,
         "fees": 0.1, "gross_pnl": 1.0, "net_pnl": 0.9,
         "edge_at_entry": 0.05, "slippage_bps": 0.0,
         "signal_confidence": 0.65, "realized_edge": 0.05},
        {"instrument_id": "d", "direction": "LONG",
         "entry_ts": datetime(2024, 6, 7, tzinfo=timezone.utc),
         "exit_ts": datetime(2024, 6, 8, tzinfo=timezone.utc),
         "entry_price": 0.50, "exit_price": 0.30, "size": 100.0,
         "fees": 0.5, "gross_pnl": -20.0, "net_pnl": -20.5,
         "edge_at_entry": 0.15, "slippage_bps": 0.0,
         "signal_confidence": 0.65, "realized_edge": -0.20},
    ]
    r = _make_result(trades_rows=trades)
    m = compute_metrics(r)
    assert m.total_trades == 4
    assert m.win_rate == pytest.approx(0.75)
    assert m.avg_win == pytest.approx((14.5 + 4.7 + 0.9) / 3)
    assert m.avg_loss == pytest.approx(-20.5)
    # profit_factor = sum(wins) / |sum(losses)|
    assert m.profit_factor == pytest.approx((14.5 + 4.7 + 0.9) / 20.5)


def test_fee_drag_uses_abs_pnl():
    """Losers still show fee drag — abs(gross_pnl) in denominator."""
    from src.layer1_research.backtesting.reporting.metrics import compute_metrics
    trades = [
        {"instrument_id": "a", "direction": "LONG",
         "entry_ts": datetime(2024, 6, 1, tzinfo=timezone.utc),
         "exit_ts": datetime(2024, 6, 2, tzinfo=timezone.utc),
         "entry_price": 0.50, "exit_price": 0.40, "size": 100.0,
         "fees": 5.0, "gross_pnl": -10.0, "net_pnl": -15.0,
         "edge_at_entry": 0.0, "slippage_bps": 0.0,
         "signal_confidence": 0.50, "realized_edge": -0.10},
    ]
    r = _make_result(trades_rows=trades)
    m = compute_metrics(r)
    assert m.total_fees == pytest.approx(5.0)
    # fee_drag = fees / abs(gross_pnl) = 5 / 10 = 0.5 (50%)
    assert m.fee_drag_pct == pytest.approx(0.5)


def test_total_return_from_equity_curve():
    from src.layer1_research.backtesting.reporting.metrics import compute_metrics
    r = _make_result(trades_rows=[], account_totals=[10_000.0, 11_250.0])
    m = compute_metrics(r)
    assert m.total_return_pct == pytest.approx(12.5)


def test_per_market_breakdown():
    from src.layer1_research.backtesting.reporting.metrics import compute_metrics
    trades = [
        {"instrument_id": "mkt_a", "direction": "LONG",
         "entry_ts": datetime(2024, 6, 1, tzinfo=timezone.utc),
         "exit_ts": datetime(2024, 6, 2, tzinfo=timezone.utc),
         "entry_price": 0.40, "exit_price": 0.55, "size": 100.0,
         "fees": 0.5, "gross_pnl": 15.0, "net_pnl": 14.5,
         "edge_at_entry": 0.25, "slippage_bps": 0.0,
         "signal_confidence": 0.65, "realized_edge": 0.15},
        {"instrument_id": "mkt_b", "direction": "LONG",
         "entry_ts": datetime(2024, 6, 3, tzinfo=timezone.utc),
         "exit_ts": datetime(2024, 6, 4, tzinfo=timezone.utc),
         "entry_price": 0.50, "exit_price": 0.30, "size": 100.0,
         "fees": 0.5, "gross_pnl": -20.0, "net_pnl": -20.5,
         "edge_at_entry": 0.15, "slippage_bps": 0.0,
         "signal_confidence": 0.65, "realized_edge": -0.20},
    ]
    r = _make_result(trades_rows=trades)
    m = compute_metrics(r)
    assert set(m.per_market.keys()) == {"mkt_a", "mkt_b"}
    assert m.per_market["mkt_a"].net_pnl == pytest.approx(14.5)
    assert m.per_market["mkt_a"].win_rate == pytest.approx(1.0)
    assert m.per_market["mkt_b"].net_pnl == pytest.approx(-20.5)
    assert m.per_market["mkt_b"].win_rate == pytest.approx(0.0)


def test_sharpe_comes_from_analyzer_stats():
    from src.layer1_research.backtesting.reporting.metrics import compute_metrics
    r = _make_result(
        trades_rows=[],
        analyzer_stats={
            "Sharpe Ratio (252 days)": 1.85,
            "Sortino Ratio (252 days)": 2.20,
            "Max Drawdown": -0.12,
        },
    )
    m = compute_metrics(r)
    assert m.sharpe_ratio == pytest.approx(1.85)
    assert m.sortino_ratio == pytest.approx(2.20)
    # max drawdown reported as positive percent
    assert m.max_drawdown_pct == pytest.approx(12.0)
```

Also, rewrite `tests/backtesting/test_metrics.py` to keep only the still-relevant helpers:

```python
"""Tests for small metric helpers."""
import pytest


def test_brier_score_perfect():
    from src.layer1_research.backtesting.reporting.metrics import brier_score
    assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == pytest.approx(0.0)


def test_brier_score_worst():
    from src.layer1_research.backtesting.reporting.metrics import brier_score
    assert brier_score([1.0, 0.0], [0, 1]) == pytest.approx(1.0)


def test_brier_score_random():
    from src.layer1_research.backtesting.reporting.metrics import brier_score
    assert brier_score([0.5, 0.5, 0.5, 0.5], [1, 0, 1, 0]) == pytest.approx(0.25)


def test_fee_drag_abs_denominator():
    """fee_drag divides by |gross_pnl| so losers still show drag."""
    from src.layer1_research.backtesting.reporting.metrics import fee_drag
    assert fee_drag(total_fees=50.0, gross_pnl=500.0) == pytest.approx(0.10)
    assert fee_drag(total_fees=50.0, gross_pnl=-500.0) == pytest.approx(0.10)
    assert fee_drag(total_fees=50.0, gross_pnl=0.0) == 0.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/backtesting/test_metrics_computation.py tests/backtesting/test_metrics.py -v`
Expected: many FAILs — `compute_metrics`/`BacktestMetrics` not defined, `fee_drag` abs test fails because current code returns 0 for negative gross_pnl.

**Note for the implementer:** the exact key names returned by `engine.trader.analyzer.get_performance_stats_returns()` vary across Nautilus versions (e.g. `"Sharpe Ratio"` vs `"Sharpe Ratio (252 days)"` vs `"Annualized Sharpe"`). The `_ANALYZER_KEY_ALIASES` mapping below covers the common names, and `_pick()` falls back to a prefix-match. If, during Step 4, `test_sharpe_comes_from_analyzer_stats` passes but the real e2e `fair_value_mr` still reports Sharpe = 0.0, insert one line `print(result.analyzer_stats.keys())` into the e2e test and extend the aliases tuple with the actual key names you see.

- [ ] **Step 3: Rewrite `metrics.py`**

Replace the entire contents of `src/layer1_research/backtesting/reporting/metrics.py`:

```python
"""Backtesting metrics — scalar summary + helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Optional

import pandas as pd

if TYPE_CHECKING:
    from src.layer1_research.backtesting.results import BacktestResult


# ---- small helpers --------------------------------------------------------

def brier_score(predictions: list[float], outcomes: list[int]) -> float:
    """Brier score: mean squared error of probability predictions. Lower is better."""
    if not predictions:
        return 0.0
    n = len(predictions)
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / n


def fee_drag(total_fees: float, gross_pnl: float) -> float:
    """Fees as a fraction of |gross_pnl|. Losers still show drag.

    Returns 0.0 iff gross_pnl is exactly zero (no trades moved the needle).
    """
    if gross_pnl == 0:
        return 0.0
    return total_fees / abs(gross_pnl)


# ---- scalar summary -------------------------------------------------------

@dataclass
class PerMarketStats:
    trades: int
    net_pnl: float
    win_rate: float
    avg_edge_at_entry: float
    avg_realized_edge: float


@dataclass
class BacktestMetrics:
    # Scalar perf (from analyzer_stats or equity curve)
    total_return_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    calmar_ratio: float
    # Trade-level
    total_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    avg_hold_time: Optional[timedelta]
    # Execution
    total_fees: float
    fee_drag_pct: float
    avg_slippage_bps: float
    # Signal quality
    avg_edge_at_order: float
    edge_realization_rate: float
    # Per-market
    per_market: dict[str, PerMarketStats] = field(default_factory=dict)


_ANALYZER_KEY_ALIASES = {
    "sharpe_ratio": ("Sharpe Ratio", "Sharpe Ratio (252 days)"),
    "sortino_ratio": ("Sortino Ratio", "Sortino Ratio (252 days)"),
    "max_drawdown": ("Max Drawdown",),
}


def _pick(stats: dict, aliases: tuple) -> Optional[float]:
    for key in aliases:
        if key in stats:
            return float(stats[key])
    # Fuzzy: match prefix
    for key, val in stats.items():
        for alias in aliases:
            if key.startswith(alias):
                return float(val)
    return None


def compute_metrics(result: "BacktestResult") -> BacktestMetrics:
    """Derive a BacktestMetrics from a BacktestResult.

    Pure function — does not mutate `result`.
    """
    trades = result.trades
    stats = result.analyzer_stats

    # Total return from equity curve (authoritative)
    start_eq = float(result.equity_curve.iloc[0])
    end_eq = float(result.equity_curve.iloc[-1])
    total_return_pct = (end_eq - start_eq) / start_eq * 100.0 if start_eq else 0.0

    sharpe = _pick(stats, _ANALYZER_KEY_ALIASES["sharpe_ratio"]) or 0.0
    sortino = _pick(stats, _ANALYZER_KEY_ALIASES["sortino_ratio"]) or 0.0
    mdd_raw = _pick(stats, _ANALYZER_KEY_ALIASES["max_drawdown"])
    # Nautilus reports max_drawdown as a negative fraction (-0.12 = -12%).
    # We surface absolute percent for display (12.0).
    mdd_pct = abs(float(mdd_raw) * 100.0) if mdd_raw is not None else 0.0
    calmar = (total_return_pct / mdd_pct) if mdd_pct > 0 else 0.0

    total_trades = int(len(trades))

    if total_trades == 0:
        return BacktestMetrics(
            total_return_pct=total_return_pct,
            sharpe_ratio=sharpe, sortino_ratio=sortino,
            max_drawdown_pct=mdd_pct, calmar_ratio=calmar,
            total_trades=0, win_rate=0.0, avg_win=0.0, avg_loss=0.0,
            profit_factor=0.0, avg_hold_time=None,
            total_fees=0.0, fee_drag_pct=0.0,
            avg_slippage_bps=0.0, avg_edge_at_order=0.0,
            edge_realization_rate=0.0, per_market={},
        )

    closed = trades[trades["exit_ts"].notna()]
    wins = closed[closed["net_pnl"] > 0]
    losses = closed[closed["net_pnl"] < 0]
    win_rate = len(wins) / len(closed) if len(closed) else 0.0
    avg_win = float(wins["net_pnl"].mean()) if len(wins) else 0.0
    avg_loss = float(losses["net_pnl"].mean()) if len(losses) else 0.0
    sum_wins = float(wins["net_pnl"].sum())
    sum_losses_abs = abs(float(losses["net_pnl"].sum()))
    profit_factor = sum_wins / sum_losses_abs if sum_losses_abs > 0 else 0.0

    # Avg hold time (closed trades only)
    if len(closed):
        deltas = closed["exit_ts"] - closed["entry_ts"]
        avg_hold = pd.to_timedelta(deltas).mean()
        avg_hold_time = avg_hold if pd.notna(avg_hold) else None
    else:
        avg_hold_time = None

    total_fees = float(trades["fees"].sum())
    gross_pnl = float(trades["gross_pnl"].sum())
    fee_drag_pct = fee_drag(total_fees, gross_pnl)

    avg_slippage_bps = float(trades["slippage_bps"].mean()) if total_trades else 0.0

    # Signal quality
    if not result.signals.empty and "edge_at_order" in result.signals.columns:
        acted = result.signals[result.signals["direction"].isin(["BUY", "SELL"])]
        avg_edge_at_order = float(acted["edge_at_order"].mean()) if len(acted) else 0.0
    else:
        avg_edge_at_order = 0.0

    # Edge realization: mean(realized_edge / edge_at_entry) over closed trades
    # with a non-zero entry edge (avoid div-by-zero).
    eligible = closed[
        closed["edge_at_entry"].abs() > 1e-9
    ]
    if len(eligible):
        ratios = eligible["realized_edge"] / eligible["edge_at_entry"]
        edge_realization_rate = float(ratios.mean())
    else:
        edge_realization_rate = 0.0

    # Per-market
    per_market: dict[str, PerMarketStats] = {}
    for inst_id, group in trades.groupby("instrument_id"):
        closed_g = group[group["exit_ts"].notna()]
        wins_g = closed_g[closed_g["net_pnl"] > 0]
        per_market[str(inst_id)] = PerMarketStats(
            trades=int(len(group)),
            net_pnl=float(group["net_pnl"].sum()),
            win_rate=(len(wins_g) / len(closed_g)) if len(closed_g) else 0.0,
            avg_edge_at_entry=float(group["edge_at_entry"].mean()),
            avg_realized_edge=(
                float(closed_g["realized_edge"].mean()) if len(closed_g) else 0.0
            ),
        )

    return BacktestMetrics(
        total_return_pct=total_return_pct,
        sharpe_ratio=sharpe, sortino_ratio=sortino,
        max_drawdown_pct=mdd_pct, calmar_ratio=calmar,
        total_trades=total_trades,
        win_rate=win_rate, avg_win=avg_win, avg_loss=avg_loss,
        profit_factor=profit_factor, avg_hold_time=avg_hold_time,
        total_fees=total_fees, fee_drag_pct=fee_drag_pct,
        avg_slippage_bps=avg_slippage_bps,
        avg_edge_at_order=avg_edge_at_order,
        edge_realization_rate=edge_realization_rate,
        per_market=per_market,
    )
```

Add a `.metrics()` method to `BacktestResult` — append inside the class body in `results.py`:

```python
    def metrics(self) -> "BacktestMetrics":
        """Return the BacktestMetrics derived from this result. Memoized."""
        if getattr(self, "_metrics_cache", None) is None:
            from src.layer1_research.backtesting.reporting.metrics import compute_metrics
            self._metrics_cache = compute_metrics(self)
        return self._metrics_cache
```

(Put the `_metrics_cache` attribute init in `__post_init__` with `self._metrics_cache = None`.)

- [ ] **Step 4: Run tests**

Run: `pytest tests/backtesting/test_metrics.py tests/backtesting/test_metrics_computation.py tests/backtesting/test_results.py -v`
Expected: all pass.

Run: `pytest tests/backtesting -x -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/reporting/metrics.py \
        src/layer1_research/backtesting/results.py \
        tests/backtesting/test_metrics.py \
        tests/backtesting/test_metrics_computation.py
git commit -m "backtest: compute_metrics returns real Sharpe/DD/win-rate/per-market"
```

---

## Task 9: Charts module

**Files:**
- Create: `src/layer1_research/backtesting/reporting/charts.py`
- Modify: `src/layer1_research/backtesting/results.py` — plot methods

- [ ] **Step 1: Create `charts.py`**

Create `src/layer1_research/backtesting/reporting/charts.py`:

```python
"""Chart functions for BacktestResult.

Each function takes a BacktestResult and an optional matplotlib Axes, and
returns the Figure. No I/O — notebooks save/show as needed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import matplotlib.pyplot as plt
import pandas as pd

if TYPE_CHECKING:
    from src.layer1_research.backtesting.results import BacktestResult


def plot_equity_curve(result: "BacktestResult", ax=None):
    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 4))
    else:
        fig = ax.figure
    eq = result.equity_curve
    ax.plot(eq.index, eq.values, label="Equity (USD)", linewidth=1.4)
    ax.axhline(result.config.starting_capital, linestyle="--", linewidth=0.8,
               alpha=0.5, label="Starting capital")
    ax.set_title(f"Equity curve — {result.config.strategy_name}")
    ax.set_xlabel("Time")
    ax.set_ylabel("USD")
    ax.legend()
    ax.grid(alpha=0.3)
    return fig


def plot_drawdown(result: "BacktestResult", ax=None):
    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 3))
    else:
        fig = ax.figure
    eq = result.equity_curve
    running_max = eq.cummax()
    dd = (eq - running_max) / running_max * 100.0
    ax.fill_between(dd.index, dd.values, 0, alpha=0.4, color="red")
    ax.plot(dd.index, dd.values, linewidth=0.9, color="darkred")
    ax.set_title("Drawdown (%)")
    ax.set_xlabel("Time")
    ax.set_ylabel("%")
    ax.grid(alpha=0.3)
    return fig


def plot_pnl_histogram(result: "BacktestResult", ax=None, bins: int = 40):
    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 4))
    else:
        fig = ax.figure
    if result.trades.empty:
        ax.text(0.5, 0.5, "No trades", ha="center", va="center", transform=ax.transAxes)
        return fig
    pnl = result.trades["net_pnl"].dropna()
    ax.hist(pnl, bins=bins, alpha=0.7, edgecolor="black")
    ax.axvline(0, color="red", linestyle="--", linewidth=1.0)
    ax.set_title("Trade P&L distribution")
    ax.set_xlabel("Net P&L (USD)")
    ax.set_ylabel("Count")
    ax.grid(alpha=0.3)
    return fig


def plot_edge_calibration(result: "BacktestResult", ax=None):
    if ax is None:
        fig, ax = plt.subplots(figsize=(6, 6))
    else:
        fig = ax.figure
    closed = result.trades[result.trades["exit_ts"].notna()]
    if closed.empty:
        ax.text(0.5, 0.5, "No closed trades", ha="center", va="center",
                transform=ax.transAxes)
        return fig
    x = closed["edge_at_entry"]
    y = closed["realized_edge"]
    lo = float(min(x.min(), y.min(), 0.0))
    hi = float(max(x.max(), y.max(), 0.0))
    ax.scatter(x, y, alpha=0.5, s=18)
    ax.plot([lo, hi], [lo, hi], linestyle="--", color="black",
            linewidth=0.8, label="y=x (perfect calibration)")
    ax.axhline(0, color="gray", linewidth=0.5)
    ax.axvline(0, color="gray", linewidth=0.5)
    ax.set_title("Edge calibration")
    ax.set_xlabel("Edge at entry")
    ax.set_ylabel("Realized edge")
    ax.legend()
    ax.grid(alpha=0.3)
    return fig


def plot_per_market_pnl(result: "BacktestResult", ax=None, top_n: int = 20):
    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 6))
    else:
        fig = ax.figure
    if result.trades.empty:
        ax.text(0.5, 0.5, "No trades", ha="center", va="center", transform=ax.transAxes)
        return fig
    pnl_by_mkt = result.trades.groupby("instrument_id")["net_pnl"].sum()
    pnl_by_mkt = pnl_by_mkt.sort_values()
    pnl_by_mkt = pd.concat([pnl_by_mkt.head(top_n // 2), pnl_by_mkt.tail(top_n // 2)])
    colors = ["red" if v < 0 else "green" for v in pnl_by_mkt.values]
    labels = [str(i)[:18] for i in pnl_by_mkt.index]
    ax.barh(range(len(pnl_by_mkt)), pnl_by_mkt.values, color=colors)
    ax.set_yticks(range(len(pnl_by_mkt)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.axvline(0, color="black", linewidth=0.5)
    ax.set_title(f"Net P&L by market (top/bottom {top_n // 2})")
    ax.set_xlabel("USD")
    ax.grid(alpha=0.3, axis="x")
    return fig
```

- [ ] **Step 2: Add plot methods to `BacktestResult`**

Append inside the `BacktestResult` class body in `results.py`:

```python
    def plot_equity_curve(self, ax=None):
        from src.layer1_research.backtesting.reporting.charts import plot_equity_curve
        return plot_equity_curve(self, ax=ax)

    def plot_drawdown(self, ax=None):
        from src.layer1_research.backtesting.reporting.charts import plot_drawdown
        return plot_drawdown(self, ax=ax)

    def plot_pnl_histogram(self, ax=None, bins: int = 40):
        from src.layer1_research.backtesting.reporting.charts import plot_pnl_histogram
        return plot_pnl_histogram(self, ax=ax, bins=bins)

    def plot_edge_calibration(self, ax=None):
        from src.layer1_research.backtesting.reporting.charts import plot_edge_calibration
        return plot_edge_calibration(self, ax=ax)

    def plot_per_market_pnl(self, ax=None, top_n: int = 20):
        from src.layer1_research.backtesting.reporting.charts import plot_per_market_pnl
        return plot_per_market_pnl(self, ax=ax, top_n=top_n)
```

- [ ] **Step 3: Smoke test that chart functions return figures**

Append to `tests/backtesting/test_results.py`:

```python
def test_plot_methods_return_figures():
    """Smoke: each plot_* returns a matplotlib Figure without raising."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: F401

    from src.layer1_research.backtesting.results import BacktestResult
    from src.layer1_research.backtesting.config import BacktestConfig

    config = BacktestConfig(
        catalog_path="data/catalog",
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 6, 30, tzinfo=timezone.utc),
        strategy_name="smoke", starting_capital=10_000.0, data_mode="trade",
    )
    import pandas as pd
    account = pd.DataFrame(
        {"total": ["10000.00 USD", "10200.00 USD", "9800.00 USD"]},
        index=pd.to_datetime(
            ["2024-06-01T00Z", "2024-06-15T00Z", "2024-06-30T00Z"], utc=True,
        ),
    )
    r = BacktestResult(
        config=config, fills=pd.DataFrame(), positions=pd.DataFrame(),
        account=account, instruments=[], analyzer_stats={},
        signals=pd.DataFrame(), trades=pd.DataFrame(),
    )
    assert r.plot_equity_curve() is not None
    assert r.plot_drawdown() is not None
    assert r.plot_pnl_histogram() is not None
    assert r.plot_edge_calibration() is not None
    assert r.plot_per_market_pnl() is not None
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/backtesting/test_results.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/reporting/charts.py \
        src/layer1_research/backtesting/results.py \
        tests/backtesting/test_results.py
git commit -m "backtest: add charts module (equity, DD, calibration, per-market, PnL hist)"
```

---

## Task 10: Update CLI report to print new `BacktestMetrics`

**Files:**
- Modify: `src/layer1_research/backtesting/reporting/cli_report.py`

- [ ] **Step 1: Rewrite `cli_report.py`**

Replace the entire contents of `src/layer1_research/backtesting/reporting/cli_report.py`:

```python
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
```

- [ ] **Step 2: Run tests to ensure nothing is broken**

Run: `pytest tests/backtesting -x -q`
Expected: all pass (no test imports the old `BacktestSummary` from cli_report).

- [ ] **Step 3: Commit**

```bash
git add src/layer1_research/backtesting/reporting/cli_report.py
git commit -m "backtest: cli_report prints new BacktestMetrics with per-market tops/bottoms"
```

---

## Task 11: `HoldCashStrategy` + correctness test

**Files:**
- Create: `src/layer1_research/backtesting/strategies/examples/hold_cash.py`
- Create: `tests/backtesting/test_correctness_strategies.py`

- [ ] **Step 1: Write the failing test**

Create `tests/backtesting/test_correctness_strategies.py`:

```python
"""Invariant checks for HoldCash and RandomTrader — validate the engine itself."""
import pytest
import shutil
import tempfile
from datetime import datetime, timezone
from tests.backtesting.fixtures.sample_data import create_becker_fixture_dir


@pytest.fixture
def built_catalog():
    becker = create_becker_fixture_dir()
    cat = tempfile.mkdtemp(prefix="cat_corr_")
    from src.layer1_research.backtesting.data.catalog import build_catalog
    from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
    build_catalog(BeckerParquetLoader(becker), cat)
    yield cat
    shutil.rmtree(becker)
    shutil.rmtree(cat)


def test_hold_cash_preserves_capital(built_catalog):
    from src.layer1_research.backtesting.config import BacktestConfig
    from src.layer1_research.backtesting.runner import BacktestRunner
    from src.layer1_research.backtesting.strategies.examples.hold_cash import (
        HoldCashStrategy,
    )

    config = BacktestConfig(
        catalog_path=built_catalog,
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 7, 1, tzinfo=timezone.utc),
        strategy_name="hold_cash", starting_capital=10_000.0, data_mode="trade",
    )
    result = BacktestRunner(config).run(HoldCashStrategy)
    final_eq = float(result.equity_curve.iloc[-1])

    assert final_eq == pytest.approx(10_000.0, abs=1e-2), (
        f"HoldCash must not change capital, got {final_eq}"
    )
    assert len(result.trades) == 0
    assert len(result.signals) == 0
    m = result.metrics()
    assert m.total_fees == pytest.approx(0.0)
    assert m.total_trades == 0
```

- [ ] **Step 2: Run it to fail**

Run: `pytest tests/backtesting/test_correctness_strategies.py::test_hold_cash_preserves_capital -v`
Expected: FAIL — `HoldCashStrategy` not defined.

- [ ] **Step 3: Create `hold_cash.py`**

Create `src/layer1_research/backtesting/strategies/examples/hold_cash.py`:

```python
"""HoldCashStrategy — correctness test for the engine.

Subscribes to nothing, never emits a signal. If the engine's account balance
drifts off starting capital over the backtest, something is wrong upstream.
"""
from typing import Optional

from nautilus_trader.model.instruments import BinaryOption

from src.layer1_research.backtesting.strategies.base import (
    PredictionMarketStrategy, PredictionMarketStrategyConfig,
)
from src.layer1_research.backtesting.strategies.signal import Signal


class HoldCashStrategy(PredictionMarketStrategy):
    """Sits on starting capital for the entire run."""

    def __init__(self, config: PredictionMarketStrategyConfig):
        super().__init__(config)

    def on_start(self):
        # Intentionally do not subscribe to anything.
        pass

    def generate_signal(self, instrument: BinaryOption, data) -> Optional[Signal]:
        return None
```

- [ ] **Step 4: Run test**

Run: `pytest tests/backtesting/test_correctness_strategies.py::test_hold_cash_preserves_capital -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/strategies/examples/hold_cash.py \
        tests/backtesting/test_correctness_strategies.py
git commit -m "backtest: add HoldCashStrategy correctness test"
```

---

## Task 12: `RandomTraderStrategy` + determinism test

**Files:**
- Create: `src/layer1_research/backtesting/strategies/examples/random_trader.py`
- Modify: `tests/backtesting/test_correctness_strategies.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/backtesting/test_correctness_strategies.py`:

```python
def test_random_trader_is_deterministic_with_seed(built_catalog):
    """Same seed -> identical trades/signals DataFrames."""
    from src.layer1_research.backtesting.config import BacktestConfig
    from src.layer1_research.backtesting.runner import BacktestRunner
    from src.layer1_research.backtesting.strategies.examples.random_trader import (
        RandomTraderStrategy,
    )
    import pandas as pd

    def _run():
        config = BacktestConfig(
            catalog_path=built_catalog,
            start=datetime(2024, 6, 1, tzinfo=timezone.utc),
            end=datetime(2024, 7, 1, tzinfo=timezone.utc),
            strategy_name="rand", starting_capital=10_000.0,
            data_mode="trade",
            strategy_params={"seed": 42, "p_trade": 0.02, "trade_size": 5.0},
        )
        return BacktestRunner(config).run(RandomTraderStrategy)

    r1 = _run()
    r2 = _run()

    # signals DataFrames must be equal (index + content)
    pd.testing.assert_frame_equal(
        r1.signals.reset_index(drop=True),
        r2.signals.reset_index(drop=True),
    )


def test_random_trader_pnl_direction_negative_or_zero(built_catalog):
    """Over many random trades with nonzero fees, expected net P&L is negative.

    We don't require statistical significance on the 20-market snapshot — we
    only require that *if* fees were applied, net P&L is not wildly positive.
    """
    from src.layer1_research.backtesting.config import BacktestConfig
    from src.layer1_research.backtesting.runner import BacktestRunner
    from src.layer1_research.backtesting.strategies.examples.random_trader import (
        RandomTraderStrategy,
    )

    config = BacktestConfig(
        catalog_path=built_catalog,
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 7, 1, tzinfo=timezone.utc),
        strategy_name="rand_neg", starting_capital=10_000.0,
        data_mode="trade",
        fee_rate_bps=100,
        strategy_params={"seed": 7, "p_trade": 0.05, "trade_size": 5.0},
    )
    result = BacktestRunner(config).run(RandomTraderStrategy)
    m = result.metrics()
    # With fees applied, net PnL over random entries should be <= 0 plus a
    # generous tolerance for small-sample noise (the fixture has only 3 trades).
    net_pnl = float(result.equity_curve.iloc[-1]) - config.starting_capital
    tolerance = max(20.0, abs(m.total_fees) * 3)
    assert net_pnl <= tolerance, (
        f"Random trader net PnL should not exceed tolerance: "
        f"got {net_pnl:.2f}, tolerance {tolerance:.2f}, fees {m.total_fees:.2f}"
    )
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/backtesting/test_correctness_strategies.py -v -k random`
Expected: FAIL — `RandomTraderStrategy` not defined.

- [ ] **Step 3: Create `random_trader.py`**

Create `src/layer1_research/backtesting/strategies/examples/random_trader.py`:

```python
"""RandomTraderStrategy — correctness test for the engine.

On each trade tick, with probability p_trade emits a BUY or SELL at the
current price. Takes a seed for reproducibility. Expected net P&L over many
trades with fees: close to -total_fees.
"""
import random
from typing import Optional

from nautilus_trader.model.instruments import BinaryOption

from src.layer1_research.backtesting.strategies.base import (
    PredictionMarketStrategy, PredictionMarketStrategyConfig,
)
from src.layer1_research.backtesting.strategies.signal import Signal


class RandomTraderConfig(PredictionMarketStrategyConfig, frozen=True):
    seed: int = 0
    p_trade: float = 0.01
    trade_size: float = 5.0


class RandomTraderStrategy(PredictionMarketStrategy):
    """Emits random BUY/SELL signals with configurable probability."""

    def __init__(self, config: RandomTraderConfig):
        super().__init__(config)
        self._rng = random.Random(config.seed)

    def generate_signal(self, instrument: BinaryOption, data) -> Optional[Signal]:
        if self._rng.random() >= self.config.p_trade:
            return None

        if hasattr(data, "price"):
            price = float(data.price)
        elif hasattr(data, "close"):
            price = float(data.close)
        else:
            return None

        direction = "BUY" if self._rng.random() < 0.5 else "SELL"
        # Confidence 0.5 = zero expected edge; the strategy is a null hypothesis.
        return Signal(
            direction=direction,
            confidence=0.5,
            target_price=price,
            size=self.config.trade_size,
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/backtesting/test_correctness_strategies.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/strategies/examples/random_trader.py \
        tests/backtesting/test_correctness_strategies.py
git commit -m "backtest: add RandomTraderStrategy with seeded determinism test"
```

---

## Task 13: MLflow on `BacktestResult` — save + reload

**Files:**
- Modify: `src/layer1_research/backtesting/results.py`
- Modify: `tests/backtesting/test_results.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/backtesting/test_results.py`:

```python
def test_to_mlflow_roundtrip(tmp_path, monkeypatch):
    """BacktestResult.to_mlflow writes artifacts; from_mlflow reads them back."""
    import mlflow
    import pandas as pd
    from src.layer1_research.backtesting.results import BacktestResult
    from src.layer1_research.backtesting.config import BacktestConfig

    config = BacktestConfig(
        catalog_path="data/catalog",
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 6, 30, tzinfo=timezone.utc),
        strategy_name="mlflow_rt", starting_capital=10_000.0, data_mode="trade",
    )
    account = pd.DataFrame(
        {"total": ["10000.00 USD", "10500.00 USD"]},
        index=pd.to_datetime(["2024-06-01T00Z", "2024-06-30T00Z"], utc=True),
    )
    result = BacktestResult(
        config=config, fills=pd.DataFrame(), positions=pd.DataFrame(),
        account=account, instruments=[], analyzer_stats={"Sharpe Ratio": 1.5},
        signals=pd.DataFrame(), trades=pd.DataFrame(),
    )

    tracking_uri = f"sqlite:///{tmp_path}/mlflow.db"
    mlflow.set_tracking_uri(tracking_uri)
    run_id = result.to_mlflow(run_name="test-run", experiment="corr-test")
    assert run_id

    reloaded = BacktestResult.from_mlflow(run_id, tracking_uri=tracking_uri)
    assert reloaded.config.strategy_name == "mlflow_rt"
    assert float(reloaded.equity_curve.iloc[-1]) == pytest.approx(10_500.0)
```

- [ ] **Step 2: Run to fail**

Run: `pytest tests/backtesting/test_results.py::test_to_mlflow_roundtrip -v`
Expected: FAIL — `to_mlflow`/`from_mlflow` not defined.

- [ ] **Step 3: Add MLflow methods to `BacktestResult`**

Append inside the `BacktestResult` class body in `results.py`:

```python
    def to_mlflow(self, run_name: Optional[str] = None,
                  experiment: str = "polymarket-backtests") -> str:
        """Log this result to MLflow. Returns the run_id."""
        import json
        import tempfile
        from pathlib import Path

        import mlflow

        mlflow.set_experiment(experiment)
        metrics = self.metrics()
        with mlflow.start_run(run_name=run_name) as run:
            # Params: config + analyzer keys (stringified)
            mlflow.log_params({
                "strategy": self.config.strategy_name,
                "start": self.config.start.isoformat(),
                "end": self.config.end.isoformat(),
                "starting_capital": self.config.starting_capital,
                "data_mode": self.config.data_mode,
                "fee_rate_bps": self.config.fee_rate_bps,
                "position_sizer": self.config.position_sizer,
            })
            if self.config.strategy_params:
                mlflow.log_params({
                    f"strategy.{k}": v for k, v in self.config.strategy_params.items()
                })

            mlflow.log_metrics({
                "total_return_pct": metrics.total_return_pct,
                "sharpe_ratio": metrics.sharpe_ratio,
                "sortino_ratio": metrics.sortino_ratio,
                "max_drawdown_pct": metrics.max_drawdown_pct,
                "calmar_ratio": metrics.calmar_ratio,
                "win_rate": metrics.win_rate,
                "total_trades": float(metrics.total_trades),
                "total_fees": metrics.total_fees,
                "fee_drag_pct": metrics.fee_drag_pct,
                "avg_slippage_bps": metrics.avg_slippage_bps,
                "avg_edge_at_order": metrics.avg_edge_at_order,
                "edge_realization_rate": metrics.edge_realization_rate,
            })

            _SKIP_COLS = {"info", "margins"}
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)

                def _write_and_log(df, name):
                    if df is None or df.empty:
                        return
                    out = df.drop(columns=_SKIP_COLS & set(df.columns),
                                  errors="ignore")
                    p = tmp_path / name
                    out.to_parquet(p)
                    mlflow.log_artifact(str(p))

                _write_and_log(self.fills, "fills.parquet")
                _write_and_log(self.positions, "positions.parquet")
                _write_and_log(self.account, "account.parquet")
                _write_and_log(self.signals, "signals.parquet")
                _write_and_log(self.trades, "trades.parquet")

                # Save config as JSON
                cfg_path = tmp_path / "config.json"
                cfg_path.write_text(json.dumps({
                    "catalog_path": self.config.catalog_path,
                    "start": self.config.start.isoformat(),
                    "end": self.config.end.isoformat(),
                    "strategy_name": self.config.strategy_name,
                    "starting_capital": self.config.starting_capital,
                    "data_mode": self.config.data_mode,
                    "fee_rate_bps": self.config.fee_rate_bps,
                    "position_sizer": self.config.position_sizer,
                    "strategy_params": self.config.strategy_params,
                }))
                mlflow.log_artifact(str(cfg_path))

            return run.info.run_id

    @classmethod
    def from_mlflow(cls, run_id: str,
                    tracking_uri: Optional[str] = None) -> "BacktestResult":
        """Reload a BacktestResult from an MLflow run's artifacts."""
        import json
        from datetime import datetime
        import mlflow
        from src.layer1_research.backtesting.config import BacktestConfig

        if tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)

        client = mlflow.tracking.MlflowClient()
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            local = client.download_artifacts(run_id, "", tmp)
            root = Path(local)

            cfg = json.loads((root / "config.json").read_text())
            config = BacktestConfig(
                catalog_path=cfg["catalog_path"],
                start=datetime.fromisoformat(cfg["start"]),
                end=datetime.fromisoformat(cfg["end"]),
                strategy_name=cfg["strategy_name"],
                starting_capital=cfg["starting_capital"],
                data_mode=cfg["data_mode"],
                fee_rate_bps=cfg["fee_rate_bps"],
                position_sizer=cfg["position_sizer"],
                strategy_params=cfg.get("strategy_params", {}),
            )

            def _load(name):
                p = root / name
                return pd.read_parquet(p) if p.exists() else pd.DataFrame()

            return cls(
                config=config,
                fills=_load("fills.parquet"),
                positions=_load("positions.parquet"),
                account=_load("account.parquet"),
                instruments=[],  # not restorable from artifacts
                analyzer_stats={},  # scalar metrics re-derived from equity curve
                signals=_load("signals.parquet"),
                trades=_load("trades.parquet"),
            )
```

Also add `import pandas as pd` at the top of `results.py` if not already there (it is).

- [ ] **Step 4: Run tests**

Run: `pytest tests/backtesting/test_results.py::test_to_mlflow_roundtrip -v`
Expected: PASS.

Run: `pytest tests/backtesting -x -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/layer1_research/backtesting/results.py tests/backtesting/test_results.py
git commit -m "backtest: BacktestResult.to_mlflow / from_mlflow (moved out of runner)"
```

---

## Task 14: Port layer0 scripts → notebooks

**Files:**
- Create: `notebooks/layer0_ingestion/check_allowances.ipynb`
- Create: `notebooks/layer0_ingestion/explore_market.ipynb`
- Create: `notebooks/layer0_ingestion/view_markets.ipynb`
- Create: `notebooks/layer0_ingestion/test_connection.ipynb`
- Create: `notebooks/layer0_ingestion/debug_uma_signal.ipynb`

- [ ] **Step 1: Create the directory**

Run:
```bash
mkdir -p notebooks/layer0_ingestion
```

- [ ] **Step 2: Port each script using `jupytext`**

Confirm `jupytext` is available; if not, install into the venv:
```bash
source .venv/bin/activate
python -c "import jupytext" 2>/dev/null || pip install jupytext
```

Convert each script to a `.ipynb` one-by-one:
```bash
jupytext --to ipynb -o notebooks/layer0_ingestion/check_allowances.ipynb scripts/check_allowances.py
jupytext --to ipynb -o notebooks/layer0_ingestion/explore_market.ipynb scripts/explore_market.py
jupytext --to ipynb -o notebooks/layer0_ingestion/view_markets.ipynb scripts/view_markets.py
jupytext --to ipynb -o notebooks/layer0_ingestion/test_connection.ipynb scripts/test_connection.py
jupytext --to ipynb -o notebooks/layer0_ingestion/debug_uma_signal.ipynb scripts/debug_uma_signal.py
```

If a script uses `if __name__ == "__main__":` + `argparse`, manually edit the resulting notebook to:
1. Replace the argparse block with a "Config" markdown cell followed by plain variable assignments.
2. Replace `main()` with direct function calls or remove the guard.

Spot-check each notebook by opening it in Jupyter or VS Code and ensuring cells are present.

- [ ] **Step 3: Verify layer0 notebooks are syntactically valid**

Run:
```bash
python -c "import json; [json.load(open(p)) for p in __import__('glob').glob('notebooks/layer0_ingestion/*.ipynb')]"
```
Expected: no exceptions.

- [ ] **Step 4: Commit**

```bash
git add notebooks/layer0_ingestion/
git commit -m "backtest: port layer0 ingestion scripts to notebooks"
```

---

## Task 15: Port layer4 scripts → notebooks

**Files:**
- Create: `notebooks/layer4_execution/preflight.ipynb`

- [ ] **Step 1: Create directory + port**

```bash
mkdir -p notebooks/layer4_execution
source .venv/bin/activate
jupytext --to ipynb -o notebooks/layer4_execution/preflight.ipynb scripts/preflight.py
```

If `preflight.py` uses argparse or CLI guards, edit the notebook to replace the argparse block with a plain "Config" section of assignments (same pattern as Task 14).

- [ ] **Step 2: Verify + commit**

```bash
python -c "import json; json.load(open('notebooks/layer4_execution/preflight.ipynb'))"
git add notebooks/layer4_execution/
git commit -m "backtest: port layer4 preflight script to notebook"
```

---

## Task 16: Layer1 — catalog build notebook

**Files:**
- Create: `notebooks/layer1_research/01_build_catalog.ipynb`

- [ ] **Step 1: Create the notebook**

Create `notebooks/layer1_research/01_build_catalog.ipynb`. You can do this via `jupytext` with a stub:

Create a temporary file `notebooks/layer1_research/_tmp_01_build_catalog.py`:
```python
# %% [markdown]
# # 01 — Build catalog
#
# One-time ETL from the Becker prediction-market-analysis Parquet dump into
# NautilusTrader's ParquetDataCatalog.

# %%
from datetime import datetime, timezone
from src.layer1_research.backtesting.data.models import MarketFilter
from src.layer1_research.backtesting.data.loaders.becker_parquet import BeckerParquetLoader
from src.layer1_research.backtesting.data.catalog import build_catalog

# %% [markdown]
# ## Config

# %%
DATA_PATH = "../prediction-market-analysis/data"   # Becker repo (sibling checkout)
CATALOG_PATH = "data/catalog"
MIN_VOLUME = 100.0
DATE_START = datetime(2023, 3, 1, tzinfo=timezone.utc)  # trades data begins 2023-03
LIMIT = 100   # cap markets for fast ETL; remove for full load

# %% [markdown]
# ## Load

# %%
loader = BeckerParquetLoader(DATA_PATH)
filters = MarketFilter(min_volume=MIN_VOLUME, date_start=DATE_START)

# %% [markdown]
# ### Preview which markets will be loaded

# %%
markets = loader.load_markets(filters=filters)
print(f"Matched {len(markets)} markets; will load first {LIMIT}")
for m in markets[:10]:
    print(f"  {m.market_id[:16]}...  {m.question[:60]}")

# %% [markdown]
# ### Build catalog

# %%
result = build_catalog(loader, CATALOG_PATH, filters=filters, limit=LIMIT)
print(f"\nMarkets:     {result.markets_loaded}")
print(f"Trades:      {result.trades_loaded:,}")
print(f"Instruments: {result.instruments_created}")
print(f"Path:        {result.catalog_path}")
```

Convert and delete the stub:
```bash
mkdir -p notebooks/layer1_research
source .venv/bin/activate
jupytext --to ipynb -o notebooks/layer1_research/01_build_catalog.ipynb \
         notebooks/layer1_research/_tmp_01_build_catalog.py
rm notebooks/layer1_research/_tmp_01_build_catalog.py
```

- [ ] **Step 2: Smoke-run the notebook with the existing catalog skipped**

Open it in the IDE (or run `jupyter nbconvert --to notebook --execute ...`) and verify imports succeed. Since re-building the catalog is destructive and slow, leave the last cell un-run in the notebook smoke check — just verify imports and loader init.

- [ ] **Step 3: Commit**

```bash
git add notebooks/layer1_research/01_build_catalog.ipynb
git commit -m "backtest: add layer1 catalog build notebook"
```

---

## Task 17: Layer1 — fair_value_mr backtest notebook

**Files:**
- Create: `notebooks/layer1_research/03_run_backtest_fair_value_mr.ipynb`

- [ ] **Step 1: Create the notebook via jupytext stub**

Create `notebooks/layer1_research/_tmp_03_fair_value_mr.py`:
```python
# %% [markdown]
# # 03 — Backtest: Fair Value Mean Reversion

# %%
from datetime import datetime, timedelta, timezone
from src.layer1_research.backtesting.config import BacktestConfig
from src.layer1_research.backtesting.runner import BacktestRunner
from src.layer1_research.backtesting.strategies.examples.fair_value_mean_reversion import (
    FairValueMeanReversionStrategy,
)
from src.layer1_research.backtesting.execution.fill_model import PredictionMarketFillConfig
from src.layer1_research.backtesting.reporting.cli_report import print_report

# %% [markdown]
# ## Config

# %%
config = BacktestConfig(
    catalog_path="data/catalog",
    start=datetime(2024, 6, 1, tzinfo=timezone.utc),
    end=datetime(2024, 9, 1, tzinfo=timezone.utc),
    strategy_name="fair_value_mr",
    starting_capital=10_000.0,
    data_mode="trade",
    fee_rate_bps=100,
    position_sizer="fixed_fractional",
    fill_model=PredictionMarketFillConfig(base_spread_pct=0.04),
    strategy_params={"lookback_trades": 20, "entry_threshold": 0.05},
)

# %% [markdown]
# ## Run

# %%
result = BacktestRunner(config).run(FairValueMeanReversionStrategy)
print_report(result.metrics())

# %% [markdown]
# ## Plots

# %%
result.plot_equity_curve()

# %%
result.plot_drawdown()

# %%
result.plot_pnl_histogram()

# %%
result.plot_edge_calibration()

# %%
result.plot_per_market_pnl()

# %% [markdown]
# ## Inspect trades and signals

# %%
result.trades.head(20)

# %%
result.signals.head(20)

# %% [markdown]
# ## Optional: log to MLflow

# %%
# run_id = result.to_mlflow(run_name=f"fair_value_mr_{config.start.date()}_{config.end.date()}")
# print(f"MLflow run: {run_id}")
```

Convert and clean up:
```bash
jupytext --to ipynb -o notebooks/layer1_research/03_run_backtest_fair_value_mr.ipynb \
         notebooks/layer1_research/_tmp_03_fair_value_mr.py
rm notebooks/layer1_research/_tmp_03_fair_value_mr.py
```

- [ ] **Step 2: Execute notebook end-to-end**

Run:
```bash
source .venv/bin/activate
jupyter nbconvert --to notebook --execute \
    notebooks/layer1_research/03_run_backtest_fair_value_mr.ipynb \
    --output 03_run_backtest_fair_value_mr.ipynb
```
Expected: no exceptions; `print_report` prints a filled-in metrics table with non-zero Sharpe.

- [ ] **Step 3: Commit**

```bash
git add notebooks/layer1_research/03_run_backtest_fair_value_mr.ipynb
git commit -m "backtest: add fair_value_mr backtest notebook"
```

---

## Task 18: Layer1 — HoldCash + RandomTrader notebooks

**Files:**
- Create: `notebooks/layer1_research/04_run_backtest_hold_cash.ipynb`
- Create: `notebooks/layer1_research/05_run_backtest_random_trader.ipynb`

- [ ] **Step 1: Create stubs + convert**

`notebooks/layer1_research/_tmp_04_hold_cash.py`:
```python
# %% [markdown]
# # 04 — Backtest: HoldCash (engine correctness check)
#
# HoldCashStrategy must end with final_equity == starting_capital and zero trades.

# %%
from datetime import datetime, timezone
from src.layer1_research.backtesting.config import BacktestConfig
from src.layer1_research.backtesting.runner import BacktestRunner
from src.layer1_research.backtesting.strategies.examples.hold_cash import HoldCashStrategy
from src.layer1_research.backtesting.reporting.cli_report import print_report

# %%
config = BacktestConfig(
    catalog_path="data/catalog",
    start=datetime(2024, 6, 1, tzinfo=timezone.utc),
    end=datetime(2024, 9, 1, tzinfo=timezone.utc),
    strategy_name="hold_cash",
    starting_capital=10_000.0,
    data_mode="trade",
)

# %%
result = BacktestRunner(config).run(HoldCashStrategy)
print_report(result.metrics())

# %% [markdown]
# ### Invariants

# %%
final_eq = float(result.equity_curve.iloc[-1])
assert final_eq == 10_000.0, f"Expected 10000, got {final_eq}"
assert len(result.trades) == 0
assert len(result.signals) == 0
print("HoldCash invariants hold.")
```

`notebooks/layer1_research/_tmp_05_random_trader.py`:
```python
# %% [markdown]
# # 05 — Backtest: RandomTrader (engine correctness check)
#
# Expected: negative net P&L of roughly -total_fees over many random trades.

# %%
from datetime import datetime, timezone
from src.layer1_research.backtesting.config import BacktestConfig
from src.layer1_research.backtesting.runner import BacktestRunner
from src.layer1_research.backtesting.strategies.examples.random_trader import (
    RandomTraderStrategy,
)
from src.layer1_research.backtesting.reporting.cli_report import print_report

# %%
config = BacktestConfig(
    catalog_path="data/catalog",
    start=datetime(2024, 6, 1, tzinfo=timezone.utc),
    end=datetime(2024, 9, 1, tzinfo=timezone.utc),
    strategy_name="random_trader",
    starting_capital=10_000.0,
    data_mode="trade",
    fee_rate_bps=100,
    strategy_params={"seed": 42, "p_trade": 0.02, "trade_size": 5.0},
)

# %%
result = BacktestRunner(config).run(RandomTraderStrategy)
print_report(result.metrics())

# %%
result.plot_equity_curve()

# %%
result.plot_pnl_histogram()

# %% [markdown]
# ### Sanity check: final P&L should be roughly -total_fees

# %%
net_pnl = float(result.equity_curve.iloc[-1]) - config.starting_capital
print(f"Net P&L:    ${net_pnl:,.2f}")
print(f"Total fees: ${result.metrics().total_fees:,.2f}")
print(f"Ratio:      {net_pnl / -result.metrics().total_fees if result.metrics().total_fees > 0 else 'n/a'}")
```

Convert both:
```bash
jupytext --to ipynb -o notebooks/layer1_research/04_run_backtest_hold_cash.ipynb \
         notebooks/layer1_research/_tmp_04_hold_cash.py
jupytext --to ipynb -o notebooks/layer1_research/05_run_backtest_random_trader.ipynb \
         notebooks/layer1_research/_tmp_05_random_trader.py
rm notebooks/layer1_research/_tmp_04_hold_cash.py notebooks/layer1_research/_tmp_05_random_trader.py
```

- [ ] **Step 2: Execute both**

```bash
jupyter nbconvert --to notebook --execute \
    notebooks/layer1_research/04_run_backtest_hold_cash.ipynb \
    --output 04_run_backtest_hold_cash.ipynb
jupyter nbconvert --to notebook --execute \
    notebooks/layer1_research/05_run_backtest_random_trader.ipynb \
    --output 05_run_backtest_random_trader.ipynb
```
Expected: both finish without errors; the `assert` in HoldCash passes.

- [ ] **Step 3: Commit**

```bash
git add notebooks/layer1_research/04_run_backtest_hold_cash.ipynb \
        notebooks/layer1_research/05_run_backtest_random_trader.ipynb
git commit -m "backtest: add HoldCash + RandomTrader backtest notebooks"
```

---

## Task 19: Layer1 — analyze_trades notebook (`99_analyze_trades.ipynb`)

**Files:**
- Create: `notebooks/layer1_research/99_analyze_trades.ipynb`

- [ ] **Step 1: Create stub**

`notebooks/layer1_research/_tmp_99_analyze_trades.py`:
```python
# %% [markdown]
# # 99 — Analyze a past MLflow run
#
# Reload a BacktestResult from a completed MLflow run and inspect it.

# %%
import mlflow
from src.layer1_research.backtesting.results import BacktestResult
from src.layer1_research.backtesting.reporting.cli_report import print_report

# %% [markdown]
# ## Pick a run

# %%
TRACKING_URI = "sqlite:///data/mlflow.db"
mlflow.set_tracking_uri(TRACKING_URI)
client = mlflow.tracking.MlflowClient()
exp = client.get_experiment_by_name("polymarket-backtests")
runs = client.search_runs([exp.experiment_id], order_by=["start_time DESC"], max_results=10)
for r in runs:
    print(r.info.run_id, r.data.params.get("strategy"), r.info.start_time)

# %% [markdown]
# Pick a run_id from the list above:

# %%
RUN_ID = runs[0].info.run_id   # or paste one from the listing

# %% [markdown]
# ## Reload + inspect

# %%
result = BacktestResult.from_mlflow(RUN_ID, tracking_uri=TRACKING_URI)
print_report(result.metrics())

# %%
result.plot_equity_curve()

# %%
result.plot_edge_calibration()

# %%
result.trades.head(20)
```

Convert:
```bash
jupytext --to ipynb -o notebooks/layer1_research/99_analyze_trades.ipynb \
         notebooks/layer1_research/_tmp_99_analyze_trades.py
rm notebooks/layer1_research/_tmp_99_analyze_trades.py
```

- [ ] **Step 2: Smoke-check imports**

Do not execute the notebook (the user may not have any mlflow runs yet). Just verify the JSON is valid:
```bash
python -c "import json; json.load(open('notebooks/layer1_research/99_analyze_trades.ipynb'))"
```

- [ ] **Step 3: Commit**

```bash
git add notebooks/layer1_research/99_analyze_trades.ipynb
git commit -m "backtest: add analyze_trades notebook (reload from MLflow)"
```

---

## Task 20: Delete `scripts/` folder

**Files:**
- Delete: entire `scripts/` directory

- [ ] **Step 1: Confirm each script has a notebook replacement**

Run:
```bash
ls notebooks/layer0_ingestion/ notebooks/layer1_research/ notebooks/layer4_execution/
ls scripts/
```
Expected: notebooks cover all migrated scripts. Missing anything? Review before deleting.

Expected notebook coverage:
- `check_allowances.py` → `notebooks/layer0_ingestion/check_allowances.ipynb`
- `debug_uma_signal.py` → `notebooks/layer0_ingestion/debug_uma_signal.ipynb`
- `explore_market.py` → `notebooks/layer0_ingestion/explore_market.ipynb`
- `test_connection.py` → `notebooks/layer0_ingestion/test_connection.ipynb`
- `view_markets.py` → `notebooks/layer0_ingestion/view_markets.ipynb`
- `preflight.py` → `notebooks/layer4_execution/preflight.ipynb`
- `load_data.py` → `notebooks/layer1_research/01_build_catalog.ipynb`
- `run_backtest.py` → replaced by per-strategy notebooks (03/04/05)
- `analyze_trades.py` → `notebooks/layer1_research/99_analyze_trades.ipynb`

- [ ] **Step 2: Delete `scripts/`**

```bash
git rm -r scripts/
```

- [ ] **Step 3: Verify test suite still passes**

Run:
```bash
source .venv/bin/activate
pytest tests/backtesting -x -q
```
Expected: all pass — no test imports from `scripts/`.

If something grep-able like `from scripts.` appears in the codebase, fix it:
```bash
grep -rn "from scripts\." src/ tests/ notebooks/ 2>/dev/null || true
grep -rn "import scripts" src/ tests/ notebooks/ 2>/dev/null || true
```

- [ ] **Step 4: Commit**

```bash
git commit -m "backtest: delete scripts/ — all entry points migrated to notebooks/"
```

---

## Task 21: Update `CLAUDE.md` and `README.md` for the new entry points

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Update `CLAUDE.md`**

Edit the "Running scripts" section (lines 11-17) to reflect the notebook-based flow:

Find:
```markdown
## Running scripts

\`\`\`bash
python scripts/check_allowances.py   # verify token allowances for EOA wallets
python src/websocket_feed.py         # test real-time WebSocket feed
\`\`\`

Set `DRY_RUN=1` in `.env` (default) to simulate orders without placing them on-chain.
```

Replace with:
```markdown
## Running things

Entry points live in `notebooks/`, grouped by layer:

- `notebooks/layer0_ingestion/` — CLOB/UMA/market-discovery helpers (e.g. `check_allowances.ipynb`, `view_markets.ipynb`)
- `notebooks/layer1_research/` — ETL + backtesting (`01_build_catalog.ipynb`, `03_run_backtest_*.ipynb`, `99_analyze_trades.ipynb`)
- `notebooks/layer4_execution/` — preflight checks

Open a notebook in VS Code or Jupyter and run top-to-bottom. The library lives in `src/`; notebooks are thin.

Set `DRY_RUN=1` in `.env` (default) to simulate orders without placing them on-chain.
```

- [ ] **Step 2: Update `README.md`**

Replace the "Running" section in `README.md` (around line 117) and the `scripts/` block in "Project Structure" (around line 79):

In "Project Structure", replace the `scripts/` block with:
```markdown
├── notebooks/                        # Entry points grouped by layer
│   ├── layer0_ingestion/             # CLOB, UMA, market discovery
│   ├── layer1_research/              # ETL + backtesting
│   └── layer4_execution/             # Preflight checks
```

In "Running", replace the `python scripts/...` block with:
```markdown
# Open any notebook under notebooks/ in VS Code or Jupyter, e.g.:
#   notebooks/layer1_research/03_run_backtest_fair_value_mr.ipynb
#   notebooks/layer1_research/04_run_backtest_hold_cash.ipynb
#   notebooks/layer0_ingestion/check_allowances.ipynb
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: point CLAUDE.md and README to notebooks/ entry points"
```

---

## Task 22: Full acceptance check

**Files:** none.

- [ ] **Step 1: Run the full test suite**

```bash
source .venv/bin/activate
pytest tests/backtesting -v
```
Expected: all tests pass.

- [ ] **Step 2: Grep for silent-failure patterns**

```bash
# Bare "except:" (no exception class)
grep -rEn "^\s*except\s*:" src/layer1_research/backtesting/ && echo "FAIL: bare except found" || echo "OK — no bare except"
# Broad "except Exception" that's not re-raising
grep -rn "except Exception" src/layer1_research/backtesting/ && echo "INVESTIGATE: broad except found" || echo "OK — no broad except"
# Magic capital fallback in strategies
grep -rn "= 10_000\.0" src/layer1_research/backtesting/strategies/ | grep -v "starting_capital\|max_position_size\|kelly_max" && echo "FAIL: magic capital" || echo "OK — no magic capital fallback"
# Silent price clamp
grep -rn "max(0\.001" src/layer1_research/backtesting/ && echo "FAIL: price clamp" || echo "OK — no price clamp"
```
Expected: each line prints its OK message.

- [ ] **Step 3: Execute the fair_value_mr notebook once more**

```bash
jupyter nbconvert --to notebook --execute \
    notebooks/layer1_research/03_run_backtest_fair_value_mr.ipynb \
    --output 03_run_backtest_fair_value_mr.ipynb
```
Expected: runs clean, the printed metrics include a non-zero Sharpe ratio and a non-empty per-market section.

- [ ] **Step 4: Execute hold_cash notebook — its `assert` is the engine regression**

```bash
jupyter nbconvert --to notebook --execute \
    notebooks/layer1_research/04_run_backtest_hold_cash.ipynb \
    --output 04_run_backtest_hold_cash.ipynb
```
Expected: runs clean; assertion cell prints "HoldCash invariants hold."

- [ ] **Step 5: Verify branch is ahead of main and ready for a PR**

```bash
git log --oneline main..HEAD
git status
```
Expected: 20+ commits, clean working tree.

- [ ] **Step 6: No commit — stop here**

The user's memory says "Never commit to main directly" and "alternatively, commit to a separate branch and send a PR." We've been committing to `feat/backtesting-overhaul`. Stop here and report to the user that the branch is ready for a PR review.

Do **not** merge to main.

---
