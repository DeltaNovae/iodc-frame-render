"""S0 self-test: prove the runner environment can do its whole per-cycle job.

Fetches one live frame from the public EUMETView WMS, composites the committed
test overlay, and encodes the result. No credentials, no uploads — this is the
toolchain proof that later stages build on (TIME pinning: S1; publishing: S4).

Run locally or via the workflow:  python selftest.py
"""

import io
import os
import sys
import urllib.request

from PIL import Image

WMS = "https://view.eumetsat.int/geoserver/msg_iodc/ows"

# Wide regional frame: Bay of Bengal through eastern India and the Myanmar coast.
# WMS 1.3.0 + EPSG:4326 => bbox axis order is lat,lon (minLat,minLon,maxLat,maxLon).
VIEW = {
    "layer": "ir108",          # 24h product; always populated, unlike visible layers at night
    "bbox": "10,80,28,100",
    "width": 889,
    "height": 800,
}

OVERLAY = os.path.join("overlays", "selftest-wide.png")
OUT = "selftest-out.jpg"

# A frame far below this is a blank/error tile rather than imagery (see S1 validation).
MIN_BYTES = 8_000


def fetch(view: dict) -> bytes:
    query = (
        f"?service=WMS&version=1.3.0&request=GetMap"
        f"&layers={view['layer']}&styles=&crs=EPSG:4326"
        f"&bbox={view['bbox']}&width={view['width']}&height={view['height']}"
        f"&format=image/jpeg"
    )
    req = urllib.request.Request(WMS + query, headers={"User-Agent": "iodc-frame-render/selftest"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        if resp.status != 200:
            raise SystemExit(f"FAIL: WMS returned HTTP {resp.status}")
        return resp.read()


def main() -> int:
    print("1. fetching a live frame from the public WMS ...")
    raw = fetch(VIEW)
    print(f"   got {len(raw) // 1024} KB")
    if len(raw) < MIN_BYTES:
        raise SystemExit(f"FAIL: implausibly small frame ({len(raw)} bytes) — blank slot?")

    frame = Image.open(io.BytesIO(raw)).convert("RGB")
    print(f"   decoded {frame.size}")
    if frame.size != (VIEW["width"], VIEW["height"]):
        raise SystemExit(f"FAIL: unexpected frame size {frame.size}")

    print("2. compositing the pre-authored overlay ...")
    overlay = Image.open(OVERLAY).convert("RGBA")
    if overlay.size != frame.size:
        raise SystemExit(f"FAIL: overlay {overlay.size} != frame {frame.size}")
    frame.paste(overlay, (0, 0), overlay)

    print("3. encoding ...")
    frame.save(OUT, "JPEG", quality=82, optimize=True)
    size_kb = os.path.getsize(OUT) // 1024
    print(f"   wrote {OUT} ({size_kb} KB)")

    print("\nPASS — fetch, composite and encode all work in this environment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
