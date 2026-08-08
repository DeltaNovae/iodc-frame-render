from datetime import datetime, timezone

import pytest
from PIL import Image

from iodc import products, solar

DHAKA = (23.8103, 90.4125)


# ── solar position ────────────────────────────────────────────────────────────
# Checked against published Dhaka sun times for the date: sunrise ~05:30 BST,
# sunset ~18:38 BST, solar noon near overhead in August (BST = UTC+6).

def test_solar_noon_is_near_overhead_in_august():
    noon = datetime(2026, 8, 8, 6, 0, tzinfo=timezone.utc)   # 12:00 BST
    assert solar.solar_elevation(*DHAKA, noon) > 80


def test_sun_is_below_the_horizon_at_local_midnight():
    midnight = datetime(2026, 8, 8, 18, 0, tzinfo=timezone.utc)   # 00:00 BST
    assert solar.solar_elevation(*DHAKA, midnight) < -40


def test_sunrise_and_sunset_land_where_published_times_say():
    before_sunrise = datetime(2026, 8, 7, 23, 0, tzinfo=timezone.utc)  # 05:00 BST
    after_sunrise = datetime(2026, 8, 8, 0, 30, tzinfo=timezone.utc)   # 06:30 BST
    before_sunset = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)   # 18:00 BST
    after_sunset = datetime(2026, 8, 8, 13, 30, tzinfo=timezone.utc)   # 19:30 BST
    assert solar.solar_elevation(*DHAKA, before_sunrise) < 0
    assert solar.solar_elevation(*DHAKA, after_sunrise) > 0
    assert solar.solar_elevation(*DHAKA, before_sunset) > 0
    assert solar.solar_elevation(*DHAKA, after_sunset) < 0


def test_december_noon_is_lower_than_august_noon():
    """Seasonal sanity: the sun is much lower in the northern winter."""
    august = solar.solar_elevation(*DHAKA, datetime(2026, 8, 8, 6, tzinfo=timezone.utc))
    december = solar.solar_elevation(*DHAKA, datetime(2026, 12, 8, 6, tzinfo=timezone.utc))
    assert august - december > 25


# ── product switching ─────────────────────────────────────────────────────────

def test_midday_selects_the_visible_product():
    product = products.choose(datetime(2026, 8, 8, 6, tzinfo=timezone.utc))
    assert product.layer == products.VISIBLE_LAYER
    assert not product.is_night
    assert product.overlay_suffix == ""


def test_night_selects_infrared_with_the_heavier_overlay():
    product = products.choose(datetime(2026, 8, 8, 18, tzinfo=timezone.utc))
    assert product.layer == products.INFRARED_LAYER
    assert product.is_night
    assert product.overlay_suffix == "-night"


def test_the_exact_slot_that_returned_a_black_frame_is_classified_as_night():
    """17:30 UTC on this date is the live case from S1: the service returned a
    valid 4.7 KB all-black JPEG for the visible layer."""
    product = products.choose(datetime(2026, 8, 8, 17, 30, tzinfo=timezone.utc))
    assert product.layer == products.INFRARED_LAYER


def test_a_barely_risen_sun_still_counts_as_night():
    """Just above the horizon the visible product is a dim smear, so the
    threshold sits well above sunrise rather than at it."""
    just_up = datetime(2026, 8, 7, 23, 45, tzinfo=timezone.utc)   # ~05:45 BST
    assert 0 < solar.solar_elevation(*DHAKA, just_up) < solar.DAYLIGHT_MIN_ELEVATION
    assert products.choose(just_up).is_night


def test_fallback_is_always_a_usable_product():
    assert products.infrared_fallback().layer == products.INFRARED_LAYER
    assert products.infrared_fallback().is_night


# ── night palette ─────────────────────────────────────────────────────────────

def test_recolor_keeps_bright_cloud_bright_and_darkens_the_background():
    grey = Image.new("L", (2, 1))
    grey.putdata([0, 255])
    out = products.recolor_night(grey)
    dark, bright = out.getpixel((0, 0)), out.getpixel((1, 0))
    assert dark == (9, 18, 34)          # deep navy, not black
    assert bright == (255, 255, 255)    # coldest tops stay white


def test_recolor_is_monotonic_so_cloud_structure_survives():
    grey = Image.new("L", (256, 1))
    grey.putdata(list(range(256)))
    values = list(products.recolor_night(grey).convert("L").getdata())
    assert values == sorted(values)


def test_recolor_tints_the_dark_end_blue_rather_than_neutral():
    """The point of the palette: a night sky, not a greyscale instrument trace."""
    grey = Image.new("L", (1, 1), 40)
    r, g, b = products.recolor_night(grey).getpixel((0, 0))
    assert b > r + 10


def test_recolor_returns_rgb_at_the_original_size():
    out = products.recolor_night(Image.new("L", (17, 5), 128))
    assert out.mode == "RGB"
    assert out.size == (17, 5)
