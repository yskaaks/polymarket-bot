# Backtesting Framework Overhaul — Design

**Date:** 2026-04-24
**Status:** Draft (pending user review)
**Scope:** `src/layer1_research/backtesting/`, `scripts/` → `notebooks/`

## Context

The repo currently runs an end-to-end Nautilus-based backtest (`fair_value_mr` strategy, Becker-parquet loader → `ParquetDataCatalog`, fill model with tiered depth, MLflow logging). It completes, but the internals have several issues that make the results untrustworthy and the surface hard to extend:

1. `sharpe_ratio`, `max_drawdown_pct`, `win_rate` are hardcoded to `0.0` in the summary — never computed.
2. `fee_drag` returns 0 when `gross_pnl <= 0`, hiding a real signal.
3. No equity curve / time-series P&L is returned to the caller — only top-line scalars.
4. Bare `except: pass` blocks in `_build_summary` silently zero out `final_equity`, `total_fees`, `win_rate` if the Nautilus report parsing fails. Conflicts with the CLAUDE.md "never hide errors" rule.
5. `strategies/base.py` has a silent `capital = 10_000.0` fallback if the account lookup fails.
6. `becker_parquet.py` silently clamps raw trade prices to `[0.001, 1.0]` instead of raising on out-of-range values.
7. `scripts/run_backtest.py` is a CLI; the user's workflow is notebook-driven.
8. `--charts` flag exists in the CLI but no chart code exists.

The user's goal: make backtesting a trustworthy, reproducible foundation, with notebooks as the entry point, so future work (new strategies, Prefect data pipelines) can build on it.

## Goals

- All reported scalar metrics are actually computed from real data.
- Results are returned as a programmatic `BacktestResult` object containing the equity curve, trades, signals, and raw reports — not just printed scalars.
- Trade-level and signal-level analytics are first-class (edge at order, realized edge, slippage, calibration).
- Silent-failure patterns (bare `except`, magic-number fallbacks, value clamping) are removed from the backtesting pipeline.
- `scripts/` is replaced by `notebooks/` grouped by layer (`layer0_ingestion/`, `layer1_research/`, `layer4_execution/`).
- Correctness-test strategies (HoldCash, Random) are added and act as engine-regression tests.
- Metrics and charts are pure functions over `BacktestResult` — independently testable.

## Non-goals

- Catalog expansion beyond the current 20-market snapshot.
- New data sources (Kalshi, L2 orderbook, Binance).
- New real strategies beyond the correctness tests.
- Refactoring layer 0, 2, 3, 4 source code (their scripts get notebooks, but `src/` is untouched).
- Live trading changes (`TradingClient`, `PolymarketClient`, `UMAClient`, UMA arb strategy).
- Prefect integration — called out as the next phase.
- Walk-forward / parameter sweep / multi-strategy portfolio runs.
- Saving charts as artifacts automatically; CLI re-add.

## Approach

**Approach A (in-place refactor) with one borrow from Approach B (Nautilus analyzer).**

Keep the current module layout. Fix internals, introduce a `BacktestResult` object, split metric computation out of the runner into pure functions, add correctness-test strategies, migrate scripts → notebooks grouped by layer. Use Nautilus's built-in `PortfolioAnalyzer` for standard scalar perf stats (Sharpe, Sortino, max drawdown, returns); compute everything else (win rate, profit factor, fee drag, edge-based metrics, per-market) ourselves from our `trades` and `signals` DataFrames.

Rationale: this gets correct numbers quickly, keeps our surface area small, and defers deeper re-layering (Approach C) to after there are 2–3 strategies actually exercising the module seams.

## Architecture

### Directory layout

```
src/layer1_research/backtesting/
├── config.py                     # BacktestConfig (unchanged)
├── runner.py                     # orchestration only — no metric computation
├── results.py                    # NEW — BacktestResult, SignalSnapshot, Trade
├── data/                         # loaders + catalog build (minor fixes)
├── execution/                    # fill model, fees, sizer (unchanged)
├── strategies/
│   ├── base.py                   # silent fallbacks removed; signal log added
│   └── examples/
│       ├── fair_value_mean_reversion.py   # existing
│       ├── hold_cash.py                   # NEW — correctness test
│       └── random_trader.py               # NEW — correctness test
└── reporting/
    ├── metrics.py                # compute_metrics(result) -> BacktestMetrics
    ├── charts.py                 # NEW — equity, drawdown, calibration, per-market
    └── cli_report.py             # pretty-printer (updated for new metrics)

notebooks/
├── layer0_ingestion/
│   ├── check_allowances.ipynb
│   ├── explore_market.ipynb
│   ├── view_markets.ipynb
│   ├── test_connection.ipynb
│   └── debug_uma_signal.ipynb
├── layer1_research/
│   ├── 01_build_catalog.ipynb          # was load_data.py
│   ├── 02_explore_data.ipynb           # existing, kept & tidied
│   ├── 03_run_backtest_fair_value_mr.ipynb
│   ├── 04_run_backtest_hold_cash.ipynb
│   ├── 05_run_backtest_random_trader.ipynb
│   └── 99_analyze_trades.ipynb         # was analyze_trades.py
└── layer4_execution/
    └── preflight.ipynb
```

