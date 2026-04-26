"""Base class for prediction market backtesting strategies."""
from typing import Optional

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, TradeTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import BinaryOption
from nautilus_trader.trading.strategy import Strategy

from src.layer1_research.backtesting.data.instruments import get_paired_token_id
from src.layer1_research.backtesting.execution.fees import polymarket_fee
from src.layer1_research.backtesting.execution.sizer import fixed_fractional_size, kelly_size
from src.layer1_research.backtesting.strategies.signal import Signal


class PredictionMarketStrategyConfig(StrategyConfig, frozen=True):
    """Configuration for PredictionMarketStrategy."""
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
        self._instrument_map: dict[InstrumentId, BinaryOption] = {}
        # Captured per emitted signal; pulled out by the runner post-run.
        self._signal_log: list = []

    def generate_signal(self, instrument: BinaryOption, data) -> Optional[Signal]:
        """Generate a trading signal from incoming market data.

        Returns a Signal to act on, or None to do nothing.
        Subclasses must override this method.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement generate_signal()"
        )

    def on_start(self):
        """Subscribe to instruments on strategy start."""
        for inst_id_str in self.config.instrument_ids:
            instrument_id = InstrumentId.from_str(inst_id_str)
            instrument = self.cache.instrument(instrument_id)
            if instrument:
                self._instrument_map[instrument_id] = instrument
                self.subscribe_trade_ticks(instrument_id)

    def on_trade_tick(self, tick: TradeTick):
        instrument = self._instrument_map.get(tick.instrument_id)
        if not instrument:
            return
        signal = self.generate_signal(instrument, tick)
        if signal:
            self._act_on_signal(signal, instrument, tick)

    def on_bar(self, bar: Bar):
        instrument = self._instrument_map.get(bar.bar_type.instrument_id)
        if not instrument:
            return
        signal = self.generate_signal(instrument, bar)
        if signal:
            self._act_on_signal(signal, instrument, bar)

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
            balance = account.balance_total(instrument.quote_currency)
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

    def _close_position(self, instrument: BinaryOption):
        positions = self.cache.positions(instrument_id=instrument.id)
        for position in positions:
            if position.is_open:
                self.close_position(position)

    def get_fee(self, price: float) -> float:
        return polymarket_fee(price, self.config.fee_rate_bps)
