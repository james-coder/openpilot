# GM EGR evidence: read-only upgrade

This is an implemented, bench-tested extension for the 2017 Volt investigation.
It does **not** command the EGR valve, start the engine, clear codes, or run a
GDS2 service-bay routine. Vehicle support for the optional identifiers must be
discovered; synthetic test results are not vehicle measurements.

## Use

With the matching application and Panda firmware installed, open **CAN Bus →
GM details → Scan**. This runs one serialized emissions/GM/EGR session. The car
must be on, in Park, stationary, with openpilot disengaged and fresh vehicle
data. Operate outdoors because this hybrid can start its engine automatically.
Cancel, loss of these conditions, excessive traffic, or the 120-second deadline
stops further requests. There is no driving-time diagnostic polling.

The extended firmware capability is `READ_ONLY_GM_EGR` (32), in addition to
EV (4), read-only OBD (8), and GM diagnostics (16): intended Volt parameter 60.
Parameter bits alone do not attest firmware: use pandad's complete signed-image
signature comparison. Without bit 32, the existing legacy GM scan remains
available and explicitly has no extended EGR evidence.

## Collected evidence

1. Existing emissions MIL, stored, pending, and permanent code queries, keeping
   their statuses distinct, followed by the existing connected-bus GM fault survey.
2. Existing ECM freeze frame zero, with its trigger code and raw responses.
3. Support discovery, fresh calibration IDs/CVNs, Mode 06 EGR results, readiness,
   and individually timestamped parked sensor readings.

New physical requests use only bus 0, request 7E0, response 7E8:

| Service | Identifiers (hex) |
| --- | --- |
| 01 support | 00, 20, 40, 60 |
| 01 readiness | 01, 41 |
| 01 snapshots | 05, 0B, 0C, 0F, 10, 2C, 2D, 33, 69, 6B |
| 06 support/results | 00, 20, 31 |
| 09 support/calibration | 00, 04, 06 |

Optional requests require advertised support. Failed discovery is unknown,
not unsupported. No identifier brute forcing, arbitrary address entry, session
change, security access, tester-present, or output control is exposed.

The scanner preserves every returned MID 31 test record, including unknown
TIDs/scaling. It labels A8/A9 only with matching FD scaling, using signed 16-bit
pressure at 0.001 kPa. Inclusive ECU-returned limits determine the stored test
outcome; no hard-coded 2.40 kPa diagnostic threshold is substituted. All-zero
value/min/max means no valid result: the monitor may not have completed or an
alternate criterion may not have been used. It is not a pass.

PID 69 fields have independent support bits. Unsupported actual-position fields
are never inferred from commanded position/error. At zero commanded EGR, relative
error is not presented as a blockage percentage. PID 6B uses its A/C/B/D ordering
and per-field normal/wide temperature range flags; conflicting flags are
explicitly ambiguous and raw-only. Sensor labels do not assume cooler inlet/outlet
placement. PID 41 cycle enablement is distinct from PID 01 support. Readiness
completion is not proof of passing.

Fresh calibration IDs and CVNs remain separate ordered lists; unequal counts
are not paired. Snapshot timestamps are acquisition times, not simultaneous
samples or timestamps of the ECM's earlier monitor execution. Good valve position
tracking does not prove adequate EGR flow. None of these results establishes a
failed part or whether a long trip is safe.

U0104 remains visible with an annotation for the deliberately disconnected ASCM
in this installation. That annotation neither hides unrelated codes nor blocks
EGR investigation. Coverage remains the connected bus, not a verified inventory
of all vehicle modules.

## Reports and bounds

The existing Scan/Cancel mailbox is unchanged. `GmLastScan` now accepts version 2
reports; readers retain version 1 compatibility without rewriting old reports.
Requests, replies, relevant CAN frames, negative responses, and decoding errors
are retained. Failed/cancelled attempts do not replace the last usable report.
`tx` evidence records are scanner-submitted requests, not hardware transmit
acknowledgments; the first live validation must independently check transmit
echoes and Panda blocked-TX counters. UTC start time and monotonic offsets are
retained together.