`scripts/` is deleted. `run_backtest.py` is not migrated — the notebook is the new entry point.

### Data layer (minor fixes)

- **Remove the price clamp** in `becker_parquet.py:164` (`max(0.001, min(1.0, price))`). If a raw trade comes back with an out-of-range price, raise.
- **Skip-threshold made explicit**: the `if raw_trade.size < 0.05: continue` filter in `catalog.py:68` is lifted to a named constant `MIN_TRADE_SIZE_PRECISION` with a comment linking it to the `size_precision=1` instrument config. No behavioural change.

The loader interface (`DataLoader`, `RawTrade`, `MarketInfo`, `MarketFilter`) and `build_catalog` orchestrator stay.

### Runner cleanup

Three changes in `runner.py`:

1. **Strip metric computation out.** `_build_summary` moves to `reporting/metrics.py` as `compute_metrics(result: BacktestResult) -> BacktestMetrics`. Runner only orchestrates: build engine → load data → run → collect raw artifacts → return a `BacktestResult`.
2. **Delete the bare `except: pass` blocks.** If a Nautilus report is missing an expected column, raise.
3. **Remove the `capital = 10_000.0` fallback** in `strategies/base.py`. If the account lookup fails, raise.

MLflow logging is removed from the runner and becomes a method on `BacktestResult` (`result.to_mlflow(run_name=...)`), called by the notebook. Runner does not touch MLflow.

New public surface:

```python
runner = BacktestRunner(config)
result: BacktestResult = runner.run(strategy_class)
```

### Results layer (`results.py`)

```python
@dataclass
class SignalSnapshot:
    ts: datetime
    instrument_id: str
    direction: str            # BUY / SELL / FLAT
    market_price: float       # price at signal time (tick close)
    confidence: float         # strategy's probability estimate
    target_price: float       # strategy's fair value
    size: float
    client_order_id: str | None
    edge_at_order: float      # BUY: confidence - market_price
                              # SELL: market_price - (1 - confidence)

@dataclass
class Trade:
    """Round-trip: entry fill -> exit fill (or position close)."""
    instrument_id: str
    direction: str            # LONG / SHORT
    entry_ts: datetime
    exit_ts: datetime | None  # None if still open at end of backtest
    entry_price: float
    exit_price: float | None
    size: float
    fees: float
    gross_pnl: float
    net_pnl: float            # gross - fees
    edge_at_entry: float      # from the signal that opened the trade
    realized_edge: float      # exit_price - entry_price for LONG (inverse for SHORT)
    slippage_bps: float       # fill vs. signal market_price
    signal_confidence: float

@dataclass
class BacktestResult:
    config: BacktestConfig
    fills: pd.DataFrame                  # cleaned Nautilus fills report
    positions: pd.DataFrame              # cleaned Nautilus positions report
    account: pd.DataFrame                # cleaned Nautilus account report
    instruments: list[BinaryOption]
    analyzer_stats: dict                 # Nautilus PortfolioAnalyzer output
    equity_curve: pd.Series              # timestamp-indexed, USD
    signals: pd.DataFrame                # one row per SignalSnapshot
    trades: pd.DataFrame                 # one row per round-trip Trade

    def metrics(self) -> BacktestMetrics: ...     # memoized
    def plot_equity_curve(self, ax=None): ...
    def plot_drawdown(self, ax=None): ...
    def plot_pnl_histogram(self, ax=None): ...
    def plot_edge_calibration(self, ax=None): ...
    def plot_per_market_pnl(self, ax=None): ...
    def to_mlflow(self, run_name: str | None = None): ...

    @classmethod
    def from_mlflow(cls, run_id: str) -> "BacktestResult": ...
```

Key points:

- String columns like `"5.01 USD"` are parsed to floats once at construction; no lambdas on read.
- `equity_curve` is built from `account` (balance over time), resampled to 1-minute bars over the backtest window.
- `analyzer_stats` stores what Nautilus's `trader.analyzer` returns.
- Nothing is computed eagerly; `.metrics()` and plots are pulled on demand.

**Signal capture mechanism.** `PredictionMarketStrategy._act_on_signal` appends a `SignalSnapshot` to `self._signal_log` before `submit_order`. `client_order_id` is captured from the returned order. After `engine.run()`, the runner pulls `strategy._signal_log` out and stores it as `result.signals`.

**Trade construction.** Post-run, iterate `fills` ordered by `ts_event` per instrument. With `OmsType.NETTING`, Nautilus position closes already carry `realized_pnl`; we match entry/exit timestamps + prices and join the signal row by `client_order_id`.

### Reporting layer

`reporting/metrics.py`:

