# Volt stopping candidate and qualification

The September 7 recordings show weak low-speed braking, speed rebound, and an
abrupt finish. The original manual examples take approximately 1.46–1.83 seconds
from 2 to 0.3 m/s, versus 2.86–4.16 seconds for the three autonomous stops. These
are different traffic situations, not matched trials. The factory ASCM is
physically powered down while openpilot is engaged in this installation.

The candidate combines a finite stopping trajectory with earlier friction
compensation as regeneration fades. It remains default-off. The actual current
qualification, failed checks, and replay traces are available at
`http://192.168.99.189:8088/#braking`. Offline improvement alone does not establish
that the car brakes better. No candidate software or settings were installed on
the device during development.

## September 8 result

The packaged candidate is `c75f28f6049d703b`. It passes 55 of 78 report checks;
12 offline qualification checks fail, so neither Test nor Personal is available.
This count includes diagnostic comparisons and must not be read as a safety score.

| Recorded stop | Recorded low-speed phase | Candidate replay | Candidate final jerk |
| --- | ---: | ---: | ---: |
| 08:58:59 | 2.86 s | 1.68 s | 1.75 m/s³ |
| 10:27:10 | 3.03 s | 1.77 s | 4.37 m/s³ |
| 10:30:01 | 4.16 s | 2.34 s | 1.26 m/s³ |

The second replay still exceeds the 1.82 m/s³ manual jerk ceiling and stops at
3.94 m, outside the 4.5 ±0.5 m target. Reduced-regen stress finishes also exceed
the comfort limit. All simulated traffic/scenario minimum-distance and solver
checks pass, including holding and lead pull-away, but comfort remains incomplete.

Recorded-command reproduction predicts stopping 1.53–4.23 seconds too early.
The reserved route has no qualifying low-speed autonomous response samples;
its manual curve error is 0.63 m/s² against the provisional 0.5 limit. These
limitations block qualification even though average acceleration error is small.
More tuning against this plant cannot establish real braking quality. The next
required evidence is a better identified low-speed pressure/regen response,
followed by controlled physical validation after the offline gates pass.

Verification: 100 controller/extraction/replay/GM tests with 23 subtests;
4 upstream longitudinal tests with 58 maneuver subtests; web server and browser
tests; production web build; Ruff and whitespace checks. Unit tests establish
implementation behavior, not driving qualification.

## Controller behavior

A fresh stationary radar lead with stable identity for 0.5 seconds, age at most
0.3 seconds, and model probability at least 0.9 can activate personalization.
A finite position/speed/acceleration reference ends at the fitted gap. Its clock
advances even when the car lags behind; small radar/odometry corrections adjust
the endpoint without restarting that clock. The reference supplies MPC comfort
costs while retaining collision penalties, actuator limits, and override behavior.
Unsupported speeds, infeasible comfort stops, stale observations, changed or
moving leads, and a closer secondary lead use the baseline planner.

The manual speed/deceleration curve is shared by the planner and final stopping
controller. Positive integral correction diminishes with speed so it cannot
cancel the final taper. A completed stationary-lead stop holds through estimator
noise. Lead pull-away still permits the normal starting transition.

The friction allocator uses a bounded inverse pressure model with nonlinear
command response and speed-dependent gain. It anticipates speed loss across the
response horizon when crediting regen and allocating friction. A delayed-demand
observer progressively reduces regen credit when measured braking falls short;
it does not wait for a fixed 0.8-second deficit and then switch. This is feedback
compensation, not an identified battery-state model. Engine-on and unknown engine
states receive conservative regen credit. Stock command limits, minimum holding
brake, urgent full braking, and pedal override remain in force.

## Data and replay

Extraction version 5 preserves original service timestamps, both radar leads,
cruise settings, model throttle probabilities, force-deceleration state, live
steering offset, calibrated vehicle acceleration/pitch, pressure, engine state,
and actuator commands. Unknown or stale telemetry is not silently treated as a
fresh zero. Commands use causal zero-order hold. Replay rejects internal gaps
rather than borrowing future measurements.

Three experiments answer different questions:

1. Recorded CAN commands through the plant test response-model reproduction.
2. Recorded planner targets through the controller are a diagnostic comparison.
3. Reconstructed lead motion runs the real planner, LongControl, and GM
   CarController against simulated ego motion. Both leads and native throttle
   gating are retained; controller state is seeded from the recording.

Traffic reconstruction assumes the lead does not react to the simulated ego
vehicle. Generated CAN messages are discarded; replay never opens a CAN socket.
A stop outside the retained recording window is incomplete, with no fabricated
finish duration, jerk, or settled gap. A forward-speed clamp cannot prove physical
rollback resistance; grade checks additionally inspect holding-force margin.

The original 51 segments contain 204 verified log/video files. Six newer trips
add 211 segments and 422 verified rlog/qlog files (2,232,547,658 bytes). New road
video is selected separately to avoid delaying log analysis. Recordings, clips,
checksums, reports, and annotations stay outside Git under
`/mnt/algo14/comma3-alpr/braking`. Event IDs and entered decisions are preserved.

## Fitting and independent evidence

