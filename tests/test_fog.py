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
    assert fog.ladder(night) == [fog.FOG_NIGHT]
    assert fog.ladder(noon) == [fog.FOG_DAY]


def test_fog_declines_to_answer_in_the_blind_band_around_sunrise():
    """Measured: at sun +2.8 deg the night recipe has gone blind (2.1%) while
    the day recipe still calls a CLEAR morning 92% fog. Neither may speak, so
    the product skips and carry_forward keeps the last good assessment."""
    from datetime import datetime, timezone
    band = datetime(2026, 1, 7, 1, 0, tzinfo=timezone.utc)     # 07:00 BST
    elevation = solar.solar_elevation(*DECISION_POINT, band)
    assert fog.FOG_NIGHT_MAX_ELEVATION < elevation < fog.FOG_DAY_MIN_ELEVATION
    assert fog.ladder(band) == []


def test_fog_switches_earlier_than_the_clouds_ladder():
    """The clouds ladder's 12 deg marks where the VISIBLE product works; fog's
    night recipe dies at sunrise. Binding them together blinded the tile
    across the peak hazard hour."""
    assert fog.FOG_DAY_MIN_ELEVATION < solar.DAYLIGHT_MIN_ELEVATION


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
    assert fog.fog_intensity(125, 100, 200, night=False) > 0
    assert fog.fog_intensity(182, 100, 200, night=True) == 0.0


def test_the_warmth_floor_is_per_instrument():
    """The two RGBs scale temperature differently (roughly 243-293 K against
    203-323 K); one number for both let the day side admit cloud kilometres up."""
    assert fog.MIN_WARMTH_NIGHT and fog.MIN_WARMTH_DAY


# ── the regression that started the rebuild ───────────────────────────────────

def test_a_sunlit_magenta_frame_is_not_read_as_fog():
    """The original bug: at sunrise the 3.9 um channel goes magenta (G near
    zero) and the old classifier called that dense fog. G near zero is now
    exactly what clear reads as."""
    sunrise_magenta = (180, 9, 202)
    assert fog.fog_intensity(*sunrise_magenta, night=True) == 0.0


def test_deep_convection_is_not_fog_however_strong_its_cloud_signal():
    """The owner-caught bug: August thunderstorm tops saturate G (248) but sit
    at B=2 — freezing, kilometres up. Fog is on the ground and therefore warm
    (B=200 on the January event)."""
    convection = (143, 248, 3)
    assert fog.fog_intensity(*convection, night=True) == 0.0
    # ...and it routes to the honest answer instead.
    assert fog.is_obscured(*convection)


def test_warm_cloud_at_the_same_g_still_reads_as_fog():
    """The discriminator is temperature, not the cloud signal itself."""
    warm = (143, 248, 200)
    assert fog.fog_intensity(*warm, night=True) > 0.9


# ── the third state ───────────────────────────────────────────────────────────

def test_thick_high_cloud_is_flagged_obscured():
    """34% of an August night frame, against 4-5% on the calibration nights."""
    assert fog.is_obscured(200, 60, 150)


def test_clear_and_foggy_pixels_are_not_obscured():
    assert not fog.is_obscured(*night_px(CLEAR_NIGHT_G))
    assert not fog.is_obscured(*night_px(DENSE_FOG_G))


# ── the render: sky in grey, fog in cyan ─────────────────────────────────────

def solid(colour):
    return Image.new("RGB", CLOSE.size, colour)


def test_dense_fog_reads_stronger_than_thin_fog():
    thin = fog.compose(solid(night_px(THIN_FOG_G)), CLOSE, night=True)
    dense = fog.compose(solid(night_px(DENSE_FOG_G)), CLOSE, night=True)
    at = (320, 320)
    # Deeper cyan: less red, and further from the grey diagonal.
    assert dense.getpixel(at)[0] < thin.getpixel(at)[0]


def test_fog_is_cyan_and_cannot_be_confused_with_cloud_grey():
    out = fog.compose(solid(night_px(DENSE_FOG_G)), CLOSE, night=True)
    r, g, b = out.getpixel((320, 320))
    assert b > r + 40 and g > r + 40          # unmistakably cyan
    assert not (r == g == b)                  # never grey


def test_a_clear_frame_still_shows_the_sky_rather_than_nothing():
    """The whole point of Option B: the evidence is always on screen. A clear
    frame is a grey sky, not a blank map."""
    out = fog.compose(solid(night_px(CLEAR_NIGHT_G)), CLOSE, night=True)
    r, g, b = out.getpixel((320, 320))
    assert r == g == b                        # grey: no fog claimed
    assert 0 < r < 255                        # but something is drawn


def test_cold_cloud_reads_bright_and_warm_ground_dark():
    """Infrared convention, the same one the storm tile uses — so a
    thunderstorm looks like a thunderstorm whatever the classifier decides."""
    cold = fog.context_tone(5)
    warm = fog.context_tone(200)
    assert cold > warm


def test_context_tone_is_monotonic_and_bounded():
    tones = [fog.context_tone(b) for b in range(0, 256, 8)]
    assert tones == sorted(tones, reverse=True)
    assert 0 <= min(tones) and max(tones) <= 255
