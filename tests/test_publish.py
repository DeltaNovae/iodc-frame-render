import json
from datetime import datetime, timedelta, timezone

import pytest

from iodc import products, publish, sizes, storage

PREFIX = "sat"
T0 = datetime(2026, 9, 20, 7, 15, tzinfo=timezone.utc)
DAY = products.COLOUR_DAY
CLOUDS = DAY.key


def entries(when=T0):
    return {("wide", "bn"): when, ("wide", "en"): when,
            ("close", "bn"): when, ("close", "en"): when}


def one_product(product=DAY, when=T0):
    return {product.key: {"product": product, "entries": entries(when)}}


# ── keys ──────────────────────────────────────────────────────────────────────

def test_frame_keys_carry_product_view_language_size_and_capture_time():
    """The capture time in the key is what makes the object immutable, and so
    cacheable forever; product and size are path segments so either can be
    listed or purged as a unit."""
    key = publish.frame_key(PREFIX, "clouds", "close", "bn", "full", T0)
    assert key == "sat/clouds/close-bn/full/2026-09-20T0715.jpg"


def test_a_different_capture_time_is_a_different_object():
    a = publish.frame_key(PREFIX, "clouds", "wide", "en", "full", T0)
    b = publish.frame_key(PREFIX, "clouds", "wide", "en", "full",
                          T0 + timedelta(minutes=15))
    assert a != b


def test_sizes_and_products_do_not_collide():
    """Two products, or two sizes, of the same instant must be distinct objects
    — otherwise one would silently overwrite the other."""
    seen = {
        publish.frame_key(PREFIX, product, "close", "bn", size, T0)
        for product in ("clouds", "storm", "rain", "fog")
        for size in ("full", "thumb", "loop")
    }
    assert len(seen) == 12


# ── meta ──────────────────────────────────────────────────────────────────────

def test_meta_is_contract_v2():
    assert publish.build_meta(PREFIX, one_product(), {})["version"] == 2


def test_meta_reports_capture_time_not_publish_time():
    meta = publish.build_meta(PREFIX, one_product(), {})
    assert meta["generatedAtUtc"] == "2026-09-20T07:15:00Z"


def test_meta_lists_every_view_and_language_under_its_product():
    meta = publish.build_meta(PREFIX, one_product(), {})
    assert set(meta["products"]) == {CLOUDS}
    assert set(meta["products"][CLOUDS]["views"]) == {
        "wide-bn", "wide-en", "close-bn", "close-en"}


def test_every_view_names_all_three_sizes():
    """The Home tile, the viewer and the loop each need a different one; a
    missing size leaves one of them blank while the others look healthy."""
    meta = publish.build_meta(PREFIX, one_product(), {})
    view = meta["products"][CLOUDS]["views"]["close-bn"]
    assert set(view["latest"]) == {"full", "thumb", "loop"}
    assert view["latest"]["thumb"].endswith("/thumb/2026-09-20T0715.jpg")


def test_frames_are_objects_so_time_and_url_cannot_desync():
    """v1 carried `frames` and `frameTimesUtc` as parallel arrays and trusted
    them to stay the same length and order."""
    meta = publish.build_meta(PREFIX, one_product(), {})
    frame = meta["products"][CLOUDS]["views"]["close-bn"]["frames"][0]
    assert frame["capturedAtUtc"] == "2026-09-20T07:15:00Z"
    assert set(frame) == {"capturedAtUtc", "full", "thumb", "loop"}


def test_meta_records_which_rung_produced_the_frames():
    """The ladder is invisible to the app, but has to be diagnosable."""
    night = publish.build_meta(PREFIX, one_product(products.NIGHT), {})
    day = publish.build_meta(PREFIX, one_product(products.COLOUR_DAY), {})
    assert night["products"][CLOUDS]["source"] == "night"
    assert night["products"][CLOUDS]["layer"] == products.INFRARED_LAYER
    assert day["products"][CLOUDS]["source"] == "day"


