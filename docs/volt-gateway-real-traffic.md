# Real traffic replay and object-bus comparison — 2026-09-16

Read-only SSH/SCP copied existing completed full `rlog.zst` files. No extra
recorder, service restart, safety change, flash, or vehicle CAN TX was performed.
Route 76 segments 2–4 supply approximately three continuous minutes. These
turned out to include driving, not merely a parked capture. Route 74 segments
0/5/10/15 provide departure and three separate highway minutes. Prior route 69
segment 10 supplies a verified stationary comparison.

Private full logs and hashed analysis outputs are under
`/home/james/diagnostics/volt-gateway/traffic/`. They are not committed: full
routes can contain personal/location data. The committed
`tools/volt_gateway/fixtures/object-bus-load.json` contains only bus-1 load bins,
labels and source hashes, with no raw payloads or location data. Tests consume
that fixture without needing the device or private routes.

## Object bus result

`traffic_compare.py` reads full rlogs, not downsampled qlogs, and uses valid,
recent `carState.vEgo` and `selfdriveState.active` samples. Full stable-state
one-second bins exclude startup/partial bins and transition seconds. Previous
state samples are retained when services arrive slightly out of timestamp order;
future state must not be used to label an earlier CAN batch.

| Condition | Stable seconds | Mean bus-1 estimate | Highest 1-second estimate |
| --- | ---: | ---: | ---: |
| Stationary, inactive, route 69/10 | 58 | 24.118% | 25.289% |
| Driving inactive, route 74/0 | 54 | 24.887% | 25.343% |
| Highway active, route 74/5 | 58 | 23.648% | 25.289% |
| Highway active, route 74/10 | 58 | 22.014% | 23.616% |
| Highway active, route 74/15 | 58 | 22.397% | 24.466% |

Highway speeds were approximately 106–121 km/h (66–75 mph). The production GM
object DBC/parser decoded radar header 1120 (`0x460`). Highway headers reported
up to 4/7/6 valid targets respectively, versus zero in the stationary sample.
Header traffic continued at approximately 15 Hz. Numeric mode feedback changed
(stationary 5, highway 2); this report does not invent meanings for those values.
Target reporting is evidence beyond utilization that these highway captures
contain radar operation. It is not proof of radar health or every target's validity.

There is no observed increase in overall object-bus traffic on engagement in
these samples. This is not a controlled engagement experiment or a guarantee
over all roads/states. Powertrain/chassis remained approximately 62.2%/44.1%
during the highway samples. No transport ID or backhaul selection is approved
by this evidence alone.

## What the tests actually do

- Preserve raw source files and SHA-256 provenance; extract all RX buses 0–2.
- Exclude TX-return/rejection source values instead of counting duplicates.
- Round-trip a deterministic sample of actual payloads/IDs/DLCs through the
  gateway stream codec. Original rlogs preserve unsampled/unknown frames.
- Replay measured 20-ms aggregate loads against the existing queue scheduler
  and adaptive update-admission model, with reproducibly seeded synthetic
  GMLAN observations, control requests, queue pressure and bus-unavailable faults.
- Include a deliberately excessive synthetic source burst to verify dropping
  and bounded queues. This offered traffic is not a claim of physically valid
  SWCAN wire timing. The simulated update admission conditions are synthetic,
  not authorizations or vehicle-state evidence.
- Exercise parked and three active-highway object-bus fixtures in ordinary pytest.

This is **load-driven simulation, not a CAN-controller emulator**. No arbitration
IDs are assigned. The probabilistic service-opportunity model does not reproduce
bitwise arbitration, ACK/error frames, controller mailboxes, retransmission,
interrupt timing, bus-off recovery or SWCAN electrical behavior. Those remain
explicit future controller-model/bench tests. Reported latency is model output,
not an upper bound guaranteed on hardware.

### Important result: short-window timestamps are not wire timing

The conservative frame estimate includes stuffing, ACK/EOF and intermission,
using 500 kbit/s and Classic CAN. Logs timestamp receive batches rather than
individual start-of-frame edges. Some powertrain 20-ms bins exceed 100% estimated
capacity despite a long-run estimate near 62%. These are preserved as warnings,
not clipped away as if measurement were accurate. Feeding them to the adaptive
model causes conservative pauses; it does not establish true bus saturation.
Even object-bus batch bursts can cause excessive update backoff.

Do not tune production safety thresholds to hide this artifact. Firmware should
use local RX/controller timing and independently bounded budgets, then be tested
on the actual controller. One-second estimates are more useful for comparison,
but still omit unseen error traffic and cannot establish capture completeness.

## Reproduction

From the repository with the development environment:

```
.venv/bin/python -m tools.volt_gateway.traffic_compare LOG.rlog.zst --output NEW-comparison.json
.venv/bin/python -m tools.volt_gateway.traffic_replay LOG.rlog.zst --seconds 60 --output NEW-replay.json
.venv/bin/python -m pytest tools/volt_gateway/test_traffic_replay.py -q -n 0
```

Supply contiguous segments in chronological order to a single replay; analyze
noncontiguous highway segments separately. Output paths must be new. Original
`comparison.json` is retained as an earlier analysis; `comparison-v2.json` fixes
cross-service timestamp classification and is the source of the table above.
