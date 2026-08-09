"""The fog product: where fog is likely, on the light map.

Fog needs TWO instruments because no single recipe survives the day:

  * **night** — `rgb_fog` (Night Microphysics). Fog and low stratus read
    magenta: R and B strong, G suppressed. Calibrated against real archives:
    the 2026-01-07 dense-fog dawn classified 86.5% of the close frame, a clear
    February night 2.7%.
  * **day** — `rgb_microphysics` (Day Microphysics). The 3.9 µm channel rides
    in G, and small fog droplets reflect it strongly, so fog reads PALE with G
    close to R. Same fog morning at 08:30: 54.8% of the Ganges-plain box; the
    clear morning: 0.0%.

**Why the split is load-bearing:** the night recipe in daylight is garbage —
the clear February *day* classified 65% "fog" at 09:00, pure solar
contamination of the 3.9 µm channel. And the hazard window (05:00–09:00,
winter mornings, highway drivers) straddles sunrise, so gating fog out of
daylight would blind the tile at its peak hour. The switch rides the same
solar-elevation decision the clouds ladder uses.

The paint is a translucent slate blanket on the light map — fog-coloured over
a light stage, distinct from rain's green-blue. Confidence is not graded:
this is an advisory product (কুয়াশার সম্ভাবনা), and the app-side disclaimer
carries the honesty about what a satellite cannot see (ground fog vs low
cloud; anything under high cloud).

Accepted residuals, on the record: thin daytime cirrus can brush the day
classifier's pale test; the terminator band (~sun 8–15°) is marginal for both
recipes and the 12° switch point is inherited from the clouds ladder rather
than derived; and the first real-world validation is December — the archive
built this, winter proves it.
"""

from __future__ import annotations

from datetime import datetime

from PIL import Image

from . import overlays, solar
from .products import DECISION_POINT, Product

FOG_NIGHT = Product("rgb_fog", is_night=False, key="fog")
FOG_DAY = Product("rgb_microphysics", is_night=False, key="fog")

#: The slate blanket. Deliberately fog-coloured — grey with a cool cast — so
#: the tile literally looks like fog lying on the map.
_PAINT = (138, 149, 164)
_ALPHA = 0.55


def ladder(when: datetime) -> list:
    """One rung per side of the terminator. No fallback chain: if the side's
    instrument fails, the product skips this cycle and carry_forward keeps the
    last good assessment."""
    if solar.is_daylight(*DECISION_POINT, when):
        return [FOG_DAY]
    return [FOG_NIGHT]


def is_fog_night(r: int, g: int, b: int) -> bool:
    """Magenta: strong R and B, suppressed G."""
    return r >= 90 and b >= 110 and g <= 0.45 * r and b >= 0.75 * r


def is_fog_day(r: int, g: int, b: int) -> bool:
    """Pale with strong 3.9 µm reflectance (G near R, nothing dark)."""
    return g >= 110 and r >= 110 and g >= 0.75 * r and min(r, g, b) >= 90


def compose(raw: Image.Image, view, night: bool) -> Image.Image:
    """The light base with the detected fog painted on. Labels are added by the
    caller's per-language loop, above the paint, like every light product."""
    frame = raw.convert("RGB")
    base = overlays.load_light_base(view).convert("RGB")
    if frame.size != base.size:
        raise ValueError(f"fog frame {frame.size} does not match base {base.size}")

    classify = is_fog_night if night else is_fog_day
    src = frame.load()
    out = base.copy()
    dst = out.load()
    pr, pg, pb = _PAINT
    a = _ALPHA
    for y in range(frame.height):
        for x in range(frame.width):
            r, g, b = src[x, y]
            if classify(r, g, b):
                br, bg, bb = dst[x, y]
                dst[x, y] = (round(br + (pr - br) * a),
                             round(bg + (pg - bg) * a),
                             round(bb + (pb - bb) * a))
    return out
