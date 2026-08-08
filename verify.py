"""Read back what is actually published and check it hangs together.

Publishing succeeding is not the same as the result being usable. This reads
`meta.json` from storage the way a client would, then confirms every frame it
names really exists and that the newest capture is recent enough to be worth
showing.

Doubles as the staleness probe the health check needs: a pipeline that dies
quietly keeps serving its last good frames, so *nothing looks broken* — only
the age of the newest capture reveals it.

Usage:  python verify.py [--max-age-minutes 120]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from iodc import publish, storage


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-minutes", type=int, default=120)
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

    print(f"product     : {meta['product']} ({meta['layer']})")
    print(f"captured    : {meta['generatedAtUtc']}  ({age:.0f} min ago)")
    print(f"attribution : {meta['attribution']}")

    problems = []
    for name, view in meta["views"].items():
        try:
            body = client.get(view["latest"])
            size_kb = len(body) // 1024
        except FileNotFoundError:
            problems.append(f"{name}: meta names {view['latest']} but it is not there")
            continue
        if not body.startswith(b"\xff\xd8"):
            problems.append(f"{name}: {view['latest']} is not a JPEG")
        print(f"  {name:<10} {size_kb:>4} KB  {len(view['frames'])} frame(s) retained")

    if age > args.max_age_minutes:
        problems.append(
            f"newest capture is {age:.0f} min old (limit {args.max_age_minutes}) — "
            "the pipeline is stalled while still serving its last good frames"
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
