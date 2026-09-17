# Gateway safety gates

Safe failure means loss of extra visibility, never interference with driving.
No code in `tools/volt_gateway` is registered with manager or called by controls,
card or pandad. No production Panda safety or firmware has been changed.

Initial board bring-up must be physically off-vehicle and receive-only, with
bounded RX tables/capture, no forwarding, no raw TX API and no all-output safety.
Watchdog/reset clears sessions and optional permissions. Capture/queue overflow
drops observations and increments counters. Corrupt config returns to silent
defaults. Bus-off recovery is bounded, not a rapid retransmission loop.

The eventual first body-write operation is semantic SET_RECIRC only after
message identity, counters, checksum, shared controls and acknowledgement/state
are understood. Initial writes parked only. Steering, brake, propulsion/torque,
accelerator, gear, ABS/stability and restraint commands are explicitly excluded.

Primary GM TX policy is at `opendbc_repo/opendbc/safety/modes/gm.h:157`.
Do not assume it permits new transport IDs. Later additions require exact
bus/ID/DLC, bounded rate and regression tests preserving all GM actuation rules.
No all-output mode or widening on protocol mismatch.

`card.py:69` owns the sendcan publisher; `pandad.cc:73` subscribes. A new daemon
must not become a competing publisher or block that path. Future integration
needs a bounded optional request arbiter, driving-message priority and no
crypto/filesystem work on a time-critical driving loop.

Before deployment test optional service absent, stopped, crashed,
permission-denied and unavailable at cold boot; verify actual engagement and
disengagement, manager readiness and alerts. Unit tests and nice priority do
not establish independence. Preserve all normal driving watchdog checks.
No deployment/restart with the vehicle moving; verify current safe state.

Unsigned observational telemetry is logging/UI/reverse engineering only.
Authentication, freshness and source-trust review are required before any
actuation or driving-safety consumer is introduced.
