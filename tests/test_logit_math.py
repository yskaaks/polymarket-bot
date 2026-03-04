"""
Tests for logit-space math utilities.

These are the foundation of all price calculations — if these are wrong,
every quote the bot places will be wrong.
"""

import math
import pytest

from src.utils import logit, expit, logit_adjust, logit_midpoint, logit_spread


class TestLogitExpit:
    """logit/expit are inverses and handle edge cases."""

    def test_logit_expit_roundtrip(self):
        """logit → expit should return the original probability."""
        for p in [0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99]:
            assert abs(expit(logit(p)) - p) < 1e-10

    def test_expit_logit_roundtrip(self):
        """expit → logit should return the original log-odds."""
        for x in [-5, -2, -1, 0, 1, 2, 5]:
            assert abs(logit(expit(x)) - x) < 1e-10

    def test_logit_at_050(self):
        """logit(0.5) = 0 (even odds)."""
        assert logit(0.5) == 0.0

    def test_expit_at_zero(self):
        """expit(0) = 0.5."""
        assert expit(0.0) == 0.5

    def test_logit_symmetry(self):
        """logit(p) = -logit(1-p)."""
        for p in [0.1, 0.3, 0.7, 0.9]:
            assert abs(logit(p) + logit(1 - p)) < 1e-10

    def test_logit_monotonic(self):
        """logit is strictly increasing."""
        probs = [0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99]
        logits = [logit(p) for p in probs]
        for i in range(len(logits) - 1):
            assert logits[i] < logits[i + 1]

    def test_logit_clamps_near_zero(self):
        """logit(0) should not crash, returns very negative."""
        result = logit(0.0)
        assert result < -15

    def test_logit_clamps_near_one(self):
        """logit(1) should not crash, returns very positive."""
        result = logit(1.0)
        assert result > 15

    def test_expit_extreme_positive(self):
        """expit(1000) should be ~1.0 without overflow."""
        assert expit(1000) > 0.999

    def test_expit_extreme_negative(self):
        """expit(-1000) should be ~0.0 without overflow."""
        assert expit(-1000) < 0.001


class TestLogitAdjust:
    """logit_adjust applies additive shifts in logit space."""

    def test_zero_adjustment(self):
        """No adjustment returns same probability."""
        for p in [0.1, 0.5, 0.9]:
            assert abs(logit_adjust(p, 0.0) - p) < 1e-10

    def test_positive_adjustment_increases(self):
        """Positive logit adjustment increases probability."""
        for p in [0.2, 0.5, 0.8]:
            result = logit_adjust(p, 0.5)
            assert result > p

    def test_negative_adjustment_decreases(self):
        """Negative logit adjustment decreases probability."""
        for p in [0.2, 0.5, 0.8]:
            result = logit_adjust(p, -0.5)
            assert result < p

    def test_bounded_output(self):
        """Result is always in (0, 1) regardless of adjustment."""
        assert 0 < logit_adjust(0.01, -10.0) < 1
        assert 0 < logit_adjust(0.99, 10.0) < 1

    def test_same_logit_shift_smaller_at_extremes(self):
        """
        Key property: a 0.3 logit shift at p=0.50 produces a LARGER
        probability change than the same shift at p=0.90.
        This is why logit-space is correct for prediction markets.
        """
        shift = 0.3
        change_at_50 = logit_adjust(0.50, shift) - 0.50
        change_at_90 = logit_adjust(0.90, shift) - 0.90

        assert change_at_50 > change_at_90
        # At 0.50: ~7.4% move, at 0.90: ~2.7% move
        assert change_at_50 > 0.05
        assert change_at_90 < 0.04


class TestLogitMidpoint:
    """Weighted averaging in logit space."""

    def test_equal_weight_symmetric(self):
        """Equal weights on symmetric probabilities → 0.50."""
        result = logit_midpoint(0.3, 0.7, weight1=0.5)
        assert abs(result - 0.5) < 1e-10

    def test_same_value(self):
        """Midpoint of same value is that value."""
        result = logit_midpoint(0.6, 0.6, weight1=0.5)
        assert abs(result - 0.6) < 1e-10

    def test_weight_one_dominates(self):
        """weight1=1.0 returns first value."""
        result = logit_midpoint(0.3, 0.8, weight1=1.0)
        assert abs(result - 0.3) < 1e-10

    def test_weight_zero_gives_second(self):
        """weight1=0.0 returns second value."""
        result = logit_midpoint(0.3, 0.8, weight1=0.0)
        assert abs(result - 0.8) < 1e-10

    def test_bounded(self):
        """Result always between the two inputs."""
        result = logit_midpoint(0.2, 0.9, weight1=0.5)
        assert 0.2 < result < 0.9


class TestLogitSpread:
    """Symmetric spread in logit space → asymmetric in prob space."""

    def test_symmetric_around_half(self):
        """At p=0.50, logit spread is symmetric in prob space too."""
        bid, ask = logit_spread(0.5, 0.2)
        assert abs((0.5 - bid) - (ask - 0.5)) < 1e-10

    def test_ordered(self):
        """bid < center < ask always."""
        for p in [0.1, 0.3, 0.5, 0.7, 0.9]:
            bid, ask = logit_spread(p, 0.15)
            assert bid < p < ask

    def test_bounded(self):
        """bid and ask always in (0, 1)."""
        for p in [0.01, 0.5, 0.99]:
            bid, ask = logit_spread(p, 0.5)
            assert 0 < bid < ask < 1

    def test_wider_spread_at_center(self):
        """
        Same logit half-width produces wider prob spread at p=0.50
        than at p=0.90 (because logit compresses near boundaries).
        """
        hw = 0.2
        bid_50, ask_50 = logit_spread(0.50, hw)
        bid_90, ask_90 = logit_spread(0.90, hw)

        spread_50 = ask_50 - bid_50
        spread_90 = ask_90 - bid_90

        assert spread_50 > spread_90

    def test_spread_values_realistic(self):
        """Verify actual spread values match expectations."""
        # At p=0.50, half-width 0.15 should give ~3.7% spread each side
        bid, ask = logit_spread(0.50, 0.15)
        spread = ask - bid
        assert 0.05 < spread < 0.10  # roughly 7% total spread

        # At p=0.90, same half-width → tighter
        bid, ask = logit_spread(0.90, 0.15)
        spread = ask - bid
        assert 0.01 < spread < 0.05  # much tighter
