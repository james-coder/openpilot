# Volt braking investigation and candidate controller

The September 7 recordings contain three autonomous stops around 08:59, 10:27,
and 10:30 Mountain. The 10:27 example includes two distinct problems: substantial
controller overshoot earlier in the approach, followed by regeneration fading,
friction pressure dropping to zero, a small speed rebound, and a sharp final stop.

**The factory ASCM is physically powered down while openpilot is engaged in this
installation.** Openpilot sends the gas/regen and friction-brake commands to the
vehicle's controllers. ASCM in platform names, DBC names, and CAN message names
identifies the supported architecture; it does not mean the factory ASCM is
controlling braking. This work does not change the harness or power switching.

## Current release status

This is a **locked, default-off prototype**, not an on-device braking update.
`VoltLongitudinalMode` defaults to `stock`. The startup configurator refuses
`smooth` until the versioned actuator profile is marked validated in a reviewed
code change, and refuses `personal` until its separate validation passes.
The developer settings screen shows Stock/Smooth/Personal offroad; candidate
modes remain disabled while validation is incomplete. Settings latch when card
starts and are carried to the planner/controller in CarParams flags, never in
panda safety flags. The selected profile identity is recorded in Params/cloudlog.
Replay retains the recorded CarParams rather than applying a local preference.

The low-order response model fits average held-out acceleration reasonably well,
but **does not reproduce the full severity of the recorded final jolts**. Average
RMSE and final-jolt reproduction are separate checks. Passing simulated stops
cannot establish road readiness while reproduction fails. Controlled vehicle
validation and reviewed personalization also remain outstanding.

No software/settings were installed or changed on the car during implementation.
The workstation review is at `http://192.168.99.189:8088/#braking`.

## What changed

- Native-rate extraction joins radar, raw tracks, model leads, acceleration,
  device-axis IMU, controller states/correction, applied commands, chassis brake
  pressure/regen signals, engine RPM, frame indices, clocks, and source versions.
  Freshness and invalid control messages are explicit. Pressure/regen values are
  raw CAN signals, not calibrated physical pressure/torque.
- Stop classification distinguishes autonomous, manual, takeover, and unknown.
  Small telemetry gaps are quantified rather than silently treated as inactive.
  Manual comparison candidates require an inactive final phase and a stable lead;
  an intervention does not establish what openpilot would have done afterwards.
- The existing React review loads summary data separately from event detail,
  synchronizes both road cameras with telemetry, defaults to the final five
  seconds, and provides persistent brightness/gamma/contrast adjustments.
  Manual-reference decisions have revision protection and never modify ALPR labels.
- The Volt candidate replaces the fixed regen/friction crossover with a bounded
  speed map, accounts for calibrated vehicle pitch, bounds delayed integral
  correction during braking, and maintains feedback through the stopping phase.
  Stationary holding avoids integrating accelerometer noise indefinitely.
  Existing command limits and the disabled GM near-stop mode are retained.
- Personal planner support uses per-instance comfort distance/braking/jerk
  parameters. The original collision-distance penalty, lead stopping-equivalence
  calculation, command limits, and emergency command authority remain unchanged.
  Default parameters reproduce the stock planner. Experimental Mode is not enabled.

## Calibration and limits

`tools/profiling/volt_response_fit.py` fits the first complete route and evaluates
the second as a holdout. It searches delay/time-constant pairs with bounded robust
least squares, then reports support per speed knot and low-speed error separately.

The candidate's friction gain and creep values come from that first-route fit.
Its regen table is a conservative monotone lower envelope of fitted regen
coefficients, with zero authority below 1.5 m/s where the observed regen signal
vanishes. These are **candidate coefficients**, not an independently measured
actuator transfer function. Engine-on/battery-limit behavior is not identified
by these engine-off drives. Unknown/engine-on states assume reduced regeneration.
Pitch compensation requires separate physical validation.

