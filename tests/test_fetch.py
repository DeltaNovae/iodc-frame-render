import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from iodc import wms
from iodc.fetch import fetch_frame
from iodc.views import WIDE

DIM = wms.TimeDimension(
    layer="ir108",
    start=datetime(2020, 1, 1, tzinfo=timezone.utc),
    end=datetime(2026, 8, 8, 17, 30, tzinfo=timezone.utc),
    step=timedelta(minutes=15),
    default=datetime(2026, 8, 8, 17, 30, tzinfo=timezone.utc),
)


def good_jpeg(size=WIDE.size) -> bytes:
    import random
    rnd = random.Random(11)
    img = Image.new("RGB", size)
    img.putdata([(rnd.randrange(40, 220),) * 3 for _ in range(size[0] * size[1])])
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def blank_jpeg(size=WIDE.size) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (0, 0, 0)).save(buf, "JPEG", quality=85)
    return buf.getvalue()


def test_uses_the_newest_slot_when_it_is_good():
    seen = []

    def getter(url):
        seen.append(url)
        return good_jpeg()

    frame = fetch_frame("ir108", WIDE, DIM, getter=getter)
    assert frame.captured_at == DIM.latest
    assert len(seen) == 1
    assert "TIME=2026-08-08T17:30:00Z" in seen[0]


def test_walks_back_a_slot_when_the_newest_is_blank():
    """The blank-slot trap: reject and step back rather than publish it."""
    calls = {"n": 0}

    def getter(url):
        calls["n"] += 1
        return blank_jpeg() if calls["n"] == 1 else good_jpeg()

    frame = fetch_frame("ir108", WIDE, DIM, getter=getter)
    assert frame.captured_at == DIM.latest - timedelta(minutes=15)
    assert calls["n"] == 2


def test_walks_back_when_a_slot_errors():
    calls = {"n": 0}

    def getter(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("upstream 503")
        return good_jpeg()

    frame = fetch_frame("ir108", WIDE, DIM, getter=getter)
    assert frame.captured_at == DIM.latest - timedelta(minutes=15)


def test_reports_the_capture_time_not_the_publish_time():
    """Downstream staleness is measured from capture; this is where it starts."""
    frame = fetch_frame("ir108", WIDE, DIM, getter=lambda url: good_jpeg())
    assert frame.captured_at.tzinfo is timezone.utc
    assert frame.captured_at == datetime(2026, 8, 8, 17, 30, tzinfo=timezone.utc)


def test_gives_up_after_exhausting_the_ladder_and_names_the_failures():
    with pytest.raises(RuntimeError, match="no usable frame"):
        fetch_frame("ir108", WIDE, DIM, ladder=3, getter=lambda url: blank_jpeg())


def test_a_wrong_sized_frame_is_rejected_rather_than_composited():
    with pytest.raises(RuntimeError, match="no usable frame"):
        fetch_frame("ir108", WIDE, DIM, ladder=2,
                    getter=lambda url: good_jpeg(size=(100, 100)))
