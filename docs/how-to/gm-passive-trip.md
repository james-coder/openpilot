# Passive Volt trip evidence

This feature sends **no CAN requests** and changes no Panda/safety configuration.
The existing active scanner remains parked-only. No periodic driving diagnostic
poll, output control, clear, session change, or guessed identifier is implemented.

## Raw preservation

openpilot's existing loggerd writes complete received `can` messages into full
`rlog.zst` segments. `qlog.zst` is heavily decimated for CAN and is NOT substituted.
All bus IDs present in the logged stream, unknown frames, and other recorded
driving messages remain available. This is every *recorded* bus, not proof that
all vehicle networks are physically connected or that capture is lossless.

The low-priority `gmtripd` process runs with normal on-road logging on the Volt.
Every ten seconds it hard-links current full rlog files under:

`/data/media/0/diagnostics/gm/trips/ROUTE--SEGMENT/rlog.zst`

These are references to the same filesystem inode, not a second raw recorder or
copy. Appends to an open segment remain visible through the link. Normal route
cleanup can remove its own filename without losing the pinned data. Video is not
pinned. The private parent directory also protects raw vehicle identifiers.

Preservation stops adding links at an 8-GiB total pin budget or below 6 GiB free
space. An already linked active segment can finish growing past the budget.
Existing pins are never silently deleted. Normal loggerd/deleter behavior is not
changed. `status.json` and the Trip context menu show last preservation status,
including errors/limits. If limited, normal logs may still exist temporarily, but
complete long-term preservation is NOT promised. Arrange export/removal of old
pins before another trip when needed; this tool has no automatic deletion command.

## Menu and extraction

**CAN Bus → Trip context → Extract** processes the latest pinned route off-road.
The extraction process runs at low priority, independently of the UI/control
loops, and stops if the vehicle goes on-road. Driving only uses the existing
logger plus the lightweight file-preservation process. The menu is a post-drive
summary, not a live EGR gauge. Park before interacting with it.

Reports include source segment names/hashes, all observed bus frame counts,
missing segment numbers, CAN-batch gaps, invalid data, native-timestamp signal
statistics, and candidate deceleration windows. Open/locked segments are skipped.
Corrupt or changed files make the report partial. An absence of detected gaps is
not proof of zero bus/USB/logger losses. No unknown message is named EGR because
it correlates with an event.

Existing mappings used:

| Input | Interpretation / restriction |
| --- | --- |
| Valid carState | Speed, acceleration, accelerator pressed, brake pressed |
| Bus 0 C9, ECMEngineStatus | Existing DBC engine RPM, throttle field, brake status |
| Bus 0 1C4, AcceleratorPedal2 | Pedal normalization already used by GM carState |
| Bus 0 4C1, ECMEngineCoolantTemp | Existing DBC coolant field, if actually present |

RPM-derived engine rotation is not independently verified gasoline combustion:
the hybrid can rotate the engine without necessarily injecting fuel. The throttle
and coolant fields are DBC-defined, not newly vehicle-validated EGR sensors.
No verified passive MAP/BARO/EGR command/error/actual/temperature mapping was found.

The detector conservatively labels pedal-released deceleration at 1100–1300 rpm
with fresh inputs as a **candidate opportunity**, never a confirmed P0401 run.
It requires >1 m/s speed, acceleration <−0.1 m/s², ≤0.2 s engine-data age and
sample gaps, and at least 0.2 s observed duration. These are our indexing
heuristics, not a reproduction of GM's monitor. Pedal release does not prove
throttle closure; MAP/BARO, fuel-cut state and remaining ECU enabling conditions
are missing. A lack of candidates cannot disprove monitor execution or P0401.

To export every decoded input at its original log timestamp (not downsampled):

```bash
python -m openpilot.tools.car_porting.gm_trip_report --offroad-only --csv /data/media/0/diagnostics/gm/trip-signals.csv
```

CSV output is optional, created exclusively, and may be partial if interrupted.
The full source rlogs remain authoritative. One full segment at a time is read;
extraction accepts at most 2000 segments and rejects compressed segments >32 MiB.
No remote-log download or qlog fallback is used.

## Parked snapshots and before/after report

Use **GM details → Scan** outdoors, with ignition on, in Park, stationary and
disengaged, before the drive, after the drive, and after the cleaning experiment.
This existing scanner retains codes/statuses, readiness, MID31, supported PIDs,
2C/2D, MAP/BARO and supported EGR temperatures. It never starts the engine or
clears codes. The old saved report is a reference, not a new pre-drive scan.

When the corresponding fresh archived snapshots exist:

```bash
python -m openpilot.tools.car_porting.gm_trip_report --offroad-only \
  --before /path/to/pre-drive.json --after /path/to/post-drive.json \
  --after-cleaning /path/to/post-cleaning.json
```

The report preserves statuses separately, compares vehicle-identity hashes and
calibration lists, and presents each phase's readiness and Mode06 records.
Acquisition time does not prove fresh monitor execution. Check chronology and
identity before interpretation. No scan is automatically labeled post-cleaning;
software cannot know when cleaning happened. No cleaning procedure is endorsed.

## Separate higher-rate research (no commands sent)

[GM's MDI guide, p. 11](https://gsi.ext.gm.com/userguides/GM_MDI_User_Guide.pdf)
confirms vehicle-specific GDS2 packages and recorded/replayable diagnostic data.
It does not publish this E80 calibration's EGR data identifiers, timing,
combined-request support, or periodic-stream setup. Searches of public GM and
[firsthand 2017 Volt tooling](https://github.com/lululombard/Chevrolet-Volt-Hacking)
did not establish a verified high-rate desired/actual/MAP stream.

The most useful next evidence is a legitimate VIN/calibration-matched GDS2
read-only data-display trace: simultaneously record request/response bytes,
parameter names/units and update timestamps, using a minimal relevant channel
list. Measure actual per-channel cadence and skew; display refresh rate is not
necessarily ECU sampling rate. A captured small combined response may help, but
its support cannot be assumed from the existence of combined/periodic services
in a generic protocol. Session setup must be reviewed separately, not replayed.

[j2534-logger](https://github.com/joeyoravec/j2534-logger) is a possible interface
trace mechanism if compatible with the chosen legitimate GDS2/MDI setup. Normal
full CAN logging can also preserve tester traffic visible on the connected buses.
Do not run our parked scanner concurrently with another tester.

[GM 2017 Hybrid Diagnostics, pp. 149–155](https://gsi.ext.gm.com/gmspo/mode6/pdf/2017/17OBDG01%20Hybrid%20Diagnostics.pdf)
describes a brief P0401 deceleration flow check distinct from position tracking.
Sparse two-minute snapshots cannot characterize that transient. No evidence here
justifies relaxing the driving TX gates. Any later proposal must enumerate exact
already-verified requests, necessary response handling, rates/burst bounds, stale
data and fault cancellation, and regression tests for existing vehicle controls.
Unrestricted safety, actuator commands, clears, sessions, and guessed identifiers
remain excluded. A proposed stream is not an implemented/approved stream.
