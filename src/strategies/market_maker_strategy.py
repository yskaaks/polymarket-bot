"""
Market-Making Strategy Orchestrator

Primary mode: WebSocket-driven requoting (sub-second reaction to book changes).
Fallback: slow poll (30s) for fill detection and universe refresh.
Runs alongside UMA arb as a separate entry point.
"""

import asyncio
import logging
import sys
import threading
import time

from config.settings import get_config
from src.layer0_ingestion.polymarket_clob import PolymarketClient
from src.layer0_ingestion.polymarket_gamma import MarketFetcher
from src.layer2_signals.fair_value import FairValueEngine
from src.layer2_signals.market_selector import MarketSelector, MarketCandidate
from src.layer3_portfolio.mm_risk_manager import (
    InventoryTracker, MMRiskManager, Fill,
)
from src.layer4_execution.quote_manager import QuoteManager
from src.layer4_execution.trading import TradingClient
from src.notifications import get_notifier
from src.orderbook import OrderbookAnalyzer
from src.utils import logit, logit_adjust
from src.websocket_feed import WebSocketFeed, PriceUpdate, TradeUpdate, OrderbookUpdate

logger = logging.getLogger(__name__)


class MarketMakerStrategy:
    """
    Market-making strategy orchestrator.

    Two execution modes:
    1. WebSocket-driven (primary): subscribes to book/trade events for active
       markets. On any book change, immediately re-estimates FV and requotes
       if needed. Sub-second reaction time.
    2. Polling fallback (30s): fill detection, universe refresh, status logging.
       NOT used for quoting decisions.
    """

    UNIVERSE_REFRESH_INTERVAL: float = 300.0  # 5 minutes
    FILL_DETECT_INTERVAL: float = 15.0  # check fills every 15s
    STATUS_LOG_INTERVAL: float = 300.0  # log status every 5 min

    def __init__(self, pm_client: PolymarketClient):
        self.config = get_config()
        self.pm_client = pm_client

        # Components
        self.fetcher = MarketFetcher()
        self.analyzer = OrderbookAnalyzer(pm_client.clob)
        self.trading = TradingClient(pm_client.clob)
        self.fv_engine = FairValueEngine()
        self.selector = MarketSelector(self.fetcher, self.analyzer)
        self.inventory = InventoryTracker()
        self.risk = MMRiskManager(self.inventory)
        self.quotes = QuoteManager(self.trading)
        self.notifier = get_notifier()

        # WebSocket feed
        self._ws_feed = WebSocketFeed()
        self._ws_feed.on_price(self._on_price_update)
        self._ws_feed.on_trade(self._on_trade_update)
        self._ws_feed.on_orderbook(self._on_book_update)

        # State
        self._candidates: list[MarketCandidate] = []
        self._candidate_by_token: dict[str, MarketCandidate] = {}
        self._subscribed_tokens: set[str] = set()
        self._last_universe_refresh: float = 0.0
        self._last_fill_detect: float = 0.0
        self._last_status_log: float = 0.0
        self._requote_lock = threading.Lock()

    # ── WebSocket callbacks (called from WS thread, must be fast) ───────

    def _on_price_update(self, update: PriceUpdate) -> None:
        """React to price change — requote if the market moved."""
        if update.token_id in self._candidate_by_token:
            self._try_requote(update.token_id)

    def _on_trade_update(self, update: TradeUpdate) -> None:
        """React to trade — someone hit the book, re-evaluate."""
        if update.token_id in self._candidate_by_token:
            self._try_requote(update.token_id)

    def _on_book_update(self, update: OrderbookUpdate) -> None:
        """React to book change — primary requote trigger."""
        if update.token_id in self._candidate_by_token:
            self._try_requote(update.token_id)

    def _try_requote(self, token_id: str) -> None:
        """Thread-safe requote attempt for a single token."""
        with self._requote_lock:
            try:
                self._process_market_by_token(token_id)
            except Exception as e:
                logger.error(f"Requote error for {token_id[:8]}...: {e}", exc_info=True)

    # ── Main entry points ───────────────────────────────────────────────

    def run(self, poll_interval: float = 0.0) -> None:
        """
        Start the market maker with WebSocket-driven requoting + polling fallback.

        WebSocket handles real-time book changes → immediate requoting.
        Polling handles: universe refresh, fill detection, status logging.
        """
        interval = poll_interval or self.FILL_DETECT_INTERVAL

        self._print_banner()
        self.notifier.send(
            f"<b>Market Maker Started</b>\n"
            f"Mode: {'DRY RUN' if self.config.dry_run else 'LIVE'} (WebSocket-driven)\n"
            f"Markets: {self.config.mm_max_markets} | "
            f"Capital: ${self.config.mm_capital:,.0f}"
        )

        # Initial universe scan (before WS starts)
        self._refresh_universe()

        # Start WebSocket in background thread
        ws_thread = self._start_ws_feed()

        # Polling fallback loop
        try:
            while True:
                now = time.time()

                # Universe refresh (every 5 min)
                if now - self._last_universe_refresh > self.UNIVERSE_REFRESH_INTERVAL:
                    self._refresh_universe()

                # Fill detection (every 15s via REST — no WS for private data)
                if now - self._last_fill_detect > self.FILL_DETECT_INTERVAL:
                    self._detect_and_record_fills()
                    self._last_fill_detect = now

                # Status log
                if now - self._last_status_log > self.STATUS_LOG_INTERVAL:
                    self._log_status()
                    self._last_status_log = now

                time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("Shutting down Market Maker...")
            self._shutdown()
        except Exception as e:
            logger.error(f"Market Maker fatal error: {e}", exc_info=True)
            self.notifier.notify_error(f"Market Maker crashed: {e}")
            self._shutdown()
            raise

    def run_poll_only(self, poll_interval: float = 0.0) -> None:
        """
        Fallback polling-only mode (no WebSocket).
        Use if WebSocket connection is unreliable.
        """
        interval = poll_interval or self.config.mm_quote_refresh

        self._print_banner()
        logger.warning("Running in POLL-ONLY mode (no WebSocket)")

        try:
            while True:
                now = time.time()

                if now - self._last_universe_refresh > self.UNIVERSE_REFRESH_INTERVAL:
                    self._refresh_universe()

                # In poll mode, process all markets every cycle
                with self._requote_lock:
                    for candidate in self._candidates:
                        token_id = candidate.token_id
                        if token_id:
                            self._process_market_by_token(token_id)

                if now - self._last_fill_detect > self.FILL_DETECT_INTERVAL:
                    self._detect_and_record_fills()
                    self._last_fill_detect = now

                if now - self._last_status_log > self.STATUS_LOG_INTERVAL:
                    self._log_status()
                    self._last_status_log = now

                time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("Shutting down Market Maker...")
            self._shutdown()
        except Exception as e:
            logger.error(f"Market Maker fatal error: {e}", exc_info=True)
            self.notifier.notify_error(f"Market Maker crashed: {e}")
            self._shutdown()
            raise

    # ── Core logic ──────────────────────────────────────────────────────

    def _process_market_by_token(self, token_id: str) -> None:
        """Process a single market: fetch book → estimate FV → check risk → requote."""
        candidate = self._candidate_by_token.get(token_id)
        if not candidate:
            return

        # Fetch fresh orderbook via REST (authoritative snapshot)
        orderbook = self.analyzer.get_orderbook(token_id)
        if orderbook is None or orderbook.midpoint is None:
            return

        # Get last trade price for VWAP blend
        last_trade = self.trading.get_last_trade_price(token_id)

        # Estimate fair value (all math in logit space)
        estimate = self.fv_engine.estimate(token_id, orderbook, last_trade)
        if estimate is None:
            return

        # Check risk
        risk_check = self.risk.should_quote(token_id)
        if not risk_check.allowed:
            logger.debug(f"Risk blocked {token_id[:8]}...: {risk_check.reason}")
            self.quotes.cancel_quote(token_id)
            return

        # Check if requote needed
        if not self.quotes.needs_requote(token_id, estimate.fair_value):
            return

        # Apply inventory skew in logit space
        skew_logit = self.inventory.get_quote_skew(token_id)
        if skew_logit != 0:
            skewed_fv = logit_adjust(estimate.fair_value, skew_logit)
            # Recompute bid/ask around skewed FV with same logit half-width
            fv_logit = logit(estimate.fair_value)
            half_width = (logit(estimate.ask_price) - logit(estimate.bid_price)) / 2
            from src.utils import logit_spread
            bid_price, ask_price = logit_spread(skewed_fv, half_width)
        else:
            bid_price = estimate.bid_price
            ask_price = estimate.ask_price

        # Compute order size
        book_depth = orderbook.total_bid_depth(5) + orderbook.total_ask_depth(5)
        size = self.risk.compute_order_size(
            token_id=token_id,
            fair_value=estimate.fair_value,
            spread=ask_price - bid_price,
            book_depth=book_depth,
        )

        if size <= 0:
            return

        # Place/update quote
        self.quotes.place_quote(
            token_id=token_id,
            fair_value=estimate.fair_value,
            bid_price=bid_price,
            ask_price=ask_price,
            size=size,
        )

    def _refresh_universe(self) -> None:
        """Scan for new market candidates and update WS subscriptions."""
        try:
            old_tokens = set(self._candidate_by_token.keys())
            self._candidates = self.selector.scan()
            self._candidate_by_token = {
                c.token_id: c for c in self._candidates if c.token_id
            }
            new_tokens = set(self._candidate_by_token.keys())

            # Cancel quotes for dropped markets
            for token_id in old_tokens - new_tokens:
                logger.info(f"Market {token_id[:8]}... dropped from universe, cancelling quote")
                self.quotes.cancel_quote(token_id)

            # Update WS subscriptions
            self._update_ws_subscriptions(new_tokens)

            self._last_universe_refresh = time.time()

        except Exception as e:
            logger.error(f"Universe refresh failed: {e}", exc_info=True)

    def _detect_and_record_fills(self) -> None:
        """Detect fills via REST polling and update inventory."""
        fills = self.quotes.detect_fills()
        for fill_event in fills:
            fill = Fill(
                token_id=fill_event.token_id,
                side=fill_event.side,
                price=fill_event.price,
                size=fill_event.size,
                timestamp=fill_event.timestamp,
                order_id=fill_event.order_id,
            )
            self.inventory.record_fill(fill)

            self.notifier.send(
                f"<b>MM Fill</b>: {fill.side} {fill.size:.1f} @ {fill.price:.4f}\n"
                f"Token: <code>{fill.token_id[:12]}...</code>"
            )

    # ── WebSocket management ────────────────────────────────────────────

    def _start_ws_feed(self) -> threading.Thread:
        """Start WebSocket feed in a background daemon thread."""
        token_ids = list(self._candidate_by_token.keys())

        def run_ws():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._ws_feed.run(token_ids))
            except Exception as e:
                logger.error(f"WebSocket feed crashed: {e}", exc_info=True)

        thread = threading.Thread(target=run_ws, daemon=True, name="mm-ws-feed")
        thread.start()
        self._subscribed_tokens = set(token_ids)
        logger.info(f"WebSocket feed started, subscribed to {len(token_ids)} tokens")
        return thread

    def _update_ws_subscriptions(self, new_tokens: set[str]) -> None:
        """Update WebSocket subscriptions when universe changes."""
        to_subscribe = new_tokens - self._subscribed_tokens
        to_unsubscribe = self._subscribed_tokens - new_tokens

        if not to_subscribe and not to_unsubscribe:
            return

        # Schedule subscription changes on the WS event loop
        ws = self._ws_feed
        if ws._ws is None:
            # WS not connected yet, will subscribe on next reconnect
            self._subscribed_tokens = new_tokens
            return

        loop = asyncio.new_event_loop()

        async def update():
            for tid in to_unsubscribe:
                try:
                    await ws.unsubscribe_market(tid)
                except Exception as e:
                    logger.warning(f"Failed to unsubscribe {tid[:8]}...: {e}")
            for tid in to_subscribe:
                try:
                    await ws.subscribe_market(tid)
                except Exception as e:
                    logger.warning(f"Failed to subscribe {tid[:8]}...: {e}")

        try:
            # Run in a new thread to avoid blocking
            def run_update():
                _loop = asyncio.new_event_loop()
                asyncio.set_event_loop(_loop)
                _loop.run_until_complete(update())
                _loop.close()

            t = threading.Thread(target=run_update, daemon=True)
            t.start()
            t.join(timeout=5.0)
        except Exception as e:
            logger.warning(f"WS subscription update failed: {e}")

        self._subscribed_tokens = new_tokens
        logger.info(
            f"WS subscriptions updated: +{len(to_subscribe)} -{len(to_unsubscribe)} "
            f"= {len(new_tokens)} total"
        )

    # ── Logging / lifecycle ─────────────────────────────────────────────

    def _print_banner(self) -> None:
        logger.info("=" * 60)
        logger.info("Market Maker Strategy")
        logger.info(f"  Mode:            {'DRY RUN' if self.config.dry_run else 'LIVE'}")
        logger.info(f"  Quoting:         WebSocket-driven (sub-second)")
        logger.info(f"  Fill detect:     REST poll every {self.FILL_DETECT_INTERVAL}s")
        logger.info(f"  Max markets:     {self.config.mm_max_markets}")
        logger.info(f"  Max pos/market:  ${self.config.mm_max_position_per_market:.0f}")
        logger.info(f"  Max exposure:    ${self.config.mm_max_total_exposure:.0f}")
        logger.info(f"  Spread range:    {self.config.mm_min_spread:.0%}-{self.config.mm_max_spread:.0%}")
        logger.info(f"  Capital:         ${self.config.mm_capital:,.0f}")
        logger.info(f"  Stop-loss/mkt:   ${self.config.mm_stop_loss_per_market:.0f}")
        logger.info("=" * 60)

    def _log_status(self) -> None:
        """Log periodic status summary."""
        total_exposure = self.inventory.get_total_exposure()
        total_pnl = sum(
            self.inventory.get_position(tid).realized_pnl
            for tid in self.inventory._positions
        )
        active_quotes = self.quotes.num_active_quotes

        logger.info(
            f"[Status] Markets: {len(self._candidates)} | "
            f"Active quotes: {active_quotes} | "
            f"WS subs: {len(self._subscribed_tokens)} | "
            f"Exposure: ${total_exposure:.0f} | "
            f"Realized PnL: ${total_pnl:.2f}"
        )

        self.notifier.notify_mm_status(
            active_markets=len(self._candidates),
            active_quotes=active_quotes,
            total_exposure=total_exposure,
            realized_pnl=total_pnl,
        )

    def _shutdown(self) -> None:
        """Graceful shutdown: cancel all quotes, close connections."""
        logger.info("Cancelling all quotes...")
        self.quotes.cancel_all_quotes()
        self._ws_feed._running = False
        self.fetcher.close()
        self.notifier.send("<b>Market Maker Stopped</b>")
        logger.info("Market Maker shutdown complete")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S',
    )

    config = get_config()

    pm_client = PolymarketClient(config)
    if not pm_client.connect():
        logger.error("Failed to connect to Polymarket CLOB. Check credentials/network.")
        sys.exit(1)

    logger.info(f"Connected to Polymarket. Authenticated: {pm_client.is_authenticated}")

    strategy = MarketMakerStrategy(pm_client=pm_client)
    strategy.run()
