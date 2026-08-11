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

from iodc import fog, overlays, products, publish, rain, sizes, storage, storm, validate, wms
from iodc.fetch import fetch_frame
from iodc.views import VIEWS

OUT_DIR = os.environ.get("RENDER_OUT", "out")

# Measured when the publish path was tuned. Subsampling is the free part:
# satellite imagery is almost all luminance detail, and a 3× magnified check
# showed the overlay's thin amber lines and Bengali labels survive 4:2:0
# indistinguishably — for a quarter fewer bytes. Quality 78 is the floor before
# artefacts become visible.
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "78"))
JPEG_SUBSAMPLING = int(os.environ.get("JPEG_SUBSAMPLING", "2"))   # 2 = 4:2:0

#: Reference layer for "which slot are we actually about to classify".
#: `ir108` is the right choice: it is always present, always rendered (no
#: daylight dependency), and shares the 15-minute grid and ~24-minute latency
#: of every layer either sun-driven ladder can pick. Rain's `h63` runs ~15
#: minutes further behind, but rain has no ladder — nothing decides on its
#: timing, so its extra lag cannot mis-select an instrument.
DECISION_LAYER = "ir108"

log = logging.getLogger("render")


def render_cycle(when: datetime, force: str | None = None,
                 pinned: bool = False) -> dict:
    """Render every product for one instant.

    A cycle is now plural: clouds walks its ladder, storm rides `ir108`
    directly. A product that fails is logged and skipped rather than failing
    the others — the section hides an absent tile, and the staleness alarm
    keys on the oldest capture, so a silently dead product still escalates.

    `force` narrows the cycle to a single branch for eyeballing; nobody
    dispatching --force-night wants storm frames in the way.

    THE LADDERS DECIDE AT THE CAPTURE SLOT, NOT AT `when`. Both sun-driven
    ladders pick an instrument from the solar elevation, and the frame they
    then classify is the newest slot upstream has published — 24 to 39 minutes
    older than now. Deciding at `when` applied the wrong instrument to a frame
    from half an hour earlier: a 12:30Z frame at sun +0.2 deg (the blind band,
    where fog declines) was classified by the NIGHT recipe because the run
    happened at 13:00Z with the sun at -6.3 deg. The night recipe scores 2.1%
    on real dense fog at sun +2.8 deg, so that frame's "no fog" meant nothing.

    The effect is to SHIFT the blind band by half an hour, so it guards the
    wrong window — and at dawn it fails towards the fog hazard window, which is
    the one hour this product exists for. Clouds survived the same offset only
    because its washed-out guard measures every frame and rejects what it
    cannot use; fog has no such backstop, so nothing caught it.
    """
    caps = wms.fetch_capabilities()

    # Snap the decision to the frame that will actually be classified. `pinned`
    # already names a specific instant, so it is its own decision time.
    decision_at = when if pinned else wms.parse_time_dimension(
        caps, DECISION_LAYER).latest

    if force == "night":
        jobs = {"clouds": [products.NIGHT]}
    elif force == "day":
        jobs = {"clouds": [products.COLOUR_DAY]}
    elif force == "lowsun":
        jobs = {"clouds": [products.LOW_SUN_DAY]}
    elif force == "storm":
        jobs = {"storm": [storm.STORM]}
    elif force == "rain":
        jobs = {"rain": [rain.RAIN]}
    elif force == "fog":
        jobs = {"fog": fog.ladder(decision_at)}
    else:
        jobs = {"clouds": products.ladder(decision_at), "storm": [storm.STORM],
                "rain": [rain.RAIN], "fog": fog.ladder(decision_at)}
    # Both times are logged: when they diverge by more than a step, that gap is
    # the thing to look at first.
    log.info("cycle at %s, deciding for slot %s (%.0f min behind)",
             wms.format_iso(when), wms.format_iso(decision_at),
             (when - decision_at).total_seconds() / 60)
    for key, rungs in jobs.items():
        log.info("%s ladder for %s: %s", key, wms.format_iso(decision_at),
                 " -> ".join(rung.layer for rung in rungs) or "DECLINES")

    # Production renders the newest slot; a pinned instant is only for
    # reproducing a specific moment (the daylight branch cannot be exercised
    # after dark otherwise).
    before = when if pinned else None

    # One upstream fetch per (layer, view, slot) regardless of how many
    # products want it: at night both clouds and storm sit on ir108, and
    # fetching the identical frame twice per view would double the upstream
    # load for nothing.
    fetched: dict = {}

    results = {"products": {}}
    for key, rungs in jobs.items():
        views = {}
        used = None
        try:
            for view in VIEWS.values():
                frame, used = _fetch_down_the_ladder(caps, rungs, view, before,
                                                     fetched)
                if used.key == "rain":
                    # The sandwich: base below the data, labels above it.
                    image = rain.compose(Image.open(io.BytesIO(frame.raw)), view)
                elif used.key == "fog":
                    image = fog.compose(Image.open(io.BytesIO(frame.raw)), view,
                                        night=used.layer == "rgb_fog")
                else:
                    image = Image.open(io.BytesIO(frame.raw)).convert("RGB")
                    image = _tone(image, used)

                for lang in overlays.languages():
                    # Rain alone keeps the light map; fog now renders its own
                    # sky (Option B) and takes the dark overlay like the other
                    # imagery products.
                    overlay = (overlays.load_light_labels(view, lang)
                               if used.key == "rain"
                               else overlays.load(view, lang,
                                                  night=used.key in ("fog", "storm")
                                                  or used.is_night))
                    composed = image.copy()
                    composed.paste(overlay, (0, 0), overlay)
                    views.setdefault(view.key, {})[lang] = {
                        "image": composed,
                        # The FULL size additionally carries the in-frame
                        # legend strip, so a cropped screenshot keeps the
                        # legend and the attribution. Thumbs stay clean
                        # previews and loop frames clean motion.
                        "stamped": _stamp(composed, view, used.key, lang),
                        "captured_at": frame.captured_at,
                        "layer": used.layer,
                    }
        except Exception as exc:
            # DELIBERATELY BROAD, and the breadth is the point: this boundary
            # exists so one product cannot take the others down with it, and a
            # boundary that only catches RuntimeError does not do that.
            #
            # Everything raised inside this block is a candidate. The fetch
            # ladder raises RuntimeError, but `overlays.find` raises
            # FileNotFoundError, a mismatched overlay raises OverlayMismatch,
            # and `rain.compose` raises ValueError — none of them a RuntimeError,
            # all of them fatal to the whole cycle before this widened.
            #
            # The failure mode that makes it serious is that those are
            # DETERMINISTIC. A truncated overlay manifest is not a bad second
            # that the next cycle survives; it fails identically every fifteen
            # minutes until someone intervenes, which is exactly the class of
            # outage this pipeline is built to not have. A cycle that loses one
            # product and publishes three is a bad afternoon; a cycle that dies
            # on all four is a dead pipeline.
            #
            # Escalation is not lost: `carry_forward` keeps the product's old
            # pointer, `generatedAtUtc` is recomputed across everything named,
            # so a product stuck here ages the section's timestamp and trips the
            # staleness alarm.
            log.warning("product %s failed this cycle and is skipped: %s: %s",
                        key, type(exc).__name__, exc)
            continue
        # Assigned only once EVERY view rendered: a product is published whole
        # or not at all, so a half-rendered one never displaces a complete one.
        # meta carries one rung per product; the views are the same instant
        # over overlapping ground, so they land on the same rung in every case
        # but a contrived one, and recording the rung actually used beats
        # recording the one merely preferred.
        results["products"][key] = {"product": used, "views": views}
    if not results["products"]:
        raise RuntimeError("every product failed this cycle")
    return results


