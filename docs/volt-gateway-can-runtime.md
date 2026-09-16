# CAN/runtime integration and flash readiness

## Decision

Do not flash this tree yet. The actual composed runtime has a required
protocol-owner callback, but the production authenticated command/recovery
dispatcher has not been supplied. The flash-busy service loop, protected
provisioning and complete boot/application image are also unfinished.
The emulator's `no_commands` callback is test-only and must not substitute
for those requirements. Flashing a test harness onto hardware would not
fulfill the gateway or independently recoverable CAN-update requirement.

## Implemented changes

- `white_can.c`: physical F413 bxCAN register operations, CAN1/2 shared filters
  and independent CAN3 filters; either historical SWCAN routing; differential
  CAN PHY enables and PB14/PB15 SWCAN normal mode. No high-voltage wakeup mode.
- SWCAN shutdown fix: `white_board.c` explicitly drives both mode pins low for
  sleep before changing muxes. Physical reset and early fault paths also clear
  those latches. CAN1 pins PB8/PB9 are now explicitly disconnected on quiescence.
- Listen-only is the default on all selected controllers. An explicitly
  provisioned backhaul may use normal mode, including ordinary CAN ACK/error
  behavior. SWCAN has no transmit API and remains hardware-silent.
- Bounded FIFO servicing (8 messages/controller/call), timestamps, standard and
  extended identifiers, RTR/DLC preservation, malformed/overflow counters,
  error state, estimated 10/100/1000-ms load windows and peak load.
- One fixed-ID/DLC8 backhaul response operation; no caller-selected bus or ID.
  One hardware mailbox, no software backlog, automatic retransmission disabled,
  at most 100 attempts/s plus a two-frame burst. Mailbox data is checked before
  setting TXRQ. A configuration CRC detects accidental field corruption; it is
  not cryptographic authentication or a defense against arbitrary code execution.
- TX requires recent RX/health service and pauses at 50% conservative estimated
  occupancy in any window. Approximate frame charges are 160 bits standard,
  185 extended. At 100 standard frames/s the charge is 3.2% of 500 kbit/s;
  this is an intentionally conservative driver ceiling, not the final adaptive
  firmware-update policy or evidence that 100 frames/s is safe on this harness.
- RX overflow or malformed DLC inhibits optional TX for the remainder of this
  boot. TX errors also inhibit it. Bus-off/error-passive, wrong clock/BTR/MCR,
  stale service or a >20-ms pending TX disables all PHYs/controllers. There is
  no automatic bus-off restart or stale queue dump.
- `recovery_link.c`: two-way ISO-TP, max 512-byte logical messages, fixed block
  grants, peer separation times with a 10-ms minimum, bounded WAIT handling,
  total/inter-frame timeouts, buffer clearing and driver backpressure without
  falsely advancing the transfer. Flow-control frames do not authorize actions.
- `white_runtime.c`: real startup -> clock -> RNG/nonce -> CAN -> observer/link,
  plus a cooperative loop with independently checked watchdog task completion.
  Failure closes transport, clears the boot nonce and quiesces CAN. The RNG has
  a non-consuming periodic status check. Quiet buses do not cause watchdog loss.

## Tests versus physical evidence

Full regression: **1,425 passed, zero failed/skipped**, with lint, ARM
core/crypto/loader builds, native crypto/loader builds and selected C static
analysis passing. The [evidence record](evidence/volt-gateway/can-runtime-20260916.json)
identifies the exact report hash. This is not physical-device validation.

Native register-model tests exercise mux alternatives, accept-all filter
placement, failed writes/readiness, FIFO floods, response shaping, wrong bus,
CAN errors, configuration corruption, mailbox write errors, ISO-TP block sizes,
padding, retransmission backpressure, deadlines and runtime failure handling.

The additional ARM harness loads flash only and starts at the reset vector,
then executes the actual composed runtime with modeled clock/RNG/CAN registers.
It checks successful silent startup and identity/RNG/CAN-init failures.
The echo/no-command protocol handlers are explicitly test-only. They do not
validate production command authorization. Hardware register timing, oscillators,
entropy, transceiver routing/voltages, exact capture loss and the vehicle bus
are not reproduced by these models.

No firmware flash, option-byte change, CAN transmission, production Tres change
or vehicle software restart was performed. IN-only USB inspection confirmed
serial `370022000651363038363036`, hardware `01`, original
`v1.7.3-EON-unknown-RELEASE`, controls disallowed and reported safety mode 0.
This is not proof of physical vehicle disconnection or electrical silence.

## Integration constraints still preventing flash

The runtime loop is **not flash-busy safe**: it calls flash-resident code.
It must not be connected to the RAM flash driver's service callback unchanged.
The protected loader must retain independent provisioning and a functional
authenticated update dispatcher when both application slots are invalid.
Production signing/pairing keys and vehicle transport IDs remain unprovisioned.
USB-only testing cannot establish physical CAN recovery; that requires a bench
peer and validated wiring. These are open requirements, not passed gates.

## Source basis

The recovered Panda `3b35621671aaa6de3fc66d85d30e4208a77e2489` supplies
`board/boards/white.h` mux/PHY routing, `board/drivers/llcan.h` 24-MHz APB1
bit timing, and `board/inc/stm32f413xx.h` register definitions.
CAN3 is at **0x40006c00**, not the CAN3 address used on some other F4 variants.
The separate/shared filter arrangement was checked against
[ST RM0430](https://www.st.com/resource/en/reference_manual/rm0430-stm32f413423-advanced-armbased-32bit-mcus-stmicroelectronics.pdf)
and [ST's filter-bank explanation](https://community.st.com/stm32-mcus-60/stm32-in-dual-can-configuration-bxcan-filter-bank-explanation-and-relation-with-can2-start-bank-parameter-136737).
Board revision, silicon errata and actual installed-harness mapping still need
physical verification; historical source is not a continuity measurement.
