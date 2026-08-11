import io
import random

import pytest
from PIL import Image

from iodc.validate import MAX_CLIPPED, MAX_MEAN, FrameInvalid, validate_frame

SIZE = (120, 100)


def encode(image: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def noisy_image(size=SIZE, seed=7) -> Image.Image:
    """Stand-in for real imagery: plenty of structure, mid-range brightness."""
    rnd = random.Random(seed)
    img = Image.new("RGB", size)
    img.putdata([(rnd.randrange(40, 220),) * 3 for _ in range(size[0] * size[1])])
    return img


def test_accepts_a_normal_frame_and_reports_stats():
    stats = validate_frame(encode(noisy_image()), SIZE)
    assert (stats.width, stats.height) == SIZE
    assert stats.stddev > 3.0
    assert stats.n_bytes > 0


def test_rejects_a_service_exception_served_as_xml():
    xml = b'<?xml version="1.0"?><ServiceExceptionReport><ServiceException code="InvalidDimensionValue"/></ServiceExceptionReport>'
    with pytest.raises(FrameInvalid, match="implausibly small"):
        validate_frame(xml, SIZE)


def test_rejects_undecodable_bytes_of_plausible_length():
    with pytest.raises(FrameInvalid, match="did not decode"):
        validate_frame(b"\x00\x01\x02" * 5000, SIZE)


def test_rejects_a_wrong_sized_frame():
    """Overlays composite 1:1, so a size mismatch must never pass silently."""
    wrong = encode(noisy_image(size=(200, 100)))
    with pytest.raises(FrameInvalid, match="unexpected size"):
        validate_frame(wrong, SIZE)


def test_rejects_a_flat_fill():
    flat = encode(Image.new("RGB", SIZE, (128, 128, 128)))
    with pytest.raises(FrameInvalid, match="featureless"):
        validate_frame(flat, SIZE, min_bytes=0)


def test_rejects_an_all_black_night_frame():
    """The exact trap: a visible-light product at a night slot."""
    black = encode(Image.new("RGB", SIZE, (0, 0, 0)))
    with pytest.raises(FrameInvalid, match="featureless|black"):
        validate_frame(black, SIZE, min_bytes=0)


def test_a_dark_but_structured_frame_is_still_accepted():
    """Night IR is legitimately dark; it must not be mistaken for a blank."""
    rnd = random.Random(3)
    img = Image.new("RGB", SIZE)
    img.putdata([(rnd.randrange(5, 60),) * 3 for _ in range(SIZE[0] * SIZE[1])])
    stats = validate_frame(encode(img), SIZE, min_bytes=0)
    assert stats.mean < 60
    assert stats.stddev > 3.0


# ── the washed-out ceiling ────────────────────────────────────────────────────
#
# Cases are the real measurements from the 2026-08-09 daylight sweep,
# reconstructed as synthetic frames: a contiguous blown-out band over
# structured mid-tones, sized and toned to reproduce each row's mean and
# clipped fraction. Contiguous rather than scattered because that is how glare
# actually arrives, and because scattered white pixels would be smeared away by
# JPEG's 8x8 blocks before the validator ever saw them.

def glare_frame(clipped: float, base: int, size=SIZE, seed=5) -> bytes:
    """A frame with a known blown-out fraction over textured mid-tones."""
    rnd = random.Random(seed)
    width, height = size
    blown_rows = int(round(height * clipped))
    img = Image.new("RGB", size)
    pixels = [(255, 255, 255)] * (width * blown_rows)
    pixels += [(min(255, max(0, base + rnd.randrange(-35, 35))),) * 3
               for _ in range(width * (height - blown_rows))]
    img.putdata(pixels)
    return encode(img, quality=95)


def test_the_ceiling_is_off_unless_asked_for():
    """Infrared and raw-visible frames run bright over heavy convection; a
    global ceiling would discard them."""
    stats = validate_frame(glare_frame(0.50, 201), SIZE, min_bytes=0)
    assert stats.mean > 200


def test_rejects_the_owner_reported_0700_frame():
    """07:00 BST: mean 228, half the pixels at pure white."""
    with pytest.raises(FrameInvalid, match="washed out"):
        validate_frame(glare_frame(0.50, 201), SIZE, min_bytes=0,
                       max_mean=MAX_MEAN, max_clipped=MAX_CLIPPED)


def test_accepts_0900_when_the_sun_has_climbed():
    """09:00 BST: mean 173, 5.6% clipped — the first hour worth publishing."""
    stats = validate_frame(glare_frame(0.056, 168), SIZE, min_bytes=0,
                           max_mean=MAX_MEAN, max_clipped=MAX_CLIPPED)
    assert stats.mean < MAX_MEAN
    assert stats.clipped < MAX_CLIPPED


def test_accepts_1700_at_the_same_sun_angle_that_ruins_0700():
    """The asymmetry that forces per-frame measurement over a sun threshold:
    17:00 and 07:00 sit at ~20 degrees, and only one of them is unusable."""
    stats = validate_frame(glare_frame(0.014, 155), SIZE, min_bytes=0,
                           max_mean=MAX_MEAN, max_clipped=MAX_CLIPPED)
    assert stats.clipped < 0.05


def test_clipping_catches_the_dusk_frame_that_mean_alone_would_pass():
    """18:00 BST: half dark, half glare, so the average looks respectable at
    137 while 14.7% of the picture is gone. This is why both gates exist."""
    raw = glare_frame(0.147, 117)
    passes_mean_only = validate_frame(raw, SIZE, min_bytes=0, max_mean=MAX_MEAN)
    assert passes_mean_only.mean < MAX_MEAN

    with pytest.raises(FrameInvalid, match="detail is gone"):
        validate_frame(raw, SIZE, min_bytes=0,
                       max_mean=MAX_MEAN, max_clipped=MAX_CLIPPED)


def test_reports_the_clipped_fraction_it_measured():
    stats = validate_frame(glare_frame(0.25, 120), SIZE, min_bytes=0)
    assert 0.23 < stats.clipped < 0.27


def test_a_clean_frame_reports_no_clipping():
    stats = validate_frame(encode(noisy_image()), SIZE)
    assert stats.clipped == 0.0
