# Volt brake-only candidate

The first release now targets brake blending and the final stop, using the
existing planner and following-distance settings. Personal approach learning is
separate. The factory ASCM is physically powered down during engagement in this
installation; these changes do not alter that hardware arrangement.

The current source-bound qualification, failed checks, staged response diagnosis,
and synchronized road video are at `http://192.168.99.189:8088/#braking`.
The device remains on its existing software. No candidate is installed or enabled.

## Current checkpoint

Candidate `0713bbbfcc2d911f` is a brake-only bundle and remains unqualified:
29 of 50 report checks pass, with 18 offline qualification failures. Counts span
different types of checks and are not safety scores. In the 10:27 replay, final
jerk is 0.90 m/s³ but the low-speed phase still takes 3.37 seconds. The other two
traffic replays do not finish within their retained windows. Recorded-command
reproduction remains inaccurate; holding/comfort/distance checks also retain
explicit failures. No acceptance threshold was relaxed to enable the candidate.

Verification: 111 controller/extraction/replay/GM tests with 23 subtests,
4 upstream longitudinal tests with 58 maneuver subtests, 11 web server tests,
production build, browser synchronization/persistence/annotation checks, and Ruff.

## Controller changes

The stopping controller starts from its previous output rather than delayed
measured acceleration. It resets the integral at that transition and eases normal
braking at up to 1.5 m/s³ instead of 0.8 m/s³. Stronger planner requests and full
braking authority still bypass the comfort limit. The taper anticipates pressure
response using predicted speed. Positive integral correction remains bounded and
diminishes near zero speed; minimum stationary holding force is retained.

A stop latch now also operates without a personal trajectory. It finishes an
initiated low-speed stop while the same stationary radar lead remains fresh and
stable, avoiding repeated release/reapplication through estimator noise. Moving,
stale, changed, or closer secondary leads clear the latch, as do disengagement,
pedal override, and Experimental mode. Stable identity requires 0.5 seconds,
freshness at most 0.3 seconds, and lead probability at least 0.9.

Brake-only operation does not load a learned curve or modify MPC weights,
following distance, or comfort parameters. Nominal gap checks use the existing
6 m planner target. Recorded-traffic checks also require preservation of baseline
minimum-distance margins. Personal-gap matching and manual curve-fit support are
reported separately and do not unlock or block brake-only operation.

## Isolating the response errors

`tools/profiling/volt_brake_diagnostics.py` works from retained event windows,
using source/content-addressed compressed array caches. Commands and telemetry
are causally held from their original service timestamps. Unknown pressure,
engine, regen, pedal, or control state does not become an assumed valid zero.
Overlapping windows are deduplicated for fitting and support counts; diagnostic
plots retain full event windows. Bulk recordings remain outside Git.

The report separates:

1. Command-to-pressure error, including low-speed error and observed brake modes.
2. Motion error with predicted pressure versus measured pressure, within separate
   uninterrupted episodes initialized at recorded wheel speed.
3. Complete recorded-command replay, with independently evolving vehicle speed,
   reported by the existing validation pipeline.

The first two stages use measured speed to isolate model components; they cannot
substitute for the complete reproduction gate. Missing intervals split episodes.
No motion is integrated across pedal interventions or unknown observations.

A bounded pressure-onset experiment adds a pressure-application phase after a
zero-pressure command transition while retaining the prior modulation model.
Selection excludes the reserved route and reports evaluation on a separate route.
The modulation fit itself used that evaluation route previously, so this is a
component diagnostic, not fully independent validation. Its parameters are never
automatically applied to the runtime controller.

Manual events are used only for measured-pressure-to-motion diagnosis, with
known engine-off, zero reported regen, and no accelerator input. Manual pressure
is never paired with openpilot's inactive commands to fit command response.
Raw CAN pressure has no established physical unit here; raw regen zero is a
selection condition, not a calibrated torque measurement. Brake mode 0xb remains
disabled; no new CAN mode is enabled by this work.

## What the downloaded data establish

The cached training windows contain approximately 10.7 seconds of qualifying
autonomous response below 2 m/s. The 18 takeover events add no unique qualifying
autonomous observations in that range under the intervention/validity guards.
Only two pressure-onset transitions occur in the onset-training partition, so
onset delay remains unidentified. The reserved route has no qualifying low-speed
autonomous response episode.

