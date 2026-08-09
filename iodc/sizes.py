"""The three sizes every frame is published at, and why there are three.

One image cannot serve all three places it is shown:

  * **full** — the viewer. Detail matters; this is the frame as measured in
    § 8.9 and nothing about it changes.
  * **thumb** — the Home tile, drawn at ~104 dp. Publishing only `full` meant
    the row downloaded ~85 KB per tile to paint a postage stamp: ~340 KB per
    Home load at four products, on 2G phones.
  * **loop** — the animation. Twelve full frames is ~1 MB per play and ~20 MB
    of decoded bitmaps on a 1–2 GB device. A loop is for **motion, not
    detail**, so a smaller frame costs almost nothing perceptually.

All three are published in the same cycle from the same composited image, so
they always agree with each other and with the capture time in their key.

Sizes are given as a **longest-edge** bound rather than fixed dimensions: the
two views have different aspect ratios (700×630 and 640×640) and neither may be
distorted to fit a square.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class Size:
    key: str
    #: Longest edge in pixels; ``None`` means publish at native resolution.
    max_edge: int | None
    #: JPEG quality. Small frames can afford less: at 160 px the eye is reading
    #: shape and colour, and the overlay's text is already below legibility.
    quality: int

    def scale(self, image: Image.Image) -> Image.Image:
        """The image at this size, or the original when it is already smaller.

        Never upscales — a size is a ceiling, not a target. LANCZOS because the
        overlay's thin amber coastlines alias badly under cheaper filters, and
        a coastline is the one thing that still has to read at 160 px.
        """
        if self.max_edge is None:
            return image
        longest = max(image.width, image.height)
        if longest <= self.max_edge:
            return image
        ratio = self.max_edge / longest
        return image.resize(
            (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
            Image.LANCZOS,
        )


FULL = Size("full", None, 78)
THUMB = Size("thumb", 160, 70)
LOOP = Size("loop", 320, 72)

SIZES = (FULL, THUMB, LOOP)

#: Ceilings asserted by the budget tests. The full-frame number is § 8.9's; the
#: other two are what the 2G arithmetic above requires rather than what a
#: particular day happened to encode to.
BUDGET_BYTES = {FULL.key: 105_000, THUMB.key: 14_000, LOOP.key: 30_000}
