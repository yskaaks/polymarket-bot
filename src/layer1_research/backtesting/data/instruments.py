"""Factory for building NautilusTrader BinaryOption instruments from MarketInfo."""
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AssetClass
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import BinaryOption
from nautilus_trader.model.objects import Price, Quantity

from src.layer1_research.backtesting.data.models import MarketInfo

POLYMARKET_VENUE = Venue("POLYMARKET")
_token_pairs: dict[str, str] = {}


def create_instruments(market: MarketInfo) -> list[BinaryOption]:
    """Create BinaryOption instruments for each outcome in a market."""
    instruments = []
    if len(market.token_ids) == 2:
        _token_pairs[market.token_ids[0]] = market.token_ids[1]
        _token_pairs[market.token_ids[1]] = market.token_ids[0]

    for i, (outcome, token_id) in enumerate(zip(market.outcomes, market.token_ids)):
        instrument_id = InstrumentId(symbol=Symbol(token_id), venue=POLYMARKET_VENUE)
        instrument = BinaryOption(
            instrument_id=instrument_id,
            raw_symbol=Symbol(token_id),
            asset_class=AssetClass.ALTERNATIVE,
            currency=USD,
            price_precision=2,
            size_precision=1,
            price_increment=Price.from_str("0.01"),
            size_increment=Quantity.from_str("0.1"),
            activation_ns=int(market.created_at.timestamp() * 1e9),
            expiration_ns=int(market.end_date.timestamp() * 1e9) if market.end_date else 0,
            ts_event=0,
            ts_init=0,
            maker_fee=0,
            taker_fee=0,
            outcome=outcome,
        )
        instruments.append(instrument)
    return instruments


def get_paired_token_id(token_id: str) -> str:
    """Get the paired token ID (YES->NO or NO->YES)."""
    return _token_pairs[token_id]


def clear_pairs():
    """Clear the token pair registry. Used in tests."""
    _token_pairs.clear()
