# Volt stopping function candidate

The personal candidate fits a mathematical stopping function to recorded manual
braking. The target is a 4.5 m stopped radar gap, with smoothness taking priority
over matching every fluctuation or exact stopping time. The factory ASCM is
physically unpowered during engagement in this installation.

Review: http://192.168.99.189:8088/#braking

## Current checkpoint

Candidate `be7d59f9e381e077` remains unqualified: 55 of 87 report checks pass,
with 21 offline failures. These include recorded-command response reproduction,
traffic finishing pace/gaps, terminal motion, and grade/reduced-regen cases.
Counts include diagnostic comparisons and are not a safety score.

| Recorded traffic | Personal-model low-speed phase | Final jerk p95 | Settled gap |
| --- | --- | --- | --- |
| 08:58 | Incomplete within recording | Unavailable | Unavailable |
| 10:27 | 2.81 s | 0.95 m/s³ | 3.90 m |
| 10:30 | Incomplete within recording | Unavailable | Unavailable |

The smoother mathematical target is not yet a qualified brake response.
No candidate was installed or selected on the device. Stock remains the rollback
and default selection; Test and Personal remain unavailable under these results.

## Fitted function

Speed is a seventh-degree Bernstein polynomial. Entry speed, acceleration and
zero entry jerk fix its first three coefficients; its final three coefficients
are zero, guaranteeing zero terminal speed, acceleration and jerk. Two shared
normalized shape parameters are fitted on the workstation. The integral of speed
fixes the sum of the two interior coefficients for an exact travelled distance.

Fitting uses event-balanced Huber speed residuals and a small duration penalty.
The deterministic bounded search reports its convergence flag; it does not claim
proof of a globally optimal fit. The current shape is approximately
`[0.7309564, 0.5880263]`. Starting state and available distance scale each curve.

Six manual stops train the shared shape; two stops from route
`00000027--420d6be12e` are excluded from training and used for evaluation.
That route was previously inspected, so it is not described as a pristine blind
test. One additional recording lacks confirmed stationary-wheel observations
under the new endpoint rule and is explicitly excluded. Unknown control samples
are excluded from fitting; distance uses independently observed wheel motion.

Twenty-one of 24 available 2/5/10 m/s approach windows admit a feasible function.
Their normalized speed RMSE is approximately 1–4%. Three windows are infeasible
under the comfort constraints and remain visible. In feasible windows, final
100 ms acceleration is at most 0.016 m/s² and jerk at most 0.296 m/s³. These are
analytic reference metrics, not measurements of the car.

The old event origin is a 0.3 m/s crossing. The new fitting endpoint requires
wheel speed below 0.03 m/s and standstill continuously for 200 ms. Recordings,
fitted curves, and both timestamps appear in the review. Existing event IDs,
annotations, brightness settings and synchronized videos are retained.

## Planner and controller

At stop entry the runtime generator evaluates at most 64 duration candidates.
A scalar projection supplies the interior coefficients. Nonincreasing Bernstein
coefficients certify nonnegative, nonincreasing speed; subdivided convex-hull
bounds and analytic extrema check acceleration and jerk throughout the curve.
Limits are 2.5 m/s² deceleration, 1.5 m/s³ jerk, and final-100-ms magnitudes of
0.05 m/s² acceleration and 0.3 m/s³ jerk. Infeasible requests return to the normal
planner. There is no optimizer, fitting, network access or file I/O in the loop.

A latched curve is evaluated analytically at MPC timestamps. It keeps one world
endpoint; range/odometry inconsistency above 1 m invalidates it rather than adding
an inconsistent position-only correction. The existing radar identity, freshness,
stationary-lead, probability and secondary-lead checks remain. Support is limited
to entry speeds up to 10 m/s; faster approaches use the normal planner until a
supported, feasible entry. Initial positive acceleration is not silently replaced
with zero.

