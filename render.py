"""Render one cycle: fetch each view, composite every language, write the set.

This is the whole per-cycle job apart from publishing, which lands next. The
shape is deliberately linear and boring — it runs unattended every 15 minutes,
and the interesting behaviour lives in the guards:

  * the product is chosen by sun elevation, but a visible frame that fails
    validation falls back to infrared rather than failing the cycle;
  * the overlay is refused unless it declares the frame's own rectangle;
  * nothing is written until every view has produced a usable frame, so a
    partial cycle never replaces a good complete one.

Usage:
    python render.py                       # now
    python render.py --at 2026-08-08T07:00:00Z
    python render.py --force-night         # exercise the other branch
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from datetime import datetime, timezone

from PIL import Image

from iodc import overlays, products, wms
from iodc.fetch import fetch_frame
from iodc.views import VIEWS

OUT_DIR = os.environ.get("RENDER_OUT", "out")
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "82"))

log = logging.getLogger("render")


def render_cycle(when: datetime, force: str | None = None,
                 pinned: bool = False) -> dict:
    product = products.choose(when)
    if force == "night":
        product = products.infrared_fallback()
    elif force == "day":
        product = products.Product(products.VISIBLE_LAYER, is_night=False)
    log.info("chose %s (%s) for %s", product.layer,
             "night" if product.is_night else "day", wms.format_iso(when))

    caps = wms.fetch_capabilities()
    results = {"product": product, "views": {}}

    # Production renders the newest slot; a pinned instant is only for
    # reproducing a specific moment (the daylight branch cannot be exercised
    # after dark otherwise).
    before = when if pinned else None

    for view in VIEWS.values():
        frame, used = _fetch_with_fallback(caps, product, view, before)
        image = Image.open(io.BytesIO(frame.raw)).convert("RGB")
        if used.is_night:
            image = products.recolor_night(image)

        for lang in overlays.languages():
            overlay = overlays.load(view, lang, used.is_night)
            composed = image.copy()
            composed.paste(overlay, (0, 0), overlay)
            results["views"].setdefault(view.key, {})[lang] = {
                "image": composed,
                "captured_at": frame.captured_at,
                "layer": used.layer,
            }
    return results


def _fetch_with_fallback(caps: bytes, product: products.Product, view, before=None):
    """Preferred product first; infrared is the safety net, never the failure."""
    dim = wms.parse_time_dimension(caps, product.layer)
    try:
        return fetch_frame(product.layer, view, dim, before=before), product
    except RuntimeError as exc:
        if product.is_night:
            raise
        log.warning("visible product unusable for %s — falling back to infrared (%s)",
                    view.key, str(exc).split(";")[0])
        fallback = products.infrared_fallback()
        dim = wms.parse_time_dimension(caps, fallback.layer)
        return fetch_frame(fallback.layer, view, dim, before=before), fallback


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", help="ISO8601 UTC instant; defaults to now")
    ap.add_argument("--force-night", action="store_const", const="night", dest="force")
    ap.add_argument("--force-day", action="store_const", const="day", dest="force")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    when = wms.parse_iso(args.at) if args.at else datetime.now(timezone.utc)

    result = render_cycle(when, args.force, pinned=bool(args.at))
    os.makedirs(OUT_DIR, exist_ok=True)
    for view_key, langs in result["views"].items():
        for lang, payload in langs.items():
            name = f"{view_key}-{lang}.jpg"
            path = os.path.join(OUT_DIR, name)
            payload["image"].save(path, "JPEG", quality=JPEG_QUALITY, optimize=True)
            log.info("  %-14s %3d KB  %s  captured %s", name,
                     os.path.getsize(path) // 1024, payload["layer"],
                     wms.format_iso(payload["captured_at"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