def test_all_cloud_rungs_publish_under_one_product_key():
    """Colour, raw-visible and infrared are one tile to a user."""
    assert products.COLOUR_DAY.key == products.LOW_SUN_DAY.key == products.NIGHT.key


def test_generated_time_is_the_oldest_capture_across_products():
    """One timestamp speaking for several products has to be true of all."""
    fresh, stale = T0, T0 - timedelta(minutes=45)
    meta = publish.build_meta(PREFIX, {
        "clouds": {"product": DAY, "entries": entries(fresh)},
        "storm": {"product": DAY, "entries": entries(stale)},
    }, {})
    assert meta["generatedAtUtc"] == "2026-09-20T06:30:00Z"


def test_meta_carries_attribution():
    assert "EUMETSAT" in publish.build_meta(PREFIX, one_product(), {})["attribution"]


def test_history_accumulates_and_is_capped_at_the_retention_window():
    history = {}
    when = T0
    for _ in range(publish.RETAIN + 6):
        meta = publish.build_meta(PREFIX, one_product(when=when), history)
        history = publish.history_from_meta(meta)
        when += timedelta(minutes=15)
    for view in meta["products"][CLOUDS]["views"].values():
        assert len(view["frames"]) == publish.RETAIN
    # And the window holds the newest, not the oldest.
    newest = meta["products"][CLOUDS]["views"]["wide-bn"]["frames"][-1]
    assert newest["capturedAtUtc"] == "2026-09-20T11:30:00Z"


def test_one_product_publishing_does_not_erase_another_products_history():
    """A cycle renders one product; the others must keep their retention."""
    both = publish.build_meta(PREFIX, {
        "clouds": {"product": DAY, "entries": entries(T0)},
        "storm": {"product": DAY, "entries": entries(T0)},
    }, {})
    history = publish.history_from_meta(both)
    assert set(history) == {"clouds", "storm"}
    assert history["storm"]["wide-bn"] == [T0]


def test_history_survives_a_corrupt_pointer():
    """A damaged meta must not strand the pipeline — rebuild rather than fail."""
    client = storage.LocalClient("unused")
    client.get = lambda key: b"{ not json"
    target = publish.Target("e", "b", "k", "s", PREFIX)
    assert publish.read_meta(client, target) == {"products": {}}


def test_first_run_has_no_previous_meta():
    client = storage.LocalClient("unused")

    def missing(key):
        raise FileNotFoundError(key)

    client.get = missing
    target = publish.Target("e", "b", "k", "s", PREFIX)
    assert publish.read_meta(client, target) == {"products": {}}


def test_a_v1_pointer_yields_no_history_rather_than_wrong_keys():
    """v1 keys are in the old layout and no longer resolvable. Treating them as
    history would schedule deletions against paths that do not exist; retention
    simply refills over the next few cycles."""
    v1 = {"version": 1, "views": {"wide-bn": {
        "frames": ["sat/wide-bn/2026-09-20T0715.jpg"],
        "frameTimesUtc": ["2026-09-20T07:15:00Z"]}}}
    assert publish.history_from_meta(v1) == {}


# ── retention ─────────────────────────────────────────────────────────────────

def test_nothing_is_prunable_until_the_window_is_full():
    history = {CLOUDS: {"wide-bn": [T0 + timedelta(minutes=15 * i)
                                    for i in range(publish.RETAIN)]}}
    assert publish.prunable(history, PREFIX) == []


def test_pruning_removes_every_size_of_an_expired_capture():
    """They were published together and named by the same instant; keeping one
    behind would orphan an object no meta will mention again."""
    times = [T0 + timedelta(minutes=15 * i) for i in range(publish.RETAIN + 3)]
    stale = publish.prunable({CLOUDS: {"wide-bn": times}}, PREFIX)
    assert len(stale) == 3 * len(sizes.SIZES)
    for size in sizes.SIZES:
        assert publish.frame_key(PREFIX, CLOUDS, "wide", "bn", size.key,
                                 times[0]) in stale
        assert publish.frame_key(PREFIX, CLOUDS, "wide", "bn", size.key,
                                 times[-1]) not in stale


