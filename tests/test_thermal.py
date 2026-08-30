"""The thermal product: infrared with nothing painted on it.

This is the only product whose correctness is mostly about what does NOT happen
to it. It is a base layer meant to be drawn under other map layers, and the
moment anything tones it, the grey-to-brightness-temperature correspondence it
exists to carry stops holding — silently, because a toned frame still looks like
a picture of weather.
"""

from PIL import Image

import render
from iodc import products, storm, thermal


# ── the product ───────────────────────────────────────────────────────────────

def test_thermal_is_its_own_product_key():
    assert thermal.THERMAL.key == "thermal"
    assert thermal.THERMAL.key != storm.STORM.key
    assert thermal.THERMAL.key != products.NIGHT.key


def test_thermal_rides_infrared_around_the_clock():
    """The reason it exists rather than reusing `clouds`: a backdrop meant to be
    read as one quantity must mean the same thing at 3 a.m. and at noon, and
    only the infrared channel does. No ladder, no washed-out guard, no
    brightening."""
    assert thermal.THERMAL.layer == products.INFRARED_LAYER
    assert not thermal.THERMAL.guard_washed_out
    assert not thermal.THERMAL.brighten


# ── the toning, which is the whole point ──────────────────────────────────────

def test_thermal_is_published_untoned():
    """🚨 The load-bearing test. Grey in, the SAME grey out.

    `_tone` dispatches on the key before it looks at `is_night`; without that,
    thermal would take the navy night palette — a frame that still looks like
    weather while no longer meaning temperature.
    """
    src = Image.new("L", (4, 4))
    src.putdata([0, 40, 90, 130, 160, 190, 210, 230, 245, 255, 12, 77,
                 100, 140, 180, 220])
    out = render._tone(src, thermal.THERMAL)
    assert list(out.convert("L").getdata()) == list(src.getdata())


def test_thermal_does_not_take_the_night_palette():
    """The specific mis-dispatch this guards. The night palette maps black to a
    deep navy, so a navy-toned thermal frame is detectable at the dark end."""
    black = Image.new("L", (1, 1), 0)
    toned_night = products.recolor_night(black).getpixel((0, 0))
    toned_thermal = render._tone(black, thermal.THERMAL).convert("RGB").getpixel((0, 0))
    assert toned_night != toned_thermal
    assert toned_thermal == (0, 0, 0)


def test_thermal_does_not_take_the_storm_ramp():
    """The reason storm could not simply be reused: its severity colours are
    baked into the pixels, so a reader cannot remove them. A grey level well
    inside the extreme band must stay grey here."""
    hot = Image.new("L", (1, 1), 250)
    assert storm.recolor_storm(hot).getpixel((0, 0)) != (250, 250, 250)
    assert render._tone(hot, thermal.THERMAL).convert("RGB").getpixel((0, 0)) == (250, 250, 250)


def test_brightness_stays_monotonic_in_temperature():
    """Cold is bright in infrared, and readers sample that relationship
    directly. Any toning that reordered or flattened it would break those
    readings without breaking the rendering — nothing downstream could tell."""
    levels = [0, 60, 120, 180, 240]
    out = [render._tone(Image.new("L", (1, 1), v), thermal.THERMAL)
           .convert("L").getpixel((0, 0)) for v in levels]
    assert out == sorted(out)
    assert out[0] < out[-1]


# ── its place in the cycle ────────────────────────────────────────────────────

def test_thermal_takes_the_night_overlay_like_the_other_infrared_products():
    """Grey infrared carries no coastline of its own, so the drawn map is the
    only orientation there is — the same reason storm and fog take it."""
    assert render._overlay_variant(thermal.THERMAL) == "night"


def test_thermal_shares_the_infrared_fetch_rather_than_adding_one():
    """What makes this product nearly free: the cycle's fetch cache is keyed on
    (layer, view, slot), and thermal asks for the very layer storm already
    fetches — so adding it costs no upstream request.
    """
    assert thermal.THERMAL.layer == storm.STORM.layer
