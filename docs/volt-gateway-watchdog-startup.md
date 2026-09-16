# Watchdog comparison and White startup candidate

Offline validation: **1,291 passed, zero failed/skipped**, including 59 new
targeted tests; lint, ARM builds and selected static analysis passed.
[Evidence](evidence/volt-gateway/startup-watchdog-20260916.json).

## Historical Volt forwarding

Inspected recovered `legacy-gm-forwarding` commit
`4e85803018ba8cfe20d7e1eb47bc7171ee38faf4` in the preserved
`/home/james/diagnostics/volt-gateway/history/vntarasov-panda.git` repository.
Search included watchdog, IWDG/WWDG use and heartbeat, excluding vendor register
definitions and vector-table symbol declarations when identifying active code.

**No active hardware watchdog or host-heartbeat watchdog was found in that
commit's main Panda application/forwarding path.** Register definitions and a
WWDG interrupt-vector name do not mean a watchdog is running. The separate
`board/pedal/main.c:249` feeds IWDG; lines 278–283 configure it for a nominal
50-ms timeout. That is the pedal target, not this White Panda forwarding build.
This source finding does not establish exact behavior of the installed binary,
whose source provenance remains incomplete, or rule out option-byte watchdog
configuration on an arbitrary device.

## Newer Panda

Local Panda revision `7ffc916578af9373a165cf9df5974720d9a258a5` and upstream
files inspected on 2026-09-16 have two distinct mechanisms:

- `simple_watchdog`: called by the 8-Hz tick; reports a fault if elapsed time
  exceeds 375,000 microseconds. The check happens when execution reaches the
  next kick. It does not independently reset a permanently stalled CPU.
- Host heartbeat: at the one-second tick, missing host heartbeats eventually
  select SILENT safety mode and power saving. Current upstream thresholds are
  five seconds with ignition and two seconds without it. This also relies on
  the MCU continuing to execute.

`fault_occurred` records fault status/bits; it is not an IWDG reset operation.
No explicit main-board IWDG/WWDG start/feed path was found in the locally
inspected board code. Do not infer hardware reset coverage merely from the
function name `simple_watchdog`.

Primary sources:
[watchdog implementation](https://github.com/commaai/panda/blob/master/board/drivers/simple_watchdog.h),
[tick/heartbeat integration](https://github.com/commaai/panda/blob/master/board/main.c),
[fault recording](https://github.com/commaai/panda/blob/master/board/sys/faults.h).
Upstream links are moving references; local revision above records the checkout
used for comparison. None of this changes the primary Panda's safety behavior.

## Gateway implementation in this increment

`white_watchdog.c` configures the F413 independent watchdog, using its independent
LSI oscillator: prescaler /32, reload 1999, nominal two seconds at 32 kHz.
Actual timeout tolerance, LSI failure behavior and physical reset timing remain
bench gates. No option bytes are written. Reset-cause flags are captured before
any clearing; the code does not set RMVF. Startup waits are bounded.

Feeds require progress from **scheduler, RX service, protocol service and health
checks** within each epoch. RX service counts its bounded work even on a quiet
bus; packet arrival does not feed the watchdog. All four tasks must report
completion, at least 50 ms must elapse, and an incomplete/late epoch beyond
250 ms latches failure. Invalid progress masks also latch failure. A frozen
timer cannot generate recurring feeds. These software thresholds are candidate
values awaiting worst-case execution timing, not automotive-qualified limits.

The board owner must immediately disable optional TX when service returns false.
The hardware reset is a final backstop, not permission to transmit until reset.
During flash operations every necessary task/callback must continue from SRAM;
repeated watchdog feeds in a flash polling loop are explicitly not sufficient.
Boot verification must also service bounded work or let the watchdog reset it;
crypto verification latency still needs measurement before setting final limits.

`white_startup.c` composes early CAN quiescence, MCU family/flash-size checks,
watchdog start and a free-running TIM2 millisecond clock. It switches safely to
the 16-MHz internal oscillator with bounded readiness/readback checks, preserving
the inherited clock until the switch is acknowledged. TIM2 does not use IRQs or
DMA. All CAN controllers remain held in reset and transceivers disabled.
**This is an initial safe clock, not an approved CAN/USB communications clock.**
Final HSE/PLL selection and corresponding timer retiming remain required.

## Recovery transport

`recovery_transport.c` is a bounded, allocation-free ISO-TP receiver with a
512-byte maximum PDU, one-second inter-frame and ten-second overall deadlines,
sequence/padding/DLC checks, explicit completed-buffer ownership, quiet-period
expiry and a trusted fixed standard-ID binding. Wrong/extended IDs are ignored;
RTR and malformed matching frames are rejected. No production ID is selected.

Flow-control indications are requests to the future TX scheduler, not direct
CAN sends. That scheduler must enforce the chosen fixed response ID, rate limits,
block size 8 and 10-ms separation. The receiver tolerates faster arrivals within
its fixed bounds; it does not itself enforce peer timing or response budgets.
Completed PDUs are **not authenticated** until the authority layer accepts the
whole message. This module has no flash, crypto, GPIO or CAN-transmit callbacks.

## Still not deployable

The additions are compiled/tested off-device, not a full reset-vector/linker/
interrupt-ownership implementation. Actual MMIO bindings, CAN controller/PHY
driver and TX budget enforcement, trusted provisioning, RNG, verified app
handoff, RAM-safe flash service and an independently bootable authenticated
recovery dispatcher remain required. No startup function has run on the Panda.

Tests exercise register sequences, startup failures, progress starvation, frozen
and wrapping clocks, latched faults and reassembly against the Python encoder.
They do not prove physical watchdog reset, CAN silence or driving behavior.
