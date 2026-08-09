"""Publishing: what goes to storage, under what keys, and what survives failure.

Three rules shape everything here.

**Frames are immutable.** Every object key carries its capture time, so a key
never changes content and can be cached forever. Re-publishing is always a new
key plus a pointer swap, never an overwrite.

**`meta.json` is the only mutable object**, and it is written *last*. Until it
names a frame, that frame does not exist as far as readers are concerned — so a
half-finished cycle is invisible rather than broken.

**Nothing is deleted before its replacement is live.** Retention prunes only
after the new meta is published, so a reader following an old meta always finds
its objects still there.

Storage settings come from the environment; nothing account-specific is in the
repository.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from . import sizes

# How many capture times to keep per view/language. Twelve slots at 15 minutes
# is a three-hour loop — long enough to read where a system is heading.
RETAIN = int(os.environ.get("RETAIN_FRAMES", "12"))

CONTENT_TYPE = "image/jpeg"

# Frames never change, so they may be cached indefinitely. `meta.json` is the
# pointer and must not be, or a client would keep finding yesterday's frames.
FRAME_CACHE_CONTROL = "public, max-age=31536000, immutable"
META_CACHE_CONTROL = "public, max-age=60"

ATTRIBUTION = "© EUMETSAT"


@dataclass(frozen=True)
class Target:
    """Where published objects go. Every field comes from the environment."""

    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    prefix: str = "sat"

    @classmethod
    def from_env(cls) -> "Target":
        missing = [name for name in
                   ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
                   if not os.environ.get(name)]
        if missing:
            raise SystemExit(f"missing storage configuration: {', '.join(missing)}")
        return cls(
            endpoint=os.environ["S3_ENDPOINT"],
            bucket=os.environ["S3_BUCKET"],
            access_key=os.environ["S3_ACCESS_KEY_ID"],
            secret_key=os.environ["S3_SECRET_ACCESS_KEY"],
            prefix=os.environ.get("S3_PREFIX", "sat"),
        )


def frame_key(prefix: str, product: str, view: str, lang: str, size: str,
              captured_at: datetime) -> str:
    """`sat/clouds/close-bn/full/2026-09-20T0715.jpg`.

    Product and size are **path segments**, not filename decorations, so a whole
    product or a whole size can be listed, cached or purged as a unit. The
    capture time stays in the key — that is what makes the object immutable and
    infinitely cacheable.
    """
    stamp = captured_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M")
    return f"{prefix}/{product}/{view}-{lang}/{size}/{stamp}.jpg"


def meta_key(prefix: str) -> str:
    return f"{prefix}/meta.json"


def build_meta(prefix: str, products: dict, history: dict) -> dict:
    """Describe the current set for readers — **contract v2**.

    `products` maps a product key to `{"product": Product, "entries": {(view,
    lang): captured_at}}`.

    Two shape changes from v1, both made while the app was still unreleased and
    the contract therefore free:

    * **A product dimension.** v1 described one product, so storm/rain/fog had
      nowhere to live.
    * **Frames are objects, not parallel arrays.** v1 carried `frames` and
      `frameTimesUtc` side by side and trusted them to stay the same length and
      order. One list of `{capturedAtUtc, full, thumb, loop}` cannot desync.

    `generatedAtUtc` is the **oldest** capture across everything published, for
    the same reason the app's caption is: one timestamp speaking for several
    products has to be true of all of them.
    """
    out_products = {}
    all_captures = []

    for product_key, payload in products.items():
        product = payload["product"]
        entries = payload["entries"]
        views = {}
        for (view_key, lang), captured_at in entries.items():
            name = f"{view_key}-{lang}"
            times = sorted(
                set(history.get(product_key, {}).get(name, [])) | {captured_at}
            )[-RETAIN:]
            views[name] = {
                "capturedAtUtc": _iso(captured_at),
                "latest": _sizes_for(prefix, product_key, view_key, lang, captured_at),
                "frames": [
                    dict(capturedAtUtc=_iso(t),
                         **_sizes_for(prefix, product_key, view_key, lang, t))
                    for t in times
                ],
            }
        all_captures.extend(entries.values())
        out_products[product_key] = {
            # Which rung of the ladder produced this set, and the upstream layer
            # behind it — both for diagnosis, neither for display.
            "source": "night" if product.is_night else "day",
            "layer": product.layer,
            "views": views,
        }

    return {
        "version": 2,
        "generatedAtUtc": _iso(min(all_captures)),
        "attribution": ATTRIBUTION,
        "products": out_products,
    }


def _sizes_for(prefix: str, product: str, view: str, lang: str,
               captured_at: datetime) -> dict:
    return {
        size.key: frame_key(prefix, product, view, lang, size.key, captured_at)
        for size in sizes.SIZES
    }


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prunable(history: dict, prefix: str) -> list:
    """Keys beyond the retention window — safe to delete only once the new
    `meta.json` is live, since no reader can still be pointed at them.

    Every size of an expired capture goes: they were published together and are
    named by the same instant, so keeping one behind would leave an object no
    meta will ever mention again.
    """
    stale = []
    for product_key, views in history.items():
        for name, times in views.items():
            view_key, lang = name.rsplit("-", 1)
            for captured_at in sorted(set(times))[:-RETAIN]:
                for size in sizes.SIZES:
                    stale.append(frame_key(prefix, product_key, view_key, lang,
                                           size.key, captured_at))
    return stale


def read_meta(client, target: Target) -> dict:
    """Previous meta, or an empty shape on the very first run."""
    try:
        body = client.get(meta_key(target.prefix))
    except FileNotFoundError:
        return {"products": {}}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        # A corrupt pointer must not strand the pipeline: rebuild from scratch.
        return {"products": {}}


def history_from_meta(meta: dict) -> dict:
    """`{product: {view-lang: [capture times]}}` from a v2 pointer.

    A v1 pointer yields nothing, which is the correct behaviour rather than a
    gap: its keys are in the old layout and no longer resolvable, so treating
    them as history would schedule deletions against paths that do not exist.
    Retention simply refills over the next few cycles.
    """
    history = {}
    for product_key, product in (meta.get("products") or {}).items():
        views = {}
        for name, view in (product.get("views") or {}).items():
            times = []
            for frame in view.get("frames", []):
                try:
                    times.append(
                        datetime.strptime(frame["capturedAtUtc"], "%Y-%m-%dT%H:%M:%SZ")
                        .replace(tzinfo=timezone.utc)
                    )
                except (ValueError, KeyError, TypeError):
                    continue
            views[name] = times
        history[product_key] = views
    return history
