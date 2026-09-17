# Experimental gateway protocol

The only CLI operations currently implemented are `benchmark` and
`simulate-update`. Neither has hardware access. Future `voltgw info`, bus/ID
statistics, observe, subscriptions and bounded capture are not live commands yet.

## Control and compatibility

Prototype protocol major/minor 1.0; bounded logical PDU 512 bytes; ISO-TP Classic
CAN framing (8-byte DLC). Simulation exercises block size 8, STmin 0, explicit
reverse flow-control cost. Reassembly limits are one second between fragments
and ten seconds total; queue/rate policy must respect these, or restart a bounded
transaction. Pauses cannot retain an arbitrarily old fragment indefinitely.

Authentication envelope: 32-byte `!4sBBBB8sQII` header (magic, major, minor,
kind, opcode, session, sequence, transaction, payload length), payload, 16-byte
HMAC tag. Verify the entire envelope before dispatch. Duplicate byte-identical
requests return cached responses, not repeated effects. Unknown opcodes must be
rejected by the future dispatcher. Simulation opcode values are not a frozen
production command registry.

Major/minor, required capabilities, build IDs, host requirements and exact
primary safety-policy identity belong in the authenticated handshake transcript.
Mismatch disables the gateway; never widen safety. Compatibility checks exist,
but pairing/handshake negotiation on a physical transport is not implemented.

## Telemetry format remains open

Benchmark candidates include ISO-TP batches (1/2/4 observations), a full record
stream and subscription-relative compact stream. Wire costs including ISO-TP
flow control: 8, 6.5, 5.5 frames per observation respectively; full streaming 5;
compact streaming 3, plus authenticated subscription/anchor refresh overhead.
These comparisons are in `benchmark.py`, not proof of F413 CPU/RAM latency.

Raw records retain source bus, ID/IDE, RTR, DLC, payload, timestamp and sequence.
Compact handles/anchors must be installed by authenticated control and immutable
within a session; missing mappings fail decode. CRCs detect accidental corruption
but do not authenticate telemetry. Telemetry is logging/UI/reverse-engineering
only. No driving/actuation consumer is permitted without a promoted trusted data
path and separate review.

Prefer two well-supported standard transport IDs if arbitration and framing
demultiplexing prove practical. No IDs are assigned. Control must not be trapped
behind an unbounded telemetry datagram; update mode suspends optional telemetry.
