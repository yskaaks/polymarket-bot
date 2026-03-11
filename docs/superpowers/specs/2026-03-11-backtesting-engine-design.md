# Backtesting Engine Design

## Overview

A modular backtesting engine built on NautilusTrader for validating signal-based directional strategies on prediction markets. Designed for Polymarket initially, with architecture supporting future market-making backtesting.

## Goals

- Backtest signal-based directional strategies against historical prediction market data
- Full portfolio simulation: P&L, Sharpe, drawdown, win rate, exposure, fees, per-market breakdowns
- Modular data ingestion from multiple sources (Parquet, S3, CSV)
- Strategy interface that mirrors live trading patterns for easy promotion to production
- Trade-by-trade or bar-based replay, configurable per run

## Framework Choice: NautilusTrader

Chosen over a custom engine because:

- Built-in `BinaryOption` instrument type and production Polymarket adapter
- Event-driven strategy interface (`on_bar()`, `on_trade_tick()`, `on_order_filled()`)
- `ParquetDataCatalog` for native Parquet data loading
- Built-in exchange simulator with order matching (needed for future MM backtesting)
- Same strategy code runs in backtest and live — zero rewrite
- Built-in portfolio stats (PnL, Sharpe, drawdown, positions)

## Architecture

### Directory Structure

Backtesting lives under `src/layer1_research/backtesting/` — the `layer1_research` directory was reserved for exactly this kind of work and is currently empty.

```
src/layer1_research/backtesting/
├── __init__.py
├── config.py                    # BacktestConfig
├── runner.py                    # BacktestRunner
├── data/
│   ├── __init__.py
│   ├── loaders/
│   │   ├── __init__.py
│   │   ├── base.py              # DataLoader ABC
│   │   ├── becker_parquet.py    # Jon-Becker repo loader
│   │   ├── s3.py                # S3 loader
│   │   └── csv.py               # CSV loader
│   ├── instruments.py           # BinaryOption factory
│   └── catalog.py               # ETL orchestrator → ParquetDataCatalog
├── strategies/
│   ├── __init__.py
│   ├── base.py                  # PredictionMarketStrategy
│   ├── signal.py                # Signal dataclass
│   └── examples/
│       ├── __init__.py
│       ├── kalshi_divergence.py
│       └── fair_value_mean_reversion.py
├── execution/
│   ├── __init__.py
│   ├── fees.py                  # PolymarketFeeModel
│   └── sizer.py                 # PositionSizer (Kelly, fixed fractional)
└── reporting/
    ├── __init__.py
    ├── metrics.py               # Custom prediction market metrics
    ├── cli_report.py            # Terminal output
    └── charts.py                # Visual reports

scripts/
├── run_backtest.py              # CLI entry point
└── load_data.py                 # One-time ETL: external data → Nautilus catalog

data/
└── catalog/                     # ParquetDataCatalog output (gitignored)
```

**Note on strategy directories:** `src/strategies/` contains live trading strategies (`MarketMakerStrategy`, `UmaArbStrategy`). `src/layer1_research/backtesting/strategies/` contains backtesting strategy classes that extend Nautilus's `Strategy`. These are separate because backtesting strategies use Nautilus's event system while live strategies use the bot's own event loop. The goal is to keep the signal logic portable between them — the `generate_signal()` core can be extracted and shared, but the surrounding lifecycle is different.

### Data Layer

**Purpose:** Get external historical data into NautilusTrader's `ParquetDataCatalog` format.

**`DataLoader` ABC** — each source implements:
- `load_markets() -> list[BinaryOption]` — market metadata as Nautilus instruments
- `load_trades() -> Iterator[TradeTick]` — trade data as Nautilus tick types

**Implementations:**
- **`BeckerParquetLoader`** — reads Jon-Becker `prediction-market-analysis` repo Parquet files via DuckDB. Maps columns (`maker`, `taker`, `asset`, `side`, `size`, `price`) to Nautilus `TradeTick`. Builds `BinaryOption` instruments from market metadata. Validates expected columns on load and raises `ValueError` with clear messages if schema doesn't match.
- **`S3Loader`** — downloads Parquet files from S3 bucket, delegates to format-specific parsing.
- **`CSVLoader`** — reads CSV with configurable column mapping.

**`instruments.py`** — factory that builds `BinaryOption` objects from market metadata (question, outcomes, token IDs, settlement date). Handles YES/NO token pair relationship.

