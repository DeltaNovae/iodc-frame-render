"""The overlay a reader composites over the loop frames.

The loop frame carries imagery only, because baking the overlay in and then
downscaling to the loop size multiplied every absolute pixel size by the same
factor — at the 320 px of the time, 0.457: 15 px labels rendered at 6.9 px and
the deliberately-heaviest 1.9 px national border landed SUB-PIXEL. So the map
layer has to reach the reader some other way, and these are the tests for that
way. Raising the loop edge shrinks the factor but never removes it, which is
why this design outlives any particular size.

Two failure modes are worth more than the rest and are tested hardest:

* **The pointer naming an object nobody uploaded.** A pointer served fine once
  while every frame 404'd, giving a blank tile under a confident caption. An
  overlay is *more* dangerous that way round: the imagery would still play, so
  the loop would simply lose its coastline and labels with nothing anywhere
  reporting an error.
* **Baked and published overlays disagreeing.** They are chosen by one function
  for exactly this reason; if they drifted, a loop would wear a different map
  from the still frame it opens with.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from PIL import Image

import render
from iodc import overlays, publish
from iodc.products import Product
from iodc.views import VIEWS

AT = datetime(2026, 8, 13, 15, 0, tzinfo=timezone.utc)


# ── The key is the hash of the bytes ─────────────────────────────────────────
# A frame earns immutability from its capture time. An overlay has no time, so
# the content hash stands in: identical pixels keep the key (and the year-long
# cache entry), changed pixels mint a new one and the swap is atomic.


def test_identical_bytes_keep_the_same_key():
    body = b"\x89PNG\r\n\x1a\n" + b"pixels"
    assert publish.overlay_digest(body) == publish.overlay_digest(bytes(body))


def test_changed_bytes_change_the_key():
    a = publish.overlay_digest(b"coastline v1")
    b = publish.overlay_digest(b"coastline v2")
    assert a != b, "a redrawn overlay must not reuse a cached key"


def test_the_key_carries_view_language_variant_and_digest():
    key = publish.overlay_key("sat", "close", "bn", "night", "1a2b3c4d")
    assert key == "sat/overlays/close-bn-night-1a2b3c4d.png"


def test_variants_do_not_collide():
    """Day and night are different pictures of the same place; they must not be
    able to land on one key even if some future asset made their bytes equal."""
    day = publish.overlay_key("sat", "wide", "bn", "day", "deadbeef")
    night = publish.overlay_key("sat", "wide", "bn", "night", "deadbeef")
    assert day != night


# ── Which overlay a product uses ─────────────────────────────────────────────


@pytest.mark.parametrize("key,is_night,expected", [
    ("rain", False, "light"),    # the light map, and the only "light" product
    ("storm", False, "night"),   # infrared around the clock
    ("fog", False, "night"),     # renders its own sky, takes the dark overlay
    ("clouds", True, "night"),
    ("clouds", False, "day"),
])
def test_the_overlay_variant_follows_the_product(key, is_night, expected):
    product = Product("layer", is_night=is_night, key=key)
    assert render._overlay_variant(product) == expected


def test_storm_and_fog_take_night_even_though_their_flag_says_otherwise():
    """`is_night` is a *rendering* flag, not a clock (`publish.build_meta` says
    so at length). Storm sets it True at noon and fog's night instrument reads
    False at 03:00 — so the variant must be decided by product identity, not by
    trusting that flag alone."""
    assert render._overlay_variant(Product("h63", is_night=True, key="fog")) == "night"
    assert render._overlay_variant(Product("ir108", is_night=False, key="storm")) == "night"


# ── What gets published ──────────────────────────────────────────────────────


def _mismatch(a, b) -> tuple:
    """(max channel error, share of samples off by more than 8/255).

    Eight is roughly where a step on a smooth gradient starts to be visible;
    the published overlay is palette-quantized, so equality is asserted within
    that band rather than byte-for-byte.
    """
    from PIL import ImageChops
    diff = ImageChops.difference(a, b)
    worst = 0
    over8 = 0
    total = a.width * a.height * 4
    for channel in diff.split():
        histogram = channel.histogram()
        for value in range(255, -1, -1):
            if histogram[value]:
                worst = max(worst, value)
                break
        over8 += sum(histogram[9:])
    return worst, over8 / total


#: What quantization measured on the committed assets (max err 31–43, <1% of
#: samples over 8) — asserted with headroom, not at the observed values, so a
#: slightly different palette does not flake the suite.
QUANT_MAX_ERR = 64
QUANT_MAX_SHARE_OVER_8 = 0.03


def test_rain_publishes_lines_merged_with_labels_not_labels_alone():
    """The gate that fired during the build.

    `rain.py` puts LINES above the data as well as labels — a heavy cell over
    Khulna was painting out the coastline. So rain's loop frame stops below
    both, and publishing labels alone would ship a loop with no coastline at
    all: the exact failure that moved the lines up in the first place.

    Comparative, because the published bytes are quantized: what is published
    must be NEAR the merge and measurably NOT the labels alone — the lines'
    pixels are exactly the difference between those two.
    """
    view = VIEWS["close"]
    published = _decode(overlays.publishable(view, "bn", "light"))

    labels_only = overlays.load_light_labels(view, "bn")
    merged = overlays.load_light_lines(view).copy()
    merged.paste(labels_only, (0, 0), labels_only)

    err_vs_merged, share_vs_merged = _mismatch(published, merged)
    assert err_vs_merged <= QUANT_MAX_ERR
    assert share_vs_merged <= QUANT_MAX_SHARE_OVER_8

    _, share_vs_labels = _mismatch(published, labels_only)
    assert share_vs_labels > share_vs_merged * 3, (
        "the published overlay is closer to labels-alone than to the merge — "
        "rain shipped with no coastline"
    )


@pytest.mark.parametrize("variant", ["day", "night"])
def test_day_and_night_publish_the_asset_the_renderer_bakes(variant):
    view = VIEWS["wide"]
    published = _decode(overlays.publishable(view, "bn", variant))
    baked = overlays.load(view, "bn", night=variant == "night")
    worst, share = _mismatch(published, baked)
    assert worst <= QUANT_MAX_ERR, "published overlay has drifted beyond quantization error"
    assert share <= QUANT_MAX_SHARE_OVER_8


@pytest.mark.parametrize("view_key,lang,variant", [
    (v, l, x) for v in ("wide", "close") for l in ("bn", "en")
    for x in ("day", "night", "light")
])
def test_every_published_overlay_fits_the_wire_budget(view_key, lang, variant):
    """~35 KB quantized against 145-157 KB as full RGBA. The ceiling is the
    guard: removing the quantization would not fail any equality test — the
    pixels stay near-identical — it would only quietly quadruple the largest
    object a loop play downloads, on the connections this app is built for.
    """
    body = overlays.publishable(VIEWS[view_key], lang, variant)
    assert len(body) <= 50_000, (
        f"{view_key}-{lang}-{variant} is {len(body)} bytes — quantization lost?"
    )


def test_an_unknown_variant_is_refused():
    with pytest.raises(ValueError, match="unknown overlay variant"):
        overlays.publishable(VIEWS["wide"], "bn", "twilight")


def test_publishing_is_byte_stable_so_the_key_does_not_churn():
    """Re-encoding must not embed anything that varies run to run — a timestamp
    chunk would mint a new key every cycle, defeating the cache and filling the
    bucket with identical overlays."""
    view = VIEWS["close"]
    first = overlays.publishable(view, "en", "night")
    second = overlays.publishable(view, "en", "night")
    assert publish.overlay_digest(first) == publish.overlay_digest(second)


def _decode(body: bytes) -> Image.Image:
    import io
    return Image.open(io.BytesIO(body)).convert("RGBA")


# ── meta.json names it, and only when there is one ───────────────────────────


def _meta_with(overlays_map):
    return publish.build_meta(
        "sat",
        {"clouds": {"product": Product("ir108", is_night=True, key="clouds"),
                    "entries": {("close", "bn"): AT},
                    "overlays": overlays_map}},
        history={},
    )


def test_meta_names_the_overlay_for_each_view():
    meta = _meta_with({("close", "bn"): "sat/overlays/close-bn-night-1a2b3c4d.png"})
    view = meta["products"]["clouds"]["views"]["close-bn"]
    assert view["overlay"] == "sat/overlays/close-bn-night-1a2b3c4d.png"


def test_a_pointer_without_overlays_omits_the_field_rather_than_nulling_it():
    """Additive means absent, not null: a reader tests for the field, and a
    null would need its own special case at the far end."""
    meta = _meta_with({})
    assert "overlay" not in meta["products"]["clouds"]["views"]["close-bn"]


def test_the_overlay_field_does_not_disturb_the_v2_shape():
    meta = _meta_with({("close", "bn"): "sat/overlays/x.png"})
    view = meta["products"]["clouds"]["views"]["close-bn"]
    assert meta["version"] == 2
    assert set(view["latest"]) == {"full", "thumb", "loop"}
    assert view["capturedAtUtc"] == "2026-08-13T15:00:00Z"


# ── The blank-tile class: the pointer may not name a ghost ───────────────────


class _RecordingClient:
    """Records puts and deletes; serves a missing pointer on first run."""

    def __init__(self):
        self.put_keys = []
        self.deleted = []

    def put(self, key, data, content_type, cache_control):
        self.put_keys.append(key)

    def get(self, key):
        raise FileNotFoundError(key)

    def delete(self, key):
        self.deleted.append(key)


class _FlatImage:
    """A real-enough image for the encode path: `size.scale` and `encode` are
    the only things that touch it here."""

    def __init__(self):
        self._image = Image.new("RGB", (700, 630), (40, 60, 90))

    def __getattr__(self, name):
        return getattr(self._image, name)


@pytest.fixture
def published(monkeypatch):
    monkeypatch.setattr(render, "encode", lambda image, quality: b"jpegbytes")

    def run(product_key, variant):
        client = _RecordingClient()
        target = publish.Target("http://e", "b", "k", "s", prefix="sat")
        payload = {"image": _FlatImage(), "bare": _FlatImage(),
                   "stamped": _FlatImage(), "overlay_variant": variant,
                   "captured_at": AT, "layer": "ir108"}
        result = {"products": {product_key: {
            "product": Product("ir108", is_night=True, key=product_key),
            "views": {"wide": {"bn": payload}},
        }}}
        meta = render.publish_cycle(result, client, target)
        return meta, client

    return run


@pytest.mark.parametrize("product_key,variant", [
    ("clouds", "night"),
    ("rain", "light"),
])
def test_every_overlay_the_pointer_names_was_actually_uploaded(published,
                                                               product_key,
                                                               variant):
    """The blank-tile class, one hop further out.

    A correct caption once shipped over a 404ing frame because the pointer and
    the uploader disagreed about the key. An overlay fails more quietly still:
    the imagery keeps playing and the loop just loses its map, with nothing
    logging an error. So the pointer's own claim is checked against what the
    client was actually asked to store.
    """
    meta, client = published(product_key, variant)

    named = meta["products"][product_key]["views"]["wide-bn"]["overlay"]
    assert named, "the pointer named no overlay at all"
    assert named in client.put_keys, (
        f"meta names {named} but it was never uploaded — the loop would play "
        f"with no map and nothing would report it"
    )


def test_the_overlay_is_uploaded_before_the_pointer_that_names_it(published):
    """Frames obey this rule and the overlay must too: until meta names it, an
    object does not exist to readers — but a pointer written first is briefly
    naming something that genuinely is not there yet."""
    meta, client = published("clouds", "night")

    named = meta["products"]["clouds"]["views"]["wide-bn"]["overlay"]
    assert client.put_keys.index(named) < client.put_keys.index("sat/meta.json")
