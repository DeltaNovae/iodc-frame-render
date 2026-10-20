"""Whose lateness is it? (2026-10-19)

EUMETSAT ran 75–105 min behind for eight hours; every cycle published the
newest slot that existed, and the age-only alarm paged ~37 times for nothing we
could fix. Each cycle now stamps its own time and the newest upstream slot into
the pointer, and `classify_freshness` tells an upstream delay from our stall.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import render
from iodc import publish, wms
from iodc.products import Product

from .test_overlay_publish import _FlatImage, _RecordingClient
from .test_render_cycle import NIGHT_RUN, NIGHT_SLOT, cycle  # noqa: F401 (fixture)

LIMIT = 90


def _t(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 10, 19, h, m, tzinfo=timezone.utc)


def _meta(cycle_at=None, upstream=None) -> dict:
    meta = {"products": {}}
    if cycle_at:
        meta["cycleAtUtc"] = publish._iso(_t(cycle_at))
    if upstream:
        meta["upstreamLatestUtc"] = publish._iso(_t(upstream))
    return meta


def _verdict(oldest, now, **stamps):
    return publish.classify_freshness(_meta(**stamps), _t(oldest), _t(now), LIMIT)


# ── The verdicts, on the 2026-10-19 cases ────────────────────────────────────

def test_fresh_is_fresh_whatever_the_stamps_say():
    assert _verdict("16:30", "17:02") == publish.FRESH


def test_the_17_00z_cycle_was_upstream_not_us():
    """Run log: 'cycle at 17:00:18Z, deciding for slot 15:30:00Z (90 min
    behind)'; verify at 17:02 read the 15:30 capture as 92 min old."""
    assert _verdict("15:30", "17:02", cycle_at="17:00", upstream="15:30") == publish.UPSTREAM_LATE


def test_rain_one_slot_behind_upstream_is_still_upstream():
    """20:45Z: oldest non-fog 19:15 (rain), upstream newest 19:30."""
    assert _verdict("19:15", "20:47", cycle_at="20:45", upstream="19:30") == publish.UPSTREAM_LATE


def test_upstream_late_past_three_hours_pages():
    assert _verdict("13:00", "16:05", cycle_at="16:00", upstream="13:00") == publish.UPSTREAM_VERY_LATE


def test_upstream_offering_newer_than_we_published_is_our_stall():
    """A product stuck while upstream moved on — carried forward each cycle, so
    the heartbeat stays fresh and only the upstream comparison can see it."""
    assert _verdict("15:00", "17:02", cycle_at="17:00", upstream="16:30") == publish.STALLED


# ── Each product against its own layer (2026-10-20) ───────────────────────────

def _layered(rain_at, clouds_at, by_layer):
    meta = _meta(cycle_at="17:45", upstream="17:15")
    meta["upstreamLatestByLayer"] = {k: publish._iso(_t(v)) for k, v in by_layer.items()}
    meta["products"] = {
        "rain": {"layer": "h63", "views": {"wide-bn": {"capturedAtUtc": publish._iso(_t(rain_at))}}},
        "clouds": {"layer": "ir108", "views": {"wide-bn": {"capturedAtUtc": publish._iso(_t(clouds_at))}}},
        # Fog pauses by design and is judged apart: never a stall here.
        "fog": {"layer": "rgb_fog", "views": {"wide-bn": {"capturedAtUtc": publish._iso(_t("12:00"))}}},
    }
    return publish.classify_freshness(meta, _t(min(rain_at, clouds_at)), _t("17:47"), LIMIT)


def test_rain_behind_ir108_but_level_with_h63_is_upstream():
    """17:45Z run: ir108 newest 17:15, h63 newest 16:00, rain published 16:00.
    Judged against ir108 this paged 'our pipeline stalled' (#206)."""
    assert _layered("16:00", "17:15", {"ir108": "17:15", "h63": "16:00"}) == publish.UPSTREAM_LATE