```python
@dataclass
class BacktestMetrics:
    # Scalar perf — from Nautilus analyzer
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
    avg_hold_time: timedelta
    # Execution quality
    total_fees: float
    fee_drag_pct: float          # fees / |gross_pnl| — uses abs() so losers still show drag
    avg_slippage_bps: float
    maker_taker_ratio: float
    # Signal quality (prediction-market specific)
    avg_edge_at_order: float
    edge_realization_rate: float # mean(realized_edge / edge_at_order) for closed trades
    # Per-market
    per_market: dict[str, PerMarketStats]

@dataclass
class PerMarketStats:
    trades: int
    net_pnl: float
    win_rate: float
    avg_edge_at_order: float
    realized_edge: float
```

Sharpe / Sortino / max DD / returns come from `trader.analyzer.get_performance_stats_pnls()` on the USD account series. Win rate, profit factor, fee drag, edge metrics, per-market stats are pandas operations on `result.trades` and `result.signals`.

`reporting/charts.py` — matplotlib functions, each takes a `BacktestResult` and an optional `ax`, returns the `Figure`:

- `plot_equity_curve` — equity with drawdown shaded underneath
- `plot_drawdown` — standalone drawdown
- `plot_pnl_histogram` — distribution of trade P&Ls
- `plot_edge_calibration` — `edge_at_order` (x) vs `realized_edge` (y), with y=x reference line
- `plot_per_market_pnl` — horizontal bar, P&L by market

`reporting/cli_report.py` stays, updated to print the full `BacktestMetrics` table and a `top_n` per-market breakdown.

### Correctness-test strategies

`strategies/examples/hold_cash.py` — `HoldCashStrategy`. Subscribes to nothing, never submits an order. Tested invariants: `final_equity == starting_capital`, `total_trades == 0`, `total_fees == 0`.

`strategies/examples/random_trader.py` — `RandomTraderStrategy`. On each trade tick, with probability `p` (default 0.01), emits a BUY or SELL signal at the current price with a random small size. Takes a seed for reproducibility. Tested invariants: determinism (same seed → identical `trades`/`signals` DataFrames); over the 20-market snapshot, `net_pnl` is negative and in the same order of magnitude as `-total_fees` (loose "zero edge minus fees" check).

Both get their own notebook under `notebooks/layer1_research/`.

### Notebook structure

Each backtest notebook follows the same shape:

```python
# 1. Imports
# 2. Config
config = BacktestConfig(...)
# 3. Run
result = BacktestRunner(config).run(FairValueMeanReversionStrategy)
# 4. Metrics
metrics = result.metrics()
print_report(metrics)
# 5. Plots
result.plot_equity_curve()
result.plot_drawdown()
result.plot_edge_calibration()
result.plot_per_market_pnl()
# 6. Inspect
result.trades.head(20)
result.signals.head(20)
# 7. Optional MLflow log
result.to_mlflow(run_name="fair_value_mr_2024Q3")
```

`99_analyze_trades.ipynb` uses `BacktestResult.from_mlflow(run_id)` to reload a past run and apply the same plots.

Layer0 / layer4 notebooks are near-verbatim ports of the existing scripts — no redesign, they exist so the framework has a consistent "notebooks are the entry point" story.

## Testing

### Unit tests (new files, under `tests/backtesting/`)

- **`test_results.py`** — `BacktestResult` construction from fake Nautilus reports; fills → trades round-trip matching; derived columns (`edge_at_order`, `realized_edge`, `slippage_bps`) computed correctly; `signals` / `trades` / `fills` joined by `client_order_id`.
- **`test_metrics_computation.py`** — `compute_metrics` on hand-built `BacktestResult` fixtures with known answers (e.g., 4 trades, 3 winners → win_rate=0.75; known returns series → known Sharpe).
- **`test_correctness_strategies.py`** — runs `HoldCashStrategy` over the 20-market catalog and asserts `final_equity == starting_capital`, `total_trades == 0`, `total_fees == 0`. Runs `RandomTraderStrategy` with a seed and asserts determinism.
- **`test_signal_capture.py`** — verifies `_signal_log` is populated correctly when `_act_on_signal` fires, with the right `edge_at_order` sign for BUY vs. SELL.

### Existing tests updated

- **`test_runner_e2e.py`** — asserts on `BacktestResult` shape instead of old scalar `BacktestSummary`.
- **`test_becker_loader.py`** — expects a raise on out-of-range raw prices instead of silent clamp.
- **`test_metrics.py`** — absorbs new metric cases.

### Notebook smoke tests (optional, CI-only)

- **`test_notebooks_smoke.py`** — uses `nbclient`/`papermill` to execute each `layer1_research/*.ipynb` top-to-bottom on the 20-market catalog; marked `@pytest.mark.slow`. Guards against notebook rot.

### Acceptance bar

1. All unit tests pass.
2. HoldCash + RandomTrader invariants hold on the 20-market catalog.
3. `fair_value_mr` notebook produces a non-zero Sharpe, a plotted equity curve, and a populated per-market breakdown.
4. No bare `except`, no hardcoded capital fallbacks, no silent price clamps remain anywhere in `src/layer1_research/backtesting/`.

## Open questions

None at spec time. Data universe expansion, new loaders, new real strategies, and Prefect integration are all explicit non-goals to be addressed in follow-up specs.
