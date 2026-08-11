"""Read back what is actually published and check it hangs together.

Publishing succeeding is not the same as the result being usable. This reads
`meta.json` from storage the way a client would, then confirms every frame it
names really exists and that the newest capture is recent enough to be worth
showing.

Doubles as the staleness probe the health check needs: a pipeline that dies
quietly keeps serving its last good frames, so *nothing looks broken* — only
the age of the newest capture reveals it.

It checks two independent properties, because a pipeline can fail either one
while passing the other:

* **Freshness** — is the newest capture recent? Catches a pipeline that has
  stopped entirely.
* **Spacing** — are captures evenly separated? Catches a pipeline that is still
  publishing but has lost its cadence, which age cannot see at all. This is the
  state a dead render trigger produces: the workflow's hourly fallback keeps
  frames fresh while the loop quietly turns to lurching.

Usage:  python verify.py [--max-age-minutes 120] [--max-gap-minutes 40]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from iodc import publish, storage


#: Which product's spacing is allowed to fail the check.
#:
#: `storm` is the only product with no conditional path: it rides `ir108`
#: directly, 24 hours a day, with no instrument ladder and no washed-out guard,
#: so every cycle either publishes it or the cycle itself failed. A gap in storm
#: is therefore always a pipeline gap.
#:
#: The others cannot carry this. `fog` deliberately DECLINES through the blind
#: band at sunrise and sunset — `carry_forward` freezes its entry and the series
#: resumes ~40 minutes later — so judging fog on spacing would page twice a day
#: for the product working exactly as designed. `clouds` walks a ladder that can
#: reject a rung on measurement, and `rain` depends on a separate upstream
#: layer. All three are still reported, because the report is also the record.
CADENCE_PRODUCT = "storm"

#: Minutes between consecutive captures before spacing counts as broken.
#:
#: The grid is 15 minutes. One skipped slot (30 min) is deliberately tolerated:
#: `render.yml` logs a failed cycle and carries on by design, and the previous
#: frames keep serving. Two skipped slots (45 min) is where a loop visibly
#: lurches, and it is also where the hourly fallback's raggedness starts to
#: show. The limit sits between them, so the alarm fires on the second miss and
#: not the first.
MAX_GAP_MINUTES = 40


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-minutes", type=int, default=120)
    ap.add_argument("--max-gap-minutes", type=float, default=MAX_GAP_MINUTES)
    ap.add_argument("--cadence-product", default=CADENCE_PRODUCT)
    args = ap.parse_args()

    target = publish.Target.from_env()
    client = storage.S3Client(target.endpoint, target.bucket,
                              target.access_key, target.secret_key)

    try:
        meta = json.loads(client.get(publish.meta_key(target.prefix)))
    except FileNotFoundError:
        print("FAIL: nothing published yet — meta.json is absent")
        return 1

    captured = datetime.strptime(meta["generatedAtUtc"], "%Y-%m-%dT%H:%M:%SZ") \
        .replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - captured).total_seconds() / 60

    print(f"captured    : {meta['generatedAtUtc']}  ({age:.0f} min ago)")
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

    if age > args.max_age_minutes:
        problems.append(
            f"newest capture is {age:.0f} min old (limit {args.max_age_minutes}) — "
            "the pipeline is stalled while still serving its last good frames"
        )

    # Spacing. Printed for every product, judged on one — see CADENCE_PRODUCT.
    # Because retention is 12 captures, each run reads the last ~3 hours, so
    # running this hourly accumulates a continuous record of the cadence rather
    # than a snapshot of it.
    print()
    gaps = publish.capture_gaps(publish.history_from_meta(meta))
    for product_key, gap in sorted(gaps.items()):
        judged = " ← judged" if product_key == args.cadence_product else ""
        if gap.minutes is None:
            print(f"cadence     : {product_key:<7} {gap.captures} capture(s) — "
                  f"nothing to measure yet{judged}")
        else:
            print(f"cadence     : {product_key:<7} widest gap {gap.minutes:>4.0f} min "
                  f"over {gap.captures:>2} captures  ({gap.series}){judged}")

    reference = gaps.get(args.cadence_product)
    if reference is None:
        problems.append(
            f"'{args.cadence_product}' is the cadence reference but meta does not "
            "name it — spacing went unjudged"
        )
    elif reference.minutes is not None and reference.minutes > args.max_gap_minutes:
        after = reference.after.strftime("%Y-%m-%dT%H:%M:%SZ")
        problems.append(
            f"{args.cadence_product} captures are {reference.minutes:.0f} min apart at "
            f"worst (limit {args.max_gap_minutes:.0f}), the hole following {after} — "
            "frames are still fresh but the cadence has slipped, which is what a "
            "dead render trigger looks like from the outside"
        )

    if problems:
        print("\nFAIL")
        for problem in problems:
            print(f"  ! {problem}")
        return 1

    print("\nPASS — meta is fresh and every frame it names is present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
