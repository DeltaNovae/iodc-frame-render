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
lifts. Clear ground keeps both channels close, so G stays low. But G alone says
only "water cloud" — **B** (the 10.8 um brightness temperature) says how HIGH,
and fog by definition lies on the ground. Both are required; see [MIN_WARMTH]
for what happened when only G was used. Measured on the
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

## Showing the sky, not just the verdict

Fog is drawn as a CONTINUOUS cyan ramp — thin pale, dense deep — over the sky
itself rendered in grey. Showing the evidence is the point: a blank map with a
verdict on it looks equally confident whether the verdict is right or wrong,
and every fog fault found so far hid behind exactly that. With cloud visible
underneath, thick high cloud reads as thick high cloud, and the standing caveat
("something is above; the ground may not be visible") becomes something the
user can SEE rather than something the caption has to promise.

## The blind band, and why declining to answer is the answer

Fog switches instruments on its own schedule, NOT the clouds ladder's 12 deg —
see [FOG_NIGHT_MAX_ELEVATION]. Between sunrise and roughly 8 deg neither recipe
can be trusted, so the ladder returns nothing and `carry_forward` keeps the last
good assessment at its true capture time. That band sits inside the peak hazard
hour, which is exactly why it must not be papered over with a guess.

Residuals on the record: thin cirrus can still tint the day signature; the grey
context shows thick high cloud but cannot rule out THIN cloud hiding fog
beneath it, so an unpainted frame is not a promise of clear ground; and
December is the first real-world test — the archive built this, winter proves
it.
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

#: Fog lies ON THE GROUND, so its top is WARM. B carries the 10.8 um brightness
#: temperature, and that is the half of the reading the first rebuild missed:
#: G alone says "water cloud", not "low". Measured —
#:
#:     region                       G     B
#:     January fog, Ganges plain   124   200   <- warm: on the ground
#:     clear ground                  6   168   <- warm, but no cloud signal
#:     August convection, Sylhet   248     2   <- freezing: tops kilometres up
#:     August convection, Bay      184     5
#:
#: Without the warmth floor, monsoon thunderstorm tops saturate G and paint as
#: dense fog — which is what shipped, and what the owner caught at 03:15 BST in
#: August. With it, that cloud is not painted as fog at all: it falls through to
#: the grey context tone, where a cold top renders BRIGHT and reads as exactly
#: what it is — thick cloud overhead, ground not visible. Showing the sky rather
#: than only the verdict is what makes that legible without a third colour.
#: Per instrument, because the two scale temperature differently: Night
#: Microphysics puts B on roughly 243-293 K, Day Microphysics on 203-323 K. One
#: number for both meant the day side admitted cloud tops near freezing — several
#: kilometres up — as "fog".
MIN_WARMTH_NIGHT = 150      # ~272 K: excludes anything colder than the ground
MIN_WARMTH_DAY = 150        # ~285 K equivalent; see the note below on why this
#: is NOT tightened further. Raising it to 175 cut real January fog at 09:00
#: from 50% to 10% while monsoon low cloud still scored 31% — it trades a true
#: positive for barely any false-positive relief, because on the day side the
#: two are not separable by temperature at all.

