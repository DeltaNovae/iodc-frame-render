"""The fog product: how much fog, not merely whether — on the light map.

## What went wrong the first time, and how the data said so

The first classifier keyed on a MAGENTA signature and was "calibrated" by
comparing a January fog morning at 07:30 against a clear February night. That
compared **sunlit against dark**, not fog against no-fog: magenta is what the
3.9 um channel does when the sun touches it, so the test was a sunrise
detector. Held at a fixed hour, the same January fog scored 1.0% at 04:00 and
99.8% at 07:30 — the fog had not changed, the sun had. Consequence in
production: monsoon dawns painted speckled fake fog, and real deep-night fog
scored nothing.

## The signature that is actually physical

In EUMETSAT Night Microphysics, **G = IR10.8 - IR3.9**, the classic water-cloud
discriminator: at night fog droplets emit far less at 3.9 um than at 10.8, so G
lifts. Clear ground keeps both channels close, so G stays low. Measured on the
Ganges plain at matched hours (fog night = 2026-01-07, clear night =
2026-02-10):

    G p90        22:00   01:00   04:00
    fog night      120     133     169     <- radiation fog thickening overnight
    clear night    116     113     109     <- flat, as clear ground should be

That rise through the night is the physics of radiation fog, and it is why G is
also the right quantity for INTENSITY rather than a yes/no.

Day frames ride `rgb_microphysics`, where the same 3.9 um channel sits in G and
fog droplets reflect it strongly. Matched hours again (G p50 on the plain):
09:00 fog **101** vs clear **5**; 11:00 fog 61 vs clear 51 as the fog burns
off. Same discriminator, different scale.

## Three states, because two would lie

Fog is drawn as a CONTINUOUS ramp — thin fog pale, dense fog deep — so the map
carries density the way the source imagery does. But a two-state map (fog /
nothing) makes a promise it cannot keep: where thick high cloud lies above,
the satellite simply cannot see the ground, and painting that as "clear" is the
dangerous reading § 5.2 warns about. So obscured sky gets its own
marking — a diagonal stipple, because texture reads as *no data* where a tint
reads as a measurement. Measured: high cloud covers 34% of an August night
frame against 4-5% on the calibration nights.

## The blind band, and why declining to answer is the answer

Fog switches instruments on its own schedule, NOT the clouds ladder's 12 deg —
see [FOG_NIGHT_MAX_ELEVATION]. Between sunrise and roughly 8 deg neither recipe
can be trusted, so the ladder returns nothing and `carry_forward` keeps the last
good assessment at its true capture time. That band sits inside the peak hazard
hour, which is exactly why it must not be papered over with a guess.

Residuals on the record: thin cirrus can still tint the day signature; the
obscured wash marks thick high cloud but cannot rule out thin cloud hiding fog;
and December is the first real-world test — the archive built this, winter
proves it.
"""

from __future__ import annotations

from datetime import datetime

from PIL import Image

from . import overlays, solar
from .products import DECISION_POINT, Product

FOG_NIGHT = Product("rgb_fog", is_night=False, key="fog")
FOG_DAY = Product("rgb_microphysics", is_night=False, key="fog")

# ── where the ramp starts and saturates, per side (measured above) ────────────
# Night: clear ground tops out near 110; dense fog reaches 170+.
NIGHT_G_LO, NIGHT_G_HI = 118, 175
# Day: clear ground stays under ~70; fog runs 100+.
DAY_G_LO, DAY_G_HI = 90, 170

#: Thick high cloud reads warm in both microphysics RGBs — red well above blue.
HIGH_CLOUD_MARGIN = 25

#: Below this the signal is noise, not thin fog; painting it would speckle.
MIN_INTENSITY = 0.12

# ── palette ───────────────────────────────────────────────────────────────────
# Fog ramps pale -> deep slate: it should look like fog lying on the land, and
# stay distinct from rain's green-blue. Obscured is a quiet warm-neutral wash
# that must never compete with fog for attention — it means "no information",
# not "hazard".
_FOG_THIN = (176, 192, 206)
_FOG_DENSE = (88, 104, 126)
_FOG_ALPHA_THIN, _FOG_ALPHA_DENSE = 0.42, 0.88

