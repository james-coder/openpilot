# Volt collision protection investigation

The factory car had an emergency-braking feature distinct from ordinary adaptive
cruise control. This installation physically removes ASCM power during openpilot
engagement. Neither the fork's stock tuning nor its forward-collision warning
restores an unpowered factory controller. The personal candidate is blocked from
vehicle testing and road use; the new envelope experiment has no actuator access.

Review: http://192.168.99.189:8088/#braking

## Factory evidence and unresolved configuration

The first-print 2017 Volt manual describes limited ACC braking and separate
Forward Automatic Braking (FAB), including hard braking, with detection and
operating limits. Its FAB section gives 5–37 mph. That is **not established as
the limit for this particular ACC-equipped car**.
[Chevrolet manual, printed pages 196, 200, 210–211](https://www.chevrolet.com/ownercenter/content/dam/gmownercenter/gmna/dynamic/manuals/2017/Chevrolet/Volt/2k17volt1stPrint.pdf#page=211).

Chevrolet's 2017 brochure lists low-speed FAB separately from full-speed ACC
with FAB. The manufacturer document is preserved by an archive.
[Chevrolet brochure, PDF page 20](https://xr793.com/wp-content/uploads/2017/07/2017-Chevrolet-Volt.pdf#page=20).
GM's October 2017 service newsletter, covering multiple models including Volt,
describes 5–50 mph operation or above 2 mph with ACC. These differing publications
require VIN/RPO/calibration-specific service information before assigning an
operating envelope; the early manual alone is insufficient.
[GM TechLink, October 2017, page 4](https://techlink.mynetworkcontent.com/wp-content/uploads/2017/11/GM_TechLink_19_October_2017.pdf#page=4).

GM's service architecture associates collision preparation, brake assist and
ACC with the ASCM. This supports treating its removal as loss of a protection
path, but is not a wiring diagram or firmware description for this VIN.
[GM TechLink, March 2017, page 4](https://gm-techlink.com/wp-content/uploads/2017/05/GM_TechLink_06_Mid-March_2017.pdf#page=4).
The comma GM documentation describes bypassing the ASCM on 2017/2018 ACC Volts.
Its mention of switching between factory and openpilot operation does not prove
factory AEB remains available during openpilot engagement.
[comma GM integration notes](https://github.com/commaai/openpilot/wiki/GM).

## What this fork actually provides

- `long_mpc.py` retains a collision-distance penalty, but that constraint has
  slack. A high predicted collision count causes the planner to publish FCW.
  This is not a dedicated emergency command that guarantees physical stopping.
- The MPC's lead processing can adjust an extremely close lead's modeled range
  for feasibility. An independent collision monitor must use verified sensor
  geometry and observations before this optimization-specific adjustment.
- Stronger deceleration requests bypass candidate comfort limits. That preserves
  bounded control authority, not a full-physical-braking guarantee.
- `opendbc/car/gm/values.py` caps requested acceleration at −4 m/s² and raw brake
  command at 400, with a comment describing roughly −4 m/s² with regeneration.
  `opendbc/safety/modes/gm.h` independently enforces 400. No limit was raised.
  A raw command, a requested acceleration, achieved deceleration, and the brake
  pedal's maximum capability are different quantities.
- The current autonomous comfort recordings do not measure emergency authority
  across battery charge, regen loss, engine operation, grade, grip or temperature.
  Extrapolating their pressure fit to command 400 is not that measurement.
- The three existing reconstructed personal traffic approaches did not cross
  zero radar gap at the earlier corrected checkpoint. An incomplete settling
  result is not itself a collision. Radar gap also requires geometric conversion
  before being used as bumper clearance, and the plant remains unqualified.

## Reference designs

GM patent US20130110368A1 / US8694222B2 describes dynamically adjusting emergency
braking from relative motion and available distance. Its flow escalates to the
highest allowed braking when the minimum avoidance distance is exhausted and
accounts for delayed brake response. This supports intervening before the end
of a comfort trajectory. It does not identify the Volt's production thresholds,
message arbitration or physical brake calibration, and it is not evidence of a
crash guarantee or permission to increase panda's limits.
[GM collision avoidance patent, description of steps 114–120](https://patents.google.com/patent/US20130110368A1/en).

Autoware provides an independent AEB module using predicted vehicle footprints,
obstacle association and stopping-distance checks. Its documentation distinguishes
the deceleration assumed by detection from the command used by the emergency
stop controller. It also treats false targets and path prediction as explicit
problems. These are useful architectural references; its ROS/point-cloud system
cannot be substituted for the Volt's radar-camera pipeline without validation.
[Autoware AEB design](https://autowarefoundation.github.io/autoware_universe/main/control/autoware_autonomous_emergency_braking/).

## Implemented offline experiment

`tools/profiling/volt_collision.py` evaluates a time-aligned snapshot independently
of the comfort polynomial. Inputs explicitly supply bumper gap, both speeds,
observation age, response delay, a lower bound on achieved ego deceleration, an
upper bound on lead deceleration, positive acceleration during response and a
clearance margin. These are hypothetical experiment assumptions, not calibration.
The current example margin is 0.5 m and does not replace the 4.5 m comfort target.

The lead may brake immediately. Ego keeps accelerating during the bounded
response delay and then brakes. Position is piecewise quadratic; the algorithm
checks interval endpoints and relative-velocity zeros through both stops.
Checking only final stopping positions can miss an earlier collision.
For a stationary lead, the required travel reduces to
`v*delay + a*delay²/2 + (v + a*delay)²/(2*braking)`.

A bounded search finds the required braking. If comfort is insufficient it
reports protective braking. If even the assumed limit cannot preserve clearance,
it requests that limit in the experiment and reports mitigation, without claiming
avoidance. Stale, nonfinite, unconfirmed and unsupported observations are unknown;
an explicitly out-of-path object is excluded. A stationary vehicle does not
receive a spurious full-brake request merely for being close to an obstacle.

This stateless, single-target experiment does **not** implement perception,
fault recovery, target-loss persistence, emergency command arbitration or CAN
output. No runtime process imports it. Unit tests compare analytical extrema to
independent dense trajectories, including transient collision, delayed response,
reduced braking, adjacent targets and unknown observations. The review labels
all seven examples hypothetical. They cannot unlock deployment.

## Required next engineering work

1. Establish actual factory configuration, ASCM/EBCM message ownership and the
   supported command/mode/pressure limits from VIN-specific service information
   and instrumented bench evidence. Investigate preserving factory protection
   or restoring factory operation before treating a software replacement as the
   quickest route. Reconnecting ASCM while openpilot transmits competing commands
   is not an established solution.
2. Measure brake response and achieved net deceleration in a controlled facility,
   with appropriate instrumentation and soft targets. Include absent regeneration,
   grade, engine state and reduced grip. Do not collect emergency examples by
   approaching real traffic and waiting for the software to save the vehicle.
3. Implement and qualify time-aligned radar/camera target association, swept-path
   geometry and uncertainty, both leads, stationary objects, cut-ins, track changes,
   missing frames, reflections, curves and overhead/adjacent objects. A `leadOne`
   status or FCW boolean is insufficient to justify a full-brake command.
4. Connect a separately observable supervisor through planner, controls and card.
   Protective/emergency requests must dominate comfort, throttle must be removed,
   actuator/panda limits retained, and actual response monitored. Define driver
   override, stale-plan behavior, target-loss persistence, release hysteresis and
   stationary holding together; dropping a threat must not silently resume gas.
5. Qualify the complete sensor-to-actuator chain, timing and fault handling with
   deterministic scenario matrices and hardware-in-the-loop tests, then controlled
   physical tests. No-collision acceptance applies to the declared supported
   envelope; outside it, mitigation and an explicit failure remain required.

Qualification now requires a separate `collision` category as well as response,
traffic, stress and current-source runtime evidence. Two explicit checks stay
failed until end-to-end protection and emergency authority are implemented and
established. The physical evidence template additionally requires emergency brake
authority and collision-target validation. Passing all comfort checks cannot
erase these missing capabilities.