Separate measured-pressure fits produce substantially different coefficients
for autonomous and manual braking (approximately 2.13 versus 4.96 per normalized
raw-pressure unit). The manual creep coefficient reaches its fit bound. These
are evidence against pooling those observations under the current assumptions,
not proof of a particular physical fault. The signal interpretation and dependence
on actuation state need verification before either fit is treated as calibration.

The next required measurements are explicit in the review: independent
zero-pressure autonomous application transitions, low-speed autonomous response
on an unfitted route, and verified pressure/regen signal interpretation. Additional
manual-style annotations are not needed to start that investigation. The device
was checked for newer trips; its newest completed route remains
`0000002a--fc03d1654f`, already archived locally.

## Qualification, compatibility, and rollback

Bundle schema version 2 adds `kind: brake | personal`. A brake bundle has no
personal curve. Its identity covers kind, coefficients, and source hashes;
adding reviewed evaluation evidence does not change that identity. Old schema,
stale source, mixed-kind, or invalid bundles cannot activate a profile.
The previous personal report is preserved outside Git at
`review/history/c75f28f6049d703b/`.

The parked settings UI offers Stock, Test, Brake, and Personal. Test uses the
qualified bundle kind; a brake test never sets the PERSONAL flag. Brake maps to
the existing `smooth` parameter and requires road qualification for a brake
bundle. Personal requires a separately qualified personal bundle. Stock is the
default and rollback choice. Startup snapshots the verified bundle once; a
selected bundled profile fails closed if its snapshot is missing or mismatched.

Test requires response reproduction, traffic, nominal, stress, and independent
response checks. Road qualification additionally requires the reserved response
check and version-matched physical evidence for stopping, holding, override,
grade, engine-on/reduced regen, driver comfort, and runtime deadlines. Physical
records are reviewed attestations with route and recording SHA-256, not an
automatic safety classifier. Templates never overwrite entered evidence.

Recorded-command reproduction requires peak acceleration error ≤0.5 m/s²,
jerk at 70–130% of the recording, stop timing within 0.5 seconds, and speed and
acceleration RMSE ≤0.5. Finishing pace uses the training manual duration range
±0.35 seconds, manual jerk ceiling, rebound ≤0.05 m/s, and a completed stop.
Incomplete stops have no invented finish metrics or extension beyond available
lead observations. Grade tests inspect holding-force margin in addition to the
simulator's forward-speed clamp; the clamp cannot prove physical rollback safety.

## Runtime resources

Scheduling is unchanged: controlsd/card/selfdrived share core 4/FIFO 53,
plannerd/radard share core 5/FIFO 51, modeld uses core 7/FIFO 54, and UI uses
core 0. The latch runs within plannerd and therefore shares radar's core. The
stopping update runs in controlsd. No additional process, stream, model inference,
network access, or file I/O is added to the control loop. Fitting and caches are
workstation-only.

A parked comma3 helper benchmark used core 2, ordinary scheduling, and niceness
19. Across ten batches of 10,000 latch calls, mean time was 0.00326 ms/call and
maximum batch mean was 0.00328 ms/call. This is not a worst-case onroad scheduling
measurement. Planner/radar deadlines and shared thermal/memory effects still
require controlled validation. No device CPU configuration was changed.

## Reproduce

Use the existing workstation environment and keep recordings under `/mnt/algo14`.
The generated 41-parameter MPC solver and Params extension must already be built.

```sh
.venv/bin/python -m tools.profiling.volt_brake_diagnostics /mnt/algo14/comma3-alpr/braking/review
.venv/bin/python -m tools.profiling.validate_volt_braking /mnt/algo14/comma3-alpr/braking/review --kind brake
.venv/bin/python -m tools.profiling.export_volt_candidate /mnt/algo14/comma3-alpr/braking/review
.venv/bin/pytest -n0 -q tools/profiling/tests/test_volt_*.py \
  selfdrive/controls/tests/test_volt_stopping.py \
  selfdrive/controls/tests/test_longcontrol.py opendbc_repo/opendbc/car/gm/tests
.venv/bin/pytest -n0 -q selfdrive/test/longitudinal_maneuvers/test_longitudinal.py
npm --prefix tools/alpr/web test
npm --prefix tools/alpr/web run build
node tools/alpr/web/braking.browser.test.mjs
```

Diagnosis is bound to its source and response-model hash; stale results fail an
offline qualification check. Validation refuses to export if controller or
validation source changes during its run. Packaging a bundle neither installs it
nor selects a device mode. Controlled empty-area trials follow offline
qualification; normal traffic use follows physical validation.
