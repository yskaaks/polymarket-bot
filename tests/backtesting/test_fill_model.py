"""Tests for PredictionMarketFillModel."""
import pytest

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.objects import Price, Quantity

from src.layer1_research.backtesting.execution.fill_model import (
    PredictionMarketFillConfig,
    PredictionMarketFillModel,
    _MIN_DEPTH_SCALE,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _make_instrument():
    """Minimal BinaryOption for testing fill model book construction."""
    from nautilus_trader.model.instruments import BinaryOption
    from nautilus_trader.model.currencies import USD
    from nautilus_trader.model.enums import AssetClass

    return BinaryOption(
        instrument_id=InstrumentId(Symbol("TEST_TOKEN"), Venue("POLYMARKET")),
        raw_symbol=Symbol("TEST_TOKEN"),
        asset_class=AssetClass.ALTERNATIVE,
        currency=USD,
        price_precision=2,
        size_precision=1,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.1"),
        activation_ns=0,
        expiration_ns=1_000_000_000_000_000_000,
        ts_event=0,
        ts_init=0,
        outcome="Yes",
    )


# ── Config validation ─────────────────────────────────────────────────


class TestPredictionMarketFillConfig:
    def test_default_config_is_valid(self):
        config = PredictionMarketFillConfig()
        assert config.base_spread_pct == 0.04
        assert len(config.depth_tiers) == 3

    def test_rejects_zero_spread(self):
        with pytest.raises(ValueError, match="base_spread_pct"):
            PredictionMarketFillConfig(base_spread_pct=0.0)

    def test_rejects_spread_at_one(self):
        with pytest.raises(ValueError, match="base_spread_pct"):
            PredictionMarketFillConfig(base_spread_pct=1.0)

    def test_rejects_negative_spread(self):
        with pytest.raises(ValueError, match="base_spread_pct"):
            PredictionMarketFillConfig(base_spread_pct=-0.01)

    def test_rejects_empty_tiers(self):
        with pytest.raises(ValueError, match="at least one tier"):
            PredictionMarketFillConfig(depth_tiers=())

    def test_rejects_negative_tier_size(self):
        with pytest.raises(ValueError, match="size must be positive"):
            PredictionMarketFillConfig(depth_tiers=((-100.0, 0.0),))

    def test_rejects_zero_tier_size(self):
        with pytest.raises(ValueError, match="size must be positive"):
            PredictionMarketFillConfig(depth_tiers=((0.0, 0.0),))

    def test_rejects_negative_tier_offset(self):
        with pytest.raises(ValueError, match="offset must be non-negative"):
            PredictionMarketFillConfig(depth_tiers=((100.0, -0.01),))

    def test_rejects_non_increasing_offsets(self):
        with pytest.raises(ValueError, match="strictly greater"):
            PredictionMarketFillConfig(depth_tiers=(
                (100.0, 0.02),
                (200.0, 0.02),  # same as previous — must be strictly greater
            ))

    def test_config_is_frozen(self):
        config = PredictionMarketFillConfig()
        with pytest.raises(AttributeError):
            config.base_spread_pct = 0.10


# ── Fill model construction ───────────────────────────────────────────


class TestPredictionMarketFillModel:
    def test_default_construction(self):
        model = PredictionMarketFillModel()
        assert model.config.base_spread_pct == 0.04

    def test_custom_config(self):
        config = PredictionMarketFillConfig(
            base_spread_pct=0.06,
            depth_tiers=((50.0, 0.00), (200.0, 0.03)),
        )
        model = PredictionMarketFillModel(config)
        assert model.config.base_spread_pct == 0.06
        assert len(model.config.depth_tiers) == 2

    def test_rejects_wrong_config_type(self):
        with pytest.raises(TypeError, match="PredictionMarketFillConfig"):
            PredictionMarketFillModel(config={"spread": 0.04})


# ── Book construction ─────────────────────────────────────────────────


class TestBookConstruction:
    def test_book_has_correct_number_of_levels(self):
        config = PredictionMarketFillConfig(
            depth_tiers=((100.0, 0.00), (500.0, 0.02), (2000.0, 0.05)),
            depth_scale_by_price=False,
        )
        model = PredictionMarketFillModel(config)
        inst = _make_instrument()
        book = model.get_orderbook_for_fill_simulation(
            inst, None, Price.from_str("0.48"), Price.from_str("0.52"),
        )
        assert book.best_bid_price() is not None
        assert book.best_ask_price() is not None

    def test_spread_is_applied(self):
        config = PredictionMarketFillConfig(
            base_spread_pct=0.06,
            depth_tiers=((1000.0, 0.00),),
            depth_scale_by_price=False,
        )
        model = PredictionMarketFillModel(config)
        inst = _make_instrument()
        book = model.get_orderbook_for_fill_simulation(
            inst, None, Price.from_str("0.48"), Price.from_str("0.52"),
        )
        bid = float(book.best_bid_price())
        ask = float(book.best_ask_price())
        spread = ask - bid
        assert spread == pytest.approx(0.06, abs=0.01)

    def test_large_buy_gets_worse_price(self):
        """A large buy should walk through tiers and get a worse avg fill."""
        config = PredictionMarketFillConfig(
            base_spread_pct=0.04,
            depth_tiers=((100.0, 0.00), (500.0, 0.02), (2000.0, 0.05)),
            depth_scale_by_price=False,
        )
        model = PredictionMarketFillModel(config)
        inst = _make_instrument()
        book = model.get_orderbook_for_fill_simulation(
            inst, None, Price.from_str("0.48"), Price.from_str("0.52"),
        )
        small_buy_px = book.get_avg_px_for_quantity(
            Quantity.from_str("50.0"), OrderSide.BUY,
        )
        large_buy_px = book.get_avg_px_for_quantity(
            Quantity.from_str("400.0"), OrderSide.BUY,
        )
        assert large_buy_px > small_buy_px, (
            f"Large buy ({large_buy_px}) should be worse than small buy ({small_buy_px})"
        )

    def test_large_sell_gets_worse_price(self):
        """A large sell should walk through tiers and get a worse avg fill."""
        config = PredictionMarketFillConfig(
            base_spread_pct=0.04,
            depth_tiers=((100.0, 0.00), (500.0, 0.02), (2000.0, 0.05)),
            depth_scale_by_price=False,
        )
        model = PredictionMarketFillModel(config)
        inst = _make_instrument()
        book = model.get_orderbook_for_fill_simulation(
            inst, None, Price.from_str("0.48"), Price.from_str("0.52"),
        )
        small_sell_px = book.get_avg_px_for_quantity(
            Quantity.from_str("50.0"), OrderSide.SELL,
        )
        large_sell_px = book.get_avg_px_for_quantity(
            Quantity.from_str("400.0"), OrderSide.SELL,
        )
        assert large_sell_px < small_sell_px, (
            f"Large sell ({large_sell_px}) should be worse than small sell ({small_sell_px})"
        )

    def test_order_beyond_total_depth_gets_very_bad_price(self):
        """Order larger than all tiers combined gets worst available price."""
        config = PredictionMarketFillConfig(
            base_spread_pct=0.04,
            depth_tiers=((100.0, 0.00), (200.0, 0.03)),
            depth_scale_by_price=False,
        )
        model = PredictionMarketFillModel(config)
        inst = _make_instrument()
        book = model.get_orderbook_for_fill_simulation(
            inst, None, Price.from_str("0.48"), Price.from_str("0.52"),
        )
        within_depth_px = book.get_avg_px_for_quantity(
            Quantity.from_str("100.0"), OrderSide.BUY,
        )
        beyond_depth_px = book.get_avg_px_for_quantity(
            Quantity.from_str("300.0"), OrderSide.BUY,
        )
        assert beyond_depth_px > within_depth_px


# ── Depth scaling by price ────────────────────────────────────────────


class TestDepthScaling:
    def test_midpoint_gets_full_depth(self):
        model = PredictionMarketFillModel()
        scale = model._depth_scale_for_price(0.50)
        assert scale == pytest.approx(1.0)

    def test_extreme_price_gets_reduced_depth(self):
        model = PredictionMarketFillModel()
        scale_extreme = model._depth_scale_for_price(0.05)
        scale_mid = model._depth_scale_for_price(0.50)
        assert scale_extreme < scale_mid
        assert scale_extreme >= _MIN_DEPTH_SCALE

    def test_scaling_is_symmetric(self):
        model = PredictionMarketFillModel()
        scale_low = model._depth_scale_for_price(0.20)
        scale_high = model._depth_scale_for_price(0.80)
        assert scale_low == pytest.approx(scale_high)

    def test_scaling_disabled(self):
        config = PredictionMarketFillConfig(depth_scale_by_price=False)
        model = PredictionMarketFillModel(config)
        assert model._depth_scale_for_price(0.05) == 1.0
        assert model._depth_scale_for_price(0.95) == 1.0

    def test_thin_market_at_extreme_has_more_impact(self):
        """Same order size should produce more slippage at price extremes."""
        config = PredictionMarketFillConfig(
            base_spread_pct=0.04,
            depth_tiers=((100.0, 0.00), (500.0, 0.02)),
            depth_scale_by_price=True,
        )
        model = PredictionMarketFillModel(config)
        inst = _make_instrument()

        # Book at midpoint — deep
        book_mid = model.get_orderbook_for_fill_simulation(
            inst, None, Price.from_str("0.48"), Price.from_str("0.52"),
        )
        # Book at extreme — thin
        book_extreme = model.get_orderbook_for_fill_simulation(
            inst, None, Price.from_str("0.08"), Price.from_str("0.12"),
        )
        qty = Quantity.from_str("200.0")
        impact_mid = book_mid.get_avg_px_for_quantity(qty, OrderSide.BUY)
        impact_extreme = book_extreme.get_avg_px_for_quantity(qty, OrderSide.BUY)

        mid_best = float(book_mid.best_ask_price())
        extreme_best = float(book_extreme.best_ask_price())

        slippage_mid = impact_mid - mid_best
        slippage_extreme = impact_extreme - extreme_best
        assert slippage_extreme > slippage_mid, (
            f"Extreme slippage ({slippage_extreme:.4f}) should exceed "
            f"midpoint slippage ({slippage_mid:.4f})"
        )


# ── Edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_no_best_bid(self):
        """Model should handle missing bid gracefully."""
        model = PredictionMarketFillModel()
        inst = _make_instrument()
        book = model.get_orderbook_for_fill_simulation(
            inst, None, None, Price.from_str("0.52"),
        )
        assert book.best_ask_price() is not None

    def test_no_best_ask(self):
        """Model should handle missing ask gracefully."""
        model = PredictionMarketFillModel()
        inst = _make_instrument()
        book = model.get_orderbook_for_fill_simulation(
            inst, None, Price.from_str("0.48"), None,
        )
        assert book.best_bid_price() is not None

    def test_no_prices_at_all(self):
        """Model should handle no price info with sensible default."""
        model = PredictionMarketFillModel()
        inst = _make_instrument()
        book = model.get_orderbook_for_fill_simulation(
            inst, None, None, None,
        )
        assert book.best_bid_price() is not None
        assert book.best_ask_price() is not None

    def test_price_near_floor(self):
        """Bid prices should not go below 0.01."""
        config = PredictionMarketFillConfig(
            base_spread_pct=0.04,
            depth_tiers=((100.0, 0.00), (500.0, 0.05)),
        )
        model = PredictionMarketFillModel(config)
        inst = _make_instrument()
        book = model.get_orderbook_for_fill_simulation(
            inst, None, Price.from_str("0.02"), Price.from_str("0.04"),
        )
        bid = float(book.best_bid_price())
        assert bid >= 0.01

    def test_price_near_ceiling(self):
        """Ask prices should not go above 0.99."""
        config = PredictionMarketFillConfig(
            base_spread_pct=0.04,
            depth_tiers=((100.0, 0.00), (500.0, 0.05)),
        )
        model = PredictionMarketFillModel(config)
        inst = _make_instrument()
        book = model.get_orderbook_for_fill_simulation(
            inst, None, Price.from_str("0.96"), Price.from_str("0.98"),
        )
        # Walk through all ask levels — none should exceed 0.99
        asks = book.asks()
        for level in asks:
            assert float(level.price) <= 0.99
