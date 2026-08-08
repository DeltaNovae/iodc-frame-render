import io
import random

import pytest
from PIL import Image

from iodc.validate import FrameInvalid, validate_frame

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
