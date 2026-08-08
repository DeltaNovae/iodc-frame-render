from datetime import datetime, timedelta, timezone

import pytest

from iodc import wms
from iodc.views import CLOSE, WIDE

# Trimmed from a real GetCapabilities response: two layers whose newest slots
# deliberately differ, which is the case that broke naive "just use the latest".
CAPS = b"""<?xml version="1.0" encoding="UTF-8"?>
<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms">
  <Capability>
    <Layer>
      <Title>root</Title>
      <Layer queryable="1">
        <Name>ir108</Name>
        <Title>High Rate SEVIRI IR10.8</Title>
        <Dimension name="time" units="ISO8601" default="2026-08-08T17:30:00Z" nearestValue="1"
          >2020-08-01T00:00:00.000Z/2026-08-08T17:30:00.000Z/PT15M</Dimension>
      </Layer>
      <Layer queryable="1">
        <Name>rgb_naturalenhncd</Name>
        <Title>Natural Colour Enhanced RGB</Title>
        <Dimension name="time" units="ISO8601" default="2026-08-08T17:15:00Z" nearestValue="1"
          >2020-08-01T00:00:00.000Z/2026-08-08T17:15:00.000Z/PT15M</Dimension>
      </Layer>
      <Layer queryable="1">
        <Name>no_time_layer</Name>
        <Title>Layer without a time dimension</Title>
      </Layer>
    </Layer>
  </Capability>
</WMS_Capabilities>
"""


def test_parses_time_extent_and_default():
    dim = wms.parse_time_dimension(CAPS, "ir108")
    assert dim.layer == "ir108"
    assert dim.start == datetime(2020, 8, 1, tzinfo=timezone.utc)
    assert dim.end == datetime(2026, 8, 8, 17, 30, tzinfo=timezone.utc)
    assert dim.default == datetime(2026, 8, 8, 17, 30, tzinfo=timezone.utc)
    assert dim.step == timedelta(minutes=15)


def test_layers_advance_independently():
    """Two products in one document can sit at different newest slots."""
    ir = wms.parse_time_dimension(CAPS, "ir108")
    vis = wms.parse_time_dimension(CAPS, "rgb_naturalenhncd")
    assert ir.latest - vis.latest == timedelta(minutes=15)


def test_latest_prefers_the_earlier_of_default_and_extent_end():
    """A slot that exists beats a slot that is merely newer."""
    dim = wms.TimeDimension(
        layer="x",
        start=datetime(2020, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 8, 17, 30, tzinfo=timezone.utc),
        step=timedelta(minutes=15),
        default=datetime(2026, 8, 8, 17, 15, tzinfo=timezone.utc),
    )
    assert dim.latest == datetime(2026, 8, 8, 17, 15, tzinfo=timezone.utc)


def test_slot_ladder_steps_backwards_by_one_step():
    dim = wms.parse_time_dimension(CAPS, "ir108")
    slots = dim.slots_desc(3)
    assert slots == [
        datetime(2026, 8, 8, 17, 30, tzinfo=timezone.utc),
        datetime(2026, 8, 8, 17, 15, tzinfo=timezone.utc),
        datetime(2026, 8, 8, 17, 0, tzinfo=timezone.utc),
    ]


def test_ladder_can_start_from_a_chosen_moment_snapped_to_the_grid():
    """Rendering a specific past instant is the only way to exercise the
    daylight branch after dark, so the ladder must be able to start there."""
    dim = wms.parse_time_dimension(CAPS, "ir108")
    slots = dim.slots_desc(3, before=datetime(2026, 8, 8, 7, 7, tzinfo=timezone.utc))
    assert slots == [
        datetime(2026, 8, 8, 7, 0, tzinfo=timezone.utc),    # snapped down
        datetime(2026, 8, 8, 6, 45, tzinfo=timezone.utc),
        datetime(2026, 8, 8, 6, 30, tzinfo=timezone.utc),
    ]


def test_a_future_moment_cannot_ask_for_a_slot_that_does_not_exist_yet():
    dim = wms.parse_time_dimension(CAPS, "ir108")
    slots = dim.slots_desc(1, before=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert slots == [dim.latest]


def test_missing_layer_and_missing_dimension_are_distinct_errors():
    with pytest.raises(ValueError, match="not found"):
        wms.parse_time_dimension(CAPS, "does_not_exist")
    with pytest.raises(ValueError, match="no time dimension"):
        wms.parse_time_dimension(CAPS, "no_time_layer")


@pytest.mark.parametrize(
    "duration,expected",
    [("PT15M", timedelta(minutes=15)), ("PT1H", timedelta(hours=1)),
     ("PT1H30M", timedelta(hours=1, minutes=30)), ("PT30S", timedelta(seconds=30))],
)
def test_parse_step(duration, expected):
    assert wms.parse_step(duration) == expected


@pytest.mark.parametrize("bad", ["P1D", "", "15M", "PT0M"])
def test_parse_step_rejects_unsupported(bad):
    with pytest.raises(ValueError):
        wms.parse_step(bad)


def test_parse_iso_accepts_both_flavours_and_is_utc():
    a = wms.parse_iso("2026-08-08T17:30:00.000Z")
    b = wms.parse_iso("2026-08-08T17:30:00Z")
    assert a == b
    assert a.tzinfo is timezone.utc


def test_getmap_url_pins_time_and_uses_lat_first_bbox():
    when = datetime(2026, 8, 8, 17, 30, tzinfo=timezone.utc)
    url = wms.build_getmap_url("ir108", WIDE, when)
    # WMS 1.3.0 + EPSG:4326 => minLat,minLon,maxLat,maxLon
    assert "bbox=10,80,28,100" in url
    assert "TIME=2026-08-08T17:30:00Z" in url
    # Derived from the view, so tuning frame sizes cannot break this test.
    assert f"width={WIDE.width}&height={WIDE.height}" in url
    assert "crs=EPSG:4326" in url


def test_views_have_matching_pixel_and_geographic_aspect():
    """Guards against a stretched frame when a bbox or size is edited."""
    for view in (WIDE, CLOSE):
        assert view.aspect_error() < 0.01, view.key


def test_http_get_retries_transient_failures_then_succeeds():
    calls = {"n": 0}

    def flaky(url, timeout=90):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("connection reset")
        return b"payload"

    def fake_open(req, timeout=90):
        return flaky(req.full_url, timeout)

    import urllib.request
    original = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=90: _ctx(fake_open(req, timeout))
    try:
        out = wms.http_get("https://example.invalid/x", attempts=3, sleep=lambda _: None)
        assert out == b"payload"
        assert calls["n"] == 3
    finally:
        urllib.request.urlopen = original


def test_http_get_gives_up_after_the_configured_attempts():
    import urllib.request
    original = urllib.request.urlopen

    def always_fail(req, timeout=90):
        raise OSError("down")

    urllib.request.urlopen = always_fail
    try:
        with pytest.raises(RuntimeError, match="after 2 attempts"):
            wms.http_get("https://example.invalid/x", attempts=2, sleep=lambda _: None)
    finally:
        urllib.request.urlopen = original


class _ctx:
    """Minimal context manager standing in for an HTTP response."""

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload
