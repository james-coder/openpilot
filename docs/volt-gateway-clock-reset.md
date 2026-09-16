# White clock, RNG and reset increment

## Implemented

`white_clock.c` selects the recovered White Panda profile: 16-MHz HSE,
PLLM=8, PLLN=96, PLLP=2, PLLQ=4; SYSCLK 96 MHz, APB1 24 MHz,
APB2 48 MHz, RNG clock 48 MHz. TIM2 is retimed to a 1-ms tick without
rewinding its counter. Voltage scaling and flash latency precede the
clock increase; every readiness wait has an iteration/time bound.
All CAN controllers must remain reset. Failures latch watchdog failure
and re-quiesce the board. No watchdog feeds occur in clock polling.

This transition is cold-start only, before sessions or bus operation:
the timer's transitional elapsed time is not calibrated. The crystal
frequency is source-derived, not physically measured on this Panda.

`white_rng.c` obtains 32-byte nonces using the hardware RNG, discards its
first word, rejects current/latched clock or seed errors and consecutive
identical words, and bounds each readiness wait. Failures clear the entire
output, disable the RNG and latch failure. There is no UID/time-based
entropy fallback. This is stuck-output detection, not entropy certification.
The authority/session owner still needs wiring to this driver; it must
invalidate sessions and stop optional TX on RNG failure.

`white_reset.S` supplies 118 vector entries (16 core + 102 external),
initial MSP, IRQ masking, flash-to-SRAM copies for RAM functions/data,
BSS zeroing and VTOR initialization. Main must be supplied by the final
trusted loader; there is no weak successful placeholder. The early fault
path is flash-resident, raises active-low PHY-disable output latches,
resets CAN controllers and requests system reset without feeding IWDG.
It does not configure GPIO output modes before RAM initialization, and
does not replace full board quiescence. A persistent fault can reset-loop.

`reset_emu.ld` and `reset_emu.c` are explicitly NEVER_FLASH harnesses,
not a production link layout. The linker reserves 8 KiB for stack and
asserts vector count. No production provisioning or recovery service is
hidden in this harness.

## Tests and limits

Full off-device validation passed: **1,337 tests, zero failures/skips**,
lint, ARM core/crypto/loader builds, native crypto/loader builds and selected
C static analysis. The [evidence record](evidence/volt-gateway/clock-reset-20260916.json)
identifies the private report and its hash.

Targeted tests cover clock ordering, failed readiness, ignored register
writes, RNG timeout/error/repeated words and lost configuration. Reset
tests load only flash physical addresses, poison SRAM with three different
patterns, execute reset, call the copied RAM function, check data/BSS/VTOR
and IRQ state, and separately execute the early fault/reset path.

These tests are register models and CPU emulation, not oscillator startup,
entropy quality, silicon errata, GPIO voltage, power-loss or physical reset
validation. The existing MCUboot handoff harness and this reset harness
are still separate: a complete reset-to-recovery/application image has
not yet been demonstrated. No device was flashed or reconfigured.

Next: compose startup/clock/RNG with real bxCAN RX/TX and a bounded
authenticated recovery dispatcher; add independent immutable provisioning;
link the complete loader/application; audit all flash-busy RAM dependencies;
exercise reset/update/revert end-to-end; then bench-test with a CAN peer.
Vehicle IDs and harness wiring remain unresolved deployment gates.

## Source basis

Historical Panda commit `3b35621671aaa6de3fc66d85d30e4208a77e2489`,
`board/gpio.h` clock initialization, supplies the White clock profile;
its STM32F413 CMSIS definitions supply register masks and IRQ count.
Current ST implementation references reviewed for sequencing and errors:

- [ST voltage-scaling implementation](https://github.com/STMicroelectronics/stm32f4xx-hal-driver/blob/master/Src/stm32f4xx_hal_pwr_ex.c)
- [ST RNG implementation](https://github.com/STMicroelectronics/stm32f4xx-hal-driver/blob/master/Src/stm32f4xx_hal_rng.c)

Generic HAL comments about other F4 clock ratings are not evidence of this
device's maximum frequency. Bench measurements and applicable silicon
errata review remain necessary before release.
