"""Frame validation — the gate a fetched image must pass before it can be published.

Three distinct failure modes have to be caught here, because all of them can
arrive with HTTP 200:

  1. **A service exception in place of an image.** The server answers errors as
     an XML document with a success status; decoding it as an image is the only
     way to notice.
  2. **A wrong-sized frame.** Overlays are composited 1:1 with no scaling, so a
     size mismatch must fail loudly rather than produce a misaligned map.
  3. **A blank or featureless frame.** The visible-light products are black at
     night, and empty slots render as flat fills. Both decode perfectly and are
     the wrong thing to publish; only pixel statistics reveal them.
  4. **A washed-out frame.** The colour RGB is solar-zenith corrected, so near
     sunrise its gain runs away and half the picture clips to pure white. It is
     a well-formed image of nothing. Opt-in, via `max_mean` / `max_clipped` —
     see the note on those constants.

Publishing is never the failure path: a frame that fails here is discarded and
whatever is already published keeps serving.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageStat, UnidentifiedImageError

# Anything much smaller than a real frame is an error page or a flat fill.
MIN_BYTES = 8_000

# A featureless frame has almost no spread; real imagery always has structure.
MIN_STDDEV = 3.0

# A frame this dark carries no usable signal (night over a visible-light product).
MIN_MEAN = 2.0

# ── the washed-out ceiling ────────────────────────────────────────────────────
#
# **Opt-in, and deliberately so.** Only the solar-zenith-corrected colour RGB
# can blow out this way; infrared and the raw visible channel legitimately run
# bright over heavy convection, and a global ceiling would throw those away.
# Callers pass these explicitly for the one product that needs them.
#
# Measured over a full daylight arc on 2026-08-09. The cut sits
# where the imagery stops being readable, with clearance either side:
#
#     local   sun    mean   250+     verdict
#     06:00    5.6    246   84.3%    reject
#     07:00   19.0    228   50.0%    reject   ← the owner-reported frame
#     08:30   39.4    185    9.2%    reject   (borderline; the cut is here)
#     09:00   46.3    173    5.6%    keep
#     12:00   82.2    146    0.1%    keep
#     17:00   20.7    156    1.4%    keep     ← 07:00's sun angle, and fine
#     18:00    7.3    137   14.7%    reject
#
# Both tests are load-bearing. Mean alone lets the dusk frames through — they
# are half dark, half glare, so the average looks reasonable. Clipping alone
# lets the uniformly bright morning ones through at the margin. The 17:00 row
# is why this is measured per frame rather than derived from sun elevation: the
# satellite sits west of Bangladesh, so morning is forward-scatter and evening
# is not, and the same elevation means different things on either side of noon.
MAX_MEAN = 185.0
MAX_CLIPPED = 0.10

# At or above this a pixel is effectively pure white; whatever detail it held
# is gone and no amount of tone-mapping brings it back.
CLIP_LEVEL = 250


class FrameInvalid(Exception):
    """Raised when a fetched frame must not be published."""


@dataclass(frozen=True)
class FrameStats:
    width: int
    height: int
    n_bytes: int
    mean: float
    stddev: float
    #: Fraction of pixels blown to pure white, 0.0–1.0.
    clipped: float = 0.0


def validate_frame(raw: bytes, expected_size: tuple,
                   min_bytes: int = MIN_BYTES,
                   min_stddev: float = MIN_STDDEV,
                   min_mean: float = MIN_MEAN,
                   max_mean: float | None = None,
                   max_clipped: float | None = None) -> FrameStats:
    """Return statistics for a usable frame, or raise :class:`FrameInvalid`.

    `max_mean` and `max_clipped` default to off: see the constants above for
    why the washed-out ceiling is applied only where it belongs.
    """
    if len(raw) < min_bytes:
        head = raw[:120].decode("utf-8", errors="replace")
        raise FrameInvalid(
            f"implausibly small response ({len(raw)} bytes < {min_bytes}); "
            f"starts with: {head!r}"
        )

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise FrameInvalid(f"response did not decode as an image: {exc}") from exc

    if image.size != tuple(expected_size):
        raise FrameInvalid(f"unexpected size {image.size}, wanted {tuple(expected_size)}")

    grey = image.convert("L")
    stat = ImageStat.Stat(grey)
    mean, stddev = stat.mean[0], stat.stddev[0]

    histogram = grey.histogram()
    pixels = sum(histogram) or 1
    clipped = sum(histogram[CLIP_LEVEL:]) / pixels

    if stddev < min_stddev:
        raise FrameInvalid(
            f"featureless frame (stddev {stddev:.2f} < {min_stddev}); "
            "empty slot or flat fill"
        )
    if mean < min_mean:
        raise FrameInvalid(
            f"frame is essentially black (mean {mean:.2f} < {min_mean}); "
            "night slot on a visible-light product?"
        )
    if max_mean is not None and mean > max_mean:
        raise FrameInvalid(
            f"washed out (mean {mean:.2f} > {max_mean}); "
            "sun too low for the solar-zenith-corrected product"
        )
    if max_clipped is not None and clipped > max_clipped:
        raise FrameInvalid(
            f"washed out ({clipped * 100:.1f}% of pixels at {CLIP_LEVEL}+, "
            f"limit {max_clipped * 100:.0f}%); detail is gone, not recoverable"
        )

    return FrameStats(image.width, image.height, len(raw), mean, stddev, clipped)