#: DAY SIDE ONLY: fog must be PALE, and paleness lives in R.
#:
#: In Day Microphysics R is VIS0.8 reflectance and fog is bright there — a
#: dense sheet sits around R 200. Warm humid monsoon land is not: it reads
#: blue, R 50-70, while still clearing the G ramp and the warmth floor on its
#: own. Without this gate the day recipe called 80% of central Bangladesh fog
#: on an August morning when the visible imagery showed bare ground.
#:
#: THIS CONDITION WAS LOST IN A REFACTOR, not omitted by design. The original
#: calibration specified "pale: G>=110, R>=110, G>=0.75R, min>=90"; rewriting
#: the test as a continuous intensity ramp kept the G ramp and the warmth floor
#: and silently dropped R, which remained a parameter this function never read.
#: It stayed invisible for a month because it only misfires OUT of the season
#: the classifier was calibrated in — winter frames were the only evidence.
#:
#: 120 chosen against three ground truths (monsoon / real fog / clear winter):
#:
#:     R floor   AUG monsoon   JAN fog 07:30   JAN fog 08:30   FEB clear
#:        none        80.3%          56.3%           31.5%        3.6%
#:         110         5.5%          56.3%           31.5%        3.6%
#:         120         2.1%          56.3%           29.2%        3.6%   <-
#:         150         0.0%          54.6%           13.6%        3.5%
#:
#: Real fog is untouched up to 130 — every genuine fog pixel is already pale,
#: so the gate is nearly free. 120 is where monsoon false positives fall BELOW
#: the clear-winter baseline; tightening past it only costs thin burning-off
#: fog, and that marginal morning is exactly the one a driver needs.
MIN_REFLECTANCE_DAY = 120

#: Below this the signal is noise, not thin fog; painting it would speckle.
MIN_INTENSITY = 0.12

# ── palette ───────────────────────────────────────────────────────────────────
# Option B (owner decision 2026-08-10): show the SKY, then highlight the fog —
# the same grammar as the storm tile. The earlier design painted a verdict on a
# blank map, so when the classifier was wrong the tile looked perfectly
# confident and nothing on screen contradicted it. Every fog fault found so far
# was invisible for exactly that reason. With the evidence drawn underneath, a
# thunderstorm reads as a thunderstorm whatever the classifier decides.
#
# Context follows the infrared convention the storm tile already uses: cold
# high cloud bright, warm ground dark. Fog then takes a CYAN ramp — saturated
# enough that it cannot be confused with white cloud top, and pale-to-deep so
# density still reads.
_CONTEXT_FLOOR, _CONTEXT_CEIL = 28, 232

_FOG_THIN = (150, 214, 224)
_FOG_DENSE = (14, 132, 158)

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
    if b < (MIN_WARMTH_NIGHT if night else MIN_WARMTH_DAY):
        # Cold top: this is cloud far above the ground, not fog on it.
        return 0.0
    if not night and r < MIN_REFLECTANCE_DAY:
        # Not pale: warm humid land clears the gates above on its own, and only
        # reflectance separates it from the fog sheet. See MIN_REFLECTANCE_DAY.
        return 0.0
    lo, hi = (NIGHT_G_LO, NIGHT_G_HI) if night else (DAY_G_LO, DAY_G_HI)
    if g <= lo:
        return 0.0
    return min(1.0, (g - lo) / float(hi - lo))


def _mix(base: tuple, colour: tuple, alpha: float) -> tuple:
    return tuple(round(base[i] + (colour[i] - base[i]) * alpha) for i in range(3))


def context_tone(b: int) -> int:
    """The sky behind the verdict, in the infrared convention: cold bright,
    warm dark. B carries the 10.8 um brightness temperature, so this is a
    genuine picture of cloud height, not decoration."""
    return round(_CONTEXT_CEIL - (b / 255.0) * (_CONTEXT_CEIL - _CONTEXT_FLOOR))


def compose(raw: Image.Image, view, night: bool) -> Image.Image:
    """The sky in grey, with fog highlighted in cyan.

    Labels are added by the caller's per-language loop, above this.
    """
    frame = raw.convert("RGB")
    src = frame.load()
    out = Image.new("RGB", frame.size)
    dst = out.load()
    for y in range(frame.height):
        for x in range(frame.width):
            r, g, b = src[x, y]
            intensity = fog_intensity(r, g, b, night)
            if intensity >= MIN_INTENSITY:
                colour = _mix(_FOG_THIN, _FOG_DENSE, intensity)
                dst[x, y] = colour
            else:
                tone = context_tone(b)
                dst[x, y] = (tone, tone, tone)
    return out
