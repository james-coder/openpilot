# First SWCAN survey and DBC leads

## Evidence quality

The USB-only control path requested a 30-second local SWCAN ID survey, with CAN
transmit disabled. Artifact: `/home/james/diagnostics/volt-gateway/vehicle-observations/swcan-survey-20260917T031913Z/survey.json`.
Six valid extended IDs were returned, but every record had count one. The
observer invalid counter increased from 66,066 to 66,631. Hardware overflow and
software queue-drop counters stayed zero on all three buses.

This exposed an observer bug, not a six-message inventory of the vehicle: it
treated cross-bus acquisition timestamp ordering as a global clock reversal and
cancelled observation. The frames identify real observed IDs; survey duration,
rates, completeness and absence claims must not be inferred from these results.

## Matching convention

There are no exact full-ID matches for these six IDs in the local DBC tree.
However, GM extended headers carry priority, message family and source fields.
The historical [GMLAN library decoder](https://github.com/mattatcha/gmlan/blob/master/GMLAN.cpp)
uses priority `(id >> 26) & 7`, family `(id >> 13) & 0x1fff`, and source
`id & 0x1fff`. The local low-speed DBCs contain message-family definitions with
zero source bits; the larger DBC additionally has DBC's bit-31 extended marker
and omits on-wire priority. It declares `UseGMParameterIDs` with default 1.

Matching below strips the DBC extended marker and compares the message family
and DLC. It does NOT change actual arbitration IDs, route frames between buses,
or establish that a signal's semantics/scaling are verified on this Volt.
All six observed source fields are `0x40`; no ECU identity is asserted solely
from that shared source value. Names below come from our local DBC, not guesswork
based on changing bytes or correlation.

| Observed full ID | DLC | Family | Larger low-speed DBC definition | Smaller DBC corroboration |
| --- | ---: | --- | --- | --- |
| `0x0C800040` | 6 | `0x400` | `Fuel_Information` | — |
| `0x10230040` | 8 | `0x118` | `Non_Drvn_Whl_Rot_Status_LS` | — |
| `0x10780040` | 8 | `0x3C0` | `HS_Indications_Slow_LS` | — |
| `0x10240040` | 8 | `0x120` | `Vehicle_Stability_LS` | `SteeringWheelAngle` |
| `0x102C0040` | 5 | `0x160` | `Immobilizer_Identifier_LS` | — |
| `0x102CA040` | 8 | `0x165` | `Engine_Information_1_LS` | `GasPedalRegenCruise` |

Files: `opendbc_repo/opendbc/dbc/gm_global_a_lowspeed_1818125.dbc` and
`gm_global_a_lowspeed.dbc`. The larger stability definition includes validity
and calibration flags as well as steering angle; decoded numbers alone are not
proof of valid sensor data. Immobilizer-related data is out of scope for control
experiments; raw captures remain private.

A [firsthand can-utils issue](https://github.com/linux-can/can-utils/issues/30)
also shows `0x10230040` in a SWCAN capture, but provides no signal definition.
The older [2013 message-name library](https://github.com/mattatcha/gmlan/blob/master/GMLAN_29bit.h)
uses different names for some families (for example `0x120`), so its name table
must not be transplanted as a decoder for this vehicle.

## Particularly useful unobserved leads

- Smaller DBC `LeftRadar`: exact ID `0x1072C0B9`, DLC 2, warning at Motorola
  bit 4. `RightRadar`: `0x1073205B`, DLC 2, warning at Motorola bit 0.
- Larger DBC family `0x396`, `Side_Blind_Zone_Alert_Status`, DLC 2: left
  lane-change threat at bit 4, clean/off/service/temporarily-unavailable flags,
  and `LfLnChngThrtAprchSpd` at Motorola bit 15, signed 8-bit, scale 1 km/h.
  This is a concrete approach-speed lead beyond a boolean, NOT evidence that
  this Volt provides raw target range, confidence or a full object list. Do not
  extrapolate the left-side layout to the right-side family.
- Larger DBC family `0x39A`, `Climate_Control_General_Status`, DLC 6, aligns
  by family with the earlier `0x10734099` lead.
- Family `0x40A`, `Climate_Control_Basic_Status_LS`, DLC 4, aligns with
  `0x10814099` and defines front blower speed as byte 1 scaled by 0.392157%.
  That field is not recirculation state.

These target IDs/families were not returned in this interrupted survey. Their
absence here is not evidence that the car does not transmit them. Repeat a
complete survey after the observer fix, then perform read-only correlations.
Do not add unverified signal definitions to a production actuation consumer.

## Observer correction (off-device)

Separate command/telemetry time from acquisition time and track acquisition
monotonicity per bus. Keep actual same-bus or control-clock reversals fail-closed.
Also track observation/capture start times, excluding queued frames acquired
before the requested window. Preserve the original timestamps rather than
relabeling queued data as fresh. Native regressions exercise interleaved buses,
delayed frames after command ticks, window boundaries and real clock reversal.
No new firmware has been deployed for this correction yet.

Full off-device validation `validation-observer-clock-20260917-01` passed 1,671
tests with no failures/skips, along with builds, static analysis, lint and source
consistency checks. Its report remains `production_ready=false`; deployed survey
behavior must still be retested after a USB-only bench flash. The earlier focused
run had 20 passes and 14 dependency-related skips; only the full configured run
closes the off-device gate. This fix does not change the already deployed comma
startup fix or grant the White/Tres any new CAN transmit permissions.

## Object02 physical survey

After flashing Object02, a complete 20-second USB-controlled SWCAN observation
returned the climate targets: `0x10734099`, DLC6, approximately 2 Hz, and
`0x10814099`, DLC4, approximately 0.4 Hz. Also observed `0x106D4099`, DLC3,
once in the window. Exact left/right radar status IDs were not in this survey;
zero-length extended frames with source fields B9/5B were present, which does
not prove radar status semantics or sensor operation.

At approximately 105 seconds uptime, physical CAN counters showed primary
282,176 frames, Object 111,395, SWCAN 18,032. All three had zero malformed,
hardware-overflow, software-drop, TX-error and ESR counts; firmware reported
running/slot A/no error. Maximum RX queue ages: 5/6/5 ms respectively, peaks
24/23/2. These are this session's measurements, not a general reliability claim.
USB remains control owner; no Panda-to-Panda exchange was attempted in that
mode. Two-minute read-only subscriptions to the three climate IDs were started
for owner recirculation-button correlation, saving raw observations privately
under diagnostics as `hvac-live-20260917-01.jsonl`. No recirculation bit has yet
been established and no vehicle control frames were transmitted.

### Recirculation correlation, second recording

Owner reported four or five spaced recirculation toggles, ending in outside-air
mode. `hvac-live-20260917-02.jsonl` captured five clean payload transitions on
extended SWCAN `0x10814099` (DLC4): `a0742400` -> `a0745000` at +18.14 s,
back to `a0742400` at +27.71 s, `a0745000` at +38.34 s,
`a0742400` at +51.91 s, and `a0745000` at +62.93 s. Times are host observation
times relative to this recording's first frame, not physical button timestamps.
Bytes 0, 1 and 3 stayed constant across these transitions. Byte 2 (zero-based)
is a strong recirculation-related candidate: 0x50 coincides with the reported
final outside-air state; 0x24 is the alternating state, provisionally recirc.
Multiple bits differ (XOR 0x74), so this is NOT yet a single-bit definition.
It is not established whether this reports requested or actual flap state;
independent labelled repetitions and other HVAC settings remain needed. No
replay/control message is authorized or inferred from this status correlation.
`0x10734099` stayed `200000b40000`; `0x106D4099` changed slowly, not in the
same five-switch pattern. Five-minute recording remains active for confirmation.

Independent owner-labelled confirmation in the same recording: explicit ON
corresponded to `a0742400` at host epoch 1789620727.688; explicit LED OFF /
outside-air corresponded to `a0745000` at 1789620742.880. Thus the observed
payload states track the recirculation selection in this configuration. This
still does not identify physical flap feedback, isolate individual bits from
other HVAC settings, or establish a writable command.

### Important interpretation correction and TX lead

Inspection of the larger local DBC explicitly labels byte 2 of family 0x40A
`AirCndCmptLdEst` (estimated compressor load), scale 0.392157 percent. Therefore
the repeatable 0x24/0x50 pattern may be an indirect compressor-load response to
recirc. The earlier wording "recirc-selection feedback" overstates what has
been established. Preserve it as an owner-labelled correlation, not a verified
recirculation decoder or evidence of novelty. This generic DBC's applicability
to this exact Volt signal is itself not proven.

A separate concrete command lead exists: DBC `Remote_Climate_Control_Req_LS`,
family 0x22D, DLC5, `RmClmCtrlRcrcSetReq` Motorola start bit 3 / length 3.
The same frame includes AC, fan, distribution, temperatures, defog and sync
requests. The DBC supplies no value table for recirc or leave-unchanged values.
On-wire priority/source, applicability, value encoding and update semantics
remain unverified. Do not fill other fields with guessed zeros or replay the
0x10814099 status frame as a command. Installed firmware still has no permitted
vehicle-side TX rule; a narrow tested semantic rule is required before TX.