The background I/O thread archives previous saved reports before replacement and
publishes immutable JSON files in `/data/media/0/diagnostics/gm/`. Files have
content-addressed names and owner-only permissions. No automatic deletion is
performed. Each report is capped at 512 KiB; archive errors are visible and
prevent replacement of the saved report. Archives contain private vehicle
identity; exported reports redact VIN recursively unless explicitly requested.

```sh
python -m tools.car_porting.export_gm_diagnostics
python -m tools.car_porting.export_gm_diagnostics --json
python -m tools.car_porting.export_gm_diagnostics --report /data/media/0/diagnostics/gm/REPORT.json --json
```

These commands only read saved data. They never open CAN or initiate a scan.

Transport bounds: 4,095 bytes per extended reply, 256 Mode 06 records, 128
diagnostic frames per tick, 2,048 captured frames and 48 requests per session.
Ordinary replies have two seconds; PID 69, MID 31, CALID and CVN replies get eight
seconds. Response-pending never extends a deadline. Existing 500 ms request and
fixed flow-control restrictions remain intact. The original emissions scanner
retains its smaller 512-byte per-response bound.

## Rollout status and verification

Implementation is based on `deploy/volt-gm-diagnostics` and packaged separately
on `deploy/volt-gm-egr`, not a wholesale deployment of the experimental
driving-control branch. Diagnostic changes are also mirrored into the development
checkout without replacing its unrelated changes. Scanner/decoder/UI tests
cover complete synthetic scans, large ISO-TP replies, support discovery, valid
zeros, malformed replies, cancellation, archive failure, old reports, and VIN
redaction. Firmware tests enumerate every service/PID pair and every single-byte
mutation of allowed requests. Full safety regression and native/H7 builds are
required before deployment.

Completed local validation: 168 scanner/UI tests and 14 subtests, including
native touch tests; full Panda safety suite, 3,231 tests run with 393 skipped;
H7 signed firmware, native pandad, and params builds; Ruff and whitespace checks.
The mirrored development checkout also passes 82 focused tests and 14 subtests,
plus 314 GM safety tests run with 22 skipped. Rendered EGR result screens were
visually inspected. These checks establish software behavior, not actual Volt
support for the optional diagnostic interfaces.

This upgrade has **not been flashed or scanned on the car yet**. During the
implementation check, the comma was reachable but ignition was off, with no fresh
gear/speed state. The previously deployed app/firmware and saved vehicle results
were left untouched. Before deployment: confirm an outdoor supervised parked
setup with stable power, back up the matching app/safety revisions and binaries,
deploy matching revisions, build on-device, flash via pandad, verify the full
signature, and restart. Capture the first manual scan and compare every transmitted
request to the allowlist; check Panda faults and blocked-TX counters. Unsupported
optional identifiers are a valid result, not permission to broaden the allowlist.

## Protocol sources

- [SAE J1979-DA OCT2011](https://www.e90post.com/forums/attachment.php?attachmentid=1509324&d=1476497126), tables B2/B46/B85/B87, E91 and Mode 09 definitions.
- [GM GMLAN Mode 06 definitions](https://gsi.ext.gm.com/gmspo/mode6/pdf/GM%20CAN%20mode%20%2406%20data%20final_dm.pdf), MID 31 A8/A9 FD. Result labels are not activation commands.
- [GM 2017 hybrid diagnostics](https://gsi.ext.gm.com/gmspo/mode6/pdf/2017/17OBDG01%20Hybrid%20Diagnostics.pdf), P0401 pages 149–151: filtered flow-monitor metric, not a raw MAP-rise threshold.
- [CARB diagnostic-result requirements](https://ww2.arb.ca.gov/sites/default/files/barcu/regact/obdii06/19682clean.pdf), zero-result conventions and inclusive limits.
- [GM 24-NA-080](https://static.nhtsa.gov/odi/tsbs/2024/MC-11010577-0001.pdf), repair context, not an actuator protocol.
