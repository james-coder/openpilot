# Volt EGR: motion versus restriction evidence

Deployed to the comma3 September 14, 2026 (logging application `279721281`,
offroad UI compatibility fix `4217da1d5`). This addition does not alter Panda safety,
send output controls, enter diagnostic sessions, clear faults, or guess Mode 22
identifiers. Bench results are not vehicle results. On-device validation passed
100 diagnostic/UI tests plus 14 subtests, and three network-worker/poller tests.
The vehicle reported ignition off and Panda no-output during installation.
UI, manager, hardwared and pandad restarted successfully; no live EGR log was
started. Engine-running evidence collection remains pending.

Panda source/safety revisions and signed firmware were unchanged; firmware SHA-256
remains `cf4fee369c3c660589a6d22482e721af80c9388f664fc379159463c177fe5ef1`.
The saved baseline archive's SHA-256 remains
`4add928490280910aa587d388c0b2dde29b43357d793fb7e93a94f9ea0c8af8f`.
The device retains the previous application at branch
`backup/egr-before-log-20260914` (`d621f6da`); the application update used a
clean fast-forward, not a replacement of the driving-control branch.

## Why engine-off did not give position

The saved ECM support response was `416001280000`: PID 69 was not advertised.
That is different from an advertised actual-position field reading zero. Engine
RPM was zero and commanded EGR was zero, so this snapshot cannot establish motion
or provide a usable nonzero-command relative-error calculation. An engine-running
scan can recheck support, but there is no evidence that starting the engine will
make PID 69 available. A manufacturer-specific interface may still exist.

## Implemented collection

Select **CAN Bus → EGR log → Scan**, with the vehicle outdoors, on, stationary,
in Park, and openpilot disengaged. This first performs the existing baseline
(codes/statuses, freeze frame, calibration, support, readiness and stored Mode 06),
then polls advertised readings for 120 seconds. It does not start the engine.
Engine-off logging is allowed, but cannot establish running EGR operation.

Each cycle requests RPM, command 2C, error 2D, command 2C again, MAP, BARO,
coolant, intake temperature, supported EGR temperatures/position, and readiness
41. Repeated commands bracket the error sample. Raw replies and individual
monotonic acquisition times are preserved, anchored to the report's UTC start.
Validated carState speed is recorded separately in m/s; it is not substituted
for an ECM PID. No accelerator-pedal signal is mislabeled as throttle.

No new firmware flag or flash is needed for these requests: they use the already
verified EGR allowlist. Requests remain at least 500 ms apart. Limits: 120 seconds
of logging, 250 seconds total including baseline, 300 non-flow-control requests,
2048 captured frames, 512 KiB per report. Safety gate loss, cancellation, overload,
or timeout stops further requests. Failed/cancelled attempts are archived without
replacing the last usable report. Old baseline archives are not rewritten.

The current allowlist does not include polling speed 0D, absolute throttle 11,
or relative throttle 45. The offline/passive decoder supports those replies if
another authorized diagnostic session obtains them. This distinction preserves
the current firmware protections; decoding a PID is not authorization to send it.

CLI alternatives (run from the openpilot checkout):

```bash
python tools/car_porting/gm_egr_log.py --start
python tools/car_porting/gm_egr_log.py --analyze /path/to/archived-report.json
python tools/car_porting/gm_egr_log.py --capture-seconds 120
```

`--start` only queues the normal card mailbox; check status for acceptance.
The existing Cancel button applies. `--capture-seconds` subscribes to CAN only:
no requests, ISO-TP flow control, Panda configuration, or session changes. It
records bus-0 ECM request/response addresses and reassembles responses produced
by an existing tester. Silence is not evidence of unsupported data. Captures are
bounded and private, under `/data/media/0/diagnostics/gm`; raw traffic can contain
identifiers. The observer may not see a different physical network. Do not run
the active logger concurrently with another tester.

## Derived feedback: intentionally not called actual position

