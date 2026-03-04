"""
Test fixtures using real Polymarket API response formats.

Data structures match exactly what comes back from:
- Gamma API: https://gamma-api.polymarket.com/markets
- CLOB API: https://clob.polymarket.com/book?token_id=...
- CLOB API: https://clob.polymarket.com/midpoint?token_id=...
- CLOB API: https://clob.polymarket.com/last-trade-price?token_id=...

Captured from live Polymarket on 2026-03-04.
"""

# ── Real Gamma API market response (mid-range price, good liquidity) ────
# Based on: "Will the Iranian regime fall by June 30?" — YES ~0.395
GAMMA_MARKET_MID_PRICE = {
    "id": "1180400",
    "question": "Will the Iranian regime fall by June 30?",
    "conditionId": "0xabc123def456789",
    "slug": "will-the-iranian-regime-fall-by-june-30",
    "endDate": "2026-06-30T00:00:00Z",
    "liquidity": "334667",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.395", "0.605"]',
    "volume": "5000000",
    "active": True,
    "closed": False,
    "volumeNum": 5000000,
    "liquidityNum": 334667,
    "volume24hr": 1202456,
    "clobTokenIds": '["38397507750621893057346880033441136112987238933685677349709401910643842844855", "99999999999999999999999999999999999999999999999999999999999999999999999999999"]',
    "negRisk": False,
    "enableOrderBook": True,
    "orderPriceMinTickSize": 0.01,
    "orderMinSize": 5,
    "category": "politics",
}

# Near 0.50 price (sports market)
GAMMA_MARKET_CENTERED = {
    "id": "1180500",
    "question": "Pistons vs. Cavaliers",
    "conditionId": "0xece8eb800e47471040918a24fe0a1cdaec640418abc64845bda2b13239ae94ac",
    "slug": "pistons-vs-cavaliers",
    "endDate": "2026-06-15T00:00:00Z",
    "liquidity": "72141",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.505", "0.495"]',
    "volume": "3000000",
    "active": True,
    "closed": False,
    "volumeNum": 3000000,
    "liquidityNum": 72141,
    "volume24hr": 1629992,
    "clobTokenIds": '["109081243913383015615425037992636571273513271639233491652524900113026919029196", "88888888888888888888888888888888888888888888888888888888888888888888888888888"]',
    "negRisk": False,
    "enableOrderBook": True,
    "orderPriceMinTickSize": 0.01,
    "orderMinSize": 5,
    "category": "sports",
}

# Extreme price (should be filtered out by market selector)
GAMMA_MARKET_EXTREME = {
    "id": "1180300",
    "question": "Khamenei out as Supreme Leader of Iran by February 28?",
    "conditionId": "0xd4bbf7f6707c67beb736135ad32a41f6db41f8ae52d3ac4919650de9eeb94ed8",
    "slug": "khamenei-out-feb-28",
    "endDate": "2026-02-28T00:00:00Z",
    "liquidity": "1561072.09506",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.9995", "0.0005"]',
    "volume": "130879666",
    "active": True,
    "closed": False,
    "volumeNum": 130879666,
    "liquidityNum": 1561072,
    "volume24hr": 21300421,
    "clobTokenIds": '["39317885422026394259056328144566743331998444273202427934141325790266108570112", "37975265083682450969967223199653164268542375291978582835346444673615244164455"]',
    "negRisk": False,
    "enableOrderBook": True,
    "orderPriceMinTickSize": 0.001,
    "orderMinSize": 5,
    "category": "politics",
}


# ── Real CLOB orderbook response format ─────────────────────────────────
# Based on: Iran regime fall market (YES token, ~0.395 mid)
CLOB_BOOK_WIDE_SPREAD = {
    "market": "0xabc123def456789",
    "asset_id": "38397507750621893057346880033441136112987238933685677349709401910643842844855",
    "timestamp": "1772585081148",
    "hash": "3af766fe825cf13fb655072571b188d104367cdb",
    "min_order_size": 5,
    "tick_size": 0.01,
    "neg_risk": False,
    "last_trade_price": "0.400",
    "bids": [
        {"price": "0.39", "size": "15568.12"},
        {"price": "0.38", "size": "19530.46"},
        {"price": "0.37", "size": "90201.04"},
        {"price": "0.36", "size": "68057.05"},
        {"price": "0.35", "size": "22324.35"},
        {"price": "0.34", "size": "5000.00"},
        {"price": "0.30", "size": "3186.76"},
        {"price": "0.20", "size": "3338.00"},
        {"price": "0.10", "size": "7849.98"},
        {"price": "0.01", "size": "64653.00"},
    ],
    "asks": [
        {"price": "0.40", "size": "10038.51"},
        {"price": "0.41", "size": "18021.43"},
        {"price": "0.42", "size": "27022.69"},
        {"price": "0.43", "size": "4208.34"},
        {"price": "0.44", "size": "14755.24"},
        {"price": "0.50", "size": "5000.00"},
        {"price": "0.60", "size": "2000.00"},
        {"price": "0.80", "size": "1000.00"},
        {"price": "0.99", "size": "500.00"},
    ],
}

