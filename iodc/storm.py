"""The storm product: where convection is strong, told in colour.

Selective colour above a severity threshold on the grey `ir108` base — grey is
weather, colour is the alert, no legend. In the rendered infrared, grey level
maps brightness temperature monotonically, so brighter = colder = taller cloud
tops, and a grey threshold stands in for a cloud-top-temperature threshold.

## The bands, and where they came from (calibrated 2026-08-09)

Measured against three archived situations rather than picked in the abstract:

    event                              >=220   >=235   >=245
    Cyclone Remal, landfall eve        14.1%    9.4%    7.0%   core burns 245+
    kalbaishakhi cells, clear day       1.1%    0.8%    0.4%   cells light, sky stays grey
    ordinary wet monsoon afternoon     10.1%    4.6%    1.3%   mostly blue, red is rare

## Why graded and not single-red

The monsoon row is the reason. Deep convection is a *most days* event here in
June–September, and a single red-above-220 painted ~10% of an ordinary
afternoon red — a tile that alarms daily teaches users to ignore red, which is
alarm fatigue, the one failure a cyclone-season product cannot afford. The
graded ramp shows that same day as mostly blue with small yellow/red cores, so
red keeps meaning something.

## Why blue → yellow → red, not the sketched blue → green → red

Green over grey cloud reads as land showing through. Yellow→red is the warning
escalation this audience already knows.
"""

from __future__ import annotations

from PIL import Image

from .products import INFRARED_LAYER, Product

#: The storm tile's product. `is_night=True` is deliberate and slightly subtle:
#: the base is always infrared, so the frame needs the night overlay's heavier
#: strokes (IR has no coastline of its own), and meta's diagnostic `source`
#: honestly reports the IR origin. The colouring below is what makes it a storm
#: frame rather than the night-sky one.
STORM = Product(INFRARED_LAYER, is_night=True, key="storm")

# Severity bands in ir108 grey levels (higher = colder = taller).
STRONG = 220     # organized deep convection — blue
SEVERE = 235     # severe cells — yellow
EXTREME = 245    # extreme tops: cyclone cores, overshooting tops — red

_BLUE = (40, 90, 220)
_YELLOW = (235, 180, 30)
_RED = (220, 30, 30)


def _blend(v: int, colour: tuple, t: float) -> tuple:
    """Mix grey level `v` towards `colour`; the base grey keeps texture alive
    inside the colour, so cell structure still reads."""
    return tuple(round(v + (c - v) * t) for c in colour)


def _entry(v: int) -> tuple:
    if v >= EXTREME:
        return _blend(v, _RED, 0.85)
    if v >= SEVERE:
        return _blend(v, _YELLOW, 0.75)
    if v >= STRONG:
        # Opacity ramps inside the band so the transition from grey is not a
        # hard edge cutting across smooth cloud.
        t = 0.40 + 0.15 * (v - STRONG) / (SEVERE - STRONG)
        return _blend(v, _BLUE, t)
    return (v, v, v)


def _build_lut() -> list:
    rs, gs, bs = [], [], []
    for v in range(256):
        r, g, b = _entry(v)
        rs.append(r)
        gs.append(g)
        bs.append(b)
    return rs + gs + bs


_LUT = _build_lut()


def recolor_storm(image: Image.Image) -> Image.Image:
    """Map a greyscale infrared frame through the storm bands."""
    return image.convert("L").convert("RGB").point(_LUT)
