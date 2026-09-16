# Authenticated recovery integration — 2026-09-16

Later same-day work completed the board composition and local USB integration;
see [current board report](volt-gateway-board-integration.md). The remaining-gates
section below is historical and superseded by that report.

## Implemented

`recovery_service.c` connects actual Mbed TLS authentication and independent
P-256 operator authorization to the inactive-slot updater. `recovery_runtime.c`
dispatches complete bounded ISO-TP messages; `recovery_client.py` implements the
matching host codec, without CAN access or private firmware signing keys.

The paired handshake binds device identity, layout, policy, build, fresh gateway
nonce and host nonce. Directional session keys authenticate commands/responses.
Session expiry, sequence numbers and exact cached retries prevent repeated flash
operations. The pairing secret alone cannot authorize programming: a separate
signed authorization binds the image and limited programming lease.

Handshake messages use `VGR1`: HELLO request is five bytes and response 147;
OPEN is 69 bytes and authenticated acknowledgement 56. Commands use the existing
32-byte `VGW1` versioned header and 16-byte HMAC tag, bounded to 512 bytes.
Opcodes 80–88 cover signed-image challenge, operator authorization, begin, chunk,
finish, status, abort, session close and inactive-slot information. There is no
raw memory write, arbitrary CAN transmit or unauthenticated factory reset.

`recovery_flash.c` supplies a RAM-only service/permit boundary for flash-busy
operation. It requires trusted safety-sampling/receive callbacks; it does not
invent safe vehicle state. It checks session and programming leases, fresh
stationary/offroad/power inputs and CAN/watchdog health. Bounded receive work
continues without recursive dispatch, crypto or transmitting during flash busy.
Required CAN, clock and quiescence helpers were moved into RAM sections.

## Test scope

* Native real-crypto tests: authentication, replay, truncation/mutation, expiry,
  denied unsafe updates and idempotent retries.
* Complete modeled CAN → ISO-TP → authentication → updater → actual MCUboot
  chain, including both slots initially invalid, lost acknowledgements and retry.
* Actual MCUboot validation/trial/confirmation/revert against storage fault models.
* Cortex-M4 execution of physical flash register code, with modeled unlock,
  busy, erase/program, timeout, failure/reset and watchdog behavior. Instruction
  fetches **and data reads** from flash are forbidden during modeled busy periods.

Emulation exercises actual code, but cannot prove physical transceiver behavior,
real power-loss recovery, silicon timing, installed wiring or driving safety.
Synthetic transport IDs and ephemeral test keys are not production allocations.
`flash_guard_emu.c` and runtime emulator images are NEVER_FLASH harnesses.

## Remaining release gates

There is not yet a complete provisioned loader/application image. Protected
provisioning, real RAM-resident vehicle/power safety sources, final linked memory
and call-graph verification, application command lifecycle and installed recovery
must be completed before deployment. Recovery must remain accessible after
replacing the legacy USB firmware; a backup alone does not prove that.

Final transport IDs, harness mapping and primary Tres compatibility remain
separate gates. No physical bench CAN peer is available; do not represent the
modeled recovery test as physical recovery validation. No Panda was flashed and
no production comma/Tres configuration was changed by this work.