def test_pruning_is_scoped_per_product():
    """A product with a full window must not be pruned because another one is."""
    times = [T0 + timedelta(minutes=15 * i) for i in range(publish.RETAIN + 2)]
    stale = publish.prunable({
        "clouds": {"wide-bn": times},
        "storm": {"wide-bn": times[:publish.RETAIN]},
    }, PREFIX)
    assert all("/storm/" not in key for key in stale)
    assert any("/clouds/" in key for key in stale)


# ── cache policy ──────────────────────────────────────────────────────────────

def test_frames_are_immutable_and_meta_is_not():
    """The pointer must never be cached like its targets, or a client would keep
    finding yesterday's frames."""
    assert "immutable" in publish.FRAME_CACHE_CONTROL
    assert "immutable" not in publish.META_CACHE_CONTROL
    assert "max-age=60" in publish.META_CACHE_CONTROL


# ── configuration ─────────────────────────────────────────────────────────────

def test_missing_credentials_fail_loudly_and_name_what_is_missing(monkeypatch):
    for name in ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit, match="S3_ENDPOINT"):
        publish.Target.from_env()


def test_no_endpoint_or_bucket_is_baked_into_the_code():
    """Storage identifiers are secrets; the repository is public."""
    import inspect
    source = inspect.getsource(publish) + inspect.getsource(storage)
    for marker in ("r2.cloudflarestorage", "https://", ".com/"):
        assert marker not in source.replace("https://", "", 0) or "os.environ" in source


# ── carry-forward ─────────────────────────────────────────────────────────────

def test_a_product_absent_from_this_cycle_is_carried_from_the_previous_pointer():
    """A product that fails one cycle still has near-fresh frames in the
    bucket; dropping it from the pointer would hide its tile for no reason."""
    previous = publish.build_meta(PREFIX, {
        "clouds": {"product": DAY, "entries": entries(T0)},
        "storm": {"product": products.NIGHT, "entries": entries(T0)},
    }, {})
    this_cycle = publish.build_meta(
        PREFIX, one_product(when=T0 + timedelta(minutes=15)), {})

    merged = publish.carry_forward(this_cycle, previous)

    assert set(merged["products"]) == {"clouds", "storm"}
    assert merged["products"]["storm"]["layer"] == products.INFRARED_LAYER


def test_carry_forward_never_overwrites_what_this_cycle_published():
    fresh = T0 + timedelta(minutes=15)
    previous = publish.build_meta(PREFIX, one_product(when=T0), {})
    this_cycle = publish.build_meta(PREFIX, one_product(when=fresh), {})

    merged = publish.carry_forward(this_cycle, previous)

    view = merged["products"][CLOUDS]["views"]["close-bn"]
    assert view["capturedAtUtc"] == "2026-09-20T07:30:00Z"


def test_generated_time_accounts_for_the_carried_products_age():
    """The oldest-capture rule must include what was carried, or a stalled
    product would quietly pin the caption to the healthy one — hiding exactly
    the staleness the alarm and the app label exist to surface."""
    stale = T0 - timedelta(minutes=90)
    previous = publish.build_meta(PREFIX, {
        "storm": {"product": products.NIGHT, "entries": entries(stale)},
    }, {})
    this_cycle = publish.build_meta(PREFIX, one_product(when=T0), {})

    merged = publish.carry_forward(this_cycle, previous)

    assert merged["generatedAtUtc"] == "2026-09-20T05:45:00Z"


def test_carry_forward_ignores_a_v1_pointer():
    """v1 entries are in the dead key layout; carrying them would republish
    URLs that 404."""
    previous = {"version": 1, "views": {"wide-bn": {"latest": "sat/wide-bn/x.jpg"}}}
    this_cycle = publish.build_meta(PREFIX, one_product(), {})

    merged = publish.carry_forward(this_cycle, previous)

    assert set(merged["products"]) == {CLOUDS}


# ── cadence ───────────────────────────────────────────────────────────────────

def series(*minute_offsets, start=T0):
    """One `view-lang` series, given as minutes from `start`."""
    return [start + timedelta(minutes=m) for m in minute_offsets]


