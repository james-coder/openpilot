# Hard braking near a car ahead: would openpilot have braked first? 2026-09-25

Question: when the driver braked hard approaching a car ahead, was openpilot already braking,
would it have braked in time on its own, and can it start braking earlier without giving up the
closer following distance adopted on 2026-09-17?

Data: every rlog on the device from 2026-09-22 to 2026-09-25, routes 000000a5 through 000000b5
(351 segments; 4.7 h moving, 3.4 h with openpilot longitudinal active, almost all on the
**relaxed** personality). Extracted on the device, read-only, into 20 Hz tables. Scripts in
`.scratch/analysis/`: `panic_brake_extract.py`, `panic_brake_events.py`, `panic_brake_sim.py`,
`panic_brake_sweep.py`, `panic_brake_noregress.py`.

## Events

A brake-pedal press above 9 mph with a radar/vision lead ahead that the car was closing on:
**64 presses, 36 while openpilot was driving.** Seven were hard (driver decel ≤ -3 m/s² or
required decel ≥ 2.5 m/s²); five of those were while engaged. Road-camera frames for the five:

| Route/segment | Speed | Driver decel | What the camera shows |
|---|---|---|---|
| aa seg 4 | 34 mph | -3.5 | Slow traffic ahead on an arterial. openpilot braked to -1.1, then eased to -0.6 while still closing at ~22 mph on the car 48 m ahead |
| af seg 13 | 41 mph | -3.9 | Traffic light; no close car in the lane |
| af seg 32 | 41 mph | -4.0 | Light turning yellow |
| b3 seg 16 | 43 mph | -3.2 | Light turning yellow/red (night) |
| af seg 18 | 39 mph | -3.5 | Car close ahead-right, cutting in or pulling out; first detected about 2 s before the press, camera only |

Three of the five were traffic lights. With Experimental mode off, openpilot does not react to
lights at all. One was a late-detected cut-in, which no planner tuning changes. One (aa seg 4) is
the case this analysis can address.

Common to the lead-car events: the slow or stopped car was first tracked only 60-120 m ahead,
the radar switched between tracks, and vision-only speed estimates were sometimes wrong.

## Cause of the late braking: COMFORT_BRAKE = 6.0

The planner's desired gap is `v_ego²/2cb + T·v_ego + 4.5 m` against the lead's stopped-equivalent
position `x_lead + v_lead²/2cb`. The 09-17 retune raised `cb` from 2.5 to 6.0 to shorten the
following gap. It does not do that: the two `cb` terms cancel whenever the speeds match, so the
steady gap is `T·v + 4.5 m` regardless of `cb`. Confirmed in simulation: an identical steady gap
at 30, 50 and 70 mph for 6.0 and 2.5. The shorter gap everyone likes comes from T_FOLLOW and
STOP_DISTANCE, which are unchanged. What `cb = 6.0` did change is that, when closing, the planner
assumes it can brake at 6 m/s², but it can only command -3.5. It waits, then brakes late and
hard, which is the aa seg 4 pattern.

## Counterfactual

Closed-loop simulation: the real LongitudinalPlanner drives a simulated Volt from 12 s before the
press. The driver never touches the pedals. The lead's motion is rebuilt from ego odometry plus
radar range. The actuator follows the target after 0.5 s (and 0.8 s as a sensitivity check) with
a 0.25 s lag. Fidelity against what openpilot actually commanded: correlation 0.69, median error
0.28 m/s². This is an approximation of the car, not the car.

50 of the 64 events had enough continuous lead tracking to simulate.

| COMFORT_BRAKE | Min gap, median | Worst min gap (0.5 s / 0.8 s delay) | aa seg 4 min gap | FCW events |
|---|---|---|---|---|
| 6.0 (was) | 6.9 m | 0.8 m / **-2.1 m** | 1.5 m | 4 |
| 4.5 | 7.3 m | 1.2 m / -1.5 m | 2.9 m | 3 |
| 3.5 | 7.9 m | 1.6 m / -1.2 m | 4.8 m | 2 |
| 3.0 | 8.4 m | 1.8 m / -0.8 m | 6.4 m | 2 |
| **2.5 (stock, now)** | 9.0 m | 2.2 m / 0.7 m | 8.7 m | 2 |

No-regression check: 120 engaged approaches to a slower car where the driver did not brake.
At 2.5, only 2 of the 120 braked more than 0.5 m/s² harder than at 6.0, and none by more than
1.0. The median gap at the end of the approach grew by 0.7 m. Braking starts somewhat earlier
and more gently when catching a slower car on the highway. Steady following is unchanged.

comma's maneuver test "approach a stopped car from 60 mph": 6.0 saturates at -3.5; 2.5 peaks at
-3.26 and stops 0.6 m farther back.

**Change:** `COMFORT_BRAKE` 6.0 → 2.5 (`long_mpc.py`), with tests that it stays within the
commandable deceleration and that the 60 mph stopped-car approach does not saturate.

## Open: following time vs. a lead that brakes hard

comma's stock maneuvers "following at 45 mph, lead brakes to a stop at 2 / 3 m/s²" fail with
the current T_FOLLOW. In the simulation the planner alone does not keep the gap, even with the
instant actuator in comma's test plant. Stock passes. COMFORT_BRAKE 2.5 halves the overlap but
does not remove it:

| Personality (T) | lead 2 m/s², cb 6.0 → 2.5 | lead 3 m/s², cb 6.0 → 2.5 |
|---|---|---|
| aggressive (0.625 s) | -1.3 → -0.5 m | -6.7 → -1.2 m |
| standard (0.725 s) | -4.3 → -1.6 m | -9.6 → -3.2 m |
| relaxed (0.875 s) | -2.3 → -0.7 m | -7.5 → -1.4 m |

The shortest following time that passes both, with cb 2.5: aggressive 0.875 s; standard and
relaxed 1.1 s (they use a higher jerk cost, so they react more slowly). The measured real
following gap on relaxed is a median of 1.07 s including the 4.5 m standstill distance. Raising
relaxed to 1.1 s adds about 4.5 m at 45 mph and 7 m at 70 mph, still well short of stock
relaxed (1.75 s). This is a decision for the driver, not made here.

## Limits

- Simulation results are not a guarantee against a collision (see VOLT_COLLISION_PROTECTION.md).
  The factory Forward Automatic Braking is unavailable while openpilot is engaged.
- The lead's motion is reconstructed from the log and does not react to the simulated car.
  Late detection and track switching are reproduced as logged, not improved.
- Traffic lights and late cut-ins are out of scope for this tuning.
