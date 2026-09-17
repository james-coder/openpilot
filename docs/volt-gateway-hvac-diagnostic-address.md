# Physical SWCAN diagnostic discovery — September 17, 2026

**Latest result:** K33 accepted both GDS2-derived recirculation/fresh-air
commands below. During the subsequent alternating test, the owner distinctly
heard airflow change and confirmed flap actuation. No button LED change.
This is a parked diagnostic override, not validated persistent cabin automation.

Owner authorized read-only diagnostic-address discovery. No actuator request,
diagnostic session change, DTC clear, firmware change or device reset was sent.

Sent standard CAN ID `0x101`, DLC8, `FE 02 1A B0 AA AA AA AA` on SWCAN.
This is GMW3110's AllNode ReadDataByIdentifier diagnostic-address query.

Received standard ID `0x651`, DLC8, `03 5A B0 99 AA AA AA AA`.
The ECU reported diagnostic address `0x99`, consistent with the HVAC controller.
Under GMW3110 §4.4.4, its physical diagnostic request CAN ID is **0x251**;
USDT response is **0x651**, and corresponding UUDT response is **0x551**
(the latter is derived, not observed here). These are standard 11-bit IDs,
not replacements for the source byte of an extended broadcast ID.

Thirteen ECU address responses were recorded. The HVAC response MCU timestamp
was 645086000 us. Evidence:
`/home/james/diagnostics/volt-gateway/vehicle-observations/diagnostic-address-discovery-1789631938075305296.jsonl`.

This establishes a live diagnostic endpoint reporting 0x99. It does not identify
the recirculation DeviceControl CPID, verify an actuator command, or read the
module's part/calibration numbers. Those remain separate steps.

Reference: https://studylib.net/doc/26162849/gmw3110-2010

## GDS2-derived CPID 01: positive vehicle replies

Owner supplied and authorized two static-derived requests from installed GM
Global v2026.09.10. Reviewed the three analysis documents in
`/home/james/git/GDS2-explore/docs/`, including the linkage to the 2017 Volt K33
Air Recirculation Door Actuator Direction command. These documents explicitly
leave GDS2 setup/release and physical effects unverified.

With fresh Park/RUN/zero-speed interlocks, sent each once on standard ID 0x251:

| Authored selection | Request | Received on standard 0x651 |
| --- | --- | --- |
| Recirculation | `07 AE 01 01 00 00 00 00` | `02 EE 01 AA AA AA AA AA` |
| Venting / fresh air | `07 AE 01 01 01 00 00 00` | `02 EE 01 AA AA AA AA AA` |

Requests were approximately 32 seconds apart. Each completed hardware TX and
received a positive service/CPID acknowledgment. This establishes acceptance,
not measured flap movement. No `0x10B02099` status was captured in either
five-second post-command subscription window. No vehicle TesterPresent,
session change, security operation, clear, release command, reset or flash was
sent. Gateway USB liveness is not vehicle TesterPresent. Exact control duration
and return-to-normal remain unverified; no persistent automation is enabled.

Private raw evidence in `vehicle-observations/`:
`k33-ae01-first-1789635854463543121.jsonl` and
`k33-ae01-fresh-1789635887788598890.jsonl`.
Both report zero host/transport drops. Owner-visible/audible effect pending.

## Alternating test: owner-confirmed physical airflow change

At the owner's request, sent four single-frame commands in order:
recirculation → fresh air → recirculation → fresh air, approximately 10 seconds
apart. Each used standard 11-bit request ID `0x251` on SWCAN; each completed
hardware transmission and received `02 EE 01 AA AA AA AA AA` on `0x651`.
There were no automatic retries, vehicle TesterPresent messages, diagnostic
session/security changes, explicit release requests, resets or firmware changes.

The owner reported no LED change, but distinctly audible airflow changes and
confirmed that the experiment was moving the vent flap. This is user-observed
physical evidence in addition to ECU acceptance—not a measured position, travel
calibration, or proof of absolute endpoint positions. The ON/OFF selection names
come from the installed GM command enum, not from an independent position sensor.

Using same-device MCU timestamps from received frames:

| Selection | Positive reply timestamp (us) | Subsequent `01 60 AA AA AA AA AA AA` (us) | Interval |
| --- | ---: | ---: | ---: |
| Recirculation | 220192000 | 225193000 | 5.001 s |
| Fresh air | 230164000 | 235170000 | 5.006 s |
| Recirculation | 240147000 | 245153000 | 5.006 s |
| Fresh air | 250080000 | 255086000 | 5.006 s |

The unsolicited `0x60` service replies are consistent with return to normal on
diagnostic timeout, as described by GMW3110. We did not send service `0x20` or
vehicle `0x3E`. Thus a persistent setting must not be inferred from command
acceptance. Actual flap position after timeout, explicit cancel behavior, and
the full GDS2 lifecycle still need verification. Do not infer that maintaining
diagnostic override while driving is appropriate; effects on HVAC protections
and diagnostic monitoring have not been validated.

### A26 versus K33

- **A26, HVAC Controls:** user-facing control panel and its selected settings.
- **K33, HVAC Control Module:** actuator controller targeted by this experiment.
  Its `0x251` endpoint agrees with both live discovery and the 2017 Volt K33
  transport definition recovered from installed GM data.

The unchanged button LED does not imply that the wrong module was targeted.
Diagnostic actuator control can differ from the panel's selected setting;
that explanation fits the observations, but the LED synchronization mechanism
has not been separately decoded. Likewise, the previously identified
`0x10B02099 / 0006070d` value is selected-state feedback, not demonstrated
physical flap-position feedback. No such report was captured during this test.

2017 Volt module data-link reference:
https://estimate.mymitchell.com/GMC/document/4/6/4/0/0/100304692_4640002_11741334.html

### Evidence and end state

Raw log:
`/home/james/diagnostics/volt-gateway/vehicle-observations/k33-ae01-four-1789635956424590122.jsonl`.
Host and transport drop counters were both zero. All four commands and the last
timeout response had already been recorded before the owner's stop message was
handled. The test process was confirmed absent; no further transmission was
initiated. No persistent automation or driving integration was enabled.
