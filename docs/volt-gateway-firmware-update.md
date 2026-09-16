# Secure CAN update design — not deployed

Update: the target-crypto ARM harness and native integration suite now execute
the [pinned C cryptographic backend](volt-gateway-target-crypto.md), including
image/authorization verification and streaming received-image/readback hashes.
The old host-hook harness remains separately tested. Storage and boot selection
are still models, not a deployed A/B loader. Host-backed crypto references below
describe the earlier harness, not the new variant.

The new lifecycle suite joins framed/authenticated transport, the native C
transaction, persistent storage modeling and reset/boot/confirm/revert decisions.
It covers 81 update cut positions, 21 journal-transition interruption cases,
corrupted images/headers/metadata and delayed/lost/reordered transport. The
boot selection/journal is a Python reference, not a target implementation or
MCUboot direct-XIP proof. Selected image bytes are verified, not executed.
Header writes and journal commit markers are atomic in this model; corrupted
header tests are not a substitute for physical torn-write validation.

## Transaction core now implemented in C

`firmware/update.c` uses the existing native authority gate and trusted platform
callbacks for environment, fixed-slot storage and streaming SHA256. Initialization
validates a bounded erase-region map; wire commands never choose an address or
erase geometry. Chunks are bounded to 256 bytes. Every region/write/read/hash
step is surrounded by fresh checks; stale (>30 ms) or disallowed environmental
samples and expired operator permission stop further operations.

The 82 transport/interruption integration cases now exercise this real C core
through a host adapter. Cortex-M4 tests also execute the updater through a tiny
RAM-slot trial commit. Neither runs the target flash peripheral or boots the
new application. The emulator still uses host cryptography.

A post-commit error does not undo a trial marker. The bootloader must verify and
apply its own trial/confirmation/revert rules after reboot. Native and Python
adapters expose failures without pretending storage changes were rolled back.
Hardware erase stalls, watchdog servicing and fresh sensing during those stalls
remain board-adapter obligations; callbacks cannot make an erase interruptible.

MCUboot 2.4.0 direct-XIP remains the leading candidate, not a selected board port.
Actual MCU capacity, bootloader size, stack/RAM, crypto time, flash stalls,
CAN recovery and power-cut revert must be demonstrated. Minimal custom signed
CAN recovery loader is the fallback, subject to the same tests. Do not reserve
two 128-KiB config journals without evaluating the actual sector geometry.

## Trusted recovery requirements

The loader must independently retain fixed CAN controller/bitrate/recovery IDs,
device identity, pairing material and firmware public key. It must remain silent
unless queried and recover with both apps invalid, without application config.
Remote application updates must not overwrite loader or trust material.

Verify signed target/layout/length plus whole-image cryptographic hash before
execution; no arbitrary memory-write command. Retain the confirmed application,
write an inactive slot, mark a trial atomically, and revert on failed confirmation.
If two viable slots do not fit, stop and design a recoverable alternative rather
than assume A/B. Deliberate signed recovery/rollback is allowed in development.

Require stationary/offroad, stable supply and fresh awake state in addition to
authenticated update entry. No guessed vehicle keep-awake messages. Power can
still disappear: test every erase/program/metadata transition on a bench.

## Current work

The two-key requirement now adds operator-signed, image-scoped ENTER and PROGRAM
authorizations to image verification. The routine credential cannot substitute.
See [authority lifecycle](volt-gateway-security.md). Records are tested through
fragmented/MAC-protected transport into the portable C authorization gate and an
inactive-slot memory adapter. Lost responses return cached results; partial or
corrupt transfers never receive a successful trial commit in that adapter.

These records are NOT an MCUboot image format. `update_engine.py` is an off-device
transaction reference, not a physical flash driver, trailer journal or loader.
Its injected conditions and timestamps are test inputs, not vehicle attestation.
The hardware implementation must independently refresh conditions/time during
long erase/program/verification operations.

MCUboot v2.4.0 source is pinned to commit
`6d3b3d2c38ab20c242e5b9abb04d050086383eb2`, Mbed TLS submodule
`2ca6c285a0dd3f33982dd57299012dacab1ff206`, outside the main repo under diagnostics.
`mcuboot_test.py` runs selected upstream C bootloader simulations with real ECDSA.
The upstream `direct-xip` Cargo feature does not enable `MCUBOOT_DIRECT_XIP_REVERT`.
Swap/revert simulation cannot satisfy the direct-XIP revert acceptance gate.
See [test layers and limitations](volt-gateway-testing.md).

`simulate-update` connects authenticated chunk requests, actual codec reassembly,
cached duplicate responses and paced ISO-TP flow control. Both directions consume
one shared modeled budget; distribution of that budget in real firmware remains
unimplemented. It bounds three retries, rejects corrupt PDUs and aborts on
expiry, reconnect or power loss. It writes only a synthetic in-memory image.

256-byte chunks cost about 60 frames including both directions. At aggregate
80 frames/s the ideal lower bound is 6.4 minutes/128 KiB, 12.8 minutes/256 KiB,
before ramp, flash/verification, contention and retries. That ceiling is an
experimental model input, not a vehicle-approved budget. Higher parked ceilings
require measurement and independent primary/slave enforcement.

The existing model requires rate >=20 frames/s before starting a PDU to avoid
conflicting with reassembly deadlines. At lower allowance it waits. Long pauses
restart a bounded transaction; stale fragments are not kept indefinitely.

Not implemented: board flash writes, target cryptographic image verification,
persistent resume, slot management, hardware retry bounds or CAN recovery.
Readback tooling must not be confused with a deployable updater.
