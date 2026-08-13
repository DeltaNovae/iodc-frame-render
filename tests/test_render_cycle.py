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


# ── product isolation ─────────────────────────────────────────────────────────
#
# The per-product boundary promises one failing product is skipped while the
# others still publish. It caught only RuntimeError — and almost nothing raised
# inside it is one: a missing overlay is FileNotFoundError, a mismatched overlay
# is OverlayMismatch, a rain/base size disagreement is ValueError. Any of those
# killed the entire cycle.
#
# What makes that serious is that they are DETERMINISTIC. A truncated overlay
# manifest is not a bad second the next cycle survives; it fails identically
# every fifteen minutes until a human intervenes, which is the one failure class
# this pipeline is built not to have.


class _FakeImage:
    """Stands in for a PIL image everywhere the cycle touches one.

    `copy` returns a NEW instance and `paste` is recorded, because the loop
    frame is defined by what has NOT been pasted onto it — a fake whose
    copy returned `self` would make the bare frame and the composited one the
    same object and quietly satisfy any assertion about the difference.
    """
    size = (640, 640)
    width, height = 640, 640

    def __init__(self):
        self.pastes = 0

    def convert(self, mode):
        return self

    def copy(self):
        return _FakeImage()

    def paste(self, *a, **k):
        self.pastes += 1


class _FakeFrame:
    def __init__(self, at):
        self.captured_at = at
        self.raw = b""


#: A deep-night slot, so all four products have an instrument and the cycle is
#: at full width. 12:30Z would put fog in its blind band, and a product that
#: declines on its own is not the thing these tests are about.
NIGHT_SLOT = datetime(2026, 8, 11, 20, 0, tzinfo=timezone.utc)   # 02:00 BST
NIGHT_RUN = datetime(2026, 8, 11, 20, 30, tzinfo=timezone.utc)


@pytest.fixture
def cycle(monkeypatch):
    """A full four-product cycle with every image operation stubbed.

    Everything the cycle can touch is stubbed — rain's and fog's own compose
    paths and the light-theme overlays included — so the ONLY failure in a test
    is the one that test injects. An earlier version left the rain overlays real
    and its assertions were partly satisfied by the harness's own errors being
    caught, which proves nothing about the code under test.
    """
    monkeypatch.setattr(render.wms, "fetch_capabilities", lambda *a, **k: b"<caps/>")
    dim = wms.TimeDimension("ir108", NIGHT_SLOT - timedelta(days=1), NIGHT_SLOT,
                            timedelta(minutes=15), NIGHT_SLOT)
    monkeypatch.setattr(render.wms, "parse_time_dimension", lambda *a, **k: dim)

    monkeypatch.setattr(render.Image, "open", lambda *a, **k: _FakeImage())
    monkeypatch.setattr(render, "_tone", lambda image, product: image)
    monkeypatch.setattr(render, "_stamp", lambda image, *a, **k: image)
    monkeypatch.setattr(render.rain, "compose_bare", lambda raw, view: _FakeImage())
    monkeypatch.setattr(render.rain, "add_lines", lambda image, view: image)
    monkeypatch.setattr(render.fog, "compose",
                        lambda raw, view, night: _FakeImage())
    monkeypatch.setattr(render.overlays, "languages", lambda: ["bn"])
    monkeypatch.setattr(render.overlays, "load", lambda *a, **k: _FakeImage())
    monkeypatch.setattr(render.overlays, "load_light_labels",
                        lambda *a, **k: _FakeImage())

    def _install(failure, failing_product=None):
        """Raise `failure` from the named product's fetch — or from all of them
        when `failing_product` is None."""
        def fake_fetch(caps, rungs, view, before=None, fetched=None):
            if not rungs:
                # Faithful to the real function: an empty ladder is a decline.
                raise RuntimeError(f"no usable instrument for {view.key}")
            if failing_product is None or rungs[0].key == failing_product:
                raise failure
            return _FakeFrame(NIGHT_SLOT), rungs[0]
        monkeypatch.setattr(render, "_fetch_down_the_ladder", fake_fetch)

    return _install


