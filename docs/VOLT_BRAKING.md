# Volt braking investigation and locked candidate

The September 7 recordings show controller overshoot earlier in the approach,
followed by weak braking, speed rebound, and an abrupt final stop. Manual finishes
spend approximately 1.46–1.83 seconds below 2 m/s, versus 2.86–4.16 seconds in the
three autonomous recordings. These are different traffic situations, not matched
trials. Stronger deceleration earlier can still produce a smoother final stop.

The factory ASCM is physically powered down while openpilot is engaged in this
installation. Openpilot supplies the brake and powertrain commands. ASCM naming
in platform/DBC files describes the supported architecture, not another active
controller. This work does not change the hardware installation.

## Release status

This is a **locked, default-off prototype**, not a driving update.
`VoltLongitudinalMode` defaults to `stock`; the startup configurator refuses
Smooth and Personal while the versioned profiles remain unvalidated. The
workstation fits are never automatically loaded onto the car. No on-device
software, settings, or CAN commands were changed during this investigation.

The pressure model improves low-speed response identification but still does not
reproduce the complete recorded response. The personal planner does not yet
consistently match manual finishing pace. The no-regen fallback avoids the
simulated contact observed without it, but that stress-case finish remains too
abrupt. The report keeps those failures visible and `deployment_ready` false.
Physical holding, rollback, engine-on/regen-limit behavior, and new independent
manual examples remain outstanding.

Review: `http://192.168.99.189:8088/#braking`.

## Data and measurement corrections

Extraction version 4 retains exact service timestamps, both complete radar
leads, cruise settings, control parameters, native telemetry, and camera frame
indices. Raw tracks now receive their freshness timestamp and are attached only
while fresh. `imu_ax` and `device_pitch` remain explicitly device-frame values;
`vehicle_ax`/`vehicle_pitch` use the same PoseCalibrator as controlsd.
`controller_pitch` is the calibrated orientation actually published in carControl.
Invalid calibration never becomes an assumed flat-road measurement.

Commands are held from their original message timestamps, without interpolation
that borrows a future command. An initial incomplete retained window is trimmed
to its first complete observation; internal observation gaps over 0.3 seconds
reject a replay. Native and simulated jerk use the same centered 0.2-second
difference and -3 to +0.5-second finishing window. Final low-speed duration counts
only the last contiguous phase. Braking onset means the first acceleration below
-0.3 m/s² **within the retained window**, not proof that braking began there.

Event identities, both road-camera clips, manual decisions, and ALPR annotations
are preserved. The original 204 files remain in the checksummed archive outside
Git. Additional archive roots can be included without replacing the first routes.

## Three distinct experiments

1. **Recorded commands:** replay actual gas/regen and friction commands through
   the response model. Check pressure, acceleration, speed, peak deceleration,
   final jerk, and stop timing. This is the model-reproduction gate.
2. **Recorded planner targets:** rerun the controller against the old targets.
   This remains a diagnostic; it cannot establish a better planner's behavior.
3. **Reconstructed traffic:** integrate recorded wheel odometry and radar range
   into lead motion, then run the real planner, LongControl, and GM CarController
   using the simulated ego speed and distance. Both observed leads are retained.
   A primary track change trims the window; ambiguous final identities and gaps
   reject it. The 10:27 window begins after its vision-to-radar track transition.

Traffic replay assumes the lead does not react to the simulated ego vehicle.
Radar/odometry noise and the response-model errors limit interpretation. It does
not prove a counterfactual road outcome. Generated CAN messages are discarded;
the simulator never opens a CAN socket. Its forward-speed clamp does not validate
rollback resistance. A finish outside the recorded window is not established.

## Response and personal-profile fitting

The host-only pressure model separates application/release dynamics and command
deadband, with a speed-dependent pressure gain. Low-speed samples receive an
explicit, reported fit weight. A second stage relates measured pressure, a
monotone regen-fade hypothesis, creep, and calibrated grade to vehicle-frame IMU
acceleration. Pressure values are raw CAN units normalized by 30000, not physical
pressure. The simulator uses GM wheel-speed quantization and the production speed
Kalman filter, rather than an arbitrary acceleration filter.

The offline candidate inverts the training calibration with bounded commands and
speed-dependent friction gain/deadband. The on-device profile constants stay at
their locked baseline. The candidate retains bounded integral correction,
feedback during stopping, stock minimum holding force, and full-braking command
authority while moving. A persistent response deficit under a stable braking
command reduces credited regeneration after 0.8 seconds; the credit remains
bounded between zero and one and resets on disengagement. This is a feedback
fallback, not an identified battery-state model. Its comfort and physical behavior
remain subject to the same release gates.

