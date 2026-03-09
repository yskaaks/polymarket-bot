"""
Kalshi cross-exchange signal provider for market making.

Polls Kalshi's public API for equivalent markets and uses their
mid-price as a reference signal for Polymarket fair value adjustment.
Same events trading on two exchanges should converge in price —
divergence is alpha.
"""

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from src.layer2_signals.fair_value import SignalProvider
from src.utils import logit

logger = logging.getLogger(__name__)

KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"


@dataclass
class KalshiMarketMatch:
    """A matched pair of Polymarket token ↔ Kalshi ticker."""
    polymarket_token_id: str
    kalshi_ticker: str
    kalshi_title: str
    polymarket_question: str
    last_kalshi_yes_bid: float = 0.0
    last_kalshi_yes_ask: float = 0.0
    last_kalshi_mid: float = 0.0
    last_updated: float = 0.0


class KalshiSignal(SignalProvider):
    """
    Signal provider that polls Kalshi for equivalent markets and
    returns logit-space adjustments when prices diverge.

    Match strategy:
    1. When a Polymarket market is registered, search Kalshi for similar titles
    2. If a match is found, periodically poll Kalshi price
    3. Emit logit adjustment toward Kalshi mid-price

    Kalshi prices are in cents (1-99), converted to 0.01-0.99 probability.
    """

    POLL_INTERVAL: float = 30.0  # poll Kalshi every 30s
    SIGNAL_WEIGHT: float = 0.4  # how much to trust Kalshi vs orderbook
    MIN_DIVERGENCE_LOGIT: float = 0.08  # minimum divergence to emit signal
    MATCH_SIMILARITY_THRESHOLD: float = 0.5  # keyword overlap threshold

    def __init__(self):
        self._matches: dict[str, KalshiMarketMatch] = {}  # token_id → match
        self._kalshi_cache: list[dict] = []  # cached Kalshi markets
        self._cache_time: float = 0.0
        self._lock = threading.Lock()
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False
        self._http = httpx.Client(timeout=10)

    @property
    def name(self) -> str:
        return "kalshi"

    def register_market(
        self,
        token_id: str,
        question: str,
    ) -> bool:
        """
        Try to find a matching Kalshi market for a Polymarket question.

        Returns True if a match was found.
        """
        kalshi_market = self._find_match(question)
        if kalshi_market is None:
            return False

        ticker = kalshi_market["ticker"]
        title = kalshi_market.get("title", ticker)

        # Get initial price
        yes_bid, yes_ask = self._get_kalshi_price(ticker)
        mid = (yes_bid + yes_ask) / 2 if yes_bid > 0 and yes_ask > 0 else 0

        match = KalshiMarketMatch(
            polymarket_token_id=token_id,
            kalshi_ticker=ticker,
            kalshi_title=title,
            polymarket_question=question,
            last_kalshi_yes_bid=yes_bid,
            last_kalshi_yes_ask=yes_ask,
            last_kalshi_mid=mid,
            last_updated=time.time(),
        )

        with self._lock:
            self._matches[token_id] = match

        logger.info(
            f"Kalshi signal: matched {token_id[:8]}... → {ticker} "
            f"(mid={mid:.2f}) | PM: {question[:50]} | K: {title[:50]}"
        )
        return True

    def unregister_market(self, token_id: str) -> None:
        with self._lock:
            self._matches.pop(token_id, None)

    def get_adjustment(self, token_id: str, current_fv: float) -> float:
        """
        Return logit-space adjustment based on Kalshi price.

        Positive = Kalshi says higher prob than current FV.
        """
        with self._lock:
            match = self._matches.get(token_id)
            if match is None:
                return 0.0
            kalshi_mid = match.last_kalshi_mid

        if kalshi_mid <= 0.01 or kalshi_mid >= 0.99:
            return 0.0

        # Compare in logit space
        kalshi_logit = logit(kalshi_mid)
        current_logit = logit(max(0.01, min(0.99, current_fv)))
        divergence = kalshi_logit - current_logit

        if abs(divergence) < self.MIN_DIVERGENCE_LOGIT:
            return 0.0

        adjustment = divergence * self.SIGNAL_WEIGHT

        logger.debug(
            f"Kalshi signal {token_id[:8]}...: kalshi_mid={kalshi_mid:.4f} "
            f"pm_fv={current_fv:.4f} adj_logit={adjustment:+.3f}"
        )

        return adjustment

    def start(self) -> None:
        """Start background polling thread."""
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return

        self._running = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="kalshi-poll"
        )
        self._poll_thread.start()
        logger.info("Kalshi signal polling started")

    def stop(self) -> None:
        self._running = False
        self._http.close()

    # ── Internal ──────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Background loop to refresh Kalshi prices for matched markets."""
        while self._running:
            try:
                with self._lock:
                    matches = list(self._matches.values())

                for match in matches:
                    if not self._running:
                        break
                    try:
                        yes_bid, yes_ask = self._get_kalshi_price(match.kalshi_ticker)
                        if yes_bid > 0 and yes_ask > 0:
                            mid = (yes_bid + yes_ask) / 2
                            with self._lock:
                                match.last_kalshi_yes_bid = yes_bid
                                match.last_kalshi_yes_ask = yes_ask
                                match.last_kalshi_mid = mid
                                match.last_updated = time.time()
                    except Exception as e:
                        logger.warning(f"Kalshi price fetch failed for {match.kalshi_ticker}: {e}")

                time.sleep(self.POLL_INTERVAL)

            except Exception as e:
                logger.error(f"Kalshi poll loop error: {e}", exc_info=True)
                time.sleep(self.POLL_INTERVAL)

    def _find_match(self, polymarket_question: str) -> Optional[dict]:
        """
        Search Kalshi for a market matching a Polymarket question.

        Uses keyword overlap scoring on market titles.
        """
        # Refresh cache if stale
        if time.time() - self._cache_time > 300:
            self._refresh_kalshi_cache()

        if not self._kalshi_cache:
            return None

        pm_keywords = self._extract_keywords(polymarket_question)
        if not pm_keywords:
            return None

        best_match = None
        best_score = 0.0

        for market in self._kalshi_cache:
            title = market.get("title", "")
            subtitle = market.get("subtitle", "")
            full_text = f"{title} {subtitle}"

            k_keywords = self._extract_keywords(full_text)
            if not k_keywords:
                continue

            # Jaccard-like similarity on keywords
            overlap = len(pm_keywords & k_keywords)
            union = len(pm_keywords | k_keywords)
            score = overlap / union if union > 0 else 0

            if score > best_score:
                best_score = score
                best_match = market

        if best_score >= self.MATCH_SIMILARITY_THRESHOLD and best_match is not None:
            logger.debug(
                f"Kalshi match (score={best_score:.2f}): "
                f"PM='{polymarket_question[:60]}' → K='{best_match.get('title', '')[:60]}'"
            )
            return best_match

        return None

    def _refresh_kalshi_cache(self) -> None:
        """Fetch active Kalshi markets and cache them."""
        try:
            markets = []
            cursor = None

            # Paginate through all open markets
            for _ in range(10):  # max 10 pages
                params = {"limit": 1000, "status": "open"}
                if cursor:
                    params["cursor"] = cursor

                r = self._http.get(f"{KALSHI_API_BASE}/markets", params=params)
                if r.status_code != 200:
                    logger.warning(f"Kalshi API returned {r.status_code}")
                    break

                data = r.json()
                page_markets = data.get("markets", [])
                markets.extend(page_markets)

                cursor = data.get("cursor")
                if not cursor or not page_markets:
                    break

            self._kalshi_cache = markets
            self._cache_time = time.time()
            logger.info(f"Cached {len(markets)} open Kalshi markets")

        except Exception as e:
            logger.warning(f"Failed to refresh Kalshi cache: {e}")

    def _get_kalshi_price(self, ticker: str) -> tuple[float, float]:
        """
        Get YES bid/ask for a Kalshi market.

        Returns (yes_bid, yes_ask) as probabilities (0-1).
        Kalshi prices are in cents (1-99).
        """
        try:
            r = self._http.get(f"{KALSHI_API_BASE}/markets/{ticker}")
            if r.status_code != 200:
                return (0.0, 0.0)

            market = r.json().get("market", {})
            # Kalshi returns prices in cents (e.g., 65 = $0.65)
            yes_bid = float(market.get("yes_bid", 0)) / 100.0
            yes_ask = float(market.get("yes_ask", 0)) / 100.0

            return (yes_bid, yes_ask)

        except Exception as e:
            logger.warning(f"Kalshi price fetch error for {ticker}: {e}")
            return (0.0, 0.0)

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        """Extract meaningful keywords from a market question/title."""
        # Lowercase, remove punctuation
        text = text.lower()
        text = re.sub(r"[^\w\s]", " ", text)
        words = text.split()

        # Remove stop words
        stop_words = {
            "will", "be", "the", "a", "an", "is", "are", "was", "were",
            "to", "of", "in", "on", "at", "by", "for", "or", "and",
            "this", "that", "it", "its", "from", "with", "as", "has",
            "have", "do", "does", "did", "not", "no", "yes", "than",
            "more", "less", "above", "below", "over", "under", "before",
            "after", "between", "during", "if", "what", "which", "who",
            "how", "when", "where", "there", "here", "up", "down",
        }

        keywords = {w for w in words if w not in stop_words and len(w) > 1}
        return keywords

    def get_match(self, token_id: str) -> Optional[KalshiMarketMatch]:
        """Get the Kalshi match for a token (for debugging)."""
        with self._lock:
            return self._matches.get(token_id)

    @property
    def num_matches(self) -> int:
        with self._lock:
            return len(self._matches)