@pytest.mark.parametrize("failure", [
    FileNotFoundError("no overlay for view=wide lang=bn night=False"),
    ValueError("rain frame (700, 630) does not match base (640, 640)"),
    KeyError("close"),
    RuntimeError("no usable frame in the newest 4 slots"),
])
def test_one_product_failing_leaves_the_others_published(cycle, failure):
    """Whatever a product raises, every other product still publishes.

    Parameterised over the exception types actually reachable inside that
    block. Only the last of them was caught before this widened."""
    cycle(failure, failing_product="rain")

    result = render.render_cycle(NIGHT_RUN)

    published = set(result["products"])
    assert "rain" not in published, "the injected failure did not take effect"
    assert published == {"clouds", "storm", "fog"}, (
        f"{type(failure).__name__} cost more than the product that raised it: "
        f"published {sorted(published)}"
    )


def test_an_overlay_mismatch_is_survivable(cycle):
    """The deterministic case spelled out: a wrong-bbox overlay fails the same
    way on every future cycle, so failing the whole run turns one bad committed
    asset into a total outage instead of one missing tile."""
    from iodc.overlays import OverlayMismatch

    cycle(OverlayMismatch("drawn for bbox [10,80,28,100], frame covers [19,86,28,95]"),
          failing_product="storm")

    result = render.render_cycle(NIGHT_RUN)

    assert set(result["products"]) == {"clouds", "rain", "fog"}


def test_every_product_failing_still_raises(cycle):
    """The boundary must not swallow a total failure into a silent success:
    publishing nothing has to be an error the workflow can see, or a dead
    pipeline would report green."""
    cycle(FileNotFoundError("overlays directory is empty"))

    with pytest.raises(RuntimeError, match="every product failed"):
        render.render_cycle(NIGHT_RUN)


# ── The loop frame carries imagery only ──────────────────────────────────────
#
# Baking the overlay in and then downscaling to 320 px multiplied every
# absolute pixel size by 0.457: 15 px labels rendered at 6.9 px and the
# deliberately-heaviest 1.9 px national border landed SUB-PIXEL. Continuous-
# tone cloud survives a resample; crisp text and 1 px lines do not. So the loop
# renders from the frame BEFORE anything a reader navigates by is added, and
# the overlay ships once at full resolution.
#
# These assert the property, not the pixels: full and thumb keep what they had,
# loop must be a DIFFERENT source. A test that only checked "loop exists" would
# have passed against the old code.


@pytest.mark.parametrize("size_key,expected", [
    ("full", "stamped"),    # composited + the in-frame legend strip
    ("thumb", "image"),     # composited: a preview keeps its labels
    ("loop", "bare"),       # imagery only — the whole point
])
def test_each_size_encodes_from_its_own_source(size_key, expected):
    payload = {"image": "IMAGE", "stamped": "STAMPED", "bare": "BARE"}
    assert render._source_for(payload, size_key) == payload[expected]


def test_a_payload_without_bare_falls_back_to_the_composited_frame():
    """Absent `bare` is the older payload shape. Falling back to the composited
    image reproduces the old behaviour; raising would turn a stale payload into
    a dead cycle, the failure mode the per-product boundary exists to avoid."""
    payload = {"image": "IMAGE", "stamped": "STAMPED"}
    assert render._source_for(payload, "loop") == "IMAGE"


def test_the_cycle_gives_every_frame_a_bare_source_that_is_not_the_composite(cycle):
    """The wiring, not just the rule.

    `bare` must reach the payload for all four products and must not be the
    same object the overlay was pasted onto — the fake records pastes, so an
    implementation that stored the composited image under `bare` fails here.
    """
    cycle(RuntimeError("unused"), failing_product="__none__")

    result = render.render_cycle(NIGHT_RUN)

    assert set(result["products"]) == {"clouds", "storm", "rain", "fog"}
    for product_key, payload in result["products"].items():
        for view_key, langs in payload["views"].items():
            for lang, view_payload in langs.items():
                where = f"{product_key}/{view_key}/{lang}"
                bare = view_payload.get("bare")
                assert bare is not None, f"{where} published no bare frame"
                assert bare is not view_payload["image"], (
                    f"{where} stored the COMPOSITED frame as bare — the loop "
                    f"would still bake in the overlay"
                )
                assert bare.pastes == 0, (
                    f"{where} pasted {bare.pastes} layer(s) onto the bare "
                    f"frame; the loop must carry imagery only"
                )
                assert view_payload["image"].pastes >= 1, (
                    f"{where} never pasted the overlay onto the composited "
                    f"frame — the harness is not exercising the real path"
                )
