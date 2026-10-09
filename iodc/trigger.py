"""Is the Cloudflare render trigger still dispatching render.yml?

Asked directly because capture spacing could not tell a dead trigger from an
upstream EUMETSAT hole: both leave gaps, and 2026-10-05..07 paged on three
holes while the trigger fired every 15 minutes throughout. A dead trigger
leaves only the hourly `schedule` fallback, so the newest DISPATCHED run ages.

Mirrors `worker/scripts/lib/sat_trigger.mjs` in the app repo (health-check
check 20): if you change the limit here, change it there.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime

RUNS_URL = ("https://api.github.com/repos/DeltaNovae/iodc-frame-render/actions/"
            "workflows/render.yml/runs?event=workflow_dispatch&per_page=5")

#: Two 15-minute slots plus queue slack: one dropped firing passes, two fail.
MAX_AGE_MINUTES = 35


def newest_dispatch_age_minutes(runs: list, now: datetime) -> float | None:
    """Minutes since the newest workflow_dispatch run was created, or None
    when none is listed."""
    times = []
    for run in runs or []:
        if run.get("event") != "workflow_dispatch":
            continue
        try:
            times.append(datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")))
        except (KeyError, ValueError):
            continue
    if not times:
        return None
    return (now - max(times)).total_seconds() / 60


def fetch_runs(timeout: float = 15) -> list:
    """render.yml's recent dispatched runs. The repo is public; GITHUB_TOKEN,
    when set, only lifts the anonymous rate limit."""
    request = urllib.request.Request(RUNS_URL, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response).get("workflow_runs", [])
