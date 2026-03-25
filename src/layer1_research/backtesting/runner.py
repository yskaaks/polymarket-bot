"""Backtest runner: wires config, engine, strategy, and reporting together."""
import inspect
import tempfile
from pathlib import Path
from typing import Type

import mlflow
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import MakerTakerFeeModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.risk.config import RiskEngineConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, AssetClass, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import BinaryOption
from nautilus_trader.model.objects import Money, Price, Quantity
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
            risk_engine=RiskEngineConfig(bypass=True),
        )
        engine = BacktestEngine(config=engine_config)

        # Add simulated venue with fee model
        engine.add_venue(
            venue=POLYMARKET_VENUE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(self._config.starting_capital, USD)],
            fee_model=MakerTakerFeeModel(),
        )

        # Load instruments from catalog, overriding fees from config
        fee_rate = Decimal(str(self._config.fee_rate_bps / 10_000))
        raw_instruments = catalog.instruments()
        if self._config.markets:
            raw_instruments = [
                inst for inst in raw_instruments
                if any(m in str(inst.id) for m in self._config.markets)
            ]

        instruments = []
        for inst in raw_instruments:
            # Rebuild with correct fee rate (catalog stores maker_fee=0)
            rebuilt = BinaryOption(
                instrument_id=inst.id,
                raw_symbol=inst.raw_symbol,
                asset_class=inst.asset_class,
                currency=inst.quote_currency,
                price_precision=inst.price_precision,
                size_precision=inst.size_precision,
                price_increment=inst.price_increment,
                size_increment=inst.size_increment,
                activation_ns=inst.activation_ns,
                expiration_ns=inst.expiration_ns,
                ts_event=inst.ts_event,
                ts_init=inst.ts_init,
                maker_fee=fee_rate,
                taker_fee=fee_rate,
                outcome=inst.outcome,
            )
            instruments.append(rebuilt)
            engine.add_instrument(rebuilt)

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

        # Configure strategy — use the strategy's own config class if it
        # declares one via __init__ type hints, so subclass-specific fields
        # (e.g. FairValueMRConfig.exit_threshold) are available.
        init_sig = inspect.signature(strategy_class.__init__)
        config_param = init_sig.parameters.get("config")
        if config_param and config_param.annotation is not inspect.Parameter.empty:
            config_cls = config_param.annotation
        else:
            config_cls = PredictionMarketStrategyConfig

        base_kwargs = dict(
            instrument_ids=instrument_id_strs,
            fee_rate_bps=self._config.fee_rate_bps,
            sizer_mode=self._config.position_sizer,
        )
        base_kwargs.update(self._config.strategy_params)
        strategy_config = config_cls(**base_kwargs)
        strategy = strategy_class(config=strategy_config)
        engine.add_strategy(strategy)

        # Run
        engine.run()

        # Collect results + MLflow tracking
        fills_report = engine.trader.generate_fills_report()
        positions_report = engine.trader.generate_positions_report()
        account_report = engine.trader.generate_account_report(POLYMARKET_VENUE)

        summary = self._build_summary(fills_report, positions_report, account_report)
        print_report(summary)

        self._log_to_mlflow(summary, fills_report, positions_report, account_report)

        engine.dispose()
        return summary

    def _build_summary(self, fills_report, positions_report, account_report) -> BacktestSummary:
        total_trades = len(fills_report) if fills_report is not None and not fills_report.empty else 0

        final_equity = self._config.starting_capital
        total_fees = 0.0

        try:
            if account_report is not None and not account_report.empty:
                last_row = account_report.iloc[-1]
                balance = last_row.get("total", self._config.starting_capital)
                final_equity = float(balance)

            if fills_report is not None and "commission" in fills_report.columns:
                # commission may be "5.01 USD" strings — extract numeric part
                total_fees = float(fills_report["commission"].apply(
                    lambda x: float(str(x).split()[0]) if str(x).strip() else 0.0
                ).sum())
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
                    # realized_pnl may be "810.47 USD" strings — extract numeric part
                    pnl = positions_report["realized_pnl"].apply(
                        lambda x: float(str(x).split()[0]) if str(x).strip() else 0.0
                    )
                    closed = pnl[pnl != 0]
                    if len(closed) > 0:
                        wins = (closed > 0).sum()
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

    def _log_to_mlflow(self, summary: BacktestSummary, fills_report, positions_report, account_report):
        """Log backtest run to MLflow: metrics, params, and trade artifacts."""
        mlflow.set_tracking_uri(f"sqlite:///{Path(self._config.catalog_path).parent / 'mlflow.db'}")
        mlflow.set_experiment("polymarket-backtests")

        with mlflow.start_run(run_name=f"{summary.strategy_name}_{summary.start}_{summary.end}"):
            # Log config as params
            mlflow.log_params({
                "strategy": summary.strategy_name,
                "start": summary.start,
                "end": summary.end,
                "starting_capital": self._config.starting_capital,
                "data_mode": self._config.data_mode,
                "fee_rate_bps": self._config.fee_rate_bps,
                "position_sizer": self._config.position_sizer,
            })
            if self._config.strategy_params:
                mlflow.log_params({
                    f"strategy.{k}": v for k, v in self._config.strategy_params.items()
                })

            # Log metrics
            mlflow.log_metrics({
                "final_equity": summary.final_equity,
                "total_return_pct": summary.total_return_pct,
                "sharpe_ratio": summary.sharpe_ratio,
                "max_drawdown_pct": summary.max_drawdown_pct,
                "win_rate": summary.win_rate,
                "total_trades": summary.total_trades,
                "total_fees": summary.total_fees,
                "fee_drag_pct": summary.fee_drag_pct,
            })

            # Save fills, positions, account as parquet artifacts
            # Drop columns that pyarrow can't serialize (empty structs, etc.)
            drop_cols = {"info", "margins"}
            with tempfile.TemporaryDirectory() as tmpdir:
                artifacts_dir = Path(tmpdir)
                if fills_report is not None and not fills_report.empty:
                    fills_path = artifacts_dir / "fills.parquet"
                    fills_report.drop(columns=drop_cols & set(fills_report.columns), errors="ignore").to_parquet(fills_path)
                    mlflow.log_artifact(str(fills_path))

                if positions_report is not None and not positions_report.empty:
                    positions_path = artifacts_dir / "positions.parquet"
                    positions_report.drop(columns=drop_cols & set(positions_report.columns), errors="ignore").to_parquet(positions_path)
                    mlflow.log_artifact(str(positions_path))

                if account_report is not None and not account_report.empty:
                    account_path = artifacts_dir / "account_history.parquet"
                    account_report.drop(columns=drop_cols & set(account_report.columns), errors="ignore").to_parquet(account_path)
                    mlflow.log_artifact(str(account_path))
