"""The fog product: two instruments, one tile.

Classifier thresholds were calibrated against archived ground truth
(2026-01-07 dense fog vs 2026-02-10 clear — § 8.11 P3); these tests pin the
behaviour those measurements bought, using the measured colours themselves.
"""

from datetime import datetime, timezone

from PIL import Image

from iodc import fog, overlays, solar
from iodc.products import DECISION_POINT
from iodc.views import CLOSE

# Colours measured from the calibration frames.
NIGHT_FOG = (155, 22, 202)        # the January magenta sheet
NIGHT_CLEAR = (50, 15, 120)       # clear winter night ground
NIGHT_HIGH_CLOUD = (200, 40, 60)  # cold tops read red in night microphysics
DAY_FOG = (160, 150, 190)         # pale, G near R — strong 3.9 um reflectance
DAY_CLEAR = (94, 11, 182)         # the purple clear plain
DAY_ICE = (120, 60, 210)          # bright but G-poor — ice, not droplets


# ── the two classifiers ───────────────────────────────────────────────────────

def test_night_classifier_matches_the_calibration():
    assert fog.is_fog_night(*NIGHT_FOG)
    assert not fog.is_fog_night(*NIGHT_CLEAR)
    assert not fog.is_fog_night(*NIGHT_HIGH_CLOUD)


def test_day_classifier_matches_the_calibration():
    assert fog.is_fog_day(*DAY_FOG)
    assert not fog.is_fog_day(*DAY_CLEAR)
    assert not fog.is_fog_day(*DAY_ICE)


def test_the_recipes_are_not_interchangeable():
    """The reason two instruments exist: each side's fog colour fails the other
    side's test. Feeding a frame to the wrong classifier finds nothing, which
    is the safe failure."""
    assert not fog.is_fog_day(*NIGHT_FOG)
    assert not fog.is_fog_night(*DAY_FOG)


# ── the ladder ────────────────────────────────────────────────────────────────

def test_night_rides_night_microphysics_and_day_rides_day():
    night = datetime(2026, 1, 7, 1, 30, tzinfo=timezone.utc)   # 07:30 BST, dark
    noon = datetime(2026, 1, 7, 6, 0, tzinfo=timezone.utc)     # 12:00 BST
    assert not solar.is_daylight(*DECISION_POINT, night)
    assert fog.ladder(night) == [fog.FOG_NIGHT]
    assert fog.ladder(noon) == [fog.FOG_DAY]


def test_both_rungs_publish_under_the_one_fog_key():
    """One tile to a user, whatever instrument produced it."""
    assert fog.FOG_NIGHT.key == fog.FOG_DAY.key == "fog"


def test_fog_takes_the_light_labels_not_the_night_overlay():
    """The base is the light map; is_night=False keeps the meta source honest
    and the label routing on the light set."""
    assert not fog.FOG_NIGHT.is_night
    assert not fog.FOG_DAY.is_night


# ── the paint ─────────────────────────────────────────────────────────────────

def frame_with_fog_patch(colour, size=CLOSE.size, patch=(100, 100, 140, 140)):
    img = Image.new("RGB", size, NIGHT_CLEAR)
    for x in range(patch[0], patch[2]):
        for y in range(patch[1], patch[3]):
            img.putpixel((x, y), colour)
    return img


def test_detected_fog_is_painted_and_clear_ground_is_not():
    out = fog.compose(frame_with_fog_patch(NIGHT_FOG), CLOSE, night=True)
    base = overlays.load_light_base(CLOSE).convert("RGB")
    assert out.getpixel((120, 120)) != base.getpixel((120, 120))   # painted
    assert out.getpixel((300, 300)) == base.getpixel((300, 300))   # untouched


def test_the_paint_is_translucent_not_a_flat_fill():
    """The base must survive under the blanket — two base pixels that differ
    (say a border line and the land beside it) must still differ after paint,
    so orientation remains legible through fog."""
    base = overlays.load_light_base(CLOSE).convert("RGB")
    pairs = [
        ((x, y), (x + 1, y))
        for y in range(100, CLOSE.height - 1, 25)
        for x in range(100, CLOSE.width - 1, 25)
        if base.getpixel((x, y)) != base.getpixel((x + 1, y))
    ]
    assert pairs, "no adjacent differing base pixels found — sampling bug"

    out = fog.compose(Image.new("RGB", CLOSE.size, NIGHT_FOG), CLOSE, night=True)
    a, b = pairs[0]
    assert out.getpixel(a) != out.getpixel(b)


def test_a_clear_frame_publishes_the_bare_map():
    out = fog.compose(Image.new("RGB", CLOSE.size, NIGHT_CLEAR), CLOSE, night=True)
    base = overlays.load_light_base(CLOSE).convert("RGB")
    assert list(out.getdata()) == list(base.getdata())


def test_compose_refuses_a_mismatched_frame():
    import pytest
    with pytest.raises(ValueError):
        fog.compose(Image.new("RGB", (100, 100)), CLOSE, night=True)
