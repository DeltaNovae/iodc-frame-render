"""The rain product: the sandwich, the lenient gates, and the PNG request."""

import io

import pytest
from PIL import Image

from iodc import overlays, rain, wms
from iodc.validate import FrameInvalid, validate_frame
from iodc.views import CLOSE, WIDE


# ── the product ───────────────────────────────────────────────────────────────

def test_rain_is_its_own_product_key():
    assert rain.RAIN.key == "rain"
    assert rain.RAIN.layer == "h63"


def test_rain_requests_png_with_alpha():
    """A JPEG request flattens the rain onto black and the base could never
    show through."""
    assert rain.RAIN.wms_format == "image/png"
    assert rain.RAIN.transparent


def test_the_getmap_url_carries_format_and_transparency():
    from datetime import datetime, timezone
    when = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    url = wms.build_getmap_url("h63", CLOSE, when, fmt="image/png",
                               transparent=True)
    assert "format=image/png" in url
    assert "transparent=true" in url
    # And the default stays JPEG with no stray parameter.
    plain = wms.build_getmap_url("ir108", CLOSE, when)
    assert "format=image/jpeg" in plain
    assert "transparent" not in plain


# ── lenient validation ────────────────────────────────────────────────────────

def encode_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def test_a_bone_dry_frame_is_a_legitimate_answer():
    """Fully transparent, flat, tiny — and correct: "no rain anywhere". The
    standard gates reject it; the lenient ones must not, or a dry winter day
    walks back to stale rain presented as current."""
    dry = encode_png(Image.new("RGBA", CLOSE.size, (0, 0, 0, 0)))
    with pytest.raises(FrameInvalid):
        validate_frame(dry, CLOSE.size)          # standard: rejected
    stats = validate_frame(dry, CLOSE.size,      # lenient: accepted
                           min_bytes=400, min_stddev=0.0, min_mean=0.0)
    assert stats.width == CLOSE.width


def test_lenient_still_rejects_a_wrong_sized_frame():
    """Lenient drops the flatness gates, never the structural ones — the base
    is composited 1:1 and a size mismatch would misplace every rain cell."""
    import random
    rnd = random.Random(7)
    noisy = Image.new("RGBA", (100, 100))
    noisy.putdata([(rnd.randrange(256),) * 3 + (255,) for _ in range(10_000)])
    with pytest.raises(FrameInvalid, match="unexpected size"):
        validate_frame(encode_png(noisy), CLOSE.size,
                       min_bytes=400, min_stddev=0.0, min_mean=0.0)


def test_lenient_still_rejects_bytes_that_are_not_an_image():
    with pytest.raises(FrameInvalid):
        validate_frame(b"<ServiceExceptionReport>" + b"x" * 500, CLOSE.size,
                       min_bytes=400, min_stddev=0.0, min_mean=0.0)


# ── the sandwich ──────────────────────────────────────────────────────────────

def rain_frame(view, spot=(320, 300)) -> Image.Image:
    """Transparent except one saturated green cell."""
    img = Image.new("RGBA", view.size, (0, 0, 0, 0))
    x0, y0 = spot
    for dx in range(30):
        for dy in range(30):
            img.putpixel((x0 + dx, y0 + dy), (110, 190, 120, 230))
    return img


def test_compose_shows_rain_over_the_base():
    out = rain.compose(rain_frame(CLOSE), CLOSE)
    r, g, b = out.getpixel((335, 315))
    assert g > r and g > b            # the cell reads green


def test_compose_shows_the_base_where_there_is_no_rain():
    out = rain.compose(rain_frame(CLOSE), CLOSE)
    base = overlays.load_light_base(CLOSE).convert("RGB")
    assert out.getpixel((50, 50)) == base.getpixel((50, 50))


def test_compose_is_opaque_rgb_ready_for_jpeg():
    out = rain.compose(rain_frame(CLOSE), CLOSE)
    assert out.mode == "RGB"
    assert out.size == CLOSE.size


def test_compose_refuses_a_mismatched_frame():
    with pytest.raises(ValueError):
        rain.compose(Image.new("RGBA", (100, 100)), CLOSE)


# ── the light assets ──────────────────────────────────────────────────────────

def test_light_assets_exist_for_both_views_and_languages():
    for view in (WIDE, CLOSE):
        assert overlays.load_light_base(view).size == view.size
        for lang in ("bn", "en"):
            assert overlays.load_light_labels(view, lang).size == view.size


def test_the_labels_layer_is_transparent_where_there_is_no_text():
    """Labels sit above the rain; an opaque labels layer would blot it out."""
    labels = overlays.load_light_labels(CLOSE, "bn")
    corner_alpha = labels.getpixel((5, 5))[3]
    assert corner_alpha == 0


def test_the_base_is_fully_opaque():
    base = overlays.load_light_base(CLOSE)
    assert base.getpixel((5, 5))[3] == 255


def test_dark_overlays_are_untouched_by_the_themed_manifest():
    """The mixed manifest must not change what the other products load."""
    for view in (WIDE, CLOSE):
        for night in (False, True):
            assert overlays.load(view, "bn", night).size == view.size
    assert overlays.languages() == ["bn", "en"]


# ── the in-frame legend strips ────────────────────────────────────────────────
# Baked into the FULL size so a cropped screenshot still carries the legend and
# the attribution. Any consumer's own branding is absent by design: only
# content neutral to whatever displays these frames enters this repository.

def test_every_product_has_strips_for_both_views():
    for view in (WIDE, CLOSE):
        for product in ("clouds", "storm", "rain", "fog"):
            strip = overlays.load_strip(view, product, "bn")
            assert strip.width == view.width
            assert strip.height < 40      # a band, not a banner


def test_language_independent_strips_serve_any_language():
    a = overlays.load_strip(CLOSE, "clouds", "bn")
    b = overlays.load_strip(CLOSE, "clouds", "en")
    assert list(a.getdata()) == list(b.getdata())


def test_stamping_touches_only_the_bottom_band():
    import render
    frame = rain.compose(rain_frame(CLOSE), CLOSE)
    stamped = render._stamp(frame, CLOSE, "rain", "bn")
    strip_h = overlays.load_strip(CLOSE, "rain", "bn").height
    top = CLOSE.height - strip_h
    assert list(stamped.crop((0, 0, CLOSE.width, top)).getdata()) == \
        list(frame.crop((0, 0, CLOSE.width, top)).getdata())
    assert list(stamped.crop((0, top, CLOSE.width, CLOSE.height)).getdata()) != \
        list(frame.crop((0, top, CLOSE.width, CLOSE.height)).getdata())


def test_a_missing_strip_degrades_to_the_clean_image():
    import render
    frame = Image.new("RGB", CLOSE.size, (90, 90, 90))
    out = render._stamp(frame, CLOSE, "lightning", "bn")
    assert list(out.getdata()) == list(frame.getdata())


def test_no_strip_lang_leaks_into_the_language_list():
    assert overlays.languages() == ["bn", "en"]
