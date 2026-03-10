"""
Crypto price signal provider for market making.

Subscribes to Binance WebSocket for real-time crypto prices. Uses Polymarket's
structured tags (from events API) to identify crypto price markets, with
question-text parsing as fallback. Converts spot price → probability via
log-normal model for fair value adjustment.
"""

import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx
import websockets
import asyncio

from src.layer2_signals.fair_value import SignalProvider
from src.utils import logit

logger = logging.getLogger(__name__)


# Tag slug → Binance symbol mapping (from Polymarket events API tags)
TAG_TO_SYMBOL = {
    "bitcoin": "BTCUSDT",
    "btc": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "eth": "ETHUSDT",
    "solana": "SOLUSDT",
    "sol": "SOLUSDT",
    "polygon": "MATICUSDT",
    "matic": "MATICUSDT",
    "dogecoin": "DOGEUSDT",
    "doge": "DOGEUSDT",
    "cardano": "ADAUSDT",
    "ada": "ADAUSDT",
    "xrp": "XRPUSDT",
    "ripple": "XRPUSDT",
    "avalanche": "AVAXUSDT",
    "avax": "AVAXUSDT",
    "chainlink": "LINKUSDT",
    "link": "LINKUSDT",
    "litecoin": "LTCUSDT",
    "ltc": "LTCUSDT",
    "polkadot": "DOTUSDT",
    "dot": "DOTUSDT",
    "sui": "SUIUSDT",
    "pepe": "PEPEUSDT",
    "fartcoin": None,  # not on Binance
    "memecoins": None,  # too generic
}

# Also match common names in question text (fallback when tags are missing)
QUESTION_ASSET_NAMES = {
    "bitcoin": "BTCUSDT",
    "btc": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "eth": "ETHUSDT",
    "ether": "ETHUSDT",
    "solana": "SOLUSDT",
    "sol": "SOLUSDT",
    "polygon": "MATICUSDT",
    "matic": "MATICUSDT",
    "dogecoin": "DOGEUSDT",
    "doge": "DOGEUSDT",
    "cardano": "ADAUSDT",
    "ada": "ADAUSDT",
    "xrp": "XRPUSDT",
    "ripple": "XRPUSDT",
    "avalanche": "AVAXUSDT",
    "avax": "AVAXUSDT",
    "chainlink": "LINKUSDT",
    "link": "LINKUSDT",
    "litecoin": "LTCUSDT",
    "ltc": "LTCUSDT",
    "polkadot": "DOTUSDT",
    "dot": "DOTUSDT",
    "sui": "SUIUSDT",
    "pepe": "PEPEUSDT",
}

# Price extraction regex — matches "$80,000", "$4k", "$1.5M", etc.
PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*([kKmM])?")

# Direction patterns
ABOVE_RE = re.compile(r"above|over|exceed|reach|hit|at or above|higher than", re.IGNORECASE)
BELOW_RE = re.compile(r"below|under|drop below|fall below|at or below|lower than", re.IGNORECASE)


@dataclass
class CryptoMarketMapping:
    """Maps a Polymarket token to a crypto price target."""
    token_id: str
    symbol: str  # Binance symbol, e.g. "BTCUSDT"
    strike: float  # Target price, e.g. 80000.0
    expiry: Optional[datetime]  # When the market resolves
    is_above: bool  # True = "above strike", False = "below strike"
    source: str  # "tags" or "question_parse" — how we identified this
    question: str  # Original question for debugging


