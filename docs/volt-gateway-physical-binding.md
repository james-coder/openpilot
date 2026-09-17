# Physical binding and verified handoff — not flashed

Validation: **1,308 tests passed, zero failed/skipped**; lint, ARM builds and
selected static analysis passed against an unchanged source snapshot.
[Evidence](evidence/volt-gateway/physical-handoff-20260916.json).

## Implemented

`white_platform.c` supplies ARM-only volatile MMIO bindings for the reviewed
White startup and flash drivers. Flash callbacks run in named RAM sections,
preserve/restore PRIMASK ownership, reject option-register access and deny byte
programming below the application slots. The low-level binding accepts trusted
internal operations only; it is never exposed as a wire memory-write API.

The flash service invokes the trusted SRAM work callback and checks the
progress-gated watchdog. Its fatal path executes from SRAM, disables the
differential transceivers, holds all CAN controllers in reset (including the
SWCAN controller), and requests a system reset. It does not return to flash code.
Physical shutdown timing and peripheral effects are still unmeasured.

`vgw_white_physical_handoff` consumes a trusted, verified MCUboot selection,
checks slot/vector consistency and conservative stack bounds, rejects exception
context, quiesces CAN, disables SysTick/NVIC sources and clears pending ticks,
sets VTOR, then switches CONTROL/MSP and branches without using the old stack.
Applications inherit an active independent watchdog and masked interrupts.
They must initialize their own runtime before enabling interrupts or confirming.
Structural checks in this function are **not** signature verification; callers
must use the successful result of `vgw_boot_select` from this boot.

## Integration evidence and limits

The isolated ARM harness tests actual binding instructions, including previously
masked/unmasked interrupt ownership, invalid flash/option accesses, watchdog
servicing, failure shutdown/reset ordering, invalid vectors and application
handoff. It is compiled with a test-only guard and named NEVER_FLASH.

The actual MCUboot/Mbed TLS ARM harness now optionally links these physical
bindings. A/B tests verify signature/selection, execute the MSP/VTOR handoff and
stop at the application's reset entry. A second unconfirmed boot reverts through
the same handoff path. Both invalid images never enter an application.

Unicorn loads the ELF's RAM sections directly: these tests do **not** validate a
real reset vector, data/ramfunc copy table, cold-start BSS clearing or complete
application initialization. Peripheral registers and flash remain modeled memory,
not a full STM32 hardware model. Broad all-memory write instrumentation caused
a pre-verification stack/control-flow failure; narrowing the hooks to the modeled
peripheral ranges removed that failure. The underlying simulator cause is not
established; do not count it as a discovered/fixed firmware defect.

## Status against the requested four steps

1. Hardware integration: real MMIO, flash critical-section bindings and verified
   application handoff are implemented/tested off-device. Final HSE/PLL and RNG,
   CAN controller/PHY driver, busy-time RAM call-graph proof and actual reset
   startup remain incomplete.
2. Recovery integration: bounded receiver, authority, updater, inner verification
   and commit exist, but a single provisioned board recovery dispatcher and
   rate-limited physical response transport are not complete.
3. Complete-image testing: constituent and newly connected loader/handoff tests
   run; there is still no complete flashable loader/application release.
4. Flash and physical validation: **not performed**. USB IN-only inspection
   confirms the labeled Panda still reports its original application.

No reviewed production CAN IDs or harness routing are established. The board
also needs an independent bench CAN peer and wiring to demonstrate its remote
recovery before relying on that path. USB enumeration alone cannot prove CAN
recovery. Keep the known-good flash until a complete tested image exists; this
is a readiness gate, not a request to preserve the original indefinitely.

Observed USB identity this increment: `370022000651363038363036`, hardware 01,
`bbaa:ddcc`, `v1.7.3-EON-unknown-RELEASE`; health reported silent mode 0, controls
disallowed, ignition flags zero and 4776 mV. These are device-reported values,
not independent proof of connector isolation or electrical silence.
