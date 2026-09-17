# White Panda interrupt receive staging

## Scope and deployment gate

This candidate addresses observed HSCAN hardware FIFO overflows in the parked
startup comparison. It has not been flashed or physically validated. Neither
the production Tres nor the installed White firmware is changed by these edits.
Transport IDs and vehicle-side transmit permissions remain separately gated.

## Design

Each active controller drains FIFO0 promptly through its RX interrupt into its
own 64-entry software ring. Each entry is 24 bytes; three rings use 4,608 bytes.
The interrupt stages raw mailbox contents, capture time and sequence number;
it does not run application decoding, cryptography, USB, flash programming or
watchdog feeding. FIFO draining is bounded to eight reads per invocation.
Remaining pending traffic can retrigger the interrupt. Short interrupt-masked
sections protect shared queue state. Foreground processing removes at most
eight frames per controller per poll, outside the critical section.

This is not a two-path spill buffer: every retained frame takes the same path.
Ordering is preserved within each bus; callbacks across buses are not globally
time-sorted. Capture timestamps and sequence numbers accompany the frames.
Park/speed/awake interlocks use capture time, never dequeue time as fresh evidence.

The F413 FIFO holds three messages, per [ST RM0430](https://www.st.com/resource/en/reference_manual/rm0430-stm32f413423-advanced-armbased-32bit-mcus-stmicroelectronics.pdf).
Waiting for it to approach full is intentionally avoided. Separate rings prevent
a busy HSCAN from consuming SWCAN's software storage. Full rings drop the new
frame and inhibit optional transmission; they never overwrite older queued data.
The hardware overflow flag is an event indication, not an exact lost-frame count.

No selective receive filtering is introduced in this candidate. Discovery still
accepts all frames. Future intentional filtering must be distinguished from loss
and must preserve required command and vehicle-state inputs.

## Integration and observability

F413 IRQ20/64/75 map CAN1/2/3 RX0 to SRAM handlers. Only FIFO0 pending interrupts
are enabled; historical USB remains polled, with its IRQ explicitly disabled.
Existing startup, fail-closed TX policy, queue bounds and watchdog remain in force.

Existing bus-status response layout is unchanged. INFO capability bit `0x20`
advertises authenticated opcode 16, with one logical-bus byte as its request.
Its successful response contains four big-endian u32 values (after status):
software drops, IRQ calls, queue high-water mark, maximum queue age in ms.
`device_cli rx-health` checks the capability before querying. Hardware FIFO
overflow remains distinct in the original bus-status response.

When optional TX has been inhibited, normal running/trial LEDs are replaced by
`rx_degraded`: red for 1.5 seconds, dark for 0.5 seconds, blue for 0.25 seconds,
then dark for 3.75 seconds. The six-second cycle repeats without blocking work.
Fault, recovery and update indications retain priority. This is a gateway
degraded-state indication, not a driving alert or an automatic reboot request.
It neither clears counters nor re-enables transmission. Any overflow currently
latches TX inhibition, so even one such event warrants this indication; mere
queue occupancy does not. Other reasons for TX inhibition can share the state,
so the detailed counters must be inspected before attributing it to overflow.

## Verification limits

Tests model the three-entry hardware FIFO, missed servicing, full software rings,
cross-bus isolation, repeated ring wrap, payload/order preservation and delayed
foreground processing with two busy HSCAN inputs plus SWCAN. An event-scheduled
test injects HSCAN frames every 300 microseconds per bus and SWCAN frames every
6 milliseconds with foreground delays up to 5 milliseconds. This is not a
cycle-accurate CPU model or a measured worst-case execution-time guarantee.

The ARM board-image tests check linked vector destinations, SRAM handlers and
interrupt-enable configuration. Actual interrupt latency, CPU headroom, startup
under vehicle traffic and long-duration loss counters still require physical
validation. Passing host tests does not establish automotive certification or
prove lossless operation under every traffic condition.

## Completed off-device validation

Final run `validation-rx-irq-20260916-03` passed 1,669 tests with no skips or
failures, along with the validator's firmware/crypto/MCUboot builds, static
analysis, lint and unchanged-source check. Report:
`/home/james/diagnostics/volt-gateway/builds/validation-rx-irq-20260916-03/report.json`
(SHA-256 `3ced3e97289ff6c1ac0d9404a9f3082de0a4fc1938f74980c5aa360051269a6e`).
The report deliberately retains `production_ready: false`.

Earlier run 01 exposed a freestanding ARM link failure from an unavailable
64-bit division helper; the capture-timestamp conversion now uses bounded
division without that dependency. Run 02 was superseded/interrupted while the
requested degraded LED indication was added. Neither is a passing release gate.
Only the unchanged-source final run above validates this candidate.