The polynomial uses the existing position/speed/acceleration tracking weights
and sets the comfort gap to 4.5 m only while valid. The old speed-bin distance
lookup is disabled for this model. Existing collision constraints and full
braking authority remain. The generated solver's 41-parameter interface stays
unchanged; legacy lookup fields are zero for polynomial references.

`LongitudinalPlan.voltStopTrajectoryActive` defaults false. Controls accepts it
only from a valid, alive plan aged at most 150 ms and in a selected personal
profile. While active, the stopping controller follows planned acceleration
without the legacy minimum-deceleration taper. Stronger braking requests still
bypass comfort slew limits. Existing bounded feedback and bumpless transitions
remain.

Personal stationary hold requires the standstill flag and raw wheel speed below
0.03 m/s for 200 ms. Creep and grade compensation remain during the moving taper;
existing stationary holding force follows confirmation. No new CAN brake mode
is enabled. Holding and rollback behaviour still require physical validation.

## Qualification and resources

Bundle schema 3 identifies the polynomial model, support, shape, gap and source
hashes. Old or mismatched personal bundles cannot activate it. Brake-only bundles
remain a separate kind with no function. Previous brake-only qualification is
retained under `review/history/0713bbbfcc2d911f/`; raw archives remain outside Git.

Recorded-command reproduction thresholds are unchanged. Personal style agreement
uses normalized speed RMSE ≤10% and duration error ≤max(1 s, 20%) on evaluation
windows. The original manual finishing-pace envelope and rebound limit remain.
New gates also measure 0.3-to-0.03 m/s time ≤1.25 s and provisional physical
terminal acceleration ≤0.15 m/s² / jerk p95 ≤0.5 m/s³ over the final 250 ms.
Physical metrics use force-based acceleration before the plant's forward-speed
clamp. Incomplete traffic stops are not extended beyond observed lead motion.

The existing response diagnosis remains unresolved: low-speed autonomous data
are sparse, pressure/regen signal interpretation needs verification, and the
reserved route has no qualifying autonomous low-speed response. Manual motion
fits are not pooled with autonomous commands to manufacture calibration.
The controller is held fixed when exercising the independent response model.

Scheduling was checked against the device: plannerd/radard share core 5/FIFO 51;
controlsd/card/selfdrived share core 4/FIFO 53; modeld uses core 7/FIFO 54. Function
generation adds work to radar's shared core. Startup warms numerical kernels;
normal updates only evaluate the polynomial.

An earlier parked core-2/nice-19 benchmark observed first-call outliers up to
31.28 ms. Convex-hull shortcuts and startup warm-up were added afterward. The
device became unreachable before the revised implementation could be measured.
`function-timing-pending.json` identifies the unmeasured source. Old timing must
not be presented as qualification of the current source. Whole planner/radar
and controls deadlines plus shared memory/thermal effects remain physical checks.

Verification: 121 Python tests with 23 subtests; 4 upstream longitudinal tests
with 58 maneuver subtests; 11 web tests; production build; browser checks for
video synchronization, function navigation, brightness persistence and isolated
annotation saves; generated C++ schema/library build; Ruff and whitespace checks. These do not establish road readiness.

## Reproduce

```sh
.venv/bin/python -m tools.profiling.volt_function_fit /mnt/algo14/comma3-alpr/braking/review
.venv/bin/python -m tools.profiling.validate_volt_braking /mnt/algo14/comma3-alpr/braking/review --kind personal
.venv/bin/python -m tools.profiling.export_volt_candidate /mnt/algo14/comma3-alpr/braking/review
.venv/bin/pytest -n0 -q tools/profiling/tests/test_volt_*.py selfdrive/controls/tests/test_volt_stopping.py selfdrive/controls/tests/test_longcontrol.py opendbc_repo/opendbc/car/gm/tests
npm --prefix tools/alpr/web run build
node tools/alpr/web/braking.browser.test.mjs
```

Packaging does not install or select a mode. Supervised empty-area trials follow
offline qualification; traffic use follows version-bound physical validation.