class CryptoPriceSignal(SignalProvider):
    """
    Signal provider that uses real-time crypto prices from Binance
    to adjust fair value for crypto-related Polymarket markets.

    Market identification (in priority order):
    1. Structured tags from Polymarket events API (e.g., "crypto-prices", "solana")
    2. Question text parsing (fallback for markets without tags)

    Price model: log-normal (Black-Scholes d2) to convert spot → probability.
    """

    DEFAULT_VOL: float = 0.80  # annualized volatility for crypto
    SIGNAL_WEIGHT: float = 0.6  # how much to trust Binance vs orderbook
    MIN_DIVERGENCE_LOGIT: float = 0.05  # minimum divergence to emit signal

    def __init__(self):
        self._prices: dict[str, float] = {}  # symbol → latest price
        self._mappings: dict[str, CryptoMarketMapping] = {}  # token_id → mapping
        self._lock = threading.Lock()
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False

        # Seed prices from REST before WS connects
        self._seed_prices()

    @property
    def name(self) -> str:
        return "crypto_price"

    def register_market(
        self,
        token_id: str,
        question: str,
        tags: list[str] | None = None,
        description: str = "",
        expiry: Optional[datetime] = None,
    ) -> bool:
        """
        Try to identify a crypto price market and register it.

        Uses tags first (from events API), then falls back to question parsing.
        Returns True if the market was recognized as a crypto price market.
        """
        tags = tags or []

        # Strategy 1: Use structured tags
        mapping = self._identify_from_tags(token_id, question, tags, description, expiry)

        # Strategy 2: Parse question text
        if mapping is None:
            mapping = self._identify_from_question(token_id, question, expiry)

        if mapping is None:
            return False

        with self._lock:
            self._mappings[token_id] = mapping

        logger.info(
            f"Crypto signal: mapped {token_id[:8]}... → {mapping.symbol} "
            f"{'above' if mapping.is_above else 'below'} ${mapping.strike:,.0f} "
            f"(source={mapping.source}, expiry={mapping.expiry})"
        )
        return True

    def unregister_market(self, token_id: str) -> None:
        with self._lock:
            self._mappings.pop(token_id, None)

    def get_adjustment(self, token_id: str, current_fv: float) -> float:
        """Return logit-space adjustment based on external crypto price."""
        with self._lock:
            mapping = self._mappings.get(token_id)
            if mapping is None:
                return 0.0
            spot = self._prices.get(mapping.symbol)

        if spot is None or spot <= 0:
            return 0.0

        model_prob = self._compute_probability(spot, mapping)
        if model_prob is None or model_prob <= 0.01 or model_prob >= 0.99:
            return 0.0

        model_logit = logit(model_prob)
        current_logit = logit(max(0.01, min(0.99, current_fv)))
        divergence = model_logit - current_logit

        if abs(divergence) < self.MIN_DIVERGENCE_LOGIT:
            return 0.0

        adjustment = divergence * self.SIGNAL_WEIGHT

        logger.debug(
            f"Crypto signal {token_id[:8]}...: spot=${spot:,.2f} "
            f"model_p={model_prob:.4f} current_fv={current_fv:.4f} "
            f"adj_logit={adjustment:+.3f}"
        )

        return adjustment

    def get_spot_price(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self._prices.get(symbol)

    def start(self) -> None:
        """Start the Binance WebSocket price feed in a background thread."""
        if self._ws_thread is not None and self._ws_thread.is_alive():
            return
        self._running = True
        self._ws_thread = threading.Thread(
            target=self._run_ws, daemon=True, name="crypto-price-ws"
        )
        self._ws_thread.start()
        logger.info("Crypto price WebSocket feed started")

    def stop(self) -> None:
        self._running = False

    # ── Identification strategies ─────────────────────────────────────────

    def _identify_from_tags(
        self,
        token_id: str,
        question: str,
        tags: list[str],
        description: str,
        expiry: Optional[datetime],
    ) -> Optional[CryptoMarketMapping]:
        """
        Identify crypto market from Polymarket event tags.

        Tags like "crypto-prices" confirm it's a price market.
        Asset-specific tags like "solana", "bitcoin" identify the underlying.
        """
        is_price_market = any(
            t in ("crypto-prices", "crypto-price") for t in tags
        )
        # Also accept "crypto" tag if question mentions a price
        if not is_price_market and "crypto" in tags:
            if PRICE_RE.search(question):
                is_price_market = True

        if not is_price_market:
            return None

        # Find the specific asset from tags
        symbol = None
        for tag in tags:
            sym = TAG_TO_SYMBOL.get(tag)
            if sym is not None:
                symbol = sym
                break

        # Fallback: check description for Binance-style references (e.g., "Binance ETH_USDT")
        if symbol is None and description:
            for name, sym in QUESTION_ASSET_NAMES.items():
                if name in description.lower():
                    symbol = sym
                    break

        # Fallback: check question for asset name
        if symbol is None:
            q_lower = question.lower()
            for name, sym in QUESTION_ASSET_NAMES.items():
                # Match whole word to avoid false positives
                if re.search(rf"\b{re.escape(name)}\b", q_lower):
                    symbol = sym
                    break

        if symbol is None:
            return None

        # Extract strike price from question
        strike = self._extract_strike(question)
        if strike is None or strike <= 0:
            return None

        # Determine direction
        is_above = not bool(BELOW_RE.search(question))

        return CryptoMarketMapping(
            token_id=token_id,
            symbol=symbol,
            strike=strike,
            expiry=expiry,
            is_above=is_above,
            source="tags",
            question=question,
        )

    def _identify_from_question(
        self,
        token_id: str,
        question: str,
        expiry: Optional[datetime],
    ) -> Optional[CryptoMarketMapping]:
        """Fallback: parse question text for crypto price patterns."""
        q_lower = question.lower()

        # Find asset name in question
        symbol = None
        for name, sym in QUESTION_ASSET_NAMES.items():
            if re.search(rf"\b{re.escape(name)}\b", q_lower):
                symbol = sym
                break

        if symbol is None:
            return None

        # Must have a price target
        strike = self._extract_strike(question)
        if strike is None or strike <= 0:
            return None

        # Must have a direction word
        has_above = bool(ABOVE_RE.search(question))
        has_below = bool(BELOW_RE.search(question))
        if not has_above and not has_below:
            return None

        is_above = has_above and not has_below

        # Try to parse expiry from question
        parsed_expiry = expiry
        if parsed_expiry is None:
            parsed_expiry = self._parse_date_from_question(question)

        return CryptoMarketMapping(
            token_id=token_id,
            symbol=symbol,
            strike=strike,
            expiry=parsed_expiry,
            is_above=is_above,
            source="question_parse",
            question=question,
        )

    # ── Price / date extraction ───────────────────────────────────────────

    @staticmethod
    def _extract_strike(question: str) -> Optional[float]:
        """Extract the price target from a question string."""
        match = PRICE_RE.search(question)
        if not match:
            return None

        num_str = match.group(1).replace(",", "")
        suffix = (match.group(2) or "").lower()

        try:
            value = float(num_str)
        except ValueError:
            return None

        if suffix == "k":
            value *= 1_000
        elif suffix == "m":
            value *= 1_000_000

        return value

    @staticmethod
    def _parse_date_from_question(question: str) -> Optional[datetime]:
        """Try to parse expiry date from common patterns in question text."""
        # Look for date-like strings after "by", "on", "before"
        date_match = re.search(
            r"(?:by|on|before)\s+(.+?)[\?\.]?\s*$", question, re.IGNORECASE
        )
        if not date_match:
            return None

        date_str = date_match.group(1).strip().rstrip("?.")
        formats = [
            "%B %d, %Y",  # March 31, 2025
            "%B %d %Y",   # March 31 2025
            "%b %d, %Y",  # Mar 31, 2025
            "%b %d %Y",   # Mar 31 2025
            "%B %d",      # March 31 (assume current year)
            "%b %d",      # Mar 31
            "%Y-%m-%d",   # 2025-03-31
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                if dt.year < 2000:
                    dt = dt.replace(year=datetime.now().year)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    # ── Binance price feed ────────────────────────────────────────────────

    def _seed_prices(self) -> None:
        """Fetch initial prices from Binance REST API."""
        try:
            r = httpx.get("https://api.binance.com/api/v3/ticker/price", timeout=5)
            if r.status_code == 200:
                known_symbols = set(TAG_TO_SYMBOL.values()) - {None}
                for item in r.json():
                    sym = item["symbol"]
                    if sym in known_symbols:
                        self._prices[sym] = float(item["price"])
                logger.info(f"Seeded {len(self._prices)} crypto prices from Binance REST")
        except Exception as e:
            logger.warning(f"Failed to seed crypto prices: {e}")

    def _run_ws(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._ws_loop())
        except Exception as e:
            logger.error(f"Crypto WS feed crashed: {e}", exc_info=True)
        finally:
            loop.close()

    async def _ws_loop(self) -> None:
        """WebSocket connection loop with auto-reconnect."""
        known_symbols = set(TAG_TO_SYMBOL.values()) - {None}
        streams = [f"{sym.lower()}@bookTicker" for sym in known_symbols]
        stream_path = "/".join(streams)
        url = f"wss://stream.binance.com:9443/stream?streams={stream_path}"

        while self._running:
            try:
                async with websockets.connect(url) as ws:
                    logger.info(f"Connected to Binance WS ({len(known_symbols)} symbols)")
                    async for msg in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(msg)
                            payload = data.get("data", data)
                            symbol = payload.get("s", "")
                            # bookTicker gives best bid/ask — use mid
                            bid = float(payload.get("b", 0))
                            ask = float(payload.get("a", 0))
                            if symbol and bid > 0 and ask > 0:
                                mid = (bid + ask) / 2
                                with self._lock:
                                    self._prices[symbol] = mid
                        except (json.JSONDecodeError, ValueError, KeyError):
                            pass
            except Exception as e:
                if self._running:
                    logger.warning(f"Binance WS error: {e}, reconnecting in 5s...")
                    await asyncio.sleep(5)

    # ── Probability model ─────────────────────────────────────────────────

    def _compute_probability(
        self, spot: float, mapping: CryptoMarketMapping
    ) -> Optional[float]:
        """
        Compute P(above strike at expiry) using log-normal model.

        P(S_T > K) = Φ(d2)  where d2 = (ln(S/K) - 0.5*σ²*T) / (σ*√T)
        """
        if spot <= 0 or mapping.strike <= 0:
            return None

        if mapping.expiry is not None:
            now = datetime.now(timezone.utc)
            dt_years = max(
                (mapping.expiry - now).total_seconds() / (365.25 * 86400),
                1 / 365.25,  # floor at 1 day
            )
        else:
            dt_years = 30 / 365.25  # default 30 days

        vol = self.DEFAULT_VOL
        sqrt_t = math.sqrt(dt_years)
        denom = vol * sqrt_t

        if denom <= 0:
            return None

        d2 = (math.log(spot / mapping.strike) - 0.5 * vol * vol * dt_years) / denom
        prob_above = _norm_cdf(d2)

        if not mapping.is_above:
            prob_above = 1.0 - prob_above

        return prob_above


def _norm_cdf(x: float) -> float:
    """Standard normal CDF (via math.erf)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