Pressure fitting separates application and release lag, command deadband,
nonlinearity, and speed-dependent gain. Physical response fitting combines
calibrated acceleration, predicted pressure, and integrated wheel-speed change
within uninterrupted autonomous episodes. Engine-unknown observations are
excluded. Pressure is normalized raw CAN data, not a physical pressure unit.
Regen fade is selected within a bounded monotone family.

The primary model pools training routes. A second model uses a route subset;
validation changes the plant while keeping controller calibration fixed. The
report explicitly labels inspected regression data. A reserved route is excluded
from fitting. `study-split.json` preserves that reservation; before fitting newly
archived trips, it selects the new route with the most qualifying manual stops
(ties use route order). Sparse reservation does not relax release support gates.

The September 8 archive has nine qualifying manual stops: seven for fitting and
two on the reserved trip `00000027--420d6be12e`. Manual stops teach style, not
openpilot command response. Support is counted by independent event and speed
band. The held-out curve error is reported separately, and held-out examples do
not set the tuning acceptance envelope. Review offers a small selection of
examples without automatically marking them representative.

## Version-bound startup and rollback

`selfdrive/car/volt_candidate.json` contains calibration, the manual curve,
controller/validation source hashes, qualification checks, and optional physical
evidence. Its identity binds coefficients and source files; adding evaluation
evidence does not change the tested controller identity. Changing implementation
or coefficients invalidates qualification. Validation refuses to export if those
sources changed while it ran.

The parked settings UI offers **Stock**, **Test**, and **Personal**:

- Stock is the default and rollback choice.
- Test requires all offline response, traffic, nominal, stress, and independent
  response gates. It is intended for supervised empty-area checks.
- Personal additionally requires release support and version-matched physical
  evidence for stopping, holding, override, grade, engine-on, reduced regen,
  driver comfort, and runtime deadlines.

Card verifies the bundle and snapshots the selected calibration once at startup
in `VoltLongitudinalActiveBundle`. Planner and controller use that same snapshot.
Missing or stale bundles cannot silently enable an experimental profile. No
fitting, network access, or file reads occur inside the control loop.

Physical evidence is a reviewed attestation, not an automatic driving-quality
classifier. Each check names a route and recording SHA-256; placeholders are
rejected. Do not mark a check passed without inspecting the matching recording
and source version. `vehicle-checks-<profile-id>.json` templates are never
silently overwritten.

## CPU and scheduling verification

Source was checked locally and on the parked comma3: card, controlsd, and
selfdrived use core 4/FIFO 53; plannerd and radard share core 5/FIFO 51; modeld uses
core 7/FIFO 54; camerad uses core 6; pandad uses core 3/FIFO 54; UI uses core 0.
The finite trajectory builder adds work on the planner/radar core. Its bounded
50 ms integration grid and at most three distance corrections replace repeated
NumPy calls and an iterative search in its inner loop.

An ephemeral parked-device helper benchmark on core 2, ordinary scheduling and
niceness 19, measured worst construction times of about 1.1 ms at 2–5 m/s and
4.7 ms at the tested supported maximum, versus about 32 ms before optimization.
The device had only cores 0–3 online while offroad. This verifies helper cost,
not whole-process timing on core 5. Actual planner/radar deadlines, thermals, and
memory must be checked during controlled operation. Affinity does not isolate
shared thermal or memory resources. No device CPU configuration was changed.

## Acceptance and reproduction

Response reproduction requires peak-acceleration error ≤0.5 m/s², jerk at
70–130% of the recording, stop timing within 0.5 seconds, and acceleration/speed
RMSE ≤0.5. Routine finishing must meet manual duration ±0.35 seconds, the manual
jerk ceiling, rebound ≤0.05 m/s, completed stop, and target gap ±0.5 m together.
No contact or solver failure is necessary but insufficient. Slow approaches,
cut-in, lead loss/pull-away, stop/resume, grades, additional delay, no regen,
engine-on, override, and urgent braking remain explicit scenarios.

Use the existing workstation environment. Build the 41-parameter MPC solver
and Params extension before running the revised planner:

```sh
PATH="$PWD/.venv/bin:$PATH" .venv/bin/scons --minimal -j8 \
  common/params_pyx.so \
  selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so
.venv/bin/python -m tools.profiling.volt_braking \
  /mnt/algo14/comma3-alpr/braking/2026-09-07/raw \
  /mnt/algo14/comma3-alpr/braking/review \
  --additional-raw /mnt/algo14/comma3-alpr/braking/2026-09-08/raw
.venv/bin/python -m tools.profiling.volt_response_fit /mnt/algo14/comma3-alpr/braking/review
.venv/bin/python -m tools.profiling.validate_volt_braking /mnt/algo14/comma3-alpr/braking/review
.venv/bin/python -m tools.profiling.export_volt_candidate /mnt/algo14/comma3-alpr/braking/review
.venv/bin/pytest -n0 -q tools/profiling/tests/test_volt_*.py \
  selfdrive/controls/tests/test_volt_stopping.py \
  selfdrive/controls/tests/test_longcontrol.py opendbc_repo/opendbc/car/gm/tests
npm --prefix tools/alpr/web test
npm --prefix tools/alpr/web run build
node tools/alpr/web/braking.browser.test.mjs
```

Packaging a bundle does not install it or select Test. Failed offline checks keep
Test unavailable. Normal traffic use requires subsequent physical validation;
the user has agreed to supervised empty-area stopping tests after qualification.
