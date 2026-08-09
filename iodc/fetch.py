"""Fetching a usable frame: walk back through capture slots until one validates.

The newest advertised slot is not always rendered yet, and a product can be
black or empty at a given moment. Rather than accept whatever comes back, try
the newest slot, validate it, and step back one slot at a time until something
passes or the ladder is exhausted.

The capture time that succeeded is returned with the bytes, because that — not
the moment we happened to publish — is what downstream staleness is measured
from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from . import wms
from .validate import FrameInvalid, FrameStats, validate_frame

log = logging.getLogger(__name__)

# How far back to walk before giving up (4 slots = one hour at PT15M).
DEFAULT_LADDER = 4


@dataclass(frozen=True)
class Frame:
    view_key: str
    layer: str
    captured_at: datetime
    raw: bytes
    stats: FrameStats


def fetch_frame(layer: str, view, time_dim: wms.TimeDimension,
                ladder: int = DEFAULT_LADDER, getter=wms.http_get,
                before=None, max_mean=None, max_clipped=None,
                fmt: str = "image/jpeg", transparent: bool = False,
                lenient: bool = False) -> Frame:
    """Fetch and validate one view of one product. Raises if every slot fails.

    ``before`` starts the ladder at a given moment rather than the newest slot.

    ``max_mean`` / ``max_clipped`` arm the washed-out ceiling for products that
    can suffer it. They reject the *slot*, so the walk-back doubles as cover for
    one glare-ruined capture inside an otherwise good hour.

    ``lenient`` drops the flatness gates for products whose empty frame is a
    legitimate answer (a dry rain frame). Structural checks still apply — and
    so does the byte floor's real job, since an XML error page still fails to
    decode as an image.
    """
    relaxations = ({"min_bytes": 400, "min_stddev": 0.0, "min_mean": 0.0}
                   if lenient else {})
    failures = []
    for when in time_dim.slots_desc(ladder, before=before):
        url = wms.build_getmap_url(layer, view, when, fmt=fmt,
                                   transparent=transparent)
        try:
            raw = getter(url)
        except Exception as exc:  # network/HTTP failure for this slot
            log.warning("%s/%s @ %s: fetch failed: %s", layer, view.key,
                        wms.format_iso(when), exc)
            failures.append(f"{wms.format_iso(when)}: {exc}")
            continue

        try:
            stats = validate_frame(raw, view.size,
                                   max_mean=max_mean, max_clipped=max_clipped,
                                   **relaxations)
        except FrameInvalid as exc:
            log.warning("%s/%s @ %s: rejected: %s", layer, view.key,
                        wms.format_iso(when), exc)
            failures.append(f"{wms.format_iso(when)}: {exc}")
            continue

        log.info("%s/%s @ %s: ok (%d KB, mean %.1f, stddev %.1f, clipped %.1f%%)",
                 layer, view.key, wms.format_iso(when), stats.n_bytes // 1024,
                 stats.mean, stats.stddev, stats.clipped * 100)
        return Frame(view.key, layer, when, raw, stats)

    raise RuntimeError(
        f"no usable frame for {layer}/{view.key} in the newest {ladder} slots; "
        + " | ".join(failures)
    )
