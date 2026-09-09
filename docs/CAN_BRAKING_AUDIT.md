# CAN usability and recorded braking audit — 2026-09-06

The CAN panel changes improve inspection; the braking work is read-only analysis.
No driving controller, car parameters, device settings or CAN transmission changed.
The device still reported root commit `ca32282dc` during the read-only SSH check.
These new UI changes have not been installed or validated on the comma 3.

## CAN panel

The old fixed-width buttons could not contain “Obstacle/Radar.” Use wider buttons
and the short label “Radar.” Signal titles now reserve room for values and Graph;
tapping the row reveals the complete bus, message and signal name. DBC units and
value tables are displayed while the underlying graph sample remains numeric.
A small width margin prevents the last character of a unit being elided by
floating-point rectangle calculations.

The graph has a thick cyan trace on a dark background, brighter axes, current
value, explicit Close and Freeze/Resume buttons, and a 30-second window. Plot taps
no longer dismiss it. Flat signals appear inside padded bounds. The buffer is
bounded to 6,000 finite samples and retains history during a CAN interruption.

The existing main layout returns to its normal screen on inactivity. The comma 3
uses a 10-second ignition-on timeout (30 seconds offroad). The CAN panel now uses
300 seconds offroad, or with fresh valid car state below 0.1 m/s and disengaged.
Moving, engagement, stale/invalid onroad vehicle state, panel exit and a panel
fault restore the normal timeout. Ignition/onroad transitions retain their normal
navigation behavior. This is a source-supported explanation for a quick return to
the driving screen, not proof of the user's exact disappearance symptom. The eight
recent copied swaglogs contained no CAN-panel exception or matching UI crash entry.

The panel already asks its three matched DBC parsers for every defined signal:

| Receive bus | Observed IDs | IDs with definitions | Defined signals at those IDs |
|---|---:|---:|---:|
| Powertrain | 130 | 35 | 86 |
| Radar | 67 | 55 | 503 |
| Chassis | 38 | 3 | 5 |
| Total | 235 | 93 | 594 |

There are 142 remaining bus/address pairs without definitions in those DBCs. The
matched files include 244 signals with unit strings and 29 with value tables,
including definitions not observed in this sample. Loading unrelated GM DBCs is
not a valid way to decode unknown traffic merely because an address coincides.
The powertrain-expansion and two lowspeed files did not match remaining powertrain
IDs. One high-voltage-network ID coincided; that does not establish the same signal
layout on this bus, so no decoding was added from it.

## What the braking recordings establish

The audit covers 40 retained segments from 17 routes: the 30-segment ALPR sample
plus ten newly copied completed rlogs from routes `0000001d--530b79565a` and
`0000001e--38ff6c294f`. New files were checked against source SHA-256 hashes.
No driver-camera video was copied. The newest routes currently have logs only in
this audit; their road context still requires video review.

At roughly 10 Hz, the script finds continuous approaches with prior motion above
2 m/s and a sustained stop below 0.5 m/s after crossing 0.3 m/s. It joins only
recent service messages (at most 0.3 seconds old), ignores invalid control messages,
and requires valid error-free radar state for nearby-lead classification. Pedal
intervention means an onset with longitudinal control active within the preceding
0.5 seconds. Foot brake and regen paddle are separate signals.

Results:

- 11 qualifying stops; 8 had a slow/stopped lead reported within 35 m near the stop.
- All 8 include a preceding foot-brake onset. All 8 already had longitudinal
  control inactive throughout the retained approach, with no missing control
  samples in that interval. They do not demonstrate autonomous abrupt braking.
- Two other stops show foot-brake intervention after active longitudinal control:
  about 33.5 mph / 8.7 seconds before stopping, and 39.7 mph / 8.1 seconds before
  stopping. The preceding commands were positive acceleration, not hard braking.
  Neither had a qualifying nearby stopped lead at the end. One initially reported
  a moving lead at about 43 m. Video is needed to interpret the road situation.
