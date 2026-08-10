"""The fog product: two instruments, a continuous ramp, three states.

Thresholds come from matched-hour archive comparison (fog night 2026-01-07 vs
clear night 2026-02-10; fog day vs clear day at the same hours) — see the
module docstring for the measured tables. These tests pin the behaviour those
measurements bought.
"""

import pytest
from PIL import Image

from iodc import fog, overlays, solar
from iodc.products import DECISION_POINT
from iodc.views import CLOSE

# Measured G values from the calibration frames (Ganges plain).
CLEAR_NIGHT_G = 105      # clear ground, flat all night
THIN_FOG_G = 133         # fog forming, 01:00
DENSE_FOG_G = 169        # fog thickened, 04:00
CLEAR_DAY_G = 40         # clear day plain
FOG_DAY_G = 130          # fog day plain


def night_px(g):
    """Night microphysics keeps R and B high everywhere; G is the signal."""
    return (182, g, 200)


def day_px(g):
    return (125, g, 172)


# ── the product ───────────────────────────────────────────────────────────────

def test_both_rungs_publish_under_the_one_fog_key():
    assert fog.FOG_NIGHT.key == fog.FOG_DAY.key == "fog"


def test_night_rides_night_microphysics_and_day_rides_day():
    from datetime import datetime, timezone
    night = datetime(2026, 1, 6, 22, 0, tzinfo=timezone.utc)   # 04:00 BST, dark
    noon = datetime(2026, 1, 7, 6, 0, tzinfo=timezone.utc)     # 12:00 BST
    assert not solar.is_daylight(*DECISION_POINT, night)
    assert fog.ladder(night) == [fog.FOG_NIGHT]
    assert fog.ladder(noon) == [fog.FOG_DAY]


# ── the signature is G, and it is CONTINUOUS ──────────────────────────────────

def test_clear_ground_scores_nothing_on_either_side():
    assert fog.fog_intensity(*night_px(CLEAR_NIGHT_G), night=True) == 0.0
    assert fog.fog_intensity(*day_px(CLEAR_DAY_G), night=False) == 0.0


def test_dense_fog_scores_near_the_top():
    assert fog.fog_intensity(*night_px(DENSE_FOG_G), night=True) > 0.7
    assert fog.fog_intensity(*day_px(FOG_DAY_G), night=False) > 0.4


def test_intensity_rises_with_density_rather_than_flipping():
    """The whole point of the rebuild: thin fog and dense fog must be
    distinguishable, because that is what tells a driver damp from blind."""
    thin = fog.fog_intensity(*night_px(THIN_FOG_G), night=True)
    dense = fog.fog_intensity(*night_px(DENSE_FOG_G), night=True)
    assert 0.0 < thin < dense <= 1.0


def test_intensity_is_monotonic_in_g():
    values = [fog.fog_intensity(*night_px(g), night=True) for g in range(90, 200, 5)]
    assert values == sorted(values)


def test_the_two_sides_use_different_scales():
    """Same discriminator, different instrument response — a day-side G of 100
    means fog, a night-side G of 100 means clear ground."""
    assert fog.fog_intensity(*day_px(100), night=False) > 0
    assert fog.fog_intensity(*night_px(100), night=True) == 0.0


# ── the regression that started the rebuild ───────────────────────────────────

def test_a_sunlit_magenta_frame_is_not_read_as_fog():
    """The original bug: at sunrise the 3.9 um channel goes magenta (G near
    zero) and the old classifier called that dense fog. G near zero is now
    exactly what clear reads as."""
    sunrise_magenta = (180, 9, 202)
    assert fog.fog_intensity(*sunrise_magenta, night=True) == 0.0


# ── the third state ───────────────────────────────────────────────────────────

def test_thick_high_cloud_is_flagged_obscured():
    """34% of an August night frame, against 4-5% on the calibration nights."""
    assert fog.is_obscured(200, 60, 150)


def test_clear_and_foggy_pixels_are_not_obscured():
    assert not fog.is_obscured(*night_px(CLEAR_NIGHT_G))
    assert not fog.is_obscured(*night_px(DENSE_FOG_G))


# ── the paint ─────────────────────────────────────────────────────────────────

def solid(colour):
    return Image.new("RGB", CLOSE.size, colour)


def test_dense_fog_paints_darker_than_thin_fog():
    thin = fog.compose(solid(night_px(THIN_FOG_G)), CLOSE, night=True)
    dense = fog.compose(solid(night_px(DENSE_FOG_G)), CLOSE, night=True)
    assert sum(dense.getpixel((320, 320))) < sum(thin.getpixel((320, 320)))


def test_a_clear_frame_publishes_the_bare_map():
    out = fog.compose(solid(night_px(CLEAR_NIGHT_G)), CLOSE, night=True)
    base = overlays.load_light_base(CLOSE).convert("RGB")
    assert list(out.getdata()) == list(base.getdata())


def test_obscured_sky_is_washed_but_stays_quieter_than_fog():
    """It means "no information", not "hazard" — it must never out-shout fog."""
    base = overlays.load_light_base(CLOSE).convert("RGB")
    obscured = fog.compose(solid((200, 60, 150)), CLOSE, night=True)
    dense = fog.compose(solid(night_px(DENSE_FOG_G)), CLOSE, night=True)
    at = (320, 320)
    assert obscured.getpixel(at) != base.getpixel(at)          # something drawn
    # ...but closer to the base than dense fog is.
    delta = lambda im: sum(abs(im.getpixel(at)[i] - base.getpixel(at)[i]) for i in range(3))
    assert delta(obscured) < delta(dense)


def test_fog_wins_over_obscured_where_both_could_apply():
    """Real fog under thin high cloud must read as fog, not "cannot tell"."""
    both = (200, DENSE_FOG_G, 150)      # warm AND high G
    assert fog.is_obscured(*both)
    assert fog.fog_intensity(*both, night=True) > 0.5
    base = overlays.load_light_base(CLOSE).convert("RGB")
    out = fog.compose(solid(both), CLOSE, night=True)
    dense = fog.compose(solid(night_px(DENSE_FOG_G)), CLOSE, night=True)
    assert out.getpixel((320, 320)) == dense.getpixel((320, 320))


def test_the_paint_is_translucent_so_orientation_survives():
    base = overlays.load_light_base(CLOSE).convert("RGB")
    pairs = [((x, y), (x + 1, y))
             for y in range(100, CLOSE.height - 1, 25)
             for x in range(100, CLOSE.width - 1, 25)
             if base.getpixel((x, y)) != base.getpixel((x + 1, y))]
    assert pairs, "no adjacent differing base pixels found — sampling bug"
    out = fog.compose(solid(night_px(DENSE_FOG_G)), CLOSE, night=True)
    a, b = pairs[0]
    assert out.getpixel(a) != out.getpixel(b)


def test_compose_refuses_a_mismatched_frame():
    with pytest.raises(ValueError):
        fog.compose(Image.new("RGB", (100, 100)), CLOSE, night=True)
