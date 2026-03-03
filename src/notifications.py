"""
Telegram notification module.

Sends trade alerts and status updates to a Telegram chat.
"""

import logging
from typing import Optional, Dict, Any

import requests

from config.settings import get_config

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends messages to Telegram via Bot API."""

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        config = get_config()
        self.bot_token = bot_token or config.telegram_bot_token
        self.chat_id = chat_id or config.telegram_chat_id
        self.enabled = bool(self.bot_token and self.chat_id)
        if not self.enabled:
            logger.info("Telegram notifications disabled (no TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)")

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message. Returns True on success."""
        if not self.enabled:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            resp = requests.post(url, json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }, timeout=10)
            if not resp.json().get("ok"):
                logger.warning(f"Telegram send failed: {resp.text}")
                return False
            return True
        except Exception as e:
            logger.warning(f"Telegram send error: {e}")
            return False

    def notify_trade(self, trade_record: Dict[str, Any]):
        """Send a formatted trade alert."""
        status = trade_record.get("status", "unknown").upper()
        outcome = trade_record.get("outcome", "?")
        price = trade_record.get("price", 0)
        size = trade_record.get("size", 0)
        edge = trade_record.get("edge", 0)
        market = trade_record.get("market", "Unknown")
        order_id = trade_record.get("order_id", "—")
        error = trade_record.get("error", "")

        if status == "LIVE":
            icon = "\u2705"  # green check
        elif status == "DRY_RUN":
            icon = "\U0001F9EA"  # test tube
        else:
            icon = "\u274C"  # red X

        msg = (
            f"{icon} <b>Trade {status}</b>\n"
            f"\n"
            f"<b>Market:</b> {_escape(market[:80])}\n"
            f"<b>Action:</b> BUY {outcome} @ {price:.4f}\n"
            f"<b>Size:</b> {size:.1f} shares\n"
            f"<b>Edge:</b> {edge:.2%}\n"
            f"<b>Order ID:</b> <code>{order_id}</code>"
        )
        if error:
            msg += f"\n<b>Error:</b> {_escape(error)}"

        self.send(msg)

    def notify_startup(self, mode: str, dry_run: bool):
        """Send startup notification."""
        self.send(
            f"\U0001F680 <b>Bot Started</b>\n"
            f"Mode: {mode}\n"
            f"Trading: {'DRY RUN' if dry_run else 'LIVE'}"
        )

    def notify_error(self, error: str):
        """Send error notification."""
        self.send(f"\u26A0\uFE0F <b>Error</b>\n<code>{_escape(error[:500])}</code>")

    def notify_mm_status(
        self,
        active_markets: int,
        active_quotes: int,
        total_exposure: float,
        realized_pnl: float,
        fills_since_last: int = 0,
    ) -> bool:
        """Send periodic market-maker status update."""
        msg = (
            f"<b>MM Status</b>\n"
            f"Markets: {active_markets} | Quotes: {active_quotes}\n"
            f"Exposure: ${total_exposure:,.0f}\n"
            f"Realized PnL: ${realized_pnl:+,.2f}\n"
            f"Fills: {fills_since_last}"
        )
        return self.send(msg)


def _escape(text: str) -> str:
    """Escape HTML special chars for Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Singleton
_notifier: Optional[TelegramNotifier] = None


def get_notifier() -> TelegramNotifier:
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier
