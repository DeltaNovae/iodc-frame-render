from datetime import datetime, timedelta, timezone

from iodc import trigger

NOW = datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc)


def run(minutes_ago, event="workflow_dispatch"):
    created = NOW - timedelta(minutes=minutes_ago)
    return {"event": event, "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ")}


def test_limit_allows_one_dropped_firing_not_two():
    assert 30 < trigger.MAX_AGE_MINUTES < 45


def test_reads_the_newest_dispatched_run_whatever_the_order():
    assert trigger.newest_dispatch_age_minutes([run(40), run(10), run(25)], NOW) == 10


def test_ignores_the_hourly_fallback_so_a_dead_trigger_still_ages():
    runs = [run(5, "schedule"), run(65, "schedule"), run(70)]
    assert trigger.newest_dispatch_age_minutes(runs, NOW) == 70


def test_none_when_no_dispatched_run_is_listed():
    assert trigger.newest_dispatch_age_minutes([run(5, "schedule")], NOW) is None
    assert trigger.newest_dispatch_age_minutes([], NOW) is None
    assert trigger.newest_dispatch_age_minutes(None, NOW) is None


def test_skips_unparseable_timestamps():
    runs = [{"event": "workflow_dispatch", "created_at": "bad"},
            {"event": "workflow_dispatch"}, run(12)]
    assert trigger.newest_dispatch_age_minutes(runs, NOW) == 12