**`catalog.py`** — orchestrator: takes a `DataLoader`, runs it, writes output to a `ParquetDataCatalog` directory. One-time ETL step.

**Bar aggregation** — handled by NautilusTrader natively. Load `TradeTick` data and configure aggregation in backtest config. Initially only time-based bars are supported (`bar_interval` in config). Volume-based and tick-count aggregation can be added later by extending `BacktestConfig`.

### Strategy Interface

**`PredictionMarketStrategy(Strategy)`** — extends Nautilus `Strategy` with prediction-market conveniences:

- Delegates `on_bar()` / `on_trade_tick()` to `generate_signal()` which subclasses implement
- `generate_signal(instrument, data) -> Signal | None` — the single method strategy authors override
- Position management helpers: `enter_long()`, `enter_short()`, `exit_position()` that handle BinaryOption mechanics (buying YES vs buying NO)
- `get_yes_no_pair(instrument_id)` — resolves paired token
- Fee-aware sizing using Polymarket parabolic fee formula

**`Signal` dataclass:**

```python
@dataclass
class Signal:
    direction: Literal["BUY", "SELL", "FLAT"]
    confidence: float        # 0.0 to 1.0
    target_price: float      # estimated fair value
    size: float | None       # None = let position sizer decide
    metadata: dict | None    # strategy-specific context for reporting
```

### Adapting Live SignalProviders for Backtesting

The existing `SignalProvider` ABC (`src/layer2_signals/fair_value.py`) returns logit-space adjustments via `get_adjustment(token_id, current_fv) -> float`. This is a different interface from the backtesting `Signal` dataclass. Additionally, live signal providers (`KalshiSignal`, `CryptoPriceSignal`) rely on real-time polling (HTTP, WebSocket) which cannot run during replay.

**Solution: Historical signal data as custom Nautilus data types.**

1. **During ETL** (`scripts/load_data.py`): for strategies that depend on external signals (Kalshi prices, crypto prices), the loader also ingests the corresponding historical data and writes it to the catalog as custom Nautilus `Data` subclasses (e.g., `KalshiPriceData`, `CryptoPriceData`).

2. **During replay**: the backtesting strategy subscribes to these custom data types via `self.subscribe_data()`. When Nautilus delivers the data in timestamp order alongside trade/bar data, the strategy's `on_data()` handler updates an internal state dict of latest external prices.

3. **In `generate_signal()`**: the strategy reads from the internal state dict to compute cross-exchange divergence or other signal logic. The core math (logit adjustments, divergence thresholds) is extracted from the live signal providers into pure functions in `src/utils.py` or a shared module, so both live and backtest code use the same calculations.

This avoids wrapping the live polling-based classes entirely. The signal math is shared; the data delivery mechanism is different (live polling vs Nautilus replay).

### Fee Model & Position Sizing

**`PolymarketFeeModel(FeeModel)`** — implements Nautilus's `FeeModel`:
- Formula: `fee = price * (1 - price) * (fee_rate_bps / 10_000)`
- Max fee at p=0.50, zero at extremes
- Per-market fee rates (most markets 0 bps, some 20-50 bps)
- Reuses existing `polymarket_taker_fee()` from `src/utils.py`

**`PositionSizer`** — determines order size given a signal:
- **Kelly criterion** — reuses `kelly_criterion()` from `src/utils.py`
- **Fixed fractional** — risk fixed % of portfolio per trade
- Configurable per-backtest run; strategies can override via `Signal.size`

### Backtest Configuration & Runner

**`BacktestConfig`:**

```python
@dataclass
class BacktestConfig:
    # Data
    catalog_path: str
    markets: list[str] | None          # specific market IDs, None = all
    start: datetime
    end: datetime

    # Strategy
    strategy_class: type[PredictionMarketStrategy]
    strategy_params: dict

    # Execution
    data_mode: Literal["trade", "bar"]
    bar_interval: timedelta | None     # required if data_mode = "bar"
    fee_rate_bps: float

    # Portfolio
    starting_capital: float            # USDC
    position_sizer: Literal["kelly", "fixed_fractional"]
    max_position_pct: float
    max_total_exposure_pct: float
```

**`BacktestRunner`:**
1. Loads config
2. Builds Nautilus `BacktestNode`/`BacktestEngine` with simulated venue, fee model, instruments, data
3. Instantiates strategy
4. Runs engine
5. Passes results to reporter

