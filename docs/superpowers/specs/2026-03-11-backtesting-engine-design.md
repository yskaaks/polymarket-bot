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

```
src/backtesting/
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

### Data Layer

**Purpose:** Get external historical data into NautilusTrader's `ParquetDataCatalog` format.

**`DataLoader` ABC** — each source implements:
- `load_markets() -> list[BinaryOption]` — market metadata as Nautilus instruments
- `load_trades() -> Iterator[TradeTick]` — trade data as Nautilus tick types

**Implementations:**
- **`BeckerParquetLoader`** — reads Jon-Becker `prediction-market-analysis` repo Parquet files via DuckDB. Maps columns (`maker`, `taker`, `asset`, `side`, `size`, `price`) to Nautilus `TradeTick`. Builds `BinaryOption` instruments from market metadata.
- **`S3Loader`** — downloads Parquet files from S3 bucket, delegates to format-specific parsing.
- **`CSVLoader`** — reads CSV with configurable column mapping.

**`instruments.py`** — factory that builds `BinaryOption` objects from market metadata (question, outcomes, token IDs, settlement date). Handles YES/NO token pair relationship.

**`catalog.py`** — orchestrator: takes a `DataLoader`, runs it, writes output to a `ParquetDataCatalog` directory. One-time ETL step.

**Bar aggregation** — handled by NautilusTrader natively. Load `TradeTick` data and configure aggregation (time-based, volume-based) in backtest config.

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

**Reusing existing code:** `SignalProvider` implementations from `src/layer2_signals/` (Kalshi signal, crypto price signal) can be wrapped as helpers that strategies call inside `generate_signal()` — adapted from live-polling to consuming historical data.

### Fee Model & Position Sizing

**`PolymarketFeeModel(FeeModel)`** — implements Nautilus's `FeeModel`:
- Formula: `fee = price * (1 - price) * (fee_rate_bps / 10_000)`
- Max fee at p=0.50, zero at extremes
- Per-market fee rates (most markets 0 bps, some 20-50 bps)
- Reuses existing fee math from `src/utils.py`

**`PositionSizer`** — determines order size given a signal:
- **Kelly criterion** — reuses `kelly_fraction()` from `src/utils.py`
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
python scripts/run_backtest.py --strategy kalshi_divergence --start 2024-01-01 --end 2024-12-31 --bar-interval 5m --charts
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

## Integration with Existing Code

- `src/utils.py` — reuse `kelly_fraction()`, `logit()`, `expit()`, fee math
- `src/layer2_signals/` — wrap existing signal providers for historical replay
- `config/settings.py` — backtest uses its own `BacktestConfig`, no changes to live `Config`
- No modifications to existing live trading code

## New Dependencies

- `nautilus_trader` — backtesting framework
- `duckdb` — querying external Parquet files during ETL
- `matplotlib` or `plotly` — chart generation (optional)

## Workflow

```bash
# 1. Load external data into Nautilus catalog
python scripts/load_data.py --source becker --path ../prediction-market-analysis/data

# 2. Run a backtest
python scripts/run_backtest.py --strategy kalshi_divergence --start 2024-01-01 --end 2024-12-31

# 3. Run with charts
python scripts/run_backtest.py --strategy kalshi_divergence --start 2024-01-01 --end 2024-12-31 --charts
```

## Data Source

Primary historical data: [Jon-Becker/prediction-market-analysis](https://github.com/Jon-Becker/prediction-market-analysis) — 36GB dataset with Polymarket and Kalshi trades in Parquet format. Contains markets, trades (maker/taker, side, price, size, timestamps), and block data.