def test_rain_behind_its_own_layer_is_our_stall():
    assert _layered("15:00", "17:15", {"ir108": "17:15", "h63": "16:00"}) == publish.STALLED


def test_a_layer_without_a_stamp_uses_the_decision_slot():
    assert _layered("16:00", "17:15", {"ir108": "17:15"}) == publish.STALLED


def test_a_silent_heartbeat_is_not_publishing():
    assert _verdict("15:00", "17:02", cycle_at="15:45", upstream="15:00") == publish.NOT_PUBLISHING


def test_an_unstamped_pointer_falls_back_to_age_only():
    """Pointers from before the stamps, or from a pinned run, must never turn a
    real stall into silence."""
    assert _verdict("15:00", "17:02") == publish.STALLED


# ── The cycle stamps them ────────────────────────────────────────────────────

def test_a_cycle_stamps_its_time_and_the_upstream_slot(cycle):
    cycle(RuntimeError("unused"), failing_product="no-such-product")
    result = render.render_cycle(NIGHT_RUN)
    assert result["cycle_at"] == NIGHT_RUN
    assert result["upstream_latest"] == NIGHT_SLOT


def test_a_cycle_stamps_each_layers_own_newest_slot(cycle, monkeypatch):
    cycle(RuntimeError("unused"), failing_product="no-such-product")
    lag = {"h63": timedelta(minutes=75)}

    def per_layer(caps, layer):
        at = NIGHT_SLOT - lag.get(layer, timedelta())
        return wms.TimeDimension(layer, at - timedelta(days=1), at, timedelta(minutes=15), at)
    monkeypatch.setattr(render.wms, "parse_time_dimension", per_layer)
    result = render.render_cycle(NIGHT_RUN)
    assert result["upstream_by_layer"]["ir108"] == NIGHT_SLOT
    assert result["upstream_by_layer"]["h63"] == NIGHT_SLOT - timedelta(minutes=75)


def test_a_pinned_cycle_stamps_nothing(cycle):
    cycle(RuntimeError("unused"), failing_product="no-such-product")
    result = render.render_cycle(NIGHT_RUN, pinned=True)
    assert "cycle_at" not in result and "upstream_latest" not in result
    assert "upstream_by_layer" not in result


def _publish(monkeypatch, **stamps):
    monkeypatch.setattr(render, "encode", lambda image, quality: b"jpegbytes")
    payload = {"image": _FlatImage(), "bare": _FlatImage(), "stamped": _FlatImage(),
               "overlay_variant": "night", "captured_at": NIGHT_SLOT, "layer": "ir108"}
    result = {"products": {"storm": {"product": Product("ir108", is_night=True, key="storm"),
                                     "views": {"wide": {"bn": payload}}}}, **stamps}
    target = publish.Target("http://e", "b", "k", "s", prefix="sat")
    return render.publish_cycle(result, _RecordingClient(), target)


def test_the_pointer_carries_the_stamps(monkeypatch):
    meta = _publish(monkeypatch, cycle_at=NIGHT_RUN, upstream_latest=NIGHT_SLOT)
    assert meta["cycleAtUtc"] == "2026-08-11T20:30:00Z"
    assert meta["upstreamLatestUtc"] == "2026-08-11T20:00:00Z"


def test_the_pointer_carries_the_per_layer_stamps(monkeypatch):
    meta = _publish(monkeypatch, cycle_at=NIGHT_RUN, upstream_latest=NIGHT_SLOT,
                    upstream_by_layer={"ir108": NIGHT_SLOT,
                                       "h63": NIGHT_SLOT - timedelta(minutes=15)})
    assert meta["upstreamLatestByLayer"] == {"h63": "2026-08-11T19:45:00Z",
                                             "ir108": "2026-08-11T20:00:00Z"}


def test_an_unstamped_cycle_writes_no_stamps(monkeypatch):
    meta = _publish(monkeypatch)
    assert "cycleAtUtc" not in meta and "upstreamLatestUtc" not in meta