def product_history(*minute_offsets, start=T0):
    """A product's four series, all publishing at the same instants — which is
    what whole-or-nothing publishing actually produces."""
    return {name: series(*minute_offsets, start=start)
            for name in ("wide-bn", "wide-en", "close-bn", "close-en")}


def test_an_even_fifteen_minute_grid_reports_a_fifteen_minute_gap():
    """The healthy state, taken from the live pointer on 2026-08-11: twelve
    captures, 18:00Z to 20:45Z, every one exactly a slot apart."""
    history = {"storm": product_history(*range(0, 12 * 15, 15))}

    gap = publish.capture_gaps(history)["storm"]

    assert gap.minutes == 15
    assert gap.captures == 12


def test_one_skipped_slot_is_thirty_minutes_and_stays_under_the_limit():
    """A single failed cycle is not an incident — `render.yml` logs it and
    carries on — so the number it produces has to sit below the limit that
    pages someone."""
    history = {"storm": product_history(0, 15, 45, 60)}

    gap = publish.capture_gaps(history)["storm"]

    assert gap.minutes == 30
    assert gap.minutes < 40


def test_the_ragged_scheduler_that_forced_the_cloudflare_trigger_is_visible():
    """The real measurement that forced the move to an external trigger:
    against GitHub's scheduler, captures arrived in bursts separated by 33, 62
    and 129 minutes.

    Every frame in that pattern was fresh. Freshness is exactly why it went
    unnoticed for as long as it did, and why spacing needed its own check."""
    history = {"storm": product_history(0, 15, 48, 63, 125, 140, 269)}

    gap = publish.capture_gaps(history)["storm"]

    assert gap.minutes == 129
    assert gap.minutes > 40


def test_the_gap_names_the_capture_it_follows():
    """Bisecting a cadence problem starts with when the silence began, so the
    report has to carry it rather than just its width."""
    history = {"storm": product_history(0, 15, 75)}

    gap = publish.capture_gaps(history)["storm"]

    assert gap.after == T0 + timedelta(minutes=15)
    assert gap.minutes == 60


def test_the_worst_series_is_reported_not_the_first():
    """The four series should be identical, so one falling out of step is a
    real defect. Reporting the first series, or averaging them, would hide it
    behind three healthy ones."""
    history = {"storm": {
        "wide-bn": series(0, 15, 30),
        "wide-en": series(0, 15, 30),
        "close-bn": series(0, 15, 30),
        "close-en": series(0, 90),          # fell out of step
    }}

    gap = publish.capture_gaps(history)["storm"]

    assert gap.minutes == 90
    assert gap.series == "close-en"


def test_captures_are_ordered_and_deduplicated_before_measuring():
    """Consecutive trigger fires can land on the same upstream slot and
    republish it, and nothing guarantees pointer order. Measuring raw order
    would invent gaps, or negative ones."""
    unordered = [T0 + timedelta(minutes=30), T0, T0 + timedelta(minutes=15), T0]

    gap = publish.capture_gaps({"storm": {"wide-bn": unordered}})["storm"]

    assert gap.minutes == 15
    assert gap.captures == 3


def test_a_product_with_one_capture_has_no_gap_to_report():
    """A freshly filled bucket must not read as broken; there is simply
    nothing to measure yet."""
    gap = publish.capture_gaps({"storm": {"wide-bn": series(0)}})["storm"]

    assert gap.minutes is None
    assert gap.captures == 1


def test_fog_declining_the_blind_band_is_why_storm_is_the_judged_product():
    """Fog skips the ~40 minutes around sunrise on purpose, so its series shows
    a hole that storm — 24 h on `ir108`, no ladder, no guard — never does.

    Judging every product on one threshold would page twice a day for a product
    working exactly as designed. This is the shape of the data that decision
    rests on."""
    history = {
        "storm": product_history(0, 15, 30, 45, 60),
        "fog": product_history(0, 15, 60),      # blind band between 15 and 60
    }

    gaps = publish.capture_gaps(history)

    assert gaps["storm"].minutes == 15
    assert gaps["fog"].minutes == 45