**CLI entry point** — `scripts/run_backtest.py`:
```bash
# Full run
python scripts/run_backtest.py --strategy kalshi_divergence --start 2024-01-01 --end 2024-12-31 --bar-interval 5m --charts

# Dry run — preview markets/instruments and date range without running simulation
python scripts/run_backtest.py --strategy kalshi_divergence --start 2024-01-01 --end 2024-12-31 --dry-run
```

### Reporting & Analysis

**Built-in from Nautilus (free):**
- Total P&L, Sharpe, Sortino, max drawdown
- Win rate, profit factor, average win/loss
- Total trades, per-instrument breakdowns

**Custom prediction market metrics (`metrics.py`):**
- Brier score — signal confidence calibration vs actual outcomes
- Edge capture — actual returns vs theoretical edge at entry
- Fee drag — total fees as % of gross P&L
- Per-market breakdown — P&L, trade count, win rate by market
- Resolution P&L — positions held to settlement vs exited early

**CLI report (`cli_report.py`):** Summary table printed to terminal after each run.

**Charts (`charts.py`):** Saved to `output/backtests/`, generated via `--charts` flag:
- Equity curve with drawdown shading
- Returns distribution histogram
- Calibration plot (signal confidence vs actual win rate)
- Per-market P&L bar chart
- Exposure over time stacked area chart

## Config Isolation

The live `Config` singleton in `config/settings.py` crashes at import time if env vars like `PRIVATE_KEY` are missing. Backtesting code must not transitively import any module that triggers this.

**Approach:** Backtesting code under `src/layer1_research/backtesting/` only imports:
- `src/utils.py` — math functions (logit, expit, fee calc, kelly). This module does not import `config/settings.py`.
- NautilusTrader types
- Standard library / third-party (DuckDB, matplotlib)

It does **not** import from `src/layer2_signals/`, `src/layer3_portfolio/`, `src/layer4_execution/`, or `src/strategies/` — all of which depend on the live config. Instead, shared signal math is extracted into pure functions in `src/utils.py` (which is already config-independent).

## Integration with Existing Code

- `src/utils.py` — reuse `kelly_criterion()`, `logit()`, `expit()`, `polymarket_taker_fee()`
- Signal math extracted to pure functions in `src/utils.py` for sharing between live and backtest
- `config/settings.py` — not imported by backtesting code; backtest uses its own `BacktestConfig`
- No modifications to existing live trading code

## New Dependencies

Added to `pyproject.toml` under `[project.optional-dependencies.backtesting]` so production deployments are not burdened:

```toml
[project.optional-dependencies]
backtesting = [
    "nautilus_trader",
    "duckdb",
    "matplotlib",
]
```

Install with: `pip install -e ".[backtesting]"`

## Testing

Tests live in `tests/backtesting/`:

```
tests/backtesting/
├── test_data_loaders.py         # Schema validation, column mapping, edge cases
├── test_instruments.py          # BinaryOption factory, YES/NO pairing
├── test_fees.py                 # Parabolic fee model vs known values
├── test_sizer.py                # Kelly and fixed fractional sizing
├── test_signal.py               # Signal dataclass validation
├── test_runner_e2e.py           # Minimal end-to-end: synthetic data → strategy → results
└── fixtures/
    └── sample_data.py           # Small synthetic Parquet/trade datasets
```

The end-to-end test uses a trivial strategy (e.g., "always buy when price < 0.30") against synthetic data with known outcomes, to verify the full pipeline produces expected P&L.

## Workflow

```bash
# 0. Install backtesting dependencies
pip install -e ".[backtesting]"

# 1. Load external data into Nautilus catalog
python scripts/load_data.py --source becker --path ../prediction-market-analysis/data

# 2. Run a backtest
python scripts/run_backtest.py --strategy kalshi_divergence --start 2024-01-01 --end 2024-12-31

# 3. Run with charts
python scripts/run_backtest.py --strategy kalshi_divergence --start 2024-01-01 --end 2024-12-31 --charts

# 4. Dry run to preview
python scripts/run_backtest.py --strategy kalshi_divergence --start 2024-01-01 --end 2024-12-31 --dry-run
```

## Data Source

Primary historical data: [Jon-Becker/prediction-market-analysis](https://github.com/Jon-Becker/prediction-market-analysis) — 36GB dataset with Polymarket and Kalshi trades in Parquet format. Contains markets, trades (maker/taker, side, price, size, timestamps), and block data.
