"""The three sizes every frame is published at, and why there are three.

One image cannot serve all three places it is shown:

  * **full** — the viewer. Detail matters; this is the frame at its measured
    native size and nothing about it changes.
  * **thumb** — the Home tile, drawn at ~104 dp. Publishing only `full` meant
    the row downloaded ~85 KB per tile to paint a postage stamp: ~340 KB per
    Home load at four products, on 2G phones.
  * **loop** — the animation. Twelve full frames is ~1 MB per play and ~20 MB
    of decoded bitmaps on a 1–2 GB device, so the loop frame stays well below
    native.

    "A loop is for motion, not detail" justified 320 px, and that was judged
    against a still. It does not survive the two-layer loop: the overlay now
    reaches the reader at its authored resolution and the imagery does not, so
    at 320 the map lines stayed crisp while the cloud beneath them went soft —
    visibly, and only during playback. Review called the imagery blurry and was
    right. 400 takes most of that back (2.00× upscale → 1.60× on the square
    view, 1.75× on the wide one) for ~20–29 KB a frame.

    Native is the wrong end of the trade: 640 costs ~52 KB a frame — ~640 KB a
    play on 2G — and ~20 MB of decoded bitmaps, past the image cache on a small
    device. Eviction, re-decode, a loop that stutters. Softness is the better
    failure of the two.

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
# A multiple of ten, and not 448: `Size.scale` rounds width and height
# independently, so the wide view's 700×630 stays exactly 10:9 only on multiples
# of ten. 448 lands on 448×403 and misregisters the overlay — the aspect test
# catches it, and nothing else in the repo says the constraint exists.
#
# 400, arrived at by shipping 440 and MEASURING what it did. Two things the
# synthetic never showed:
#
#   * The WIDE view is the binding case, not close — 440×396 encodes LARGER
#     than 440×440 despite having fewer pixels, because it spans far more
#     varied terrain and so carries more high-frequency detail.
#   * Real imagery encodes ~1.43× the synthetic. Published 440 px wide frames
#     measured 33,692 B (storm) and 30,366 B (clouds) against a 30,000 ceiling
#     the test had called comfortable.
#
# 400 puts the worst real frame at ~28.8 KB — under, though only just. Anything
# larger breaches: 420 still measures ~31.4 KB. The remaining sharpness gap
# between 400 and 440 is 1.75× vs 1.59× upscale on the wide view; the ceiling
# is the harder constraint and it wins.
LOOP = Size("loop", 400, 72)

SIZES = (FULL, THUMB, LOOP)

#: Ceilings asserted by the budget tests. The full-frame number is the measured
#: one; the other two are what the 2G arithmetic above requires rather than what
#: a particular day happened to encode to.
BUDGET_BYTES = {FULL.key: 105_000, THUMB.key: 14_000, LOOP.key: 30_000}
