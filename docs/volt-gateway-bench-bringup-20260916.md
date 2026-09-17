# USB-only bench bring-up — 2026-09-16

Device: labeled `VOLT CAN FORWARDING`, serial
`370022000651363038363036`; vehicle connector disconnected.
Production comma/Tres untouched.

Measured identity is recorded in `white-panda-inventory.md` and
`evidence/volt-gateway/ram-probe-20260916.json`: DBGMCU_IDCODE
`0x10006463`, DEV_ID `0x463`, REV_ID `0x1000`, flash-size register
1024 KiB, and White revision-C strap. This does not establish every character
of the package's printed part number.

## Initial installation and recovery

The corrected shared LED renderer passed the full 1591-test suite, board builds,
lint and static checks in external build `validation-flash-20260916-02`.
The old RAM probe's separate endless RGB loop was removed; see LED documentation.

The owner-authorized, signed, all-CAN-listen-only initial 1-MiB image was written
and read back in full. SHA-256:
`49a7521e29e2c013d29ebf96cbc19b993a86412e8d98c662dce4acb8f369eb92`.
Readback matched byte for byte. Option bytes remained
`efaa1055efaa1055ff7f0080ff7f0080`; no protection changes were attempted.
Original actual 1-MiB flash backup SHA-256:
`b34e88c141a128ad6505fb88260c158da9df59fdfec4b7a75c3a155638ff6279`.
Flash artifacts contain pairing material and remain outside Git/public packages.

The new application did not remain available for authenticated USB commands.
The new loader's actual cold USB recovery command was successfully used to
return it to ROM DFU. This verifies that recovery path, not complete operation.
Do not describe this image as ready for a vehicle.

## Startup diagnosis

A separate SRAM-only `--listen` diagnostic links the actual watchdog, clock,
RNG and silent CAN initialization, but no flash programmer, pairing material,
USB command dispatcher or CAN response transmitter. It is for this disconnected
USB bench only. It records bounded stage values, not secret-bearing memory dumps.

Probe 03 recorded `0x201c67cf`: startup failed with the watchdog started,
SR update bits clear, PR readback 6 instead of requested 3, RLR 1999.
Probe 04, with bounded pre-write busy waiting and synchronized value readback,
advanced to stage 205 (CAN initialization). This confirms watchdog startup
progress on silicon, not the precise cycle-level cause of the earlier mismatch.
The installed flash has not yet received that correction.

[ST RM0430, IWDG register definitions](https://www.st.com/resource/en/reference_manual/rm0430-stm32f413423-advanced-armbased-32bit-mcus-stmicroelectronics.pdf)
requires waiting for PVU/RVU before changing the corresponding registers.
The correction retains exact configuration checks and bounded failure; it does
not disable the watchdog or accept an incorrect prescaler.

## Emulation accuracy and limits

Unicorn executes Cortex-M4 instructions, with explicit peripheral models—not a
complete STM32F413 silicon simulator. Whole-image tests map 1 MiB of flash and
the conservative 128-KiB RAM envelope, use the actual full ID register and
1024-KiB size register, and execute separately linked A/B images. Previously
the ID fixture had the same DEV_ID and REV_ID but omitted measured middle bits.
The revision is identification data, not a revision-specific silicon model.

Regression tests now exercise inherited busy watchdog updates and delayed PR/RLR
visibility, both through native MMIO callbacks and whole-image ARM execution.
These delays are conservative test scenarios, not measured silicon timing.
The board models still approximate clock/RNG/ADC/USB/CAN/flash behavior; they
cannot establish physical transceiver routing, electrical behavior, real bus
arbitration or power-loss recovery. Each observed hardware discrepancy must
be investigated and converted to an appropriate regression rather than waived.
