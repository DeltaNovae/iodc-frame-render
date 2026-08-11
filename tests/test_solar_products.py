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
    product = products.ladder(datetime(2026, 8, 8, 6, tzinfo=timezone.utc))[0]
    assert product.layer == products.VISIBLE_LAYER
    assert not product.is_night


def test_night_selects_infrared():
    product = products.ladder(datetime(2026, 8, 8, 18, tzinfo=timezone.utc))[0]
    assert product.layer == products.INFRARED_LAYER
    assert product.is_night


def test_the_exact_slot_that_returned_a_black_frame_is_classified_as_night():
    """17:30 UTC on this date is the live case from S1: the service returned a
    valid 4.7 KB all-black JPEG for the visible layer."""
    product = products.ladder(datetime(2026, 8, 8, 17, 30, tzinfo=timezone.utc))[0]
    assert product.layer == products.INFRARED_LAYER


def test_a_barely_risen_sun_still_counts_as_night():
    """Just above the horizon the visible product is a dim smear, so the
    threshold sits well above sunrise rather than at it."""
    just_up = datetime(2026, 8, 7, 23, 45, tzinfo=timezone.utc)   # ~05:45 BST
    assert 0 < solar.solar_elevation(*DHAKA, just_up) < solar.DAYLIGHT_MIN_ELEVATION
    assert products.ladder(just_up)[0].is_night


def test_the_last_rung_is_always_a_usable_product():
    assert products.NIGHT.layer == products.INFRARED_LAYER
    assert products.NIGHT.is_night


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


# ── the product ladder ────────────────────────────────────────────────────────

def test_daylight_offers_colour_then_raw_visible_then_infrared():
    """The middle rung is the whole point: a low-sun frame degrades to a duller
    *daytime* picture rather than to something that looks like night."""
    rungs = products.ladder(datetime(2026, 8, 8, 6, tzinfo=timezone.utc))
    assert [rung.layer for rung in rungs] == [
        products.VISIBLE_LAYER, products.LOW_SUN_LAYER, products.INFRARED_LAYER
    ]


def test_night_offers_infrared_alone():
    """No point paying two round trips per view to be told it is dark."""
    rungs = products.ladder(datetime(2026, 8, 8, 18, tzinfo=timezone.utc))
    assert [rung.layer for rung in rungs] == [products.INFRARED_LAYER]


def test_only_the_colour_rung_is_guarded_against_washing_out():
    """Infrared and raw visible legitimately run bright over heavy convection;
    guarding them would throw good frames away."""
    day, low_sun, night = products.ladder(
        datetime(2026, 8, 8, 6, tzinfo=timezone.utc))
    assert day.guard_washed_out
    assert not low_sun.guard_washed_out
    assert not night.guard_washed_out


def test_the_low_sun_rung_is_a_daytime_frame():
    _, low_sun, _ = products.ladder(datetime(2026, 8, 8, 6, tzinfo=timezone.utc))
    assert not low_sun.is_night
    assert low_sun.brighten


def test_only_the_low_sun_rung_is_brightened():
    day, low_sun, night = products.ladder(
        datetime(2026, 8, 8, 6, tzinfo=timezone.utc))
    assert [day.brighten, low_sun.brighten, night.brighten] == [False, True, False]


# ── low-sun brightening ───────────────────────────────────────────────────────

def dim_ramp(high: int, size=(256, 1)) -> Image.Image:
    """A greyscale ramp topping out at `high` — a dim frame with structure."""
    img = Image.new("L", size)
    img.putdata([round(i * high / (size[0] - 1)) for i in range(size[0])])
    return img.convert("RGB")


def test_brighten_lifts_a_dim_frame_to_a_readable_exposure():
    """The measured 07:00 raw-visible frame peaks near 100 of 255."""
    out = products.brighten(dim_ramp(100))
    assert max(out.convert("L").getdata()) == pytest.approx(245, abs=3)


def test_brighten_never_creates_the_clipping_it_exists_to_avoid():
    for high in (40, 100, 180, 240):
        out = products.brighten(dim_ramp(high))
        assert max(out.convert("L").getdata()) < 255


def test_brighten_caps_its_gain_so_near_darkness_is_not_amplified_into_noise():
    """A frame this dark is night arriving early; it belongs on the infrared
    rung, not stretched by 8x."""
    out = products.brighten(dim_ramp(30))
    assert max(out.convert("L").getdata()) == pytest.approx(
        30 * products.BRIGHTEN_MAX_GAIN, abs=3)


def test_brighten_leaves_an_already_bright_frame_alone():
    out = products.brighten(dim_ramp(252))
    assert list(out.getdata()) == list(dim_ramp(252).getdata())


def test_brighten_is_monotonic_so_cloud_structure_survives():
    values = list(products.brighten(dim_ramp(100)).convert("L").getdata())
    assert values == sorted(values)


def test_brighten_keeps_size_and_mode():
    out = products.brighten(dim_ramp(90, size=(64, 8)))
    assert out.size == (64, 8)
    assert out.mode == "RGB"