def _stamp(image, view, product_key: str, lang: str):
    """The image with its legend strip along the bottom edge.

    A missing strip degrades to the clean image rather than failing the
    product: the strip is publication polish, and losing a cycle over it
    would invert the priorities.
    """
    try:
        strip = overlays.load_strip(view, product_key, lang)
    except (FileNotFoundError, overlays.OverlayMismatch) as exc:
        log.warning("no legend strip for %s/%s/%s (%s) — publishing clean",
                    product_key, view.key, lang, exc)
        return image
    stamped = image.copy()
    stamped.paste(strip, (0, image.height - strip.height), strip)
    return stamped


def _tone(image, product):
    """The product's colouring. Dispatch is by product key first because the
    storm frame is built FROM the night layer — `is_night` alone would paint it
    navy."""
    if product.key == "storm":
        return storm.recolor_storm(image)
    if product.is_night:
        return products.recolor_night(image)
    if product.brighten:
        return products.brighten(image)
    return image


def _fetch_down_the_ladder(caps: bytes, rungs: list, view, before=None,
                           fetched: dict = None):
    """Walk the ladder; the first rung yielding a usable frame wins.

    Only exhausting every rung is a failure. Each rejection is logged with its
    reason, because "which rung did this morning land on, and why" is the
    question this design will actually be asked.
    """
    if not rungs:
        # A product may decline to answer this cycle — fog does so in the
        # blind band around sunrise. carry_forward keeps its last good frame.
        raise RuntimeError(f"no usable instrument for {view.key} at this hour")

    problems = []
    for rung in rungs:
        # The whole fetch configuration is the cache key: a frame accepted
        # under one validation regime or format must not satisfy a request
        # made under another.
        cache_key = (rung.layer, view.key, before, rung.guard_washed_out,
                     rung.wms_format, rung.transparent, rung.lenient)
        if fetched is not None and cache_key in fetched:
            return fetched[cache_key], rung
        dim = wms.parse_time_dimension(caps, rung.layer)
        guard = {}
        if rung.guard_washed_out:
            guard = {"max_mean": validate.MAX_MEAN,
                     "max_clipped": validate.MAX_CLIPPED}
        try:
            frame = fetch_frame(rung.layer, view, dim, before=before,
                                fmt=rung.wms_format, transparent=rung.transparent,
                                lenient=rung.lenient, **guard)
            if fetched is not None:
                fetched[cache_key] = frame
            return frame, rung
        except RuntimeError as exc:
            problems.append(f"{rung.layer}: {exc}")
            log.warning("%s unusable for %s — trying the next rung",
                        rung.layer, view.key)

    raise RuntimeError(
        f"every product failed for {view.key}: " + " | ".join(problems))