Manual samples fit a bounded speed/deceleration curve. Integrating v/b(v) yields
stopping distance; a monotone cubic representation enters the MPC **comfort**
objective. The collision-distance penalty and emergency limits are unchanged.
Personalization requires a radar lead with stable identity for 0.5 seconds,
observation age ≤0.3 seconds, lead speed below 0.5 m/s, and model probability ≥0.9.
It blends in over 0.5 seconds, tapers out at the supported speed limit, and disables
immediately for stale, moving, or changed leads. The observed manual gap is
constrained to the existing 4.5–8 m profile range; actual outcomes are reported
separately. Unsupported speeds retain the baseline objective.

Only two manual examples currently qualify. The first route seeds the offline
fit and the second is regression evaluation. `study-split.json` records already
inspected routes and automatically reserves the first new route with at least
three qualifying manual stops **before fitting**. That reservation is immutable.
The approach fit excludes it, reports support by independent event/speed band,
and requires at least three training examples per fitted band for release support.
Manual stops teach style; they are not openpilot command-response calibration.
The current approximately ten-example collection target is not itself a proof
of calibration quality. Review offers at most five automatically selected clips
at a time and never marks unreviewed examples as approved.

## Review interfaces

The existing native event, decision, and validation APIs remain compatible.
The validation report now separates reproduction, fixed-target diagnostics,
traffic cases, scenario checks, collection support, and model limitations.
`GET /api/braking/simulation/:id/:mode` serves only the allowlisted command,
target, and traffic traces. Invalid identifiers/modes return 404. The browser
compares recorded, stock-model, brake-candidate, and personal-approach speed,
acceleration, and gap over the whole approach or final five seconds. It shows
stop completion, duration, jerk, rebound, gap, and retained-window onset separately.

## Reproduce locally

Use the existing workstation environment and host-only requirements in
`tools/profiling/requirements-braking.txt`. Keep recordings/reports on `/mnt/algo14`.

```sh
.venv/bin/python tools/profiling/volt_braking.py \
  /mnt/algo14/comma3-alpr/braking/2026-09-07/raw \
  /mnt/algo14/comma3-alpr/braking/review
# For new trips, repeat with --additional-raw /path/to/new/archive/raw --video.
.venv/bin/python tools/profiling/volt_response_fit.py \
  /mnt/algo14/comma3-alpr/braking/review
PATH="$PWD/.venv/bin:$PATH" .venv/bin/scons --minimal -j8 \
  selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so
.venv/bin/python tools/profiling/validate_volt_braking.py \
  /mnt/algo14/comma3-alpr/braking/review
.venv/bin/pytest -n0 -q tools/profiling/tests/test_volt_replay.py \
  tools/profiling/tests/test_volt_style.py tools/profiling/tests/test_volt_braking.py \
  selfdrive/controls/tests/test_volt_stopping.py selfdrive/controls/tests/test_longcontrol.py \
  opendbc_repo/opendbc/car/gm/tests
.venv/bin/pytest -n0 -q selfdrive/test/longitudinal_maneuvers/test_longitudinal.py
npm --prefix tools/alpr/web test
npm --prefix tools/alpr/web run build
node tools/alpr/web/braking.browser.test.mjs
```

The MPC parameter count changes from 8 to 41, so rebuild its generated solver
before running the revised planner. There is no cereal or panda safety-schema
change. The browser test uses isolated decisions and read-only recording fixtures.

## Acceptance and controlled validation

Reproduction requires peak-acceleration error ≤0.5 m/s², final jerk 70–130% of the
recording, stop timing within 0.5 seconds, and acceleration/speed RMSE ≤0.5 in their
respective units. Average error alone cannot release a profile. Routine finishing
pace uses the manual duration range ±0.35 seconds, manual jerk ceiling, speed
rebound ≤0.05 m/s, and a completed stop. These are provisional design targets.

Scenarios include stationary/decelerating leads, cut-in, lead loss, pull-away,
stop/resume, grades, extra delay, missing regen, engine-on, pedal override, and
full-braking requests. No contact/solver failure is necessary but not sufficient;
reduced-braking scenarios also retain explicit comfort checks.

Only after offline gates pass should a separately reviewed controlled vehicle
trial evaluate actual response, holding, rollback, and brake limits. Ordinary
traffic deployment follows physical validation and a reviewed profile release.
Stock remains the default and the offroad rollback option.
