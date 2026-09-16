# MCUboot direct-XIP/revert C integration — off-device only

## What is now implemented

`tools/volt_gateway/mcuboot_port.py` builds immutable MCUboot 2.4.0 commit
`6d3b3d2c38ab20c242e5b9abb04d050086383eb2` via `git archive`, without upstream
source patches. Both `MCUBOOT_DIRECT_XIP` and `MCUBOOT_DIRECT_XIP_REVERT` are
enabled. Its real C signature verification uses the pinned Mbed TLS 3.6.7
backend and bounded 24-KiB allocator, not a Python verification callback.

`firmware/boot/boot_port.c` provides a narrow storage boundary:

- Only two fixed candidate image regions can be accessed. The first 256 KiB
  cannot be read, written or erased through this flash-map API.
- Loader writes are limited to aligned trailer metadata, not image payload.
- Every write is read back; every erased sector is checked in full.
- Read/write/erase errors latch until trusted initialization on reset.
- Application selection checks the latch even if upstream returns success.
- Signed protected metadata must match the trusted 12-byte device identity and
  32-byte layout digest exactly. Missing, extra or differently bound metadata
  is rejected. These values and the public key must come from trusted loader
  provisioning, never application config or a CAN request.
- Header flags, fixed slot address, header size, stack and Thumb entry address
  are checked before returning a selection. There is no application jump yet.
- Confirmation operates only on the image selected during this boot; callers
  cannot supply another slot. Actual application health gating is not integrated.

The fixed protected TLV schema uses type `0xa0`, length 44. It is included in
MCUboot's signed hash. Test fixtures are not release-packager output; the
existing outer gateway manifest/operator authorization and the MCUboot inner
image still require release/update integration.

## Why the storage latch matters

In the pinned upstream `boot/bootutil/src/loader.c`, `boot_select_or_erase`
logs a failed `boot_write_copy_done` but can continue with a successful return.
The port explicitly refuses handoff after any such storage error. It also
rejects a driver reporting success without actually programming the bytes.

A failed confirmation call is not proof that confirmation was not stored:
the one-way flag may already have reached flash before failure. Tests allow
either verified image after that reset, but never an unverified application.
An update is therefore not an atomic pointer flip. Signed slots, persistent
trial/confirmation flags and checked recovery behavior provide the protection.

If the upstream-selected signed image has an invalid target/vector binding,
this boundary fails closed rather than jumping to it. It does not immediately
retry the other slot within that call. An unconfirmed trial can subsequently
revert; an invalid *confirmed* image may require the future recovery service.

## Conditional layout, not an approved hardware layout

The candidate uses 640-KiB slots at flash offsets `0x40000` and `0xe0000`,
each covering five 128-KiB sectors. It excludes the first 256 KiB without
committing that entire space to a bootloader or to two config journals.
This geometry follows the F413/423 sector table in
[ST RM0430](https://www.st.com/resource/en/reference_manual/dm00305666-stm32f413-423-advanced-arm-based-32-bit-mcus-stmicroelectronics.pdf).
The attached chip's exact suffix/revision and RAM still need direct confirmation.
The current stack validation deliberately uses a conservative 128-KiB envelope.

Fixed-address applications need separately linked slot variants; they cannot
simply be copied to the other address. MCUboot's header load address comparison
uses the slot's flash-device offset; vector addresses remain absolute CPU
addresses. Trial/confirmation behavior follows
[MCUboot's direct-XIP design](https://docs.mcuboot.com/design.html).

## Test scope and reproduction

Validation on 2026-09-16 passed **756 tests, zero failures/skips**, including
70 tests for this port. Lint, existing ARM core/crypto builds, the native loader
build and selected GCC analysis also passed. See the
[evidence record](evidence/volt-gateway/boot-port-20260916.json) for artifact hashes.

`test_mcuboot_port.py` builds and executes the real upstream C loader against
`boot_test.c` memory-backed flash, using ephemeral test keys. Coverage includes:

- Trial boot followed by reset/revert, and confirmation persisting in either slot.
- Before-write failures, partial writes/erases, falsely successful operations,
  and abrupt simulated loss before/after persistent operations.
- Failures at each of the five revert sector erases, plus metadata confirmation.
- Read errors, corrupted headers/payload/signatures, wrong signing key, wrong
  device/layout binding, missing binding and invalid vectors/slot addresses.
- Both images invalid: no application selection; no claim of working CAN recovery.
- Loader/provisioning region and confirmed fallback preservation checks.

Set `VOLTGW_MCUBOOT_CHECKOUT` to a repository containing the exact pinned commit
and `VOLTGW_MBEDTLS_ARCHIVE` to the verified official 3.6.7 archive. Then run:

```
.venv/bin/python -m pytest tools/volt_gateway/test_mcuboot_port.py -q -n 0
.venv/bin/python -m tools.volt_gateway.validate --output NEW_PRIVATE_DIRECTORY
```

Tests never download sources. Missing dependencies are reported as incomplete,
not a passed gate. The native builder statically analyzes our C adapter and
test binding with GCC. Only upstream unused-parameter warnings are suppressed.

## Still required before a flashable release

This is a native C test library, **not a deployable bootstub**. It does not prove
ARM startup/link size, hardware flash-stall timing, watchdog/RX servicing during
verification, real stack high-water use, electrical power-loss behavior or
application execution. The current FIH profile is explicitly OFF pending
target integration; this is not a fault-injection-hardened production build.

Next: integrate the selected loader into an ARM execution harness, then target
startup/flash adapters and the authenticated CAN recovery service. Recovery
must retain controller/bitrate, identities, keys and transport IDs independently
of both invalid application slots. Provisioned secrets, RNG, protected-loader
enforcement, interrupted programming and restored-original-image recovery
remain release gates. No USB or vehicle interfaces are opened by these tests.

No deployment, driving engagement test, physical A/B recovery proof or production
readiness is claimed. The original White Panda firmware and production Tres
remain unchanged.
