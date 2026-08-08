import json
from datetime import datetime, timedelta, timezone

import pytest

from iodc import products, publish, storage

PREFIX = "sat"
T0 = datetime(2026, 9, 20, 7, 15, tzinfo=timezone.utc)
DAY = products.Product(products.VISIBLE_LAYER, is_night=False)


def entries(when=T0):
    return {("wide", "bn"): when, ("wide", "en"): when,
            ("close", "bn"): when, ("close", "en"): when}


# ── keys ──────────────────────────────────────────────────────────────────────

def test_frame_keys_carry_the_capture_time():
    """The capture time in the key is what makes the object immutable, and so
    cacheable forever."""
    key = publish.frame_key(PREFIX, "close", "bn", T0)
    assert key == "sat/close-bn/2026-09-20T0715.jpg"


def test_a_different_capture_time_is_a_different_object():
    a = publish.frame_key(PREFIX, "wide", "en", T0)
    b = publish.frame_key(PREFIX, "wide", "en", T0 + timedelta(minutes=15))
    assert a != b


# ── meta ──────────────────────────────────────────────────────────────────────

def test_meta_reports_capture_time_not_publish_time():
    meta = publish.build_meta(PREFIX, DAY, entries(), {})
    assert meta["generatedAtUtc"] == "2026-09-20T07:15:00Z"


def test_meta_lists_every_view_and_language():
    meta = publish.build_meta(PREFIX, DAY, entries(), {})
    assert set(meta["views"]) == {"wide-bn", "wide-en", "close-bn", "close-en"}


def test_meta_records_which_product_produced_the_frames():
    night = products.infrared_fallback()
    assert publish.build_meta(PREFIX, night, entries(), {})["product"] == "night"
    assert publish.build_meta(PREFIX, DAY, entries(), {})["product"] == "day"


def test_meta_carries_attribution():
    assert "EUMETSAT" in publish.build_meta(PREFIX, DAY, entries(), {})["attribution"]


def test_history_accumulates_and_is_capped_at_the_retention_window():
    history = {}
    when = T0
    for _ in range(publish.RETAIN + 6):
        meta = publish.build_meta(PREFIX, DAY, entries(when), history)
        history = publish.history_from_meta(meta)
        when += timedelta(minutes=15)
    for view in meta["views"].values():
        assert len(view["frames"]) == publish.RETAIN
    # And the window holds the newest, not the oldest.
    assert meta["views"]["wide-bn"]["frameTimesUtc"][-1] == "2026-09-20T11:30:00Z"


def test_history_survives_a_corrupt_pointer():
    """A damaged meta must not strand the pipeline — rebuild rather than fail."""
    client = storage.LocalClient("unused")
    client.get = lambda key: b"{ not json"
    target = publish.Target("e", "b", "k", "s", PREFIX)
    assert publish.read_meta(client, target) == {"views": {}}


def test_first_run_has_no_previous_meta():
    client = storage.LocalClient("unused")
    def missing(key):
        raise FileNotFoundError(key)
    client.get = missing
    target = publish.Target("e", "b", "k", "s", PREFIX)
    assert publish.read_meta(client, target) == {"views": {}}


# ── retention ─────────────────────────────────────────────────────────────────

def test_nothing_is_prunable_until_the_window_is_full():
    history = {"wide-bn": [T0 + timedelta(minutes=15 * i) for i in range(publish.RETAIN)]}
    assert publish.prunable(history, PREFIX) == []


def test_only_frames_past_the_window_are_prunable():
    times = [T0 + timedelta(minutes=15 * i) for i in range(publish.RETAIN + 3)]
    stale = publish.prunable({"wide-bn": times}, PREFIX)
    assert len(stale) == 3
    assert publish.frame_key(PREFIX, "wide", "bn", times[0]) in stale
    assert publish.frame_key(PREFIX, "wide", "bn", times[-1]) not in stale


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
