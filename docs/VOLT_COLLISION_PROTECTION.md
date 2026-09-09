# Volt collision protection investigation

The factory car had an emergency-braking feature distinct from ordinary adaptive
cruise control. This installation physically removes ASCM power during openpilot
engagement. Neither the fork's stock tuning nor its forward-collision warning
restores an unpowered factory controller. The personal candidate is blocked from
vehicle testing and road use. A separately gated protection supervisor now reaches
production controls and GM CAN generation in a socket-free bench. No protection
calibration is installed, and no vehicle mode has been enabled.

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

## Envelope and production supervisor

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

The equation now lives in `selfdrive/controls/lib/volt_collision.py`; the original
seven-example report imports it. The production supervisor in
`selfdrive/controls/lib/volt_protection.py` runs independently of the comfort
polynomial and MPC, with no file I/O or optimization solver in its update.

It evaluates both original radar lead slots before MPC range adjustment. Radar
messages now distinguish a measured radar point, an actual vision match, raw
vision probability, and observation time. A low-speed radar override cannot
inherit vision confirmation. Two distinct, consistent observations are needed.
The model-frame path is transformed to radar coordinates; range, lateral, speed,
age and geometry margins are explicit calibration inputs. The matched vision
frame's timestamp is checked separately from the latest model message.

Controls apply the protective ceiling before and after PID/stopping transitions.
The standstill flag alone cannot soften an existing full braking request while
raw wheel speed still indicates motion. A qualified friction floor overrides
normal allocation in GM CarController, removes propulsion and requests 400 when
the assumed envelope is exhausted. Panda's existing limits and interlocks remain.

The backend independently requires the startup authority, calibration identity,
fresh command and fresh assessed observation. Degraded operation can retain only
an established, continuously received intervention. Target loss does not clear
that intervention or resume propulsion. Confirmed clearance of the same target
requires 0.5 seconds before stepping down through qualified commands. Holding
requires both standstill and raw wheel speed below 0.03 m/s for 0.2 seconds, with
independent backend confirmation. Driver brake, accelerator or regen override
clears protection. Existing disengagement and panda rules still take priority;
this is not a redundant actuator system that survives a dead controls process.

A stale whole longitudinal plan cannot supply positive acceleration. Card does
not retransmit an invalid, future-dated or older-than-150-ms control message.
Protection state, reason, track, observation time, requested acceleration/floor,
predicted minimum clearance and backend acceptance/rejection are logged through
CarControl, ControlsState and CarOutput. Active protection feeds FCW; degraded
active operation requests takeover through the existing soft-disable mechanism.
Monitor-only operation cannot change actuation or add takeover alerts.

`tools/profiling/volt_protection_scenarios.py` exercises production arbitration
and actual CAN packing without opening sockets. Ten scenarios cover stationary
and braking leads, cut-in, adjacent objects, delay, target loss, driver override,
insufficient initial distance, inadequate assumed grip and downhill motion.
The plant assumes friction response and zero regen; it is not fitted emergency
physics. Unit tests additionally check both leads, duplicate IDs, provenance,
faults, release, holding, backend rejection, stale card inputs and panda acceptance
of the emitted brake packet. Neither these tests nor the seven equations can
unlock vehicle activation.

## Separate qualification and mode selection

`VoltProtectionMode` requests Off, Monitor, Test or Enabled independently of the
comfort mode. A requested mode cannot bypass qualification. Startup snapshots a
source-bound `selfdrive/car/volt_protection_candidate.json` and independently
installs backend authority only for a qualified actuating mode. Missing, changed
or malformed calibration fails closed. There is deliberately no installed
emergency calibration in this commit.

Supply measured `ProtectionCalibration` fields in JSON, omitting the authority's
profile ID. Each brake/deceleration pair must represent a demonstrated lower
bound over the supported operating conditions; 400's value cannot be inferred
from the comfort model. Then create an **unqualified** candidate and evidence
template on the host:

```sh
.venv/bin/python -m tools.profiling.qualify_volt_protection measured-calibration.json candidate.json
```

The generated evidence template uses null results. Filling it requires archived
measurements and a matching source/calibration ID; rerun with `--evidence` only
after review. This command does not install anything. Required gates are:

- Monitor: current-source comma 3 whole-process timing, cold start, candidate
  workload, memory and thermal evidence. Host/helper timings cannot qualify it.
- Test: Monitor plus geometry, target association, friction response, CAN
  arbitration, scenario matrix and fault-handling evidence.
- Enabled: Test plus soft-target trials, driver overrides, grade/grip/regen
  coverage and independent review.

Runtime evidence covers controlsd/card/selfdrived on core 4 at FIFO 53 with 10 ms
periods, plannerd/radard on core 5 at FIFO 51 with 50 ms periods, and modeld on core
7 at FIFO 54 with 50 ms periods. The validator requires at least 60 seconds of
samples, no missed deadlines, no memory pressure and no thermal throttling.
An isolated replay/bench harness must collect evidence before mode qualification;
changing the startup gates to collect it is not an acceptable shortcut.

The host benchmark runs 20,000 cycles with GC disabled and production-style
message construction. Its results are saved separately in the review directory;
they demonstrate a bounded host workload, not device scheduling qualification.

```sh
.venv/bin/python -m tools.profiling.volt_protection_scenarios /mnt/algo14/comma3-alpr/braking/review/protection-validation.json
.venv/bin/python -m tools.profiling.benchmark_volt_protection /mnt/algo14/comma3-alpr/braking/review/protection-host-benchmark.json
```

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
3. Physically qualify the implemented time-aligned radar/camera association and
   expand path coverage to validated swept-path
   geometry and uncertainty, both leads, stationary objects, cut-ins, track changes,
   missing frames, reflections, curves and overhead/adjacent objects. A `leadOne`
   status or FCW boolean is insufficient to justify a full-brake command.
4. Verify the connected supervisor, command arbitration and response monitoring
   against hardware-in-the-loop observations. Establish delayed EBCM acceptance,
   saturation, brake modes and measured holding independently of the software
   packet tests. No new CAN mode or larger brake limit is enabled.
5. Qualify the complete sensor-to-actuator chain, timing and fault handling with
   deterministic scenario matrices and hardware-in-the-loop tests, then controlled
   physical tests. No-collision acceptance applies to the declared supported
   envelope; outside it, mitigation and an explicit failure remain required.

Qualification now requires a separate `collision` category as well as response,
traffic, stress and current-source runtime evidence. Two explicit checks stay
failed until end-to-end protection and emergency authority are independently
qualified. The synthetic production-path check is reported separately. The physical evidence template additionally requires emergency brake
authority and collision-target validation. Passing all comfort checks cannot
erase these missing capabilities.
