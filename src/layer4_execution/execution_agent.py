import json
import logging
import os
from typing import Dict, Any

from config.settings import get_config
from src.layer0_ingestion.polymarket_clob import PolymarketClient
from src.layer4_execution.trading import TradingClient
from src.utils import round_size, round_price, is_valid_price, now_timestamp

logger = logging.getLogger(__name__)

TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "logs", "trades.jsonl")


def _append_trade_log(record: dict):
    """Append a trade record to the JSONL log file."""
    try:
        os.makedirs(os.path.dirname(TRADE_LOG_PATH), exist_ok=True)
        with open(TRADE_LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        logger.warning(f"    Failed to write trade log: {e}")


class ExecutionAgent:
    """
    Layer 4: Execution
    Takes an approved signal and executes the trade on the Polymarket CLOB.
    """
    def __init__(self, pm_client: PolymarketClient):
        self.pm_client = pm_client
        self.config = get_config()
        self.trading_client = None
        if pm_client.is_authenticated:
            self.trading_client = TradingClient(pm_client.clob)

    def execute_trade(self, signal: Dict[str, Any]):
        """
        Given a validated signal, place the optimal order.
        """
        condition_id = signal.get("condition_id")
        resolved_price = signal.get("resolved_price")
        uma_outcome = signal.get("uma_outcome", "?")
        pm_price = signal.get("pm_price", 0)
        edge = signal.get("edge", 0)
        market_question = signal.get("market_question", "Unknown")
        token_ids = signal.get("token_ids", [])

        logger.info(f"    Execution [{signal.get('signal_type', 'UNKNOWN')}]")
        logger.info(f"      Market:   \"{market_question}\"")
        logger.info(f"      Action:   BUY {uma_outcome} @ {pm_price:.4f}")
        logger.info(f"      Edge:     {edge:.2%}")
        logger.info(f"      Cond ID:  {condition_id}")

        # Determine winning token ID
        token_idx = 0 if uma_outcome == "YES" else 1
        if not token_ids or len(token_ids) <= token_idx:
            logger.error(f"      No token ID for {uma_outcome} outcome")
            return

        winning_token_id = token_ids[token_idx]
        buy_price = round_price(pm_price)

        # Calculate order size: max_order_size USDC / price = shares
        if buy_price <= 0:
            logger.error(f"      Invalid buy price: {buy_price}")
            return

        raw_size = self.config.max_order_size / buy_price
        size = round_size(raw_size)

        logger.info(f"      Size:     {size:.1f} shares (${self.config.max_order_size:.2f} / {buy_price:.4f})")

        if size <= 0:
            logger.warning(f"      Order size too small after rounding")
            return

        if not is_valid_price(buy_price):
            logger.error(f"      Price {buy_price} outside valid range [0.01, 0.99]")
            return

        # Build trade log record
        trade_record = {
            "timestamp": now_timestamp(),
            "signal_type": signal.get("signal_type"),
            "condition_id": condition_id,
            "market": market_question,
            "outcome": uma_outcome,
            "token_id": winning_token_id[:32],
            "price": buy_price,
            "size": size,
            "edge": round(edge, 4),
            "max_order_size": self.config.max_order_size,
        }

        if not self.pm_client.is_authenticated or not self.trading_client:
            logger.info(f"      DRY RUN — no order placed (not authenticated)")
            trade_record["status"] = "dry_run"
            trade_record["reason"] = "not_authenticated"
            _append_trade_log(trade_record)
            return

        # Place order via TradingClient (respects config.dry_run internally)
        result = self.trading_client.place_limit_order(
            token_id=winning_token_id,
            side="BUY",
            price=buy_price,
            size=size,
        )

        if result.success:
            is_dry = result.data and result.data.get("dry_run", False)
            status = "dry_run" if is_dry else "live"
            logger.info(f"      ORDER {status.upper()}: id={result.order_id}")
            trade_record["status"] = status
            trade_record["order_id"] = result.order_id
        else:
            logger.error(f"      ORDER FAILED: {result.error}")
            trade_record["status"] = "failed"
            trade_record["error"] = result.error

        _append_trade_log(trade_record)
