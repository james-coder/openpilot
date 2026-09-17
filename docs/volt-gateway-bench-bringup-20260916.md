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
The later corrected signed images below include that correction.

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

## CAN initialization fixes

A bounded CAN-only MMIO breadcrumb in the RAM probe captured the failing read:
address `0x40006600`, value `0x2a1c0e00`, following a write of `0x00000e00`.
The original whole-word FMR comparison incorrectly assumed reserved bits were
zero. The corrected driver preserves reserved fields and verifies FINIT and
CAN2SB only where applicable. CAN3 does not share CAN1/2's filter-bank split.
This follows the field-specific approach in
[ST's CAN HAL](https://github.com/STMicroelectronics/stm32f4xx-hal-driver/blob/master/Src/stm32f4xx_hal_can.c).
Native and whole-image fixtures now include the nonzero FMR reset fields.

The next breadcrumb showed CAN3 MSR `0x9` after MCR `0x18`: INAK remained set
despite clearing INRQ. The historical White `gpio.h` initializes GMLAN RX with
a pull-up on PB12; the new quiesce/mux code had removed receive bias. Restoring
a pull-up on the selected SWCAN RX pad (PB12/CAN2 or PB3/CAN3) allowed the actual
board to synchronize. TX remains unpulled and all controllers remain silent.
The native model now refuses SWCAN synchronization without this receive bias.
This is not proof of physical SWCAN reception from another node.

RAM listen probe 07 SHA-256:
`4a1cc279bbea4571cda940563d306db123456aca5be6514d3190e3036b9bfcc9`.
It completed its 60-second watchdog/clock/RNG/silent-CAN/USB service loop and
left stage **211**, then reset as designed. The installed loader's recovery
command returned it to ROM DFU. No flash or option bytes changed during these
RAM tests. Full signed-image operation remains a separate check.

## Corrected signed image

Full off-device validation `validation-flash-20260916-04`: **1612 passed,
zero failed/skipped**, all 27 checks passed (including builds/lint/analyses).
Report SHA-256:
`8e4ad2e2e3cba169a5b4f73f41684c3c48b6586ef302ff70ab2a80d8d71dcc9a`.

Corrected private factory-image SHA-256:
`ea2c246c28752537ffd2a3aef07efa789330ab914f7e48784835086e4fa4dc91`.
ROM DFU updated only reviewed changed sectors: `0x08000000..0x08010000`,
`0x08040000..0x08060000`, `0x080a0000..0x080c0000` (ends exclusive).
Provisioning and all bytes outside those regions were verified unchanged
before programming. A subsequent full 1-MiB upload matched the intended image
byte for byte; option bytes again matched the original values. DEBUG memory
access is absent from this installed image. These are programming checks,
not yet evidence of physical CAN reception or vehicle readiness.

## Loader-to-application handoff diagnosis

The corrected image still reset. Fixed, non-secret SRAM breadcrumbs at
`0x2001c800` established that signed selection and handoff succeeded: image A
reached application runtime initialization. More detailed markers narrowed this
to stage 103, RNG initialization. The loader's running RNG was inherited across
the jump, while application startup changed HCLK and disabled/reconfigured PLL.
ST RM0430 documents latched RNG clock-error state when its clock is unsuitable.

Startup now disables an inherited RNG before touching clocks, verifies that it
stopped, and leaves subsequent RNG clock/seed/repetition checks unchanged. It
does not clear error status to conceal faults. Native tests enforce ordering
and failed-stop behavior. The complete ARM image model now latches a clock
error if PLL is disabled while RNG remains enabled. Both signed A/B boot paths,
USB-only confirmed boot, standalone recovery and USB dispatch tests passed
after the change. These are model results, separate from hardware validation.

Breadcrumbs are outside linker-allocated BSS and below the reserved stack;
their magic/complement are checked before reporting. They expose only image
and stage, not arbitrary memory. The DEBUG arbitrary-memory interface remains
restricted to the separate SRAM-only probe and absent from signed images.

The handoff-fix image was programmed and fully read back with SHA-256
`a3e1fc3f1cbcbc7e9a05831efbb714e1ee1fbeb41abc80c4aa81ef0822084867`.
Full off-device validation `validation-flash-20260916-05` passed all 27 checks,
**1615 tests, no failures/skips**; report SHA-256
`0d82247da0285465681aaf109007a5d3fdff0f3edeb80e2396d0ef318aaaeb0f`.
This image did not establish usable application USB: Windows reported
`Unknown USB Device (Device Descriptor Request Failed)` at physical port 2-2.
A targeted Windows device restart was denied; no elevation or global policy
change was attempted. A first reconnect listener missed the recovery window
because its wrapper omitted per-process script execution settings; the corrected
listener retains exact physical-port/serial checks and sends only the fixed
loader recovery request. Owner reconnect assistance was requested explicitly.
The corrected listener subsequently caught the owner-assisted USB reconnect,
verified `voltgw-recovery-v1`, and successfully requested ROM recovery. WSL
reattachment and the original option bytes were verified before further writes.

## USB readiness sequencing

Normal USB initialization was before boot/signature verification. That exposed
enumeration while the cooperative loop could not service USB. Normal USB is now
connected only after storage, application/session setup and boot confirmation
are complete. The separate cold-start recovery window remains first, and is
disconnected before normal runtime initialization. While waiting for safe power
to change trial metadata, normal USB remains disconnected rather than presenting
an unserviced interface. CAN/power/watchdog checks are not bypassed.

The whole-image ARM test now asserts normal USB initialization cannot precede
application initialization; cold recovery is exempt. All six complete-image
cases pass. This addresses an identified lifecycle defect; attribution of the
physical Windows failure and stable USB operation require the hardware retest.

## Working physical USB bench image

The USB-ready image was subsequently programmed into the same reviewed sectors
and the entire 1-MiB flash read back byte-for-byte. SHA-256:
`917cdfefb2bb4cde74f12970a3b587645a54e3daade2419b16f6f4847d294d4e`.
The installed signed A/B and loader ELF builds match the complete-validation
builds exactly. No option bytes were programmed. Provisioning remains canonical
listen-only: CAN3 SWCAN, CAN1/2 HSCAN, no backhaul transmitter or transport IDs.

Full validation `validation-flash-20260916-06`: **1615 passed, zero failed or
skipped**, all 27 checks passed. Report SHA-256:
`f4d99de63d07ed81fe125f64b58b4b5f0ced7cd16b97a901959ee5b5ad4d72d5`.

After the non-programming ROM jump and WSL reattachment, physical device serial
`370022000651363038363036` answered `voltgw-v1`. Paired authentication succeeded.
The authenticated build ID matched the signed slot-A ELF's build identity:
`59698ece0e1c3c4764186013d26eefe930b73404bdf16b772a8514aaeabc5b81`.

Physical USB checks passed:

- INFO, status, bus status on logical buses 0/1/3, empty vehicle TX policy.
- Clear IDs, two-second observation, two-second capture and bounded pagination.
  No frames were expected or observed on this disconnected bench.
- Sixteen authenticated status samples over more than 60 seconds; uptime rose
  monotonically from 49,973 to 112,687 ms.
- Exact authenticated duplicate returned its cached reply; obsolete sequence,
  invalid MAC and prior-session request after reopening USB were rejected.
  The legitimate sequence remained usable following the invalid MAC.
- Final authentication-check uptime was 141,041 ms; all three bus queries still
  reported zero transmissions, transmit errors and ESR error status.

External evidence in the dated flashing directory:
`usb-functional-01.json`, SHA-256
`c55579473bc7fe0c443ac0badace1c34341f53e5d52caf7b192b7cbb99957470`;
`usb-authentication-01.json`, SHA-256
`56dc53a3a95c9913b22ab352f038f0456eed765c655fa3f3ee948a66d907a945`.
No keys, passphrases, raw authentication packets or secret-bearing flash images
were included in these reports or committed.

**Scope:** working signed application on the actual USB-only bench Panda. This
is not physical SWCAN/HSCAN peer reception, electrical TX measurement, remote
CAN update, power-loss/A-B recovery, or in-car validation. The final image was
started via ROM jump; a fresh cold power cycle of this final image remains
unverified. The owner-assisted cold reconnect recovered the preceding image.
Hardware bootloader write protection remains unchanged/unenabled. Production
comma/openpilot/Tres and vehicle wiring were not touched.
