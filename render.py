"""Render one cycle: fetch each view, composite every language, write the set.

This is the whole per-cycle job apart from publishing, which lands next. The
shape is deliberately linear and boring — it runs unattended every 15 minutes,
and the interesting behaviour lives in the guards:

  * the product is a *ladder*, not a choice: colour, then raw visible, then
    infrared, and the first rung that survives validation is published, so a
    washed-out or unrendered frame degrades instead of failing the cycle;
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
import json
import logging
import os
import sys
from datetime import datetime, timezone

from PIL import Image

from iodc import overlays, products, publish, storage, validate, wms
from iodc.fetch import fetch_frame
from iodc.views import VIEWS

OUT_DIR = os.environ.get("RENDER_OUT", "out")

# Measured at S4 (§ 8.9). Subsampling is the free part: satellite imagery is
# almost all luminance detail, and a 3× magnified check showed the overlay's
# thin amber lines and Bengali labels survive 4:2:0 indistinguishably — for a
# quarter fewer bytes. Quality 78 is the floor before artefacts become visible.
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "78"))
JPEG_SUBSAMPLING = int(os.environ.get("JPEG_SUBSAMPLING", "2"))   # 2 = 4:2:0

log = logging.getLogger("render")


def render_cycle(when: datetime, force: str | None = None,
                 pinned: bool = False) -> dict:
    product = products.choose(when)
    if force == "night":
        rungs = [products.NIGHT]
    elif force == "day":
        rungs = [products.COLOUR_DAY]
    elif force == "lowsun":
        rungs = [products.LOW_SUN_DAY]
    else:
        rungs = products.ladder(when)
    log.info("ladder for %s: %s", wms.format_iso(when),
             " -> ".join(rung.layer for rung in rungs))

    caps = wms.fetch_capabilities()
    results = {"product": rungs[0], "views": {}}

    # Production renders the newest slot; a pinned instant is only for
    # reproducing a specific moment (the daylight branch cannot be exercised
    # after dark otherwise).
    before = when if pinned else None

    for view in VIEWS.values():
        frame, used = _fetch_down_the_ladder(caps, rungs, view, before)
        image = Image.open(io.BytesIO(frame.raw)).convert("RGB")
        if used.is_night:
            image = products.recolor_night(image)
        elif used.brighten:
            image = products.brighten(image)

        for lang in overlays.languages():
            overlay = overlays.load(view, lang, used.is_night)
            composed = image.copy()
            composed.paste(overlay, (0, 0), overlay)
            results["views"].setdefault(view.key, {})[lang] = {
                "image": composed,
                "captured_at": frame.captured_at,
                "layer": used.layer,
            }
        # meta carries one product for the set, and the views are the same
        # instant over overlapping ground — so they land on the same rung in
        # every case but a contrived one. Recording the rung actually used
        # beats recording the one merely preferred.
        results["product"] = used
    return results


def _fetch_down_the_ladder(caps: bytes, rungs: list, view, before=None):
    """Walk the ladder; the first rung yielding a usable frame wins.

    Only exhausting every rung is a failure. Each rejection is logged with its
    reason, because "which rung did this morning land on, and why" is the
    question this design will actually be asked.
    """
    problems = []
    for rung in rungs:
        dim = wms.parse_time_dimension(caps, rung.layer)
        guard = {}
        if rung.guard_washed_out:
            guard = {"max_mean": validate.MAX_MEAN,
                     "max_clipped": validate.MAX_CLIPPED}
        try:
            return fetch_frame(rung.layer, view, dim, before=before, **guard), rung
        except RuntimeError as exc:
            problems.append(f"{rung.layer}: {exc}")
            log.warning("%s unusable for %s — trying the next rung",
                        rung.layer, view.key)

    raise RuntimeError(
        f"every product failed for {view.key}: " + " | ".join(problems))


def encode(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=JPEG_QUALITY, subsampling=JPEG_SUBSAMPLING,
               optimize=True)
    return buf.getvalue()


def publish_cycle(result: dict, client, target: publish.Target) -> dict:
    """Upload frames, then the pointer, then prune — in that order.

    The order is the whole design. Frames go up under immutable keys, so they
    are invisible until something names them. `meta.json` is written next and
    is what makes the cycle visible, atomically. Only then are expired frames
    removed, so a reader holding the previous meta always finds its objects.
    """
    previous = publish.read_meta(client, target)
    history = publish.history_from_meta(previous)

    entries = {}
    for view_key, langs in result["views"].items():
        for lang, payload in langs.items():
            captured_at = payload["captured_at"]
            key = publish.frame_key(target.prefix, view_key, lang, captured_at)
            body = encode(payload["image"])
            client.put(key, body, publish.CONTENT_TYPE, publish.FRAME_CACHE_CONTROL)
            log.info("  put %-42s %3d KB", key, len(body) // 1024)
            entries[(view_key, lang)] = captured_at

    meta = publish.build_meta(target.prefix, result["product"], entries, history)
    client.put(publish.meta_key(target.prefix),
               json.dumps(meta, indent=2).encode("utf-8"),
               "application/json", publish.META_CACHE_CONTROL)
    log.info("  put %-42s (now visible)", publish.meta_key(target.prefix))

    merged = publish.history_from_meta(meta)
    for key in publish.prunable(history | merged, target.prefix):
        if key not in {v["latest"] for v in meta["views"].values()}:
            client.delete(key)
            log.info("  pruned %s", key)
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", help="ISO8601 UTC instant; defaults to now")
    ap.add_argument("--force-night", action="store_const", const="night", dest="force")
    ap.add_argument("--force-day", action="store_const", const="day", dest="force")
    ap.add_argument("--force-lowsun", action="store_const", const="lowsun", dest="force",
                    help="the raw-visible rung, unguarded — inspect it directly")
    ap.add_argument("--publish", action="store_true",
                    help="upload to object storage (needs S3_* in the environment)")
    ap.add_argument("--dry-run", metavar="DIR",
                    help="publish into a local directory instead — same code path")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    when = wms.parse_iso(args.at) if args.at else datetime.now(timezone.utc)

    result = render_cycle(when, args.force, pinned=bool(args.at))

    if args.publish or args.dry_run:
        if args.dry_run:
            client = storage.LocalClient(args.dry_run)
            target = publish.Target("local", "local", "", "", os.environ.get("S3_PREFIX", "sat"))
        else:
            target = publish.Target.from_env()
            client = storage.S3Client(target.endpoint, target.bucket,
                                      target.access_key, target.secret_key)
        publish_cycle(result, client, target)
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    for view_key, langs in result["views"].items():
        for lang, payload in langs.items():
            name = f"{view_key}-{lang}.jpg"
            path = os.path.join(OUT_DIR, name)
            with open(path, "wb") as fh:
                fh.write(encode(payload["image"]))
            log.info("  %-14s %3d KB  %s  captured %s", name,
                     os.path.getsize(path) // 1024, payload["layer"],
                     wms.format_iso(payload["captured_at"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