def encode(image, quality: int = None) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=quality or JPEG_QUALITY,
               subsampling=JPEG_SUBSAMPLING, optimize=True)
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

    to_publish = {}
    for product_key, payload in result["products"].items():
        entries = {}
        for view_key, langs in payload["views"].items():
            for lang, view_payload in langs.items():
                captured_at = view_payload["captured_at"]
                # All three sizes derive from the one composited image, so they
                # can never disagree with each other or with the capture time
                # they are keyed by.
                for size in sizes.SIZES:
                    key = publish.frame_key(target.prefix, product_key, view_key,
                                            lang, size.key, captured_at)
                    source = (view_payload.get("stamped", view_payload["image"])
                              if size.key == "full" else view_payload["image"])
                    body = encode(size.scale(source), quality=size.quality)
                    client.put(key, body, publish.CONTENT_TYPE,
                               publish.FRAME_CACHE_CONTROL)
                    log.info("  put %-58s %3d KB", key, len(body) // 1024)
                entries[(view_key, lang)] = captured_at
        to_publish[product_key] = {"product": payload["product"],
                                   "entries": entries}

    meta = publish.carry_forward(
        publish.build_meta(target.prefix, to_publish, history), previous)
    client.put(publish.meta_key(target.prefix),
               json.dumps(meta, indent=2).encode("utf-8"),
               "application/json", publish.META_CACHE_CONTROL)
    log.info("  put %-58s (now visible)", publish.meta_key(target.prefix))

    # Merge rather than replace: a cycle publishes one product, and pruning must
    # not treat the products it did not touch as having no history.
    merged = _merge_history(history, publish.history_from_meta(meta))
    live = {
        url
        for product in meta["products"].values()
        for view in product["views"].values()
        for url in view["latest"].values()
    }
    for key in publish.prunable(merged, target.prefix):
        if key not in live:
            client.delete(key)
            log.info("  pruned %s", key)
    return meta


def _merge_history(older: dict, newer: dict) -> dict:
    out = {k: {n: list(t) for n, t in v.items()} for k, v in older.items()}
    for product_key, views in newer.items():
        target = out.setdefault(product_key, {})
        for name, times in views.items():
            target[name] = sorted(set(target.get(name, [])) | set(times))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", help="ISO8601 UTC instant; defaults to now")
    ap.add_argument("--force-night", action="store_const", const="night", dest="force")
    ap.add_argument("--force-day", action="store_const", const="day", dest="force")
    ap.add_argument("--force-lowsun", action="store_const", const="lowsun", dest="force",
                    help="the raw-visible rung, unguarded — inspect it directly")
    ap.add_argument("--force-storm", action="store_const", const="storm", dest="force",
                    help="the storm product alone")
    ap.add_argument("--force-rain", action="store_const", const="rain", dest="force",
                    help="the rain product alone")
    ap.add_argument("--force-fog", action="store_const", const="fog", dest="force",
                    help="the fog product alone")
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

    # Local output writes every size too — the point of looking at a render by
    # hand is usually to judge whether the small ones are still readable.
    os.makedirs(OUT_DIR, exist_ok=True)
    for product_key, product_payload in result["products"].items():
        _write_local(product_key, product_payload["views"])
    return 0


def _write_local(product_key: str, views: dict) -> None:
    for view_key, langs in views.items():
        for lang, payload in langs.items():
            for size in sizes.SIZES:
                name = f"{product_key}-{view_key}-{lang}-{size.key}.jpg"
                path = os.path.join(OUT_DIR, name)
                source = (payload.get("stamped", payload["image"])
                          if size.key == "full" else payload["image"])
                with open(path, "wb") as fh:
                    fh.write(encode(size.scale(source), quality=size.quality))
                log.info("  %-40s %3d KB  %s  captured %s", name,
                         os.path.getsize(path) // 1024, payload["layer"],
                         wms.format_iso(payload["captured_at"]))


if __name__ == "__main__":
    sys.exit(main())
