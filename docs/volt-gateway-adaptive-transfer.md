# Adaptive firmware transfer — off-device prototype

Status: implemented Python admission/retry model only. No USB/CAN transport,
firmware installation, primary-Tres changes, or vehicle validation. No transport
arbitration IDs or production rate settings are approved by this work.

## Admission policy

`tools/volt_gateway/adaptive.py` models a 500-kbit/s standard-ID backhaul using
bounded 20-ms occupancy samples and 100/300/1000-ms rolling windows. Input must
include observed gateway traffic, not just OEM traffic. A conservative 135-bit
cost is used for a standard eight-byte CAN frame. Actual instrumentation must
account for error traffic, missed RX, extended frames, and hardware retries;
uncertain measurements must prevent admission, not appear as spare capacity.

The experimental defaults are an 80-frame/s ceiling, 65% aggregate load target,
75% stop threshold, and a 5-frame/s-per-second upward ramp. These numbers are
simulation inputs, NOT evidence of a safe vehicle budget. Admission uses the
largest window estimate plus a positive-trend projection, backs off rapidly,
and pauses on a single overloaded sample. It requires one second of clean
samples before starting/resuming, expires observations after 30 ms, clears
credit on a pause, and permits at most two frames of burst credit.

Every sample must affirm stationary/offroad, stable power, vehicle awake,
authenticated session, compatible policy, and no errors. The future trusted
loader must independently enforce these requirements. Host declarations alone
do not establish vehicle or supply state. An awake check is not a guarantee
against future power loss: interrupted erase/program recovery remains required.

This is traffic shaping, not a software replacement for CAN arbitration.
Low-priority IDs and a hard maximum are necessary even when apparent capacity
is abundant. No attempt is made to use all spare bandwidth.

## Reliability and expected speed

The existing wire-cost model uses 256-byte firmware chunks: a 308-byte
authenticated request and 52-byte authenticated ACK, with ISO-TP framing and
flow control. This costs approximately 60 physical CAN frames per chunk,
including both directions. At an aggregate 80 frames/s, ideal transfer time
is about 6.4 minutes per 128 KiB, or 12.8 minutes per 256 KiB. These are lower
bounds excluding startup ramp, bus contention, flash time, pauses and retries.
Do not confuse an aggregate budget with separate per-direction budgets.

`ChunkTransfer` models stop-and-wait offsets, exact next-offset ACKs, a five-second
ACK deadline after the complete paced transmission, and at most three retries.
All retries and flow-control/ACK frames must consume the shared budget. The
model does not authenticate ACKs itself: callers must first validate image,
session and MAC, and duplicate commands must never repeat destructive work.

Persistent resume, whole-image hash/signature verification, flash journaling,
power-cut recovery and rate coordination between the two physical senders are
NOT implemented by this model. Before hardware integration, demonstrate a
bounded split/reservation of the aggregate budget so independently paced peers
cannot each consume the full allowance. Hardware automatic retransmission also
needs explicit bounds; Python attempt accounting alone cannot enforce this.

## Tests and remaining gates

Run:

```
.venv/bin/python -m pytest tools/volt_gateway/test_offline.py tools/volt_gateway/test_adaptive.py -n 0
.venv/bin/ruff check tools/volt_gateway
```

Coverage includes initialization, rate ceiling, burst credit, stale/invalid
measurements, positive load trends, overload/recovery, all admission gates,
bounded history, ACK offsets, duplicates, expiry and retry exhaustion. Existing
codec/authentication tests remain separate from the transfer-state model.

The subsequent `simulate-update` increment now integrates both peers, MAC
verification, FC frames, ACKs and cached retry responses; see
[firmware update status](volt-gateway-firmware-update.md). It suppresses
telemetry during update and measures status-response latency at chunk boundaries.
It still does not distribute grants between physical controllers, model electrical
arbitration or program flash. Next gates: hardware counters/flash-stall analysis,
target firmware and bench power-cut tests. No model result proves driving
independence or vehicle readiness.

## Labeled Panda preservation incident — 2026-09-16

Device serial `370022000651363038363036`, labeled `VOLT CAN FORWARDING`.
A USB application-to-softloader reset (`D1`, value 1) was accepted during the
backup investigation; the device then ceased enumerating in Windows. No flash
unlock, erase, programming, option-byte, safety-mode or CAN TX command was sent.
There was no background flashing process to kill. The reported green blink
was consistent with a waiting softloader, not evidence of programming.

After the owner unplugged/replugged USB and reported blue blinking, a read-only
version query confirmed `v1.7.3-EON-unknown-RELEASE` responds again. No further
mode transitions were attempted. A complete binary backup still does not
exist; user approval to replace firmware remains conditional on obtaining it.
