# iodc-frame-render

Scheduled renderer for Bay of Bengal satellite frames.

Fetches imagery from EUMETSAT's public [EUMETView WMS](https://view.eumetsat.int)
(Meteosat-9, Indian Ocean Data Coverage service — `msg_iodc` layers, declared
"Fees: none, AccessConstraints: none"), composites a pre-authored static overlay
(coastlines, borders, distance rings, place labels), and publishes the result to
S3-compatible object storage for downstream display.

## How it works

```
GitHub Actions (scheduled)
  → read available TIME slots from GetCapabilities, pin the newest explicitly
  → GetMap for each configured view (wide regional / close-up)
  → validate the frame (size, dimensions, luminance sanity)
  → alpha-composite the matching overlay PNG (1:1, no scaling)
  → encode JPEG, upload with a rolling window of recent frames + meta.json
```

Overlays are **pre-rendered PNGs** committed under `overlays/`, one per view and
language, produced separately at exactly the frame's pixel dimensions. The runner
never draws text or geometry — it only composites and encodes.

Frames are immutable per capture timestamp, so they cache indefinitely; `meta.json`
lists the current set. On any upstream failure the previous frames keep serving.

## Configuration

All storage settings come from the environment (repository secrets in CI) —
nothing endpoint- or account-specific is committed:

| Variable | Purpose |
|---|---|
| `S3_ENDPOINT` | S3-compatible endpoint URL |
| `S3_BUCKET` | Target bucket |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | Write-scoped credentials |
| `S3_PREFIX` | Key prefix for published objects |

## Attribution

Imagery: © EUMETSAT. Coastlines and borders derived from
[Natural Earth](https://www.naturalearthdata.com/) (public domain).

## Licence

Code: MIT. Imagery is subject to EUMETSAT's terms; overlay artwork is provided
for use with this renderer.
