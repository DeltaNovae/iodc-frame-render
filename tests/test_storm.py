"""The storm product: bands, colouring, and its place beside clouds.

The bands were calibrated against archived events (Cyclone Remal, a
kalbaishakhi day, an ordinary monsoon afternoon); these tests pin
the *behaviour* those numbers bought, not the numbers themselves.
"""

from PIL import Image

from iodc import products, storm


def lut_colour(v: int) -> tuple:
    """The published colour for one grey level, via the real code path."""
    img = Image.new("L", (1, 1), v)
    return storm.recolor_storm(img).getpixel((0, 0))


# ── the product ───────────────────────────────────────────────────────────────

def test_storm_is_its_own_product_key():
    assert storm.STORM.key == "storm"
    assert storm.STORM.key != products.NIGHT.key


def test_storm_rides_infrared_so_it_works_around_the_clock():
    """The whole reason storm needs no day/night ladder and no washed-out
    guard: ir108 sees in the dark and cannot blow out."""
    assert storm.STORM.layer == products.INFRARED_LAYER
    assert not storm.STORM.guard_washed_out
    assert not storm.STORM.brighten


def test_storm_takes_the_heavy_night_overlay():
    """The base is grey infrared with no coastline of its own — the drawn map
    is the only orientation there is, same as the night product.

    Asserted on `is_night`, which is what the cycle actually reads when it picks
    an overlay. This used to assert a derived `overlay_suffix` property no
    production code consulted, so it pinned a symbol rather than the behaviour."""
    assert storm.STORM.is_night


# ── the bands ─────────────────────────────────────────────────────────────────

def test_ordinary_cloud_stays_grey():
    """Grey is weather; colour is the alert. Below the strong band the frame
    must be exactly the infrared image, untinted."""
    for v in (0, 60, 130, 200, storm.STRONG - 1):
        assert lut_colour(v) == (v, v, v), v


def test_the_strong_band_reads_blue():
    r, g, b = lut_colour((storm.STRONG + storm.SEVERE) // 2)
    assert b > r and b > g


def test_the_severe_band_reads_yellow():
    r, g, b = lut_colour((storm.SEVERE + storm.EXTREME) // 2)
    assert r > b and g > b


def test_the_extreme_band_reads_red():
    r, g, b = lut_colour(250)
    assert r > g + 60 and r > b + 60


def test_band_edges_are_exact():
    """Off-by-one at a threshold would silently move the severity line."""
    assert lut_colour(storm.STRONG - 1) == tuple([storm.STRONG - 1] * 3)
    r, g, b = lut_colour(storm.STRONG)
    assert b > r                                  # first blue pixel
    r, g, b = lut_colour(storm.SEVERE)
    assert r > b and g > b                        # first yellow pixel
    r, g, b = lut_colour(storm.EXTREME)
    assert r > g + 60                             # first red pixel


def test_bands_are_ordered_and_inside_the_ir_range():
    assert 0 < storm.STRONG < storm.SEVERE < storm.EXTREME <= 255


def test_texture_survives_inside_a_band():
    """The grey base is blended, not replaced: two different grey levels in the
    same band must stay distinguishable, or cell structure flattens into a
    poster blob."""
    a = lut_colour(storm.SEVERE + 1)
    b = lut_colour(storm.EXTREME - 1)
    assert a != b


def test_recolor_accepts_rgb_input_and_keeps_size():
    out = storm.recolor_storm(Image.new("RGB", (30, 20), (100, 100, 100)))
    assert out.mode == "RGB"
    assert out.size == (30, 20)


# ── what the calibration promised ─────────────────────────────────────────────

def test_a_quiet_frame_shows_no_colour_at_all():
    """A clear or ordinarily cloudy frame — nothing at or above the strong
    band — publishes as pure greyscale."""
    img = Image.new("L", (40, 40))
    img.putdata([min(210, (x * 7) % 215) for x in range(1600)])
    out = storm.recolor_storm(img)
    r, g, b = out.split()
    assert list(r.getdata()) == list(g.getdata()) == list(b.getdata())


def test_red_is_reserved_for_the_extreme_tail():
    """The monsoon lesson: most coloured pixels on an ordinary convective day
    sit in the blue band. Red appearing below EXTREME would recreate the
    alarm-fatigue failure the graded ramp exists to avoid."""
    for v in range(storm.STRONG, storm.EXTREME):
        r, g, b = lut_colour(v)
        assert not (r > g + 60 and r > b + 60), f"red leaked down to {v}"
