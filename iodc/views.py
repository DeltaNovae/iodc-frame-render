"""Frame geometry: what rectangle of the world each published view covers.

WMS 1.3.0 with EPSG:4326 takes bbox in **lat,lon** order
(minLat,minLon,maxLat,maxLon) — the axis-order trap that silently returns a
transposed or empty frame if you pass lon first.

Pixel aspect is kept equal to the bbox aspect so the plate-carrée grid maps
1:1 and nothing is stretched beyond the projection's own behaviour.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BBox:
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    def as_wms(self) -> str:
        """WMS 1.3.0 / EPSG:4326 order: lat first."""
        return f"{self.min_lat},{self.min_lon},{self.max_lat},{self.max_lon}"

    @property
    def lat_span(self) -> float:
        return self.max_lat - self.min_lat

    @property
    def lon_span(self) -> float:
        return self.max_lon - self.min_lon


@dataclass(frozen=True)
class View:
    key: str
    bbox: BBox
    width: int
    height: int

    @property
    def size(self) -> tuple:
        return (self.width, self.height)

    def aspect_error(self) -> float:
        """Relative mismatch between pixel aspect and geographic aspect."""
        pixel = self.width / self.height
        geo = self.bbox.lon_span / self.bbox.lat_span
        return abs(pixel - geo) / geo


# Frame sizes were measured, not guessed (§ 8.9). The satellite resolves ~4.5 km
# over Bangladesh, so both views still request more pixels than exist — asking
# for even more only encodes upscaling artefacts at 2G users' expense.
#
# What pins the floor is not the sensor but the OVERLAY: its labels are drawn at
# absolute pixel sizes, so they cannot be shrunk with the frame and still be
# read. That is why "publish small and let the device upscale" does not work
# here — the text would blur.

# Wide regional: the "what's coming" view — Bay of Bengal up through eastern
# India, Nepal's edge and the Myanmar coast. Deliberately not clipped to any
# country: cyclones form deep in the Bay days before landfall.
# 700×630 ⇒ ~3.0 km/px, 1.5× the sensor. ~102 KB day / 87 KB night.
WIDE = View(
    key="wide",
    bbox=BBox(min_lat=10, min_lon=80, max_lat=28, max_lon=100),
    width=700,
    height=630,
)

# Close-up: the "what's over us now" view — the delta plus roughly 150–230 km
# of margin on every side.
# 640×640 ⇒ ~1.44 km/px, still 3.1× the sensor. ~85 KB day / 72 KB night.
CLOSE = View(
    key="close",
    bbox=BBox(min_lat=19, min_lon=86, max_lat=28, max_lon=95),
    width=640,
    height=640,
)

VIEWS = {v.key: v for v in (WIDE, CLOSE)}
