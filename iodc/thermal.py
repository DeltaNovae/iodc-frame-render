"""The thermal product: infrared as measured, with nothing painted on it.

This is the only product that is not for looking at on its own. It is a **base
layer**, published so that another map can be drawn over it, so every choice
here is the opposite of the others': no severity colours, no night palette, no
brightening, no legend. Grey in, grey out.

## Why a fifth product rather than reusing one of the four

`storm` already rides `ir108` around the clock, which makes it the obvious
candidate, and it was tried first. It fails for a reason that cannot be worked
around downstream: **its severity ramp is baked into the pixels.** Red and
yellow painted at render time cannot be removed by a reader, so anything drawn
over a storm frame in its own colours carries two unrelated colour languages at
once — the frame's severity and the overlay's — and a viewer has no way to tell
which red means what.

`clouds` fails differently: it walks a day/night ladder, so by daylight it is
reflected sunlight (`rgb_naturalenhncd` or `vis006`) and only after dark is it
infrared. A backdrop meant to be read as one quantity should mean the same
thing at 3 a.m. and at noon, and deep convection is visible in brightness
temperature, not in a photograph.

So: `ir108`, untoned, all day.

## What it costs, which is almost nothing

The cycle's fetch cache is keyed on (layer, view, slot) and shared across
products, so this rides the very fetch `storm` already makes. **No additional
upstream request.** There is no recolouring pass either, so the render cost is
lower than any other product's. Only the R2 writes are new.

## Reading the grey

Infrared arrives inverted: cold is bright. Brighter therefore means a colder,
taller cloud top, which means deeper convection. That monotonic relationship is
the whole reason this product is useful unpainted, and it is the same mapping
`storm.py` calibrated its bands against. Anything that tones this frame breaks
that correspondence silently — the frame still looks like weather while no
longer carrying a temperature.
"""

from __future__ import annotations

from .products import INFRARED_LAYER, Product

#: `is_night=True` matches `storm`: the base is infrared, so the frame needs the
#: night overlay's heavier strokes, and meta's diagnostic `source` honestly
#: reports the infrared origin. ⚠️ It must NOT reach `products.recolor_night` —
#: `render._tone` dispatches on the key first, exactly as it does for storm.
THERMAL = Product(INFRARED_LAYER, is_night=True, key="thermal")