# Obscured is drawn as a STIPPLE, not a flat tint. Flat, it measured
# (236,234,230) against clear's (251,250,247) — fifteen levels apart, invisible
# on a phone in daylight, so "cannot see the ground" read as "clear": exactly
# the dangerous confusion § 5.2 exists to prevent. Texture is the cartographic
# convention for *no data*, and it cannot be mistaken for a measurement however
# the screen is lit.
_OBSCURED = (150, 143, 132)
_OBSCURED_ALPHA = 0.45
#: Diagonal period. Every third pixel along x+y — dense enough to read as a
#: field at a glance, open enough that the map beneath stays legible.
_STIPPLE = 3


#: Fog switches instruments on its OWN schedule, not the clouds ladder's 12 deg.
#: That threshold marks where the VISIBLE product becomes usable; fog's night
#: recipe dies far earlier, the moment sunlight touches the 3.9 um channel.
#: Walked across the January fog morning, plain-box hit rate:
#:
#:     BST     sun    night-instr   day-instr
#:     06:30  -3.4        64.4%       (dark)
#:     07:00  +2.8         2.1%        85.2%   <- but 92% on a CLEAR morning
#:     07:30  +8.9         0.0%        82.1%   <- and 2.9% clear: trustworthy
#:
#: So the night recipe is good below the horizon, the day recipe only once the
#: sun is properly up, and BETWEEN them neither is: the night one has gone
#: blind while the day one still cries fog on a clear sky.
FOG_NIGHT_MAX_ELEVATION = 0.0
FOG_DAY_MIN_ELEVATION = 8.0


def ladder(when: datetime) -> list:
    """The usable instrument, or NOTHING during the blind band around sunrise.

    Returning an empty ladder is a deliberate answer, not a gap: the product
    skips the cycle, `carry_forward` keeps the last good assessment, and the
    frame carries its true (slightly older) capture time so the app's own
    staleness label stays honest. Radiation fog does not vanish in the forty
    minutes it takes the sun to clear 8 degrees, so yesterday-minute fog is a
    better answer than either instrument's guess — and far better than the
    false "clear" the day recipe would print.

    The band is symmetric at dusk, where the same two instruments swap back.
    """
    elevation = solar.solar_elevation(*DECISION_POINT, when)
    if elevation <= FOG_NIGHT_MAX_ELEVATION:
        return [FOG_NIGHT]
    if elevation >= FOG_DAY_MIN_ELEVATION:
        return [FOG_DAY]
    return []


def fog_intensity(r: int, g: int, b: int, night: bool) -> float:
    """0 = no fog signal, 1 = as dense as the scale goes.

    Continuous on purpose: a binary answer throws away the density the source
    imagery carries, and density is what tells a driver whether a morning is
    merely damp or genuinely blind.
    """
    lo, hi = (NIGHT_G_LO, NIGHT_G_HI) if night else (DAY_G_LO, DAY_G_HI)
    if g <= lo:
        return 0.0
    return min(1.0, (g - lo) / float(hi - lo))


def is_obscured(r: int, g: int, b: int) -> bool:
    """Thick high cloud above — whether fog lies beneath is unknowable here."""
    return r > b + HIGH_CLOUD_MARGIN


def _mix(base: tuple, colour: tuple, alpha: float) -> tuple:
    return tuple(round(base[i] + (colour[i] - base[i]) * alpha) for i in range(3))


def compose(raw: Image.Image, view, night: bool) -> Image.Image:
    """The light base with fog painted by intensity, and obscured sky washed.

    Labels are added by the caller's per-language loop, above this, so a place
    name is never lost under either wash.
    """
    frame = raw.convert("RGB")
    base = overlays.load_light_base(view).convert("RGB")
    if frame.size != base.size:
        raise ValueError(f"fog frame {frame.size} does not match base {base.size}")

    src = frame.load()
    out = base.copy()
    dst = out.load()
    for y in range(frame.height):
        for x in range(frame.width):
            r, g, b = src[x, y]
            intensity = fog_intensity(r, g, b, night)
            if intensity >= MIN_INTENSITY:
                colour = _mix(_FOG_THIN, _FOG_DENSE, intensity)
                alpha = _FOG_ALPHA_THIN + (_FOG_ALPHA_DENSE - _FOG_ALPHA_THIN) * intensity
                dst[x, y] = _mix(dst[x, y], colour, alpha)
            elif is_obscured(r, g, b) and (x + y) % _STIPPLE == 0:
                # Only where there is no fog signal: real fog under thin high
                # cloud should read as fog, not as "cannot tell".
                dst[x, y] = _mix(dst[x, y], _OBSCURED, _OBSCURED_ALPHA)
    return out