SAE defines relative EGR error as `(actual - commanded) / commanded * 100`.
Algebraically the normalized estimate is `commanded * (1 + error / 100)`;
this assumes that the ECU uses that definition in the same normalized domain.
It is not independent evidence that the valve physically moved. See
[SAE J1979-DA, tables B32/B33](https://www.e90post.com/forums/attachment.php?attachmentid=1509324&d=1476497126).

Implementation quality gates (our conservative heuristics, not GM specifications):

- An error reply must lie strictly between two command replies spanning ≤1.2 s.
- Both commands must be ≥5%, and differ by no more than one command quantization step.
- Error endpoints 00/FF are rejected as potentially clipped/special values.
- A preceding RPM reading within 3 s must indicate at least 700 rpm.
- The estimate and its quantization-only interval must stay within 0–100%.

Rejected samples remain raw and get a reason count. The uncertainty interval
does not include transport delay, ECU filtering, sensor error, or movement between
samples. Stable endpoints cannot prove a stable trajectory. No automatic
"valve good" or "replace cooler" decision is made from these estimates.

## Monitor status and event detection

PID 01 exposes EGR/VVT support and completion since clearing; PID 41 exposes
cycle enablement and completion. Neither is an instantaneous P0401-running bit,
nor does completion mean a pass. Stored Mode 06 results retain their acquisition
time, not an invented execution timestamp.

GM's P0401 monitor evaluates a brief deceleration MAP response using internal
expected/filtered quantities; the published approximately 0.4-second event cannot
be resolved by the parked polling cadence. P0404 separately evaluates position
tracking. See [GM 2017 Hybrid Diagnostics, pp. 149–155](https://gsi.ext.gm.com/gmspo/mode6/pdf/2017/17OBDG01%20Hybrid%20Diagnostics.pdf).

The offline detector labels only candidate deceleration windows when closely
timed RPM, MAP, BARO, decreasing speed, relative throttle, and command replies
are present. It reports missing inputs explicitly. It uses raw, not
altitude-compensated MAP; not all enabling conditions are known. Candidate events
are not confirmed ECU monitor executions. There is deliberately no 2.40-kPa raw
MAP-rise pass rule, restriction percentage, or inferred "monitor disabled" from
failure to detect a candidate.

## Enhanced position: current research result and next evidence

The recorded calibration IDs are 12683203, 12683310, 12680662, 12650377,
12667265, 12683316, 12683317, 12680665. No exact-calibration enhanced EGR position
request/offset/scaling has been verified. Searches included the firsthand
[2017 Volt reverse-engineering repository](https://github.com/lululombard/Chevrolet-Volt-Hacking),
[OBDb Volt definitions](https://github.com/OBDb/Chevrolet-Volt), OVMS, and legacy
GM/VPW PID collections. Legacy 22114B/221171 definitions are not E80 evidence;
223040 temperature-related notes are not a position mapping. No candidates were sent.

A legitimate VIN-selected GDS2 **data-display** session is the most direct next
source. Capture exact calibration IDs/CVNs, displayed parameter names/units,
screenshots and timestamps, plus request/response bytes while selecting one EGR
channel at a time. Correlate varied readings across repeated samples; identify
request, response offset, scaling, support/invalid flags and any initialization
dependency. Do not replay the initialization transcript. If it includes session
changes or controls, present those separately for approval before reproducing any.

The new passive observer can preserve relevant raw frames visible to comma.
For a PC interface, [j2534-logger](https://github.com/joeyoravec/j2534-logger)
provides a logging shim around a J2534 driver; compatibility with the particular
GDS2/MDI setup must be established, not assumed. No GDS2 package, interface,
automated GDS2 channel adapter, or verified enhanced-position mapping has been
obtained by this implementation.

## Smart EGR encoded PWM: separate hardware path

The exact 2017 GM document describes encoded-PWM position/state communication
at P1437 (p. 304). P1426 (p. 301) uses a nominal 10% duty indication for an
internal fault, demonstrating why duty cycle cannot simply mean valve-opening
percentage. This is not a CAN signal and no verified full encoding map was found.
[GM 2017 Hybrid Diagnostics](https://gsi.ext.gm.com/gmspo/mode6/pdf/2017/17OBDG01%20Hybrid%20Diagnostics.pdf).

Before any electrical observation, obtain official VIN-matched SI wiring,
connector end views and terminal IDs, circuit numbers, signal direction, reference
ground, supply and signal voltage limits, frequency/encoding specification,
approved breakout/backprobe method, and waveform test procedure. Then use suitable
high-impedance automotive measurement equipment following that procedure. Do not
connect the signal to Panda CAN pins or a GPIO, guess a ground/pin, load the line,
disconnect it to force behavior, or assume an ordinary analog position sensor.
No physical probing was performed and no pinout is supplied without those sources.

## Conditional diagnostic conclusion table

These are interpretation branches, not present findings or parts-replacement orders.

| Reliable evidence under applicable conditions | Favored direction | Remaining caution |
| --- | --- | --- |
| Independently verified valve tracks; contemporaneous flow evidence poor | Cooler/passages restriction | Check flow-sensing/exhaust conditions too; good tracking does not prove flow |
| Independently verified valve fails to track | Valve/control/wiring/binding | Determine mechanical versus electrical cause before replacement |
| Valve tracking and applicable flow test both good | Monitor conditions, calibration, sensors, intermittent fault | One old stored pass does not disprove the recorded P0401 |
| Current saved engine-off snapshot, no actual position, stored flow result only | Insufficient to distinguish valve from restriction | Do not calculate percent plugged or claim motion was tested |

The saved September 14 baseline still has P0401 stored/pending/permanent,
PID 69 not advertised, RPM/command zero, and a stored A9/FD result within its
returned limits. Those facts do not establish a present flow pass or a repaired
system. Baseline evidence remains unchanged.
