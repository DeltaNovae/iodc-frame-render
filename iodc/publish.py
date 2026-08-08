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


def frame_key(prefix: str, view: str, lang: str, captured_at: datetime) -> str:
    """`sat/close-bn/2026-09-20T0715.jpg` — the capture time is in the key, which
    is what makes the object immutable and infinitely cacheable."""
    stamp = captured_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M")
    return f"{prefix}/{view}-{lang}/{stamp}.jpg"


def meta_key(prefix: str) -> str:
    return f"{prefix}/meta.json"


def build_meta(prefix: str, product, entries: dict, history: dict) -> dict:
    """Describe the current set for readers.

    `generatedAtUtc` is the **capture** time, never the publish moment: staleness
    downstream is measured from when the satellite saw the scene, not from when
    this job happened to run.
    """
    views = {}
    for (view_key, lang), captured_at in entries.items():
        name = f"{view_key}-{lang}"
        frames = sorted(set(history.get(name, [])) | {captured_at})[-RETAIN:]
        views[name] = {
            "latest": frame_key(prefix, view_key, lang, captured_at),
            "capturedAtUtc": _iso(captured_at),
            "frames": [frame_key(prefix, view_key, lang, t) for t in frames],
            "frameTimesUtc": [_iso(t) for t in frames],
        }

    captured = max(entries.values())
    return {
        "version": 1,
        "product": "night" if product.is_night else "day",
        "layer": product.layer,
        "generatedAtUtc": _iso(captured),
        "attribution": ATTRIBUTION,
        "views": views,
    }


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prunable(history: dict, prefix: str) -> list:
    """Keys beyond the retention window — safe to delete only once the new
    `meta.json` is live, since no reader can still be pointed at them."""
    stale = []
    for name, times in history.items():
        view_key, lang = name.rsplit("-", 1)
        for captured_at in sorted(set(times))[:-RETAIN]:
            stale.append(frame_key(prefix, view_key, lang, captured_at))
    return stale


def read_meta(client, target: Target) -> dict:
    """Previous meta, or an empty shape on the very first run."""
    try:
        body = client.get(meta_key(target.prefix))
    except FileNotFoundError:
        return {"views": {}}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        # A corrupt pointer must not strand the pipeline: rebuild from scratch.
        return {"views": {}}


def history_from_meta(meta: dict) -> dict:
    history = {}
    for name, view in (meta.get("views") or {}).items():
        times = []
        for stamp in view.get("frameTimesUtc", []):
            try:
                times.append(datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
                             .replace(tzinfo=timezone.utc))
            except ValueError:
                continue
        history[name] = times
    return history