# Based on: Pistons vs Cavaliers (YES token, ~0.505 mid, tight spread)
CLOB_BOOK_TIGHT_SPREAD = {
    "market": "0xece8eb800e47471040918a24fe0a1cdaec640418abc64845bda2b13239ae94ac",
    "asset_id": "109081243913383015615425037992636571273513271639233491652524900113026919029196",
    "timestamp": "1772585034065",
    "hash": "abc123",
    "min_order_size": 5,
    "tick_size": 0.01,
    "neg_risk": False,
    "last_trade_price": "0.510",
    "bids": [
        {"price": "0.50", "size": "11264.68"},
        {"price": "0.49", "size": "2534.59"},
        {"price": "0.48", "size": "29712.14"},
        {"price": "0.47", "size": "8084.34"},
        {"price": "0.46", "size": "6059.43"},
    ],
    "asks": [
        {"price": "0.51", "size": "592.18"},
        {"price": "0.52", "size": "891.43"},
        {"price": "0.53", "size": "7369.44"},
        {"price": "0.54", "size": "7568.30"},
        {"price": "0.55", "size": "27243.85"},
    ],
}

# Thin book (low liquidity, should produce wider spreads)
CLOB_BOOK_THIN = {
    "market": "0xthinmarket",
    "asset_id": "11111111111111111111111111111111111111111111111111111111111111111111111111111",
    "timestamp": "1772585000000",
    "hash": "thin123",
    "min_order_size": 5,
    "tick_size": 0.01,
    "neg_risk": False,
    "last_trade_price": "0.550",
    "bids": [
        {"price": "0.50", "size": "15.00"},
        {"price": "0.45", "size": "25.00"},
        {"price": "0.40", "size": "10.00"},
    ],
    "asks": [
        {"price": "0.60", "size": "20.00"},
        {"price": "0.65", "size": "15.00"},
        {"price": "0.70", "size": "10.00"},
    ],
}

# Asymmetric book (heavy bid side — bullish imbalance)
CLOB_BOOK_BULLISH_IMBALANCE = {
    "market": "0xbullish",
    "asset_id": "22222222222222222222222222222222222222222222222222222222222222222222222222222",
    "timestamp": "1772585000000",
    "hash": "bull123",
    "min_order_size": 5,
    "tick_size": 0.01,
    "neg_risk": False,
    "last_trade_price": "0.600",
    "bids": [
        {"price": "0.59", "size": "50000.00"},
        {"price": "0.58", "size": "40000.00"},
        {"price": "0.57", "size": "30000.00"},
        {"price": "0.56", "size": "20000.00"},
        {"price": "0.55", "size": "10000.00"},
    ],
    "asks": [
        {"price": "0.61", "size": "2000.00"},
        {"price": "0.62", "size": "1500.00"},
        {"price": "0.63", "size": "1000.00"},
        {"price": "0.64", "size": "500.00"},
        {"price": "0.65", "size": "500.00"},
    ],
}

# ── CLOB price endpoint responses ───────────────────────────────────────
CLOB_MIDPOINT_RESPONSE = {"mid": "0.395"}
CLOB_LAST_TRADE_RESPONSE = {"price": "0.4", "side": "BUY"}
CLOB_PRICE_RESPONSE = {"price": "0.39"}


# ── Helper to build Orderbook from CLOB response ───────────────────────
def clob_book_to_orderbook(clob_book: dict):
    """Convert a raw CLOB book response to our Orderbook dataclass."""
    from src.orderbook import Orderbook, OrderbookLevel

    bids = [
        OrderbookLevel(price=float(b["price"]), size=float(b["size"]))
        for b in clob_book["bids"]
    ]
    asks = [
        OrderbookLevel(price=float(a["price"]), size=float(a["size"]))
        for a in clob_book["asks"]
    ]

    # Match OrderbookAnalyzer sorting: bids high→low, asks low→high
    bids.sort(key=lambda x: x.price, reverse=True)
    asks.sort(key=lambda x: x.price)

    return Orderbook(
        token_id=clob_book["asset_id"],
        bids=bids,
        asks=asks,
        timestamp=clob_book.get("timestamp"),
    )
