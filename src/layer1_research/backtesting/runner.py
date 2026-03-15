"""Backtest runner: wires config, engine, strategy, and reporting together."""
from typing import Type

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.layer1_research.backtesting.config import BacktestConfig
from src.layer1_research.backtesting.reporting.cli_report import print_report
from src.layer1_research.backtesting.reporting.metrics import BacktestSummary, fee_drag
from src.layer1_research.backtesting.strategies.base import (
    PredictionMarketStrategy, PredictionMarketStrategyConfig,
)

POLYMARKET_VENUE = Venue("POLYMARKET")


class BacktestRunner:
    """Orchestrates a backtest run from config to results."""

    def __init__(self, config: BacktestConfig):
        self._config = config

    def run(self, strategy_class: Type[PredictionMarketStrategy]) -> BacktestSummary:
        catalog = ParquetDataCatalog(self._config.catalog_path)

        engine_config = BacktestEngineConfig(
            logging=LoggingConfig(log_level="WARNING"),
        )
        engine = BacktestEngine(config=engine_config)

        # Add simulated venue
        engine.add_venue(
            venue=POLYMARKET_VENUE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(self._config.starting_capital, USD)],
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

        # Load trade ticks, passing instrument_ids as strings
        instrument_id_strs = [str(inst.id) for inst in instruments]
        ticks = catalog.trade_ticks(instrument_ids=instrument_id_strs)

        # Filter by date range
        start_ns = int(self._config.start.timestamp() * 1e9)
        end_ns = int(self._config.end.timestamp() * 1e9)
        filtered_ticks = [
            t for t in ticks
            if start_ns <= t.ts_event <= end_ns
        ]

        if filtered_ticks:
            engine.add_data(filtered_ticks)

        # Configure strategy
        strategy_config = PredictionMarketStrategyConfig(
            instrument_ids=instrument_id_strs,
            fee_rate_bps=self._config.fee_rate_bps,
            sizer_mode=self._config.position_sizer,
        )
        strategy = strategy_class(config=strategy_config)
        engine.add_strategy(strategy)

        # Run
        engine.run()

        # Collect results
        summary = self._build_summary(engine, strategy)
        print_report(summary)
        engine.dispose()

        return summary

    def _build_summary(self, engine: BacktestEngine, strategy: PredictionMarketStrategy) -> BacktestSummary:
        fills_report = engine.trader.generate_fills_report()
        positions_report = engine.trader.generate_positions_report()
        total_trades = len(fills_report) if fills_report is not None and not fills_report.empty else 0

        final_equity = self._config.starting_capital
        total_fees = 0.0

        try:
            account_report = engine.trader.generate_account_report(POLYMARKET_VENUE)
            if account_report is not None and not account_report.empty:
                last_row = account_report.iloc[-1]
                balance = last_row.get("total", self._config.starting_capital)
                if isinstance(balance, (int, float)):
                    final_equity = float(balance)

            if fills_report is not None and "commission" in fills_report.columns:
                total_fees = float(fills_report["commission"].sum())
        except Exception:
            pass

        total_return_pct = (
            (final_equity - self._config.starting_capital)
            / self._config.starting_capital * 100
        )

        win_rate = 0.0
        try:
            if positions_report is not None and not positions_report.empty:
                if "realized_pnl" in positions_report.columns:
                    closed = positions_report[positions_report["realized_pnl"] != 0]
                    if len(closed) > 0:
                        wins = (closed["realized_pnl"] > 0).sum()
                        win_rate = wins / len(closed)
        except Exception:
            pass

        gross_pnl = final_equity - self._config.starting_capital + total_fees

        return BacktestSummary(
            strategy_name=self._config.strategy_name,
            start=self._config.start.strftime("%Y-%m-%d"),
            end=self._config.end.strftime("%Y-%m-%d"),
            starting_capital=self._config.starting_capital,
            final_equity=final_equity,
            total_return_pct=total_return_pct,
            sharpe_ratio=0.0,
            max_drawdown_pct=0.0,
            win_rate=win_rate,
            total_trades=total_trades,
            total_fees=total_fees,
            brier=None,
            fee_drag_pct=fee_drag(total_fees, gross_pnl),
        )
