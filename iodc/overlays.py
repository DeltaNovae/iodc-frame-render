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

import io
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
        # Themed entries (the rain map's light base/labels) share the manifest
        # but are found by their own lookups below.
        if entry.get("theme"):
            continue
        if (entry["view"] == view_key and entry["lang"] == lang
                and bool(entry["night"]) == night):
            return entry
    raise FileNotFoundError(
        f"no overlay for view={view_key} lang={lang} night={night}"
    )


def _find_light(view_key: str, role: str, lang=None) -> dict:
    for entry in _manifest():
        if (entry.get("theme") == "light" and entry.get("role") == role
                and entry["view"] == view_key and entry.get("lang") == lang):
            return entry
    raise FileNotFoundError(
        f"no light-theme {role} for view={view_key} lang={lang}"
    )


def _verified(entry: dict, view) -> Image.Image:
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


def load(view, lang: str, night: bool) -> Image.Image:
    """Return the overlay for this view/language/time-of-day, or refuse."""
    return _verified(find(view.key, lang, night), view)


def load_light_base(view) -> Image.Image:
    """The rain map's opaque stage — sea and land FILL only. Carries no
    language because it carries no words, and no line work either: lines have
    to be drawn above the data, not under it."""
    return _verified(_find_light(view.key, "base"), view)


def load_light_lines(view) -> Image.Image:
    """Coast, borders and division boundaries, transparent, for drawing ABOVE
    the data. A heavy rain cell was painting over the coastline, taking with it
    the one mark that tells a reader where they are (owner report)."""
    return _verified(_find_light(view.key, "lines"), view)


def load_light_labels(view, lang: str) -> Image.Image:
    """The rain map's text, drawn ABOVE the data so a rain cell can never make
    a place name unreadable."""
    return _verified(_find_light(view.key, "labels", lang), view)


def load_strip(view, product: str, lang: str) -> Image.Image:
    """The in-frame legend strip for one product — chips plus the EUMETSAT
    attribution, baked so a cropped screenshot still carries both.

    Language-independent strips (clouds: attribution only) are stored with
    ``lang: null`` and match any request. Verified against the view's WIDTH
    only: a strip is a bottom-edge band, not a full-frame overlay.
    """
    for entry in _manifest():
        if (entry.get("role") == "strip" and entry["view"] == view.key
                and entry.get("product") == product
                and entry.get("lang") in (lang, None)):
            image = Image.open(os.path.join(OVERLAY_DIR, entry["file"])).convert("RGBA")
            if image.width != view.width:
                raise OverlayMismatch(
                    f"{entry['file']} is {image.width} px wide but the frame is "
                    f"{view.width} — the strip would not span the bottom edge"
                )
            return image
    raise FileNotFoundError(f"no strip for view={view.key} product={product} lang={lang}")


def publishable(view, lang: str, variant: str) -> bytes:
    """The overlay a READER composites, as PNG bytes.

    The loop frame carries imagery only, so whatever a reader navigates by has
    to reach them separately. What that is differs by variant:

      * ``day`` / ``night`` — the same single asset the renderer bakes into the
        full frame.
      * ``light`` — rain's, and the one that is **not** the baked overlay.
        Rain's sandwich puts LINES above the data as well as labels
        (`rain.py`), so its loop frame stops below both and the published
        overlay must carry the two merged. Publishing labels alone would ship a
        loop with no coastline at all.

    Every variant goes through `_verified`, so a bbox or size mismatch is
    refused here exactly as it is at composite time — a wrong overlay published
    for readers to draw is the same "confident, beautifully drawn, completely
    wrong map" this module exists to prevent, just delivered one hop later.
    """
    if variant == "light":
        image = load_light_lines(view).copy()
        labels = load_light_labels(view, lang)
        image.paste(labels, (0, 0), labels)
    elif variant in ("day", "night"):
        image = load(view, lang, night=variant == "night")
    else:
        raise ValueError(f"unknown overlay variant {variant!r}")

    buffer = io.BytesIO()
    # Palette-quantized, and the numbers are why (measured 2026-08-13 on the
    # committed assets): 145-157 KB as full RGBA -> 30-37 KB at 256 colours,
    # ~77% smaller, with the error confined to anti-aliased halo edges — max
    # channel error 31-43/255 touching under 1% of pixels. The overlay is the
    # single largest object a loop play downloads, so on a 2G connection this
    # is the difference between the map arriving with the motion and after it.
    #
    # FASTOCTREE keeps the alpha channel in the palette and is deterministic,
    # so the content hash — and therefore the key and its year-long cache
    # entry — stays stable across identical rebuilds. No metadata beyond the
    # pixels, for the same reason.
    image.quantize(colors=256, method=Image.FASTOCTREE).save(
        buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def languages() -> list:
    return sorted({entry["lang"] for entry in _manifest()
                   if entry.get("lang") and entry.get("role") != "strip"})
