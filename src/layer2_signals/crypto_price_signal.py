"""
Crypto price signal provider for market making.

Subscribes to Binance WebSocket for real-time crypto prices, parses Polymarket
market questions to detect crypto price targets, and converts spot price →
probability for fair value adjustment.
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


# Common crypto assets and their Binance symbols
CRYPTO_ASSETS = {
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

# Regex patterns for parsing crypto market questions
# Matches: "Will Bitcoin be above $80,000 on March 31?"
# Matches: "Will ETH reach $4,000 by December 31, 2025?"
# Matches: "Bitcoin above $100k?"
PRICE_PATTERNS = [
    # "above/below/reach $X,XXX by/on DATE"
    re.compile(
        r"(?:will\s+)?(\w+)\s+(?:be\s+)?(?:above|over|exceed|reach|hit|at or above)\s+"
        r"\$?([\d,]+(?:\.\d+)?[kKmM]?)\s*(?:by|on|before)\s+(.+?)[\?\.]?\s*$",
        re.IGNORECASE,
    ),
    # "above/below $X,XXX" (no date)
    re.compile(
        r"(?:will\s+)?(\w+)\s+(?:be\s+)?(?:above|over|exceed|reach|hit|at or above)\s+"
        r"\$?([\d,]+(?:\.\d+)?[kKmM]?)",
        re.IGNORECASE,
    ),
    # "below/under $X,XXX"
    re.compile(
        r"(?:will\s+)?(\w+)\s+(?:be\s+)?(?:below|under|drop below|fall below|at or below)\s+"
        r"\$?([\d,]+(?:\.\d+)?[kKmM]?)",
        re.IGNORECASE,
    ),
]


@dataclass
class CryptoMarketMapping:
    """Maps a Polymarket token to a crypto price target."""
    token_id: str
    symbol: str  # Binance symbol, e.g. "BTCUSDT"
    strike: float  # Target price, e.g. 80000.0
    expiry: Optional[datetime]  # When the market resolves
    is_above: bool  # True = "above strike", False = "below strike"
    question: str  # Original question for debugging


class CryptoPriceSignal(SignalProvider):
    """
    Signal provider that uses real-time crypto prices from Binance
    to adjust fair value for crypto-related Polymarket markets.

    Uses a simple normal CDF model:
    - P(above strike) = Φ((log(spot/strike)) / (vol * sqrt(T)))
    - Where vol is annualized implied volatility (default 80% for crypto)
    """

    # Annualized volatility assumption for crypto
    DEFAULT_VOL: float = 0.80
    # How much to trust external signal vs orderbook (0-1)
    SIGNAL_WEIGHT: float = 0.6
    # Minimum divergence in logit space before emitting adjustment
    MIN_DIVERGENCE_LOGIT: float = 0.05

    def __init__(self):
        self._prices: dict[str, float] = {}  # symbol → latest price
        self._mappings: dict[str, CryptoMarketMapping] = {}  # token_id → mapping
        self._lock = threading.Lock()
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False
        self._symbols_needed: set[str] = set()

        # Seed prices from REST before WS connects
        self._seed_prices()

    @property
    def name(self) -> str:
        return "crypto_price"

    def register_market(
        self,
        token_id: str,
        question: str,
        expiry: Optional[datetime] = None,
    ) -> bool:
        """
        Try to parse a market question and register it for crypto price tracking.

        Returns True if the market was recognized as a crypto price market.
        """
        mapping = self._parse_question(token_id, question, expiry)
        if mapping is None:
            return False

        with self._lock:
            self._mappings[token_id] = mapping
            self._symbols_needed.add(mapping.symbol)

        logger.info(
            f"Crypto signal: mapped {token_id[:8]}... → {mapping.symbol} "
            f"{'above' if mapping.is_above else 'below'} ${mapping.strike:,.0f} "
            f"(expiry: {mapping.expiry})"
        )
        return True

    def unregister_market(self, token_id: str) -> None:
        with self._lock:
            self._mappings.pop(token_id, None)

    def get_adjustment(self, token_id: str, current_fv: float) -> float:
        """
        Return logit-space adjustment based on external crypto price.

        Positive = bullish (price suggests higher prob than current FV).
        """
        with self._lock:
            mapping = self._mappings.get(token_id)
            if mapping is None:
                return 0.0
            spot = self._prices.get(mapping.symbol)

        if spot is None or spot <= 0:
            return 0.0

        # Compute model probability
        model_prob = self._compute_probability(spot, mapping)
        if model_prob is None or model_prob <= 0.01 or model_prob >= 0.99:
            return 0.0

        # Compare in logit space
        model_logit = logit(model_prob)
        current_logit = logit(max(0.01, min(0.99, current_fv)))
        divergence = model_logit - current_logit

        # Only adjust if divergence exceeds threshold
        if abs(divergence) < self.MIN_DIVERGENCE_LOGIT:
            return 0.0

        # Apply signal weight
        adjustment = divergence * self.SIGNAL_WEIGHT

        logger.debug(
            f"Crypto signal {token_id[:8]}...: spot=${spot:,.2f} "
            f"model_p={model_prob:.4f} current_fv={current_fv:.4f} "
            f"adj_logit={adjustment:+.3f}"
        )

        return adjustment

    def get_spot_price(self, symbol: str) -> Optional[float]:
        """Get current spot price for a Binance symbol."""
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

    # ── Internal ──────────────────────────────────────────────────────────

    def _seed_prices(self) -> None:
        """Fetch initial prices from Binance REST API."""
        try:
            r = httpx.get(
                "https://api.binance.com/api/v3/ticker/price",
                timeout=5,
            )
            if r.status_code == 200:
                for item in r.json():
                    sym = item["symbol"]
                    if sym in set(CRYPTO_ASSETS.values()):
                        self._prices[sym] = float(item["price"])
                logger.info(f"Seeded {len(self._prices)} crypto prices from Binance REST")
        except Exception as e:
            logger.warning(f"Failed to seed crypto prices: {e}")

    def _run_ws(self) -> None:
        """Run Binance WebSocket in a background thread."""
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
        # Subscribe to all known crypto symbols via combined stream
        all_symbols = set(CRYPTO_ASSETS.values())
        streams = [f"{sym.lower()}@miniTicker" for sym in all_symbols]
        stream_path = "/".join(streams)
        url = f"wss://stream.binance.com:9443/stream?streams={stream_path}"

        while self._running:
            try:
                async with websockets.connect(url) as ws:
                    logger.info(f"Connected to Binance WS ({len(all_symbols)} symbols)")
                    async for msg in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(msg)
                            payload = data.get("data", data)
                            symbol = payload.get("s", "")
                            price = float(payload.get("c", 0))
                            if symbol and price > 0:
                                with self._lock:
                                    self._prices[symbol] = price
                        except (json.JSONDecodeError, ValueError, KeyError):
                            pass
            except Exception as e:
                if self._running:
                    logger.warning(f"Binance WS error: {e}, reconnecting in 5s...")
                    await asyncio.sleep(5)

    def _parse_question(
        self,
        token_id: str,
        question: str,
        expiry: Optional[datetime],
    ) -> Optional[CryptoMarketMapping]:
        """Try to parse a Polymarket question into a crypto price target."""
        for pattern in PRICE_PATTERNS:
            match = pattern.search(question)
            if not match:
                continue

            asset_name = match.group(1).lower()
            price_str = match.group(2)

            # Look up Binance symbol
            symbol = CRYPTO_ASSETS.get(asset_name)
            if symbol is None:
                continue

            # Parse strike price
            strike = self._parse_price(price_str)
            if strike is None or strike <= 0:
                continue

            # Determine direction
            is_above = not bool(
                re.search(r"below|under|drop|fall", question, re.IGNORECASE)
            )

            # Parse expiry from question if not provided
            parsed_expiry = expiry
            if parsed_expiry is None and len(match.groups()) >= 3:
                parsed_expiry = self._parse_date(match.group(3))

            return CryptoMarketMapping(
                token_id=token_id,
                symbol=symbol,
                strike=strike,
                expiry=parsed_expiry,
                is_above=is_above,
                question=question,
            )

        return None

    @staticmethod
    def _parse_price(price_str: str) -> Optional[float]:
        """Parse price string like '80,000', '80k', '4.5k'."""
        s = price_str.replace(",", "").strip()
        multiplier = 1.0
        if s.lower().endswith("k"):
            multiplier = 1_000
            s = s[:-1]
        elif s.lower().endswith("m"):
            multiplier = 1_000_000
            s = s[:-1]
        try:
            return float(s) * multiplier
        except ValueError:
            return None

    @staticmethod
    def _parse_date(date_str: str) -> Optional[datetime]:
        """Try to parse common date formats from market questions."""
        date_str = date_str.strip().rstrip("?.")
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

    def _compute_probability(
        self, spot: float, mapping: CryptoMarketMapping
    ) -> Optional[float]:
        """
        Compute P(above strike at expiry) using log-normal model.

        P(S_T > K) = Φ(d2)  where d2 = (ln(S/K) - 0.5*σ²*T) / (σ*√T)

        For simplicity, we use d1-like: (ln(S/K)) / (σ*√T)
        which gives the risk-neutral probability under GBM.
        """
        if spot <= 0 or mapping.strike <= 0:
            return None

        # Time to expiry in years
        if mapping.expiry is not None:
            now = datetime.now(timezone.utc)
            dt_years = max(
                (mapping.expiry - now).total_seconds() / (365.25 * 86400),
                1 / 365.25,  # floor at 1 day
            )
        else:
            dt_years = 30 / 365.25  # default 30 days if unknown

        vol = self.DEFAULT_VOL
        sqrt_t = math.sqrt(dt_years)
        denom = vol * sqrt_t

        if denom <= 0:
            return None

        # d2 = (ln(S/K) - 0.5*vol^2*T) / (vol*sqrt(T))
        d2 = (math.log(spot / mapping.strike) - 0.5 * vol * vol * dt_years) / denom

        # Normal CDF approximation
        prob_above = _norm_cdf(d2)

        if not mapping.is_above:
            prob_above = 1.0 - prob_above

        return prob_above


def _norm_cdf(x: float) -> float:
    """Standard normal CDF approximation (Abramowitz & Stegun)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
