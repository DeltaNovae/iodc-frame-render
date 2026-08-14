"""The rain product: where rain is falling, on a light map.

The opposite grammar to the satellite frames: the data is sparse
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

## The loop frame stops at the data

`compose` builds the whole sandwich for the full and thumbnail sizes. The loop
frame stops after the rain (`compose_bare`), because the two navigation layers
above it — lines *and* labels — are shipped once as a separate overlay and
composited by the reader instead of being baked into twelve frames and then
destroyed by the downscale to the loop size. Rain is the one product where **lines**
must travel with that overlay too, for exactly the reason recorded above: they
sit above the data, so they cannot stay behind with the base.

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


def compose_bare(rain_rgba: Image.Image, view) -> Image.Image:
    """Base + rain, and nothing a reader navigates by.

    The loop frame's source. Everything above the data — lines and labels —
    is left off so it can be delivered once at full resolution rather than
    baked into every frame and then downscaled into mush.
    """
    base = overlays.load_light_base(view).convert("RGB")
    rain = rain_rgba.convert("RGBA")
    if rain.size != base.size:
        # The validator already enforces the frame size; this guards the base.
        raise ValueError(f"rain frame {rain.size} does not match base {base.size}")
    base.paste(rain, (0, 0), rain)
    return base


def add_lines(image: Image.Image, view) -> Image.Image:
    """The lines layer, pasted in place. Split out of `compose` so the loop
    frame can stop below it and the published overlay can carry it."""
    lines = overlays.load_light_lines(view)
    image.paste(lines, (0, 0), lines)
    return image


def compose(rain_rgba: Image.Image, view) -> Image.Image:
    """The full sandwich below the labels: base + rain + lines.

    What the full and thumbnail sizes render from. Labels are added by the
    caller's per-language loop, exactly like every other product's overlay.
    """
    return add_lines(compose_bare(rain_rgba, view), view)
