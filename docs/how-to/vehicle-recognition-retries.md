# Vehicle recognition retries

Live `card` enables bounded CAN fingerprint retries; replay/offline callers keep
the existing packet-based path. This does not alter fingerprint allowlists,
firmware identification, Panda firmware, safety modes, or CAN transmissions.

The first attempt retains the normal packet thresholds with a three-second
monotonic deadline. After failure, two fresh attempts are available. Each drains
CAN for one second, then observes for at least two seconds and 102 packet batches,
with a three-second deadline. The retry phase has an eight-second total budget.
Deadlines are checked around receives; the live socket has a 20 ms timeout, so
receive/processing and scheduling latency can slightly exceed these budgets.
These are bounded waits, not hard-real-time timing guarantees.

Only a unique match is accepted, and differing unique matches between buses
are rejected. Each received batch is checked completely before acceptance.
Candidates and fingerprints are reset for each attempt; only the winning
attempt's fingerprint configures the interface. VIN/FW queries are not repeated.
A unique firmware match or explicit fingerprint skips retries. A cached vehicle
identity alone cannot override a CAN failure.

No vehicle interface is initialized or final CarParams published until this
finishes. Existing startup readiness gating remains in force. Exhausted retries
fall back to MOCK/dashcam mode, with vehicle control disabled. Retries never run
in the driving control loop. The initial wait for any CAN before identification
is unchanged and is not part of this timeout budget.

`can_fingerprint_attempt` records the attempt, duration, packet count, outcome,
remaining candidates, fingerprint, and first eliminating message for each
candidate/bus (ID, length, payload, elapsed time). Evidence uses existing rotating
logs; no separate recorder is added. These records can distinguish transient
traffic from persistent fingerprint mismatches without guessing new exceptions.

`CarRecognitionAttempts` is written once before CarParams publication and cleared
on manager start/onroad transitions and before a new identification. Selfdrived
reads it once at initialization, not in its realtime loop. Failed recognition
shows, for example, **Car Unrecognized: 3 Attempts Failed** in startup/permanent
alerts and the count in the offroad alert. Missing counts retain the old wording;
replay ignores the live parameter.

Tests use fake clocks. The retry tests use synthetic CAN payloads; the
diagnostic-traffic tests below replay frames recorded from the car. Subsequent
parked starts and natural use are needed to assess the real intermittent
failure rate. Persistent unknown traffic still requires evidence-backed
diagnosis, not blanket ID exceptions.

## Diagnostic-tester traffic

Routes 000000e1--1d060d506c and 000000e5--75bdf4918a ran as Dashcam Mode because
all three attempts failed. A diagnostic tester on the shared HS-CAN swept GMLAN
readDTCByStatusMask requests (`03 A9 81 9A`) to 0x252, 0x242, 0x241, 0x7E0, 0x7E4,
0x7E5, 0x7E1 and 0x254 (plus `22 xx xx` polling) for 5-13 s. The module replies
appeared on 0x541, 0x542, 0x552, 0x5E8, 0x5E9, 0x5EC, 0x5ED and 0x652. The
Volt was eliminated each attempt by the first sweep frame whose ID is not in its
fingerprint (0x252, then 0x542, then 0x7E5). Boots where the sweep missed the
attempt windows identified normally.

`is_diagnostic_tester_frame()` in `car_helpers.py` now identifies such frames, and
`eliminate_for_fingerprint()` (used by `can_fingerprint`, live and offline) lets
them keep a candidate whose fingerprints do not list the frame's ID at all. It
applies only to bus 0 and only to 8-byte frames of exactly these shapes. All
frames are ISO-TP single frames (first byte = length 1..7) whose bytes after the
length are a single filler value, `00` or `AA`, or absent.

| Class | IDs | Payload |
| --- | --- | --- |
| Request | 0x240-0x25F, 0x7E0-0x7E7 | `03 A9 81 xx` or `03 22 xx xx` |
| Response | 0x540-0x55F, 0x5E8-0x5EF, 0x640-0x65F, 0x7E8-0x7EF | `03 7F A9 xx` or `03 7F 22 xx`; `0n 62 xx xx ..` with n = 4..7 |
| DTC report | 0x540-0x55F, 0x5E8-0x5EF | `81 xx xx xx xx` + three filler bytes (`00 00 00` or `AA AA AA`) |

Limits, all deliberate:

- A candidate that lists the ID in any fingerprint is judged exactly as before,
  including a length mismatch. There is no ID exception: 0x241, 0x641 and
  0x7E4 stay ordinary fingerprint IDs for the cars that list them.
- Everything else still eliminates: other buses, other lengths, other IDs
  (including the 0x7DF/0x7E0/0x7E8 and 0x7E3 `02 1A` handling that already
  existed), other services (1A/5A, 3E, 19/59, 10, 27, ...), and non-filler or
  mixed trailing bytes. Services 1A/5A are left out on purpose: no failed
  attempt involved them and an existing test pins that a 0x7E5 `02 1A` frame
  still eliminates. A real `1A`/`5A` probe of 0x7E7/0x7EF (recorded at about 2 s
  after boot in the good route and at 11 s in 000000e5) can still fail an attempt
  that overlaps it; so can the 0x641 reply `05 62 90 FB xx xx 80 55`, whose
  trailing bytes are not filler, but 0x241/0x641 are already in the Volt
  fingerprint, so that one does not matter for the Volt.
- Acceptance is unchanged: only a unique match is accepted, differing matches
  between buses are rejected, the attempt/time budgets above are unchanged, and
  the fingerprint passed to the interface still contains every observed ID. The
  rule can only keep more candidates, so it can turn a former mismatch into a
  recognition or into an ambiguity (still MOCK), never remove a candidate that
  was previously kept.
- `can_fingerprint_attempt` additionally records `diagnostic_ignored` (frames that
  would have removed a candidate and did not) and `diagnostic_ids` (their IDs,
  at most 32). The `eliminations` list still records every real elimination.
- No extra delayed attempt was added. Once such frames no longer eliminate,
  "only diagnostic frames eliminated" cannot occur; any other trigger would lengthen
  startup for genuinely unrecognized cars.

`opendbc/car/tests/test_fingerprint_diagnostic_traffic.py` replays the recorded
frames (`gm_diag_sweep_fixture.json`: bus-0 frames in the ranges above from both
failed routes and one good boot, plus the good boot's ordinary traffic) through
`can_fingerprint_with_retries`: the Volt is recognized on attempt 1, an attempt
window slid across the whole sweep always matches, and with the rule disabled the
replay fails three attempts on the same three frames the car logged. It also
checks every fingerprint in the database with and without injected sweep frames,
that real non-diagnostic extra frames still eliminate every car, and that the
rule never removes more than the previous behaviour. This is replay only: it has
not been run on the vehicle or a CAN bus.

## Screen validation

`tools/profiling/render_recognition_alerts.py` renders the actual alert widgets
and fonts at 2160x1080, checking all counts (including missing count), with and
without the sidebar. It asserts text widths and offroad content height, and
exports 20 previews for visual inspection. The initial long startup title
overflowed with the sidebar open; the final layout uses "Car Unrecognized"
above "3 Attempts Failed - Dashcam Mode". ASCII punctuation avoids unsupported
font glyphs. The permanent banner retains "Dashcam Mode" above the recognition
failure/count. Offroad details wrap within the card without requiring scrolling.
