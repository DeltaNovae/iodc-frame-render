"""The rain product: where rain is falling, on a light map.

The opposite grammar to the satellite frames (§ 5.1): the data is sparse
colour, so the map is an empty, light stage for it. `h63` — the ground
precipitation-rate product — arrives with its own legible ramp (pale green
light rain → blue heavy) on a transparent background, so unlike storm there is
no palette to invent. The work is the sandwich:

    light base (opaque: sea and land FILL only)
      → h63 rain, alpha-composited
        → lines (coast, borders, divisions)
          → labels (dark text, light halo)

Everything a reader navigates by goes ABOVE the data. Labels always did; the
LINES did not, and a heavy cell over Khulna was painting out the coastline and
the border with it — the marks that say where you are (owner report).

## Validation is deliberately lenient

A bone-dry frame is fully transparent — flat, featureless, tiny — and it is a
*legitimate answer*: "no rain anywhere". The standard gates would reject it and
walk back four slots, and on a dry winter day every slot would fail, leaving
carry_forward serving yesterday's rain as if it were current. Stale rain
presented as fresh is worse than an honest empty map, so the flatness gates are
off and only the structural ones (decodes, right size) remain.

The accepted residual: an upstream *unrendered* slot is indistinguishable from
a genuinely dry one, and would publish as "no rain". The capture time and the
staleness label still govern, and the wide view — mostly ocean — is almost
never truly empty in any season, which bounds how long the mistake can persist
unnoticed.
"""

from __future__ import annotations

from PIL import Image

from . import overlays
from .products import Product

#: `transparent=True` is what makes the sandwich possible: a JPEG request
#: flattens the rain onto black and the base could never show through.
RAIN = Product("h63", is_night=False, key="rain",
               wms_format="image/png", transparent=True, lenient=True)


def compose(rain_rgba: Image.Image, view) -> Image.Image:
    """The base with the rain on it. Labels are added by the caller's per-
    language loop, exactly like every other product's overlay."""
    base = overlays.load_light_base(view).convert("RGB")
    rain = rain_rgba.convert("RGBA")
    if rain.size != base.size:
        # The validator already enforces the frame size; this guards the base.
        raise ValueError(f"rain frame {rain.size} does not match base {base.size}")
    base.paste(rain, (0, 0), rain)
    lines = overlays.load_light_lines(view)
    base.paste(lines, (0, 0), lines)
    return base
