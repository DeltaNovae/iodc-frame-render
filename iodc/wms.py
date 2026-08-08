"""Talking to the WMS: discover valid capture slots, then fetch a pinned frame.

Why the TIME dimension is handled explicitly rather than left to the server:

  * The service advertises each layer's slots as an ISO8601 interval
    (``start/end/PT15M``) plus a ``default``. Layers do NOT advance in
    lockstep — one product can sit a slot or two behind another.
  * Relying on the default makes the published frame's real capture time
    unknowable, and `meta.json` must state it (staleness downstream is
    derived from capture time, never from publish time).
  * Pinning also makes retry meaningful: if the newest slot is not yet
    rendered, stepping back one slot is a precise, legal request rather
    than a hopeful repeat.
"""

from __future__ import annotations

import math
import time as _time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

WMS_BASE = "https://view.eumetsat.int/geoserver/msg_iodc/ows"
WMS_NS = {"wms": "http://www.opengis.net/wms"}
USER_AGENT = "iodc-frame-render/1.0"

_ISO_FORMATS = ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    """Parse the ISO8601 flavours this service emits, always tz-aware UTC."""
    value = value.strip()
    for fmt in _ISO_FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unrecognised ISO8601 timestamp: {value!r}")


def format_iso(dt: datetime) -> str:
    """The form the service accepts back in a GetMap TIME parameter."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_step(duration: str) -> timedelta:
    """Parse the subset of ISO8601 durations this service uses (PT#M / PT#H)."""
    text = duration.strip().upper()
    if not text.startswith("PT"):
        raise ValueError(f"unsupported duration: {duration!r}")
    body, number = text[2:], ""
    total = timedelta()
    for ch in body:
        if ch.isdigit():
            number += ch
        elif ch == "H":
            total += timedelta(hours=int(number or 0))
            number = ""
        elif ch == "M":
            total += timedelta(minutes=int(number or 0))
            number = ""
        elif ch == "S":
            total += timedelta(seconds=int(number or 0))
            number = ""
        else:
            raise ValueError(f"unsupported duration: {duration!r}")
    if total == timedelta():
        raise ValueError(f"zero-length duration: {duration!r}")
    return total


@dataclass(frozen=True)
class TimeDimension:
    """A layer's advertised capture slots."""

    layer: str
    start: datetime
    end: datetime
    step: timedelta
    default: datetime

    @property
    def latest(self) -> datetime:
        """Newest slot we are willing to request.

        The advertised ``default`` and the interval end normally agree; when
        they do not, take the earlier. The older slot is the one more likely
        to be fully rendered, and a frame that exists beats a frame that is
        newer by one step.
        """
        return min(self.default, self.end)

    def slots_desc(self, count: int, before: datetime | None = None) -> list:
        """``count`` slots, newest first — the retry ladder.

        ``before`` starts the ladder at a chosen moment instead of the newest
        slot, snapped down to the advertised grid. Production always wants the
        newest frame; this exists for rendering a specific past moment, which
        is the only way to exercise the daylight branch after dark.
        """
        newest = self.latest
        if before is not None:
            elapsed = (before - self.start).total_seconds()
            step_seconds = self.step.total_seconds()
            snapped = self.start + self.step * math.floor(elapsed / step_seconds)
            newest = min(snapped, newest)
        return [newest - (self.step * i) for i in range(count)]


def fetch_capabilities(base_url: str = WMS_BASE, timeout: int = 60) -> bytes:
    url = f"{base_url}?service=WMS&version=1.3.0&request=GetCapabilities"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_time_dimension(capabilities_xml: bytes, layer: str) -> TimeDimension:
    """Pull one layer's time extent out of a GetCapabilities document."""
    root = ET.fromstring(capabilities_xml)
    for element in root.iter(f"{{{WMS_NS['wms']}}}Layer"):
        name_el = element.find("wms:Name", WMS_NS)
        if name_el is None or name_el.text != layer:
            continue
        for dim in element.findall("wms:Dimension", WMS_NS):
            if (dim.get("name") or "").lower() != "time":
                continue
            extent = (dim.text or "").strip()
            parts = extent.split("/")
            if len(parts) != 3:
                raise ValueError(
                    f"layer {layer!r}: unsupported time extent {extent!r} "
                    "(expected start/end/step)"
                )
            start, end, step = parse_iso(parts[0]), parse_iso(parts[1]), parse_step(parts[2])
            default_raw = dim.get("default")
            default = parse_iso(default_raw) if default_raw else end
            return TimeDimension(layer, start, end, step, default)
        raise ValueError(f"layer {layer!r} advertises no time dimension")
    raise ValueError(f"layer {layer!r} not found in capabilities")


def build_getmap_url(layer: str, view, when: datetime, base_url: str = WMS_BASE) -> str:
    """A GetMap request with the capture slot pinned explicitly."""
    return (
        f"{base_url}?service=WMS&version=1.3.0&request=GetMap"
        f"&layers={layer}&styles=&crs=EPSG:4326"
        f"&bbox={view.bbox.as_wms()}"
        f"&width={view.width}&height={view.height}"
        f"&format=image/jpeg"
        f"&TIME={format_iso(when)}"
    )


def http_get(url: str, timeout: int = 90, attempts: int = 3, backoff: float = 2.0,
             sleep=_time.sleep) -> bytes:
    """GET with a small retry ladder for transient upstream failures.

    A 4xx is not retried — a malformed request will fail identically however
    many times it is repeated.
    """
    last_error = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise
            last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < attempts - 1:
            sleep(backoff * (2 ** attempt))
    raise RuntimeError(f"upstream fetch failed after {attempts} attempts: {last_error}")
