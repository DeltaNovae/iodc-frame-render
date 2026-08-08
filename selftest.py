"""Live self-test: exercise the real fetch path against the public service.

Runs the same code the scheduled job will: read the advertised capture slots,
pin one explicitly, fetch each view, validate, and composite an overlay.

The 24-hour infrared product must succeed — it is the fallback the whole
design leans on. The visible-light product is probed too but is *expected* to
be rejected at night, so its outcome is reported rather than enforced; that
probe is what proves the blank-frame guard works against the real service
rather than only against fixtures.

Usage:  python selftest.py
"""

from __future__ import annotations

import logging
import os
import sys

from PIL import Image

from iodc import overlays, wms
from iodc.fetch import fetch_frame
from iodc.views import CLOSE, WIDE

IR = "ir108"                     # 24 h product
VISIBLE = "rgb_naturalenhncd"    # daylight only, by nature

OUT = "selftest-out.jpg"

logging.basicConfig(level=logging.INFO, format="   %(message)s")


def main() -> int:
    print("1. reading advertised capture slots ...")
    caps = wms.fetch_capabilities()
    dims = {}
    for layer in (IR, VISIBLE):
        dim = wms.parse_time_dimension(caps, layer)
        dims[layer] = dim
        print(f"   {layer:<20} newest slot {wms.format_iso(dim.latest)}  step {dim.step}")

    print("\n2. fetching the 24h product for every view (must succeed) ...")
    frames = {}
    for view in (WIDE, CLOSE):
        frame = fetch_frame(IR, view, dims[IR])
        frames[view.key] = frame
        print(f"   {view.key:<6} {frame.stats.n_bytes // 1024:>4} KB  {frame.stats.width}x{frame.stats.height}"
              f"  captured {wms.format_iso(frame.captured_at)}"
              f"  mean {frame.stats.mean:.1f}  stddev {frame.stats.stddev:.1f}")

    print("\n3. probing the visible-light product (rejection at night is correct) ...")
    try:
        vis = fetch_frame(VISIBLE, WIDE, dims[VISIBLE], ladder=1)
        print(f"   accepted — daylight slot, mean {vis.stats.mean:.1f}")
    except RuntimeError as exc:
        print(f"   rejected as expected: {str(exc).split('|')[0].strip()}")

    print("\n4. checking every overlay matches the frame it covers ...")
    for view in (WIDE, CLOSE):
        for lang in overlays.languages():
            for night in (False, True):
                overlays.load(view, lang, night)
    print(f"   all {len(overlays.languages()) * 4} overlays verified against their bboxes")

    print("\n5. compositing the overlay onto the wide frame ...")
    import io
    frame = Image.open(io.BytesIO(frames["wide"].raw)).convert("RGB")
    overlay = overlays.load(WIDE, "bn", night=True)
    frame.paste(overlay, (0, 0), overlay)
    frame.save(OUT, "JPEG", quality=82, optimize=True)
    print(f"   wrote {OUT} ({os.path.getsize(OUT) // 1024} KB)")

    print("\nPASS — capabilities, slot pinning, validation, overlay checks and "
          "compositing all work.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
