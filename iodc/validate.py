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


class FrameInvalid(Exception):
    """Raised when a fetched frame must not be published."""


@dataclass(frozen=True)
class FrameStats:
    width: int
    height: int
    n_bytes: int
    mean: float
    stddev: float


def validate_frame(raw: bytes, expected_size: tuple,
                   min_bytes: int = MIN_BYTES,
                   min_stddev: float = MIN_STDDEV,
                   min_mean: float = MIN_MEAN) -> FrameStats:
    """Return statistics for a usable frame, or raise :class:`FrameInvalid`."""
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

    return FrameStats(image.width, image.height, len(raw), mean, stddev)
