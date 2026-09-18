# Stopping gap to the lead: recorded stops, 2026-09-18

Question: when the car comes to a complete stop behind another vehicle, how much gap is
left, and how does openpilot compare with the driver?

Data: every full rlog on the device on 2026-09-18, routes 00000080 through 00000092
(423 segments). Script: `.scratch/analysis/stop_gaps.py`. Distances are the radar's
`dRel`, which the GM radar interface reports from the front of the car.

## Definitions

- **Complete stop**: speed below 0.1 m/s for at least 1 s, after having been above 1.5 m/s.
- **Openpilot stopped the car**: engaged at the moment of the stop and no brake pedal in
  the previous 2 s.
- **Driver stopped the car**: everything else (manual driving, or brake pedal while engaged).

## Result

**Openpilot stopped the car: 0 times.** Across all recordings the lowest speed at which
openpilot was still engaged was 19 mph on a normal drive (3 mph once, during the
2026-09-17 brake-on-engage incident). The driver brakes, which disengages, at 25-30 mph or
above on every stop. The car reports an 18 mph minimum engage speed. Openpilot's stopping
behaviour on this car has therefore never been exercised in the recordings.

**Driver stopped the car: 36 times**, 18 with a lead detected at the moment of stopping.

Gap to the lead when the driver stopped (18 stops):

| min | p25 | median | mean | p75 | max |
|---|---|---|---|---|---|
| 11 ft | 13 ft | 15 ft | 22 ft | 19 ft | 87 ft |

| gap | stops |
|---|---|
| 10-15 ft | 10 |
| 15-20 ft | 5 |
| 30-40 ft | 1 |
| 40-50 ft | 1 |
| 75-100 ft | 1 |

Fifteen of eighteen stops land between 11 and 20 ft. The three long ones are stops well
short of a distant car (e.g. queuing at a light while the car ahead was still rolling).

The 18 stops with no lead: 10 had no radar lead in the previous 10 s (empty road ahead),
8 had a lead that was last seen 44-121 ft ahead and drove off before the stop. Only one
stop had a recent close lead (15 ft, last seen 9.9 s earlier). There is no evidence in
this data of the lead dropping out at bumper-to-bumper range at the moment of stopping.

Routes 00000080-00000085, 00000087 (long recordings on the scanner-only deployment
branch) were stationary for their entire length and contribute no stops.

## What the planner targets today

| | stop distance at standstill |
|---|---|
| `main-james` (`STOP_DISTANCE = 4.5 m`) | 15 ft |
| stock openpilot (`6.0 m`) | 20 ft |

The driver's stated preference is 15-22 ft. The `main-james` target sits at the bottom of
that range and matches the driver's median exactly; stock sits in the middle of it.

## Caveats and next step

- The manual sample is one driver, 18 stops, from six drives. Enough to see the habit,
  not enough for fine tuning.
- Openpilot's own stopping has not been observed. Before adjusting the standstill
  distance, do a deliberate low-speed test: engage below 30 mph on a quiet road or lot,
  approach a stopped lead, and let openpilot bring the car to a stop while ready to brake.
  Then rerun the script; the openpilot-stopped group will populate.
