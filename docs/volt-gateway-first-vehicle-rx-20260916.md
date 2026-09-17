# First vehicle-connected passive receive test

Owner reported the labeled White Panda and the existing Tres/comma connected
through an OBD splitter, with the car off. USB was attached to WSL without
resetting or reflashing the device. Vehicle connector continuity remains
unmeasured. No vehicle CAN messages, wake requests, diagnostic requests, firmware
changes or production-comma changes were made during this test.

Application identity matched the bench-tested image:
`a0218b9313cbe02957836746e3920c48708516a68978753fc6a3dfa662262515`.
SWCAN controller 3, HSCAN mask 3, backhaul disabled, empty vehicle TX policy.

## Observations

The first 30-second observation was quiet on all available interfaces. HSCAN
logical bus 0 already had 44,998 received frames and 396 FIFO-overflow events
since startup. These counters stayed constant through the quiet observation.

During the subsequent door experiment, the owner reported an open door, closed
it when requested, and subsequently reopened it for comfort. Event timestamps
are conversational approximations, not synchronized button markers. No further
door cycling was requested.

Between sampled uptime 196,401 and 307,863 ms:

| Interface | RX count before → after | FIFO overflow before → after | Discovered IDs |
| --- | --- | --- | --- |
| HSCAN logical 0 / CAN1 | 44,998 → 94,304 | 396 → 408 | 110 |
| HSCAN logical 1 / CAN2 | 0 → 0 | 0 → 0 | 0 |
| SWCAN logical 3 / CAN3 | 0 → 0 | 0 → 0 | 0 |

The bounded ID-observation interval was 90 device seconds; host polling and
fetching extend the overall sampling duration. Do not equate that ID table with
a complete raw recording of the entire sampling period. Bus-0 IDs included
0x3E9, 0x135 and 0x1F1, consistent with expected GM powertrain/platform traffic.
All sampled TX/error counters and ESR values remained zero. Firmware counters
are not an independent electrical measurement of silence or bus health.

## Conclusions and unresolved gates

- Physical reception through the splitter is demonstrated on CAN1, not on the
  other interfaces. The car-off networks were not continuously active.
- SWCAN reception is **not demonstrated**. Silence cannot distinguish splitter/
  harness pin routing, sleeping networks, PHY/mux problems, or configuration.
  Inspect splitter identification/pin routing before assuming full pin continuity
  or selecting a transport bus. No blind mux changes or wake injection.
- FIFO overflows increased by 12 during live traffic. This is actual evidence of
  receive loss; the count is overflow events, not an exact number of lost frames.
  Root cause is not established. Investigate runtime service latency and improve
  the FIFO/timing model off-device before claiming complete capture capability.
- Existing USB-only simulations and cold-boot tests did not establish lossless
  reception under physical vehicle traffic. Production gateway readiness remains
  false. No primary-Tres safety exception or installed backhaul is authorized by
  this result.

Private evidence is retained outside `/tmp` and Git under
`/home/james/diagnostics/volt-gateway/vehicle-observations/`:

- `car-off-20260917T021144Z.json`
- `door-listen-20260917T021444Z.json`, SHA-256
  `4b803cda0cefec1a618dbd89497354e99aa1959476a1fffb6d6de57a7ad42628`

The authenticated observation session was closed after fetching the bounded
tables. The device remains configured for silent reception.
