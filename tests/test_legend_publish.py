"""The legend strip a reader draws above a PLAYING loop (2026-10-10).

The strip is baked only into the full still. The loop frames carry imagery
only, so a playing loop had no legend at all — the strip has to reach the
reader as its own object, exactly as the map overlay does. Same two failure
modes as the overlay (`test_overlay_publish.py`), tested the same way: the
pointer naming an object nobody uploaded, and the published strip drifting
from the one baked into the still.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from PIL import Image

import render
from iodc import overlays, publish
from iodc.products import Product
from iodc.views import VIEWS

from .test_overlay_publish import _RecordingClient, _FlatImage

AT = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)


def _decode(body: bytes) -> Image.Image:
    return Image.open(io.BytesIO(body)).convert("RGBA")


def test_the_key_carries_view_language_product_and_digest():
    assert (publish.legend_key("sat", "close", "bn", "storm", "1a2b3c4d")
            == "sat/legends/close-bn-storm-1a2b3c4d.png")


@pytest.mark.parametrize("view_key", ["close", "wide"])
@pytest.mark.parametrize("product", ["clouds", "storm", "rain", "fog"])
@pytest.mark.parametrize("lang", ["bn", "en"])
def test_the_published_legend_is_the_strip_baked_into_the_still(view_key, product, lang):
    """Play and pause must show the same legend, so the published strip is the
    baked one — same size, same pixels within palette quantization."""
    view = VIEWS[view_key]
    baked = overlays.load_strip(view, product, lang)
    published = _decode(overlays.publishable_legend(view, product, lang))
    assert published.size == baked.size
    worst = max(
        max(abs(a - b) for a, b in zip(p, q))
        for p, q in zip(published.getdata(), baked.getdata())
    )
    assert worst <= 64, f"published legend drifted from the baked one by {worst}"


def test_publishing_is_byte_stable_so_the_key_does_not_churn():
    view = VIEWS["close"]
    a = overlays.publishable_legend(view, "fog", "bn")
    b = overlays.publishable_legend(view, "fog", "bn")
    assert publish.overlay_digest(a) == publish.overlay_digest(b)


def test_a_legend_stays_small_on_the_wire():
    """Downloaded once per tile and language, but on 2G — keep it a few KB."""
    for view in VIEWS.values():
        for product in ("clouds", "storm", "rain", "fog"):
            body = overlays.publishable_legend(view, product, "bn")
            assert len(body) <= 12 * 1024, (view.key, product, len(body))


def test_a_product_without_a_strip_has_no_legend():
    with pytest.raises(FileNotFoundError):
        overlays.publishable_legend(VIEWS["close"], "thermal", "bn")


def _meta_with(legends):
    return publish.build_meta(
        "sat",
        {"storm": {"product": Product("ir108", is_night=True, key="storm"),
                   "entries": {("close", "bn"): AT},
                   "legends": legends}},
        history={},
    )


def test_meta_names_the_legend_for_each_view():
    meta = _meta_with({("close", "bn"): "sat/legends/close-bn-storm-1a2b3c4d.png"})
    assert (meta["products"]["storm"]["views"]["close-bn"]["legend"]
            == "sat/legends/close-bn-storm-1a2b3c4d.png")


def test_a_pointer_without_legends_omits_the_field_rather_than_nulling_it():
    assert "legend" not in _meta_with({})["products"]["storm"]["views"]["close-bn"]


@pytest.fixture
def published(monkeypatch):
    monkeypatch.setattr(render, "encode", lambda image, quality: b"jpegbytes")

    def run(product_key):
        client = _RecordingClient()
        target = publish.Target("http://e", "b", "k", "s", prefix="sat")
        payload = {"image": _FlatImage(), "bare": _FlatImage(),
                   "stamped": _FlatImage(), "overlay_variant": "night",
                   "captured_at": AT, "layer": "ir108"}
        result = {"products": {product_key: {
            "product": Product("ir108", is_night=True, key=product_key),
            "views": {"wide": {"bn": payload}},
        }}}
        return render.publish_cycle(result, client, target), client

    return run


@pytest.mark.parametrize("product_key", ["storm", "rain", "fog", "clouds"])
def test_every_legend_the_pointer_names_was_uploaded_before_it(published, product_key):
    """A pointer naming a legend nobody stored would play the loop with the
    legend silently missing — the overlay's failure mode again."""
    meta, client = published(product_key)
    named = meta["products"][product_key]["views"]["wide-bn"]["legend"]
    assert named.startswith(f"sat/legends/wide-bn-{product_key}-")
    assert named in client.put_keys
    assert client.put_keys.index(named) < client.put_keys.index("sat/meta.json")


def test_thermal_publishes_without_a_legend_and_still_publishes(published):
    meta, client = published("thermal")
    view = meta["products"]["thermal"]["views"]["wide-bn"]
    assert "legend" not in view
    assert "sat/meta.json" in client.put_keys
