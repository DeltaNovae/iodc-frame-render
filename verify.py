"""Read back what is actually published and check it hangs together.

Publishing succeeding is not the same as the result being usable. This reads
`meta.json` from storage the way a client would, then confirms every frame it
names really exists and that the newest capture is recent enough to be worth
showing.

Doubles as the staleness probe the health check needs: a pipeline that dies
quietly keeps serving its last good frames, so *nothing looks broken* — only
the age of the newest capture reveals it.

It judges two independent properties, because a pipeline can fail either one
while passing the other:

* **Freshness** — is the newest capture recent? Catches a pipeline that has
  stopped, or runs but publishes nothing.
* **Trigger** (`--check-trigger`) — is the Cloudflare cron still dispatching
  render.yml? A dead trigger leaves the hourly fallback keeping frames fresh
  while the loop turns to lurching, which age cannot see at all.

**Spacing** — are captures evenly separated? — is printed as the record, on
the trailing captures only, but no longer fails (2026-10-09). It was the
trigger's proxy, and an upstream EUMETSAT hole breaks it identically:
2026-10-05..07 paged three times while the trigger fired every 15 minutes.
See `iodc/trigger.py`.

Usage:  python verify.py [--max-age-minutes 90] [--max-gap-minutes 40] [--check-trigger]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from iodc import publish, storage, trigger


#: Which product's spacing is reported as the reference.
#:
#: `storm` is the only product with no conditional path: it rides `ir108`
#: directly, 24 hours a day, with no instrument ladder and no washed-out guard,
#: so every cycle either publishes it or found no new upstream slot.
#:
#: The others cannot carry this. `fog` deliberately DECLINES through the blind
#: band at sunrise and sunset — `carry_forward` freezes its entry and the series
#: resumes ~40 minutes later — so judging fog on spacing would page twice a day
#: for the product working exactly as designed. `clouds` walks a ladder that can
#: reject a rung on measurement, and `rain` depends on a separate upstream
#: layer. All three are still reported, because the report is also the record.
CADENCE_PRODUCT = "storm"

#: Minutes between captures above which the report names a gap. The grid is
#: 15; two skipped slots (45) is where a loop visibly lurches. Reported only.
MAX_GAP_MINUTES = 40

#: The app's own "old image" label: we hear of a stall no later than users see it.
#: Judged on the non-fog products — the same rule as the app caption (B013).
MAX_AGE_MINUTES = 90

#: Fog alone: above its longest planned sunrise/sunset pause (~106 min seen at
#: dusk 2026-10-09), so it fires only when fog itself is stuck.
MAX_FOG_AGE_MINUTES = 150
FOG = "fog"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-minutes", type=int, default=MAX_AGE_MINUTES)
    ap.add_argument("--max-fog-age-minutes", type=int, default=MAX_FOG_AGE_MINUTES)
    ap.add_argument("--max-gap-minutes", type=float, default=MAX_GAP_MINUTES)
    ap.add_argument("--cadence-product", default=CADENCE_PRODUCT)
    ap.add_argument("--check-trigger", action="store_true",
                    help="fail when the Cloudflare cron has stopped dispatching render.yml")
    args = ap.parse_args()

    target = publish.Target.from_env()
    client = storage.S3Client(target.endpoint, target.bucket,
                              target.access_key, target.secret_key)

    try:
        meta = json.loads(client.get(publish.meta_key(target.prefix)))
    except FileNotFoundError:
        print("FAIL: nothing published yet — meta.json is absent")
        return 1

    now = datetime.now(timezone.utc)
    users = publish.oldest_capture(meta, lambda key: key != FOG)
    fog_at = publish.oldest_capture(meta, lambda key: key == FOG)
    age = None if users is None else (now - users).total_seconds() / 60
    fog_age = None if fog_at is None else (now - fog_at).total_seconds() / 60

    print(f"captured    : {users:%Y-%m-%dT%H:%M:%SZ}  ({age:.0f} min ago, oldest non-fog)"
          if users else "captured    : no non-fog capture named")
    if fog_at:
        print(f"fog         : {fog_at:%Y-%m-%dT%H:%M:%SZ}  ({fog_age:.0f} min ago)")
    print(f"attribution : {meta['attribution']}")
    print(f"version     : {meta.get('version')}")

    problems = []
    products = meta.get("products")
    if not products:
        problems.append("meta names no products — nothing is being served")
        products = {}

    # Every size is checked, not only the one the viewer uses: a missing thumb
    # would leave the Home row blank while the viewer looked perfectly healthy,
    # which is precisely the class of failure this script exists to catch.
    for product_key, product in products.items():
        print(f"product     : {product_key} "
              f"({product.get('source')} / {product.get('layer')})")
        for name, view in product["views"].items():
            report = []
            for size_key, url in view["latest"].items():
                try:
                    body = client.get(url)
                except FileNotFoundError:
                    problems.append(
                        f"{product_key}/{name}: meta names {url} but it is not there")
                    continue
                if not body.startswith(b"\xff\xd8"):
                    problems.append(f"{product_key}/{name}: {url} is not a JPEG")
                report.append(f"{size_key} {len(body) // 1024:>3} KB")
            print(f"  {name:<10} {' · '.join(report):<36} "
                  f"{len(view.get('frames', []))} frame(s) retained")

    if age is None:
        problems.append("meta names no non-fog capture — freshness unjudged")
    elif age > args.max_age_minutes:
        problems.append(
            f"oldest non-fog capture is {age:.0f} min old (limit {args.max_age_minutes}) — "
            "the pipeline is stalled while still serving its last good frames"
        )
    if fog_age is not None and fog_age > args.max_fog_age_minutes:
        problems.append(
            f"fog capture is {fog_age:.0f} min old (limit {args.max_fog_age_minutes}, above "
            "its sunrise/sunset pause) — fog is stuck while the others are fresh"
        )

    # Spacing. Printed for every product; the reference one gets a note.
    # Because retention is 12 captures, each run reads the last ~3 hours, so
    # running this hourly accumulates a continuous record of the cadence rather
    # than a snapshot of it.
    print()
    gaps = publish.capture_gaps(publish.history_from_meta(meta))
    for product_key, gap in sorted(gaps.items()):
        judged = " ← reference" if product_key == args.cadence_product else ""
        if gap.minutes is None:
            print(f"cadence     : {product_key:<7} {gap.captures} capture(s) — "
                  f"nothing to measure yet{judged}")
        else:
            print(f"cadence     : {product_key:<7} widest gap {gap.minutes:>4.0f} min "
                  f"over {gap.captures:>2} captures  ({gap.series}){judged}")

    reference = gaps.get(args.cadence_product)
    if reference and reference.minutes is not None and reference.minutes > args.max_gap_minutes:
        after = reference.after.strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"note        : {args.cadence_product} gap of {reference.minutes:.0f} min after "
              f"{after} — upstream skipped slots, unless the trigger check fails")

    if args.check_trigger:
        try:
            age = trigger.newest_dispatch_age_minutes(
                trigger.fetch_runs(), datetime.now(timezone.utc))
        except Exception as e:  # any failure leaves the trigger unjudged — say so
            problems.append(f"render trigger unjudged — GitHub API: {e}")
        else:
            shown = "none listed" if age is None else f"{age:.0f} min ago"
            print(f"trigger     : newest dispatched render run {shown}")
            if age is None or age > trigger.MAX_AGE_MINUTES:
                problems.append(
                    f"newest dispatched render run {shown} (limit {trigger.MAX_AGE_MINUTES}) — "
                    "the Cloudflare cron satellite-render-trigger is not firing; only the "
                    "hourly fallback is rendering")

    if problems:
        print("\nFAIL")
        for problem in problems:
            print(f"  ! {problem}")
        return 1

    print("\nPASS — meta is fresh and every frame it names is present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
