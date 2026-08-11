"""Which INSTANT the sun-driven ladders decide at.

Regression cover for a defect that shipped and was invisible to 164 passing
tests, because nothing exercised `render_cycle` at all — it does network I/O,
so it was never covered, and the one thing worth pinning about it lived
entirely in that uncovered path.

The defect: the ladders were evaluated at `when` (the moment the run starts),
while the frame they classify is the newest slot upstream has published — 24 to
39 minutes older. A 12:30Z frame at sun +0.2 deg, which is inside fog's blind
band, was classified with the NIGHT recipe because the run happened at 13:00Z
with the sun at -6.3 deg. The night recipe scores 2.1% on real dense fog at sun
+2.8 deg, so that frame's confident "no fog" carried no information.

Worse than a wrong answer: it SHIFTS the blind band by half an hour, so the
one mechanism built to refuse untrustworthy hours guards the wrong hour — and
at dawn it fails towards the fog hazard window this product exists for.
"""

from datetime import datetime, timedelta, timezone

import pytest

import render
from iodc import fog, wms


@pytest.fixture
def slot_at(monkeypatch):
    """Pin what upstream advertises as newest, and stub the network away."""
    def _pin(newest: datetime):
        monkeypatch.setattr(wms, "fetch_capabilities", lambda *a, **k: b"<caps/>")
        monkeypatch.setattr(render.wms, "fetch_capabilities",
                            lambda *a, **k: b"<caps/>")
        dim = wms.TimeDimension(
            layer="ir108",   # not render.DECISION_LAYER: this test must
                             # detect the BEHAVIOUR, not the presence of a symbol
            start=newest - timedelta(days=1),
            end=newest,
            step=timedelta(minutes=15),
            default=newest,
        )
        monkeypatch.setattr(render.wms, "parse_time_dimension",
                            lambda *a, **k: dim)
        return dim
    return _pin


def _run(run_at, **kw):
    """A cycle where the only product declines raises by design — that is the
    "every product failed" guard, not a test failure. The ladder has already
    been consulted by then, which is what these tests inspect."""
    try:
        render.render_cycle(run_at, force="fog", **kw)
    except RuntimeError as exc:
        assert "every product failed" in str(exc)


def _record_fog_ladder(monkeypatch):
    seen = []

    def spy(at):
        seen.append(at)
        return []          # decline, so nothing tries to fetch a frame
    monkeypatch.setattr(render.fog, "ladder", spy)
    return seen


def test_ladders_decide_at_the_capture_slot_not_at_run_time(slot_at, monkeypatch):
    """The exact production case that misfired."""
    run_at = datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)   # sun -6.3
    slot = datetime(2026, 8, 11, 12, 30, tzinfo=timezone.utc)    # sun +0.2
    slot_at(slot)
    seen = _record_fog_ladder(monkeypatch)

    _run(run_at)

    assert seen, "the fog ladder was never consulted"
    assert seen[0] == slot, (
        f"decided at {seen[0]}, but the frame it classifies is from {slot}"
    )
    assert seen[0] != run_at


def test_the_blind_band_is_judged_on_the_frame_being_classified(slot_at,
                                                                monkeypatch):
    """The consequence that matters: with the real ladder, a twilight frame
    must DECLINE even though the sun has since dropped below the horizon."""
    run_at = datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)
    slot = datetime(2026, 8, 11, 12, 30, tzinfo=timezone.utc)
    slot_at(slot)

    # Hold the REAL ladder: the spy below replaces fog.ladder module-wide, so
    # asserting through the patched name would only re-test the spy.
    real_ladder = fog.ladder
    assert real_ladder(slot) == [], "the 12:30Z slot should be in the blind band"
    assert real_ladder(run_at), "13:00Z is night — the old code decided here"

    seen = _record_fog_ladder(monkeypatch)
    _run(run_at)
    assert real_ladder(seen[0]) == [], (
        "the cycle decided at an instant where fog would NOT decline, so the "
        "blind band is guarding the wrong half hour"
    )


def test_a_pinned_instant_is_its_own_decision_time(slot_at, monkeypatch):
    """`--at` reproduces a specific moment; the slot must not override it, or
    the daylight branch could never be exercised after dark."""
    pinned = datetime(2026, 1, 7, 2, 30, tzinfo=timezone.utc)
    slot_at(datetime(2026, 8, 11, 12, 30, tzinfo=timezone.utc))
    seen = _record_fog_ladder(monkeypatch)

    _run(pinned, pinned=True)

    assert seen[0] == pinned
