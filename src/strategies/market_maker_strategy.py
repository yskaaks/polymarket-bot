"""
Market-Making Strategy Orchestrator

Main loop: scan markets → estimate fair value → manage quotes → track fills.
Runs alongside UMA arb as a separate entry point.
"""

import logging
import sys
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

logger = logging.getLogger(__name__)


class MarketMakerStrategy:
    """
    Market-making strategy orchestrator.

    Main loop (every mm_quote_refresh seconds):
    1. Refresh market universe (every 5 min via MarketSelector.scan())
    2. Per active market: fetch book → estimate FV → compute skew → check risk → requote
    3. Detect fills → update inventory → log + notify
    4. Enforce risk limits (circuit breaker check)
    """

    UNIVERSE_REFRESH_INTERVAL: float = 300.0  # 5 minutes

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

        # State
        self._candidates: list[MarketCandidate] = []
        self._last_universe_refresh: float = 0.0
        self._cycle_count: int = 0

    def run_loop(self, poll_interval: float = 0.0) -> None:
        """
        Main polling loop.

        Args:
            poll_interval: Override quote refresh interval (0 = use config)
        """
        interval = poll_interval or self.config.mm_quote_refresh

        logger.info("=" * 60)
        logger.info("Market Maker Strategy")
        logger.info(f"  Mode:            {'DRY RUN' if self.config.dry_run else 'LIVE'}")
        logger.info(f"  Poll interval:   {interval}s")
        logger.info(f"  Max markets:     {self.config.mm_max_markets}")
        logger.info(f"  Max pos/market:  ${self.config.mm_max_position_per_market:.0f}")
        logger.info(f"  Max exposure:    ${self.config.mm_max_total_exposure:.0f}")
        logger.info(f"  Spread range:    {self.config.mm_min_spread:.0%}-{self.config.mm_max_spread:.0%}")
        logger.info(f"  Capital:         ${self.config.mm_capital:,.0f}")
        logger.info(f"  Stop-loss/mkt:   ${self.config.mm_stop_loss_per_market:.0f}")
        logger.info("=" * 60)

        self.notifier.send(
            f"<b>Market Maker Started</b>\n"
            f"Mode: {'DRY RUN' if self.config.dry_run else 'LIVE'}\n"
            f"Markets: {self.config.mm_max_markets} | "
            f"Capital: ${self.config.mm_capital:,.0f}"
        )

        try:
            while True:
                self._cycle_count += 1
                self._run_cycle()
                time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("Shutting down Market Maker...")
            self._shutdown()
        except Exception as e:
            logger.error(f"Market Maker fatal error: {e}", exc_info=True)
            self.notifier.notify_error(f"Market Maker crashed: {e}")
            self._shutdown()
            raise

    def _run_cycle(self) -> None:
        """Execute one cycle of the market-making loop."""
        now = time.time()

        # 1. Refresh market universe periodically
        if now - self._last_universe_refresh > self.UNIVERSE_REFRESH_INTERVAL:
            self._refresh_universe()
            self._last_universe_refresh = now

        if not self._candidates:
            if self._cycle_count % 30 == 1:  # log every ~5 min at 10s interval
                logger.info("No market candidates available, waiting...")
            return

        # 2. Per active market: fetch book → estimate FV → manage quotes
        for candidate in self._candidates:
            self._process_market(candidate)

        # 3. Detect fills and update inventory
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

        # 4. Periodic status log
        if self._cycle_count % 30 == 0:
            self._log_status()

    def _refresh_universe(self) -> None:
        """Scan for new market candidates."""
        try:
            self._candidates = self.selector.scan()

            # Cancel quotes for markets no longer in candidates
            candidate_tokens = {c.token_id for c in self._candidates}
            for token_id in self.quotes.active_tokens:
                if token_id not in candidate_tokens:
                    logger.info(f"Market {token_id[:8]}... dropped from universe, cancelling quote")
                    self.quotes.cancel_quote(token_id)

        except Exception as e:
            logger.error(f"Universe refresh failed: {e}", exc_info=True)

    def _process_market(self, candidate: MarketCandidate) -> None:
        """Process a single market: estimate FV, check risk, manage quote."""
        token_id = candidate.token_id
        if not token_id:
            return

        try:
            # Fetch fresh orderbook
            orderbook = self.analyzer.get_orderbook(token_id)
            if orderbook is None or orderbook.midpoint is None:
                return

            # Get last trade price for VWAP blend
            last_trade = self.trading.get_last_trade_price(token_id)

            # Estimate fair value
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

            # Compute inventory skew
            skew = self.inventory.get_quote_skew(token_id)

            # Compute order size
            book_depth = orderbook.total_bid_depth(5) + orderbook.total_ask_depth(5)
            size = self.risk.compute_order_size(
                token_id=token_id,
                fair_value=estimate.fair_value,
                spread=estimate.spread,
                book_depth=book_depth,
            )

            if size <= 0:
                return

            # Place/update quote
            self.quotes.place_quote(
                token_id=token_id,
                fair_value=estimate.fair_value,
                spread=estimate.spread,
                size=size,
                inventory_skew=skew,
            )

        except Exception as e:
            logger.error(f"Error processing market {token_id[:8]}...: {e}", exc_info=True)

    def _log_status(self) -> None:
        """Log periodic status summary."""
        total_exposure = self.inventory.get_total_exposure()
        total_pnl = sum(
            self.inventory.get_position(tid).realized_pnl
            for tid in self.inventory._positions
        )
        active_quotes = self.quotes.num_active_quotes

        logger.info(
            f"[Status] Cycle #{self._cycle_count} | "
            f"Markets: {len(self._candidates)} | "
            f"Active quotes: {active_quotes} | "
            f"Exposure: ${total_exposure:.0f} | "
            f"Realized PnL: ${total_pnl:.2f}"
        )

    def _shutdown(self) -> None:
        """Graceful shutdown: cancel all quotes."""
        logger.info("Cancelling all quotes...")
        self.quotes.cancel_all_quotes()
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
    strategy.run_loop()
