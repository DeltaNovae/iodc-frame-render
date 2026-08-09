"""Which product to show, and how each one is toned.

No single layer works around the clock, so a cycle walks a short **ladder** and
publishes the first rung that survives validation:

  1. `rgb_naturalenhncd` — colour, and the one that reads as a photograph:
     green land, blue sea. Solar-zenith corrected, which is precisely why it
     cannot be the only daytime rung.
  2. `vis006` — the raw visible channel. Greyscale and dim, but it carries no
     sun-angle correction and therefore *cannot* blow out. Brightened here.
  3. `ir108` — infrared, the only thing that sees in the dark. Recoloured so a
     night frame looks like a night sky rather than an instrument trace.

**Why rung 2 exists** (owner-reported 2026-08-09; plan § 8.10): rung 1 divides
out the solar zenith angle, and near the horizon that divisor tends to zero. At
07:00 local, half the frame clipped to pure white — a well-formed picture of
nothing. Raising the daylight threshold could not fix it, because Meteosat-9
sits west of Bangladesh: mornings are forward-scatter and evenings are not, so
17:00 is clean at the very sun angle that ruins 07:00. The ladder therefore
decides by *measuring each frame*, which is why the fix lives in a validation
ceiling rather than in the clock.

Every rung is a preference, never a promise: whatever fails, the next is tried,
and only exhausting the ladder fails the cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PIL import Image

from . import solar

VISIBLE_LAYER = "rgb_naturalenhncd"
LOW_SUN_LAYER = "vis006"
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
    #: Guard against the washed-out failure. Only the solar-zenith-corrected
    #: colour product can fail this way; the others legitimately run bright over
    #: heavy convection and must not be judged by it.
    guard_washed_out: bool = False
    #: Brighten before publishing. The raw visible channel is honest but dim.
    brighten: bool = False

    @property
    def overlay_suffix(self) -> str:
        """Night overlays carry heavier strokes — infrared has no coastline of
        its own, so the drawn map is the only orientation there is."""
        return "-night" if self.is_night else ""


COLOUR_DAY = Product(VISIBLE_LAYER, is_night=False, guard_washed_out=True)
LOW_SUN_DAY = Product(LOW_SUN_LAYER, is_night=False, brighten=True)
NIGHT = Product(INFRARED_LAYER, is_night=True)


def ladder(when: datetime) -> list:
    """The products to try, best first.

    In daylight the greyscale rung sits between colour and infrared, so a
    low-sun frame degrades to a duller *daytime* picture rather than jumping
    straight to something that looks like night at breakfast.

    After dark the visible rungs are omitted rather than attempted-and-rejected:
    they would cost two pointless round trips per view per cycle, and their
    failure carries no information at 2 a.m.
    """
    if solar.is_daylight(*DECISION_POINT, when):
        return [COLOUR_DAY, LOW_SUN_DAY, NIGHT]
    return [NIGHT]


def choose(when: datetime) -> Product:
    """The preferred product — the first rung. Kept for callers that only need
    to name the product, such as logging before a fetch is attempted."""
    return ladder(when)[0]


def infrared_fallback() -> Product:
    """The last rung, by name, for callers forcing the night branch."""
    return NIGHT


# ── low-sun toning ────────────────────────────────────────────────────────────

#: Where the stretch puts the frame's brightest real content. Deliberately shy
#: of 255: the whole point of this rung is that nothing reaches pure white.
BRIGHTEN_TARGET = 245

#: Percentile treated as "brightest real content". A handful of specular pixels
#: must not decide the exposure for the whole frame.
BRIGHTEN_PERCENTILE = 99.5

#: Ceiling on the gain. A frame dark enough to need more than this is night
#: arriving early, and belongs on the infrared rung, not stretched into noise.
BRIGHTEN_MAX_GAIN = 4.0


def brighten(image: Image.Image) -> Image.Image:
    """Lift a dim raw-visible frame to a readable exposure.

    A linear gain, not a curve: the raw channel's tonal relationships are
    physically meaningful and worth preserving. The gain is set from a high
    percentile rather than the maximum so that a few hot pixels cannot drag the
    rest of the frame dark, and it is capped so the result can never be pushed
    into the very clipping this rung exists to avoid.
    """
    histogram = image.convert("L").histogram()
    total = sum(histogram)
    if not total:
        return image

    cumulative, high = 0, 255
    for value, count in enumerate(histogram):
        cumulative += count
        if cumulative >= total * BRIGHTEN_PERCENTILE / 100:
            high = max(value, 1)
            break

    gain = min(BRIGHTEN_TARGET / high, BRIGHTEN_MAX_GAIN)
    if gain <= 1.0:
        return image
    return image.point(lambda v: min(255, int(v * gain)))


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
