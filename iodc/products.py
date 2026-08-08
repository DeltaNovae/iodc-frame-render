"""Which product to show, and how the night one is coloured.

There is no single layer that works around the clock: the visible-light product
is black at night, and the infrared one is a grey data readout that looks
nothing like a sky. So the published frame switches between them on the sun's
elevation, and the infrared half is recoloured so the two halves of the day
belong to the same picture.

The switch is a *preference*, never a promise — the caller falls back to
infrared whenever the visible product fails to validate, so a mistimed
threshold or an unrendered slot degrades to a working frame instead of a
broken one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PIL import Image

from . import solar

VISIBLE_LAYER = "rgb_naturalenhncd"
INFRARED_LAYER = "ir108"

# Where the day/night decision is made. The wide frame spans ~20° of longitude,
# so near dawn and dusk the terminator crosses it and one edge disagrees with
# the other; judging at the centre keeps the choice stable and is the accepted
# v1 simplification (§ 6.1 Q6a).
DECISION_POINT = (23.8103, 90.4125)


@dataclass(frozen=True)
class Product:
    layer: str
    is_night: bool

    @property
    def overlay_suffix(self) -> str:
        """Night overlays carry heavier strokes — infrared has no coastline of
        its own, so the drawn map is the only orientation there is."""
        return "-night" if self.is_night else ""


def choose(when: datetime) -> Product:
    if solar.is_daylight(*DECISION_POINT, when):
        return Product(VISIBLE_LAYER, is_night=False)
    return Product(INFRARED_LAYER, is_night=True)


def infrared_fallback() -> Product:
    """Used when the visible product was preferred but did not survive
    validation — a dim slot, an unrendered one, or a threshold set a shade too
    generously."""
    return Product(INFRARED_LAYER, is_night=True)


# ── night palette ─────────────────────────────────────────────────────────────
# Infrared arrives inverted (cold cloud tops bright, warm surface dark), which
# already reads like cloud. Recolouring keeps that reading but moves the dark
# end towards deep navy so the frame looks like a night sky rather than a
# greyscale instrument trace, and lands closer to the daytime product's palette
# so the switch at dusk is not jarring.
_STOPS = [
    (0,   (9, 18, 34)),       # warm surface — deep navy, near black
    (70,  (28, 46, 72)),      # faint low cloud emerging from the dark
    (130, (108, 128, 154)),   # mid-level cloud — cool blue-grey
    (190, (198, 210, 226)),   # thick cloud
    (255, (255, 255, 255)),   # coldest, tallest tops — white
]


def _build_lut() -> list:
    lut_r, lut_g, lut_b = [], [], []
    for value in range(256):
        for i in range(len(_STOPS) - 1):
            low, high = _STOPS[i], _STOPS[i + 1]
            if low[0] <= value <= high[0]:
                span = high[0] - low[0]
                t = (value - low[0]) / span if span else 0.0
                lut_r.append(round(low[1][0] + t * (high[1][0] - low[1][0])))
                lut_g.append(round(low[1][1] + t * (high[1][1] - low[1][1])))
                lut_b.append(round(low[1][2] + t * (high[1][2] - low[1][2])))
                break
    return lut_r + lut_g + lut_b


_LUT = _build_lut()


def recolor_night(image: Image.Image) -> Image.Image:
    """Map a greyscale infrared frame through the night palette."""
    return image.convert("L").convert("RGB").point(_LUT)