- Of 15,285 active-control samples, 15,267 had known settings: Aggressive personality,
  Experimental mode off. The remaining 18 had unavailable mode/personality state.

This convenience sample cannot establish “always,” infer why the driver braked,
or predict what would have happened without intervention. Reported lead identity
may change. Post-disengagement command fields are deliberately hidden in the web
plots so manual braking is not attributed to openpilot.

## Existing behavior and next steps

This checkout reports version 0.11.2. In
`selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py`, the planner already uses
speed-dependent stopping distance, lead speed, and acceleration/jerk costs.
Following times are Aggressive 1.25 s, Standard 1.45 s, Relaxed 1.75 s. Aggressive
multiplies acceleration-change and jerk weights by 0.5; the other two use 1.0.
The low-speed stopping controller also ramps acceleration; recorded Volt parameters
include a stopping deceleration rate of 0.8, stop acceleration -2.0 m/s² and
longitudinal actuator delay 0.5 seconds. None were changed.

The longitudinal planner implements learned gas gating and, in Experimental mode,
combines the model's acceleration/stop output with the MPC. These are general
learned behaviors, not automatic adaptation to this driver's personal stops.
Comma described gas gating in its [0.9.8 release](https://blog.comma.ai/098release/)
and later learned driving improvements in its
[0.11 release](https://blog.comma.ai/011release/). The Toyota braking fix mentioned
in the former is not a demonstrated fix for a Volt.

1. Compare the existing Relaxed personality as the first comfort setting, without
   treating it as a proven fix or relying on an untested autonomous stop.
2. Retain complete approaches, matching road video and intervention times. Separate
   planner target, actuator command and measured acceleration; compare speed bands,
   lead range/relative speed, context, minimum gaps, jerk and intervention timing.
3. Diagnose whether late lead recognition, planner preferences, or actuator/regen
   response explains a repeatable uncomfortable case before choosing a change.
4. If personalization remains useful, fit a bounded preference model offline using
   more than speed: relative speed, range, context and vehicle response matter.
   A speed-only linear regression cannot distinguish same-speed approaches to a
   moving vehicle and a stopped one. Use held-out routes and controlled validation
   before any controller integration; eight manual approaches are insufficient.

## Reproduction and local review

Bulk data and generated previews are outside Git, under `/mnt/algo14/comma3-alpr`.
The source hashes are included in `braking-audit.json`; extraction caches are
invalidated by schema version and source hash. Reproduce from the repository:

```sh
PYTHONPATH="$PWD" .venv/bin/python -m tools.profiling.braking_audit \
  /mnt/algo14/comma3-alpr/2026-09-study \
  /mnt/algo14/comma3-diagnostics/2026-09-06/can-braking-audit/recordings \
  --cache /mnt/algo14/comma3-diagnostics/2026-09-06/can-braking-audit/cache \
  --output /mnt/algo14/comma3-alpr/braking-audit.json \
  --coverage-output /mnt/algo14/comma3-alpr/can-coverage.json

DISPLAY=:0 BIG=1 SCALE=1 OFFSCREEN=1 PYTHONPATH="$PWD" \
  .venv/bin/python -m tools.profiling.render_can_diagnostics \
  --output /mnt/algo14/comma3-alpr/diagnostics-ui
```

The second command needs an existing desktop display. It renders synthetic example
CAN values and an illustrative graph using the actual changed widgets, with isolated
parameters and no CAN subscription. It also checks Freeze/Resume, Close, plot taps,
and a constant-value graph. Desktop rendering does not validate target performance.

Review: `http://192.168.99.189:8088/#can-review` and
`http://192.168.99.189:8088/#braking`. The existing React review application serves
read-only diagnostic reports. Existing plate annotations and their write controls
are preserved. Automated browser annotation checks use isolated temporary data.

Validation completed: 26 Python tests, 10 web API tests, the isolated Chromium
review flow, desktop widget rendering/graph interaction checks, Vite production
build and Ruff for changed Python sources. The running review service was also
reached from another LAN host. No comma 3 runtime or driving validation is claimed.
