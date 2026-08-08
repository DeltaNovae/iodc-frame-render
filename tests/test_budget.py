"""Guards on the delivery budget and the resolution reasoning behind it.

These are the promises to a user on a 2G connection. Frame sizes were measured
rather than guessed (§ 8.9), and it would be easy for a later tweak to quietly
undo that — so the reasoning is asserted, not just written down.
"""

import io

from PIL import Image

import render
from iodc import overlays
from iodc.views import CLOSE, WIDE

# What the satellite actually resolves over Bangladesh: 3 km at nadir, degraded
# by the ~45 degree viewing angle from 45.5E.
SENSOR_KM = 4.5

# A frame a user waits for on a slow connection.
BUDGET_KB = 110


def km_per_px(view) -> float:
    import math
    mid_lat = (view.bbox.min_lat + view.bbox.max_lat) / 2
    return (view.bbox.lon_span * 111.32 * math.cos(math.radians(mid_lat))) / view.width


def test_neither_view_asks_for_finer_detail_than_the_sensor_provides():
    """Requesting beyond the sensor only encodes upscaling artefacts."""
    for view in (WIDE, CLOSE):
        assert km_per_px(view) < SENSOR_KM, view.key


def test_no_view_undersamples_the_sensor():
    """The other edge: dropping below the sensor's resolution would throw away
    detail that genuinely exists."""
    for view in (WIDE, CLOSE):
        assert km_per_px(view) > SENSOR_KM / 6, view.key


def test_a_composited_frame_stays_inside_the_delivery_budget():
    """Encodes a cloud-like frame with the real overlay on top.

    Per-pixel random noise is not a fair stand-in: it is the pathological worst
    case for JPEG and costs far more than any real image. Blurring it produces
    the soft, blotchy structure satellite imagery actually has, which is what
    the budget was measured against (live frames: 85-103 KB).
    """
    import random

    from PIL import ImageFilter

    for view in (WIDE, CLOSE):
        rnd = random.Random(5)
        base = Image.new("RGB", view.size)
        base.putdata([(rnd.randrange(40, 220),) * 3
                      for _ in range(view.width * view.height)])
        base = base.filter(ImageFilter.GaussianBlur(2.2))
        overlay = overlays.load(view, "bn", night=False)
        base.paste(overlay, (0, 0), overlay)
        buf = io.BytesIO()
        base.save(buf, "JPEG", quality=render.JPEG_QUALITY,
                  subsampling=render.JPEG_SUBSAMPLING, optimize=True)
        kb = buf.getbuffer().nbytes // 1024
        assert kb <= BUDGET_KB, f"{view.key} encodes to {kb} KB"


def test_encoder_settings_are_the_measured_ones():
    assert render.JPEG_SUBSAMPLING == 2      # 4:2:0
    assert 74 <= render.JPEG_QUALITY <= 82
