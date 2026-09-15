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

Tests use synthetic CAN payloads and fake clocks, not claimed replays of the
unavailable failed route recordings. Subsequent parked starts and natural use
are needed to assess the real intermittent failure rate. Persistent unknown
traffic still requires evidence-backed diagnosis, not blanket ID exceptions.

## Screen validation

`tools/profiling/render_recognition_alerts.py` renders the actual alert widgets
and fonts at 2160x1080, checking all counts (including missing count), with and
without the sidebar. It asserts text widths and offroad content height, and
exports 20 previews for visual inspection. The initial long startup title
overflowed with the sidebar open; the final layout uses "Car Unrecognized"
above "3 Attempts Failed - Dashcam Mode". ASCII punctuation avoids unsupported
font glyphs. The permanent banner retains "Dashcam Mode" above the recognition
failure/count. Offroad details wrap within the card without requiring scrolling.