The simulator runs the real longitudinal planner, LongControl at 100 Hz, and GM
CarController at its normal 25 Hz longitudinal cadence. Generated CAN messages
are discarded; it never opens a CAN socket. The fitted vehicle response includes
delay, a response time constant, creep, regen, friction braking, and grade.
Its forward-speed clamp does not constitute a rollback test. Physical holding and
rollback resistance still need controlled checks.

Recorded-target replay holds the recorded planner targets fixed. It evaluates
actuator tracking, not a counterfactual traffic outcome. Separate closed-loop
scenarios exercise the actual planner with stationary leads, grades, extra delay,
and reduced/absent regeneration. The broad upstream planner suite remains a
separate compatibility check.

Only two stable-lead manual finishes currently qualify automatically. Their
observed median settled radar gap is about 3.38 m; this is **not** directly assigned
to MPC's STOP_DISTANCE. The style report retains speed-binned deceleration and
review state. Review, more representative support, and validated actuator/gap
mapping are needed before a personalized runtime profile can be released.

## Reproduce locally

Bulk recordings and reports belong on `/mnt/algo14`, outside Git. Use the repo
Python environment with the host-only requirements:

```sh
uv pip install --python .venv/bin/python -r tools/profiling/requirements-braking.txt
.venv/bin/python tools/profiling/archive_braking.py \
  /mnt/algo14/comma3-alpr/braking/2026-09-07/raw \
  00000022--70dddcd1a2 00000023--b274eaf2b2
.venv/bin/python tools/profiling/volt_braking.py \
  /mnt/algo14/comma3-alpr/braking/2026-09-07/raw \
  /mnt/algo14/comma3-alpr/braking/review --video
.venv/bin/python tools/profiling/volt_response_fit.py \
  /mnt/algo14/comma3-alpr/braking/review
.venv/bin/python tools/profiling/validate_volt_braking.py \
  /mnt/algo14/comma3-alpr/braking/review
```

The archive script fetches logs first, excludes the driver camera, and compares
local SHA-256 hashes with hashes calculated on the device. A verified manifest
is written only when every expected file matches. Previously captured
`remote-manifest.json` can be supplied with `--local-manifest` after disconnection.
The extractor refuses to reuse an event ID for a different stop; use a new review
directory if changing the retained input set would renumber old events.

Build the changed solver and parameter bindings before controller tests:

```sh
PATH="$PWD/.venv/bin:$PATH" .venv/bin/scons --minimal -j8 \
  selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so \
  common/params_pyx.so
.venv/bin/pytest -n0 -q selfdrive/controls/tests/test_volt_stopping.py \
  tools/profiling/tests/test_volt_braking.py selfdrive/controls/tests/test_longcontrol.py
.venv/bin/pytest -n0 -q selfdrive/test/longitudinal_maneuvers/test_longitudinal.py
.venv/bin/pytest -n0 -q opendbc_repo/opendbc/car/gm/tests
npm --prefix tools/alpr/web test
npm --prefix tools/alpr/web run build
node tools/alpr/web/braking.browser.test.mjs
```

The native browser test uses copied metadata and isolated decisions; production
recordings are read-only fixtures. `-n0` avoids an existing pytest-xdist enum
serialization issue in opendbc's subtests.

## Remaining release gates

1. Reproduce the recorded peak and final jolt, not only average acceleration.
   Expand the response model only when additional independent data supports it.
2. Demonstrate reduced routine-stop jerk/overshoot without renewed creep,
   degraded collision margins, or loss of emergency command authority.
3. Verify stationary holding, grade response, stop/resume transitions, pedal
   override, and engine/regen-limit conditions in a controlled empty area.
   Do not use scripted maneuver mode behind traffic.
4. Review manual references, fit the personal profile, and validate on withheld
   events. Keep unsupported speeds/conditions at the validated baseline.
5. Review the pinned openpilot/opendbc pair and explicitly release the validated
   profile. Stock remains the offroad rollback option.
