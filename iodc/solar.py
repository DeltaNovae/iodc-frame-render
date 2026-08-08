"""Solar elevation — decides whether the visible-light product is worth asking for.

The switch is deliberately *not* sunrise/sunset. A few degrees above the horizon
the visible product is a dim, useless smear: technically daylight, practically
unusable. What matters is whether the scene is lit well enough to read, so the
test is the sun's elevation angle at the frame's centre against a threshold well
above the horizon.

Standard NOAA solar-position equations, accurate to well under a degree — far
more precision than a threshold decision needs.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

# Below this the visible product is too dim to publish. Chosen above civil
# twilight: the frame should look like daylight, not like a rumour of it.
DAYLIGHT_MIN_ELEVATION = 12.0


def _julian_day(when: datetime) -> float:
    when = when.astimezone(timezone.utc)
    year, month = when.year, when.month
    day = (when.day + when.hour / 24 + when.minute / 1440
           + when.second / 86400)
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return (math.floor(365.25 * (year + 4716))
            + math.floor(30.6001 * (month + 1)) + day + b - 1524.5)


def solar_elevation(lat: float, lon: float, when: datetime) -> float:
    """The sun's elevation angle in degrees; negative means below the horizon."""
    jd = _julian_day(when)
    n = jd - 2451545.0

    mean_long = (280.460 + 0.9856474 * n) % 360
    mean_anom = math.radians((357.528 + 0.9856003 * n) % 360)
    ecliptic_long = math.radians(
        mean_long + 1.915 * math.sin(mean_anom) + 0.020 * math.sin(2 * mean_anom)
    )
    obliquity = math.radians(23.439 - 0.0000004 * n)

    declination = math.asin(math.sin(obliquity) * math.sin(ecliptic_long))
    right_ascension = math.atan2(
        math.cos(obliquity) * math.sin(ecliptic_long), math.cos(ecliptic_long)
    )

    gmst = (18.697374558 + 24.06570982441908 * n) % 24
    local_sidereal = math.radians((gmst * 15 + lon) % 360)
    hour_angle = local_sidereal - right_ascension

    lat_rad = math.radians(lat)
    elevation = math.asin(
        math.sin(lat_rad) * math.sin(declination)
        + math.cos(lat_rad) * math.cos(declination) * math.cos(hour_angle)
    )
    return math.degrees(elevation)


def is_daylight(lat: float, lon: float, when: datetime,
                threshold: float = DAYLIGHT_MIN_ELEVATION) -> bool:
    return solar_elevation(lat, lon, when) >= threshold
