"""Loading pre-authored overlays, and refusing the wrong one.

Overlays are drawn elsewhere and committed here as finished pixels, so this
module's real job is verification. The dangerous failure is not a missing file —
that raises loudly — but an overlay drawn for a *different rectangle of the
world* at the same pixel size. It would composite perfectly and produce a
confident, beautifully drawn, completely wrong map.

So every overlay declares the bbox it was drawn for, and it is checked against
the frame's bbox before compositing.
"""

from __future__ import annotations

import json
import os

from PIL import Image

OVERLAY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "overlays")
MANIFEST = os.path.join(OVERLAY_DIR, "manifest.json")


class OverlayMismatch(Exception):
    """The overlay does not describe the frame it was about to be drawn on."""


def _manifest() -> list:
    with open(MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)["overlays"]


def find(view_key: str, lang: str, night: bool) -> dict:
    for entry in _manifest():
        if (entry["view"] == view_key and entry["lang"] == lang
                and bool(entry["night"]) == night):
            return entry
    raise FileNotFoundError(
        f"no overlay for view={view_key} lang={lang} night={night}"
    )


def load(view, lang: str, night: bool) -> Image.Image:
    """Return the overlay for this view/language/time-of-day, or refuse."""
    entry = find(view.key, lang, night)

    declared = [float(v) for v in entry["bbox"]]
    actual = [float(view.bbox.min_lat), float(view.bbox.min_lon),
              float(view.bbox.max_lat), float(view.bbox.max_lon)]
    if declared != actual:
        raise OverlayMismatch(
            f"{entry['file']} was drawn for bbox {declared} but the frame covers "
            f"{actual} — compositing it would produce a wrong map"
        )

    if tuple(entry["size"]) != tuple(view.size):
        raise OverlayMismatch(
            f"{entry['file']} is {entry['size']} but the frame is {list(view.size)}"
        )

    image = Image.open(os.path.join(OVERLAY_DIR, entry["file"])).convert("RGBA")
    if image.size != tuple(view.size):
        raise OverlayMismatch(
            f"{entry['file']} decodes to {image.size}, not {tuple(view.size)} — "
            "the manifest disagrees with the file"
        )
    return image


def languages() -> list:
    return sorted({entry["lang"] for entry in _manifest()})
