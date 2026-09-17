# Physical SWCAN diagnostic discovery — September 17, 2026

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
