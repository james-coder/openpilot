# Cut-in relaxation v3: what went wrong in v2 and replay validation

Branch: `cutin-v3` (on top of `main-james` d04e6ccc3). Not deployed to the car.

## What v2 did wrong (from route 00000091--5d5009657b, 2026-09-17)

1. **Overspeed after every cut-in.** v2 relaxed the comfort target (`params[:,6]/[:,7]`)
   after the cruise obstacle had already been placed using the nominal stop distance and
   comfort brake. The cost function then pulled ego toward an obstacle that sat farther out
   than its own target, so it accelerated past the set speed. Logged: 70 set, 74.6-75.3
   actual, 3.8 s per episode.
2. **Accepted cut-ins that were not cut-ins.** Track 63 was accepted at 319 ft ahead with
   confidence 0.55 while ego was already above the set speed. Nothing needed relaxing.
3. **Start/end thrash.** One-frame lead dropouts reset the state; the 1 s confidence latch
   in radard immediately started a new event 0.1-0.3 s later.

## What v3 changes

- `cutin_relaxation.update()` runs before the cruise obstacle is built and returns one
  effective (stop_distance, comfort_brake) pair. `gap_targets()` derives the cruise
  obstacle, the cost target and the compiled danger-zone scale from that one pair.
- Lead-status dropouts under 0.5 s do not end an event; the same track cannot start a new
  event for 2 s after one ends.
- No event is created when the observed gap is already at or above the current target.
- Stop distance no longer moves. `CUTIN_COMFORT_BRAKE_MAX = 15.0`, giving a floor gap of
  55% of nominal at 70 mph (nominal 357 ft / 3.5 s, floor 196 ft / 1.9 s). Recovery 8-25 s
  depending on how tight the merge was.

## Replay of the recorded drive through plannerd (local ACADOS build, stock danger zone baked)

708 s episode (segments 11+12 replayed together so the planner is warm), 70 mph set:

| t (s) | logged speed | logged v2 aTarget | replayed v3 aTarget |
|---|---|---|---|
| 1635.5 (cut-in accepted at 319 ft) | 70.8 | +0.52 | -0.17 |
| 1637.0 | 73.2 | +0.70 | -0.15 |
| 1638.0 | 74.6 | +0.01 | -0.19 |
| 1640.0 | 73.2 | -0.73 | -0.33 |

787 s episode (segment 13): logged v2 accelerated in 67% of frames while over the set
speed; v3 replay 0% of frames, max aTarget +0.04.

The two other over-set windows on that drive were not cut-in related: 297 s and 898 s are
the driver pressing set while the car was still faster than the new set speed. v3 and v2
replay the same there.

Caveat: replaying a segment on its own shows a fake 5 s acceleration ramp at the segment
start (planner speed filter starts at zero). Always replay the previous segment too.

## Still unvalidated

- radard's v3 detection changes (recent-adjacency decay, transition logging) only ran in
  unit tests; the replay used the confidence values logged by v2's radard.
- Real on-road cut-ins with v3. Deploy only after a deliberate decision; `main-james`
  stays the road branch.
