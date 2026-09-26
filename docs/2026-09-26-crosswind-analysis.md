# Riding off-center in crosswinds: analysis and wind-gated hold, 2026-09-26

Complaint: in strong crosswinds on open highway, openpilot rides steadily to one side of the lane, with a tire
on the line. Driving, and other drivers' reactions, feel wrong, and there's no way to correct it short of taking over.

Data: 12.8 h of engaged driving from Sep 4 to Sep 26 (local archives plus the device; qlog-level signals at 1 Hz).
The Sep 21 return trip's middle portion (Las Vegas → Nephi) is not available. Wind was back-filled from Open-Meteo's
historical API per location and hour; crosswind is the component across the GPS heading. Scripts are in
`.scratch/analysis/crosswind_*.py`. Lane offset is `(leftY + rightY)/2` from `drivingModelData.laneLineMeta`
(+ = car left of center).

## Findings

- **A general left bias of about 18 cm, in all conditions.** Here openpilot's own target points left (the
  model's desired curvature is toward the car's side), the steering follows it closely, and nothing pushes back.
  This is the driving model's lane position, not a steering shortfall. Work zones produce long left stretches
  (Sep 4, I-80, cones on the right lane line: 12 min at 0.48 m left).
- **The windy case is different.** On Sep 4, westbound I-80 in Wyoming at 16:28-16:43, the wind was
  22 mph with gusts to 31, and 12-20 mph of it across the car from the left:
  - The car rode a median of **0.61 m right** of center (0.82 m at the 10th percentile), with the right tire
    within 20 cm of the line 56% of the time.
  - The steering showed a steady **0.86° angle error**: openpilot kept asking for more left than the car
    delivered. Calm stretches show about 0.0-0.1°.
  - Steering output was a median of **17% of the limit** (90th percentile 36%), never saturated.
  - The driver steered 26% of the time.
- **Why:** the Volt's angle PID has no integral term (`kiV = [0.]`). A steady side push can therefore only be
  answered with a steady angle error, and the car settles off-center with most of its steering authority unused.

## Change: crosswind hold, wind-gated (`selfdrive/controls/lib/crosswind_hold.py`, `latcontrol_pid.py`)

- **Integral only while windd reports a fresh strong crosswind.** It turns on at ≥ 12 mph across the GPS heading
  and off below 8 mph. It needs windd to be alive (status < 30 s old), a reading < 30 min old, and
  GPS speed > 5 m/s. Otherwise the controller is bit-for-bit the stock P-only one (tested).
- **Gain and cap:** ki 0.05 at 40 m/s, ramping from 0 at 15 m/s. The hold term is capped at 40% of the torque
  limit. The 3 Nm limit, rate limits and driver-override allowance are unchanged and panda-enforced.
- **Driver interaction:** the integrator is frozen while the driver steers (stock), cleared after more than 1 s of
  driver steering and on disengage, and fades out over about 2 s when the wind drops.
- **Visible:** the wind badge turns amber and shows `HOLD` while it is active and openpilot is steering.
- **Setting:** Toggles → Crosswind Hold, Auto (default) / Off, applied next drive.
- **Toy closed-loop checks:** this removes the steady offset without oscillation. A gain 3× higher is still
  smooth; jitter appears at about 6×.

## Limits

The simulation is a toy and wind is a modelled hourly value, not a measurement at the car. The first windy drive
with this should be one where the driver is ready to correct. It does not address the general left bias, which is
the model's chosen position rather than a push.
