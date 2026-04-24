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


def _parse_money(raw) -> float:
    """Parse a commission/fee value that may be a float or a string like '5.01 USD'."""
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return 0.0
    return float(s.split()[0])


class BacktestRunner:
    """Orchestrates a backtest run: data -> engine -> result."""

    def __init__(self, config: BacktestConfig):
        self._config = config

    def run(self, strategy_class: Type[PredictionMarketStrategy]) -> BacktestResult:
        if self._config.data_mode != "trade":
            raise NotImplementedError(
                f"data_mode={self._config.data_mode!r} is not yet supported; "
                "only 'trade' is wired up in the runner currently"
            )
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
        """Pull scalar perf stats from Nautilus's backtest result object.

        We grab the full dict; reporting.metrics decides which keys to surface.
        engine.get_result() exposes stats_pnls and stats_returns as dicts.
        """
        try:
            engine_result = engine.get_result()
        except Exception as e:
            raise RuntimeError(
                f"failed to get engine result for analyzer stats: {e}"
            ) from e

        stats = {}
        pnls = engine_result.stats_pnls or {}
        stats.update(pnls)
        returns = engine_result.stats_returns or {}
        stats.update(returns)
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

        f = fills.copy().sort_values("ts_event")
        trades_rows = []

        for inst_id, group in f.groupby("instrument_id"):
            position = 0.0
            entry_price = None
            entry_ts = None
            entry_fees = 0.0
            entry_client_oid = None
            entry_size_abs = 0.0

            for _, fill in group.iterrows():
                side = _parse_order_side(fill["order_side"])
                qty = float(fill["last_qty"])
                px = float(fill["last_px"])
                fee = _parse_money(fill.get("commission"))
                ts = _to_datetime_utc(fill["ts_event"])
                client_oid = fill.get("client_order_id")

                signed = qty if side == "BUY" else -qty
                new_position = position + signed

                if abs(position) < 1e-9 and not abs(new_position) < 1e-9:
                    entry_price = px
                    entry_ts = ts
                    entry_fees = fee
                    entry_client_oid = client_oid
                    entry_size_abs = abs(signed)
                elif not abs(position) < 1e-9 and (position > 0) != (new_position > 0) and not abs(new_position) < 1e-9:
                    # Flip: position crosses zero to the other side
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
                elif abs(new_position) < 1e-9 and not abs(position) < 1e-9:
                    # Full close: position returns to zero
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
                    if (position > 0) == (signed > 0):
                        # Same-direction add — weight-avg entry price
                        entry_fees += fee
                        total = entry_size_abs + abs(signed)
                        entry_price = (
                            (entry_price * entry_size_abs + px * abs(signed)) / total
                        )
                        entry_size_abs = total
                    else:
                        # Partial reduction (not a full close, not a flip) — emit a partial Trade
                        reduced_size = abs(signed)
                        trades_rows.append(self._build_trade_row(
                            inst_id, position > 0, entry_ts, ts,
                            entry_price, px, reduced_size,
                            entry_fees + fee, entry_client_oid, signals_df,
                        ))
                        entry_size_abs -= reduced_size
                        entry_fees = 0.0
                        # entry_price, entry_ts, entry_client_oid stay unchanged

                position = new_position

            if not abs(position) < 1e-9 and entry_ts is not None:
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
