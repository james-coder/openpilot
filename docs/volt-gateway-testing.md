# Gateway testing: integrated workflows, simulation and hardware evidence

## Target crypto integration — 2026-09-16

**686 passed, zero failed/skipped**, with Ruff, Cortex-M4/core builds, pinned
ARM/native crypto builds and GCC analysis passing. Native crypto compilation
includes analysis of the adapter. ARM reproducibility and no-host-crypto tests
passed. [Public evidence summary](evidence/volt-gateway/target-crypto-20260916.json).
Private report:
`/home/james/diagnostics/volt-gateway/builds/validation-target-crypto-20260916-04/report.json`.

The linked CPU harness has 22,596 bytes text, 8 initialized data and 60,656 BSS,
including the 24-KiB crypto arena and observation buffers. This is not a whole-
firmware resource budget, actual hardware timing or flash/boot validation.
The ELF remains NEVER_FLASH and `production_ready` remains false.
See [backend, tests and limitations](volt-gateway-target-crypto.md).

Run `validation-target-crypto-20260916-02` was correctly invalidated because
source changed during validation; its passing test count was not accepted as a
release gate. Runs 03 and final 04 were rerun against stable source. Initial ARM
instruction-limit failures are explained in the backend notes, not hidden.

## Latest integrated-lifecycle results — 2026-09-16

**547 tests passed, zero failed/skipped**, including generated-input transport
tests, lifecycle fault cases, native C integration and the existing Cortex-M4
harness. Ruff, Cortex-M4 object builds and GCC analysis passed. Report:
`/home/james/diagnostics/volt-gateway/builds/validation-lifecycle-20260916-01/report.json`.

The lifecycle tests use persistent model storage with a Python boot-selection
oracle. They do not execute an updated application or validate target MCUboot
integration. The report therefore continues to say `production_ready: false`.
No flashing, vehicle traffic or primary Tres changes accompanied this run.

## Latest practical-hardening results — 2026-09-16

Unified validation completed with **414 passed, zero failed, zero skipped**.
Ruff, Cortex-M4 object builds and GCC `-fanalyzer` checks for authority,
observation and update cores passed. The private hashed report is
`/home/james/diagnostics/volt-gateway/builds/validation-hardening-20260916-02/report.json`.
Reproduce with `python -m tools.volt_gateway.validate --output NEW_DIRECTORY`.

This adds the real portable C update transaction to native transport/interruption
tests and Cortex-M4 execution through simulated flash/trial commit. Native tests
exercise changing conditions and expiry inside erase/write/read operations.
New CAN behavioral tests cover arbitration/non-preemption, finite queues,
missing ACKs, conflicting IDs, bounded retries and injected bus-off.

**Not complete firmware-update emulation:** no new application boot, confirmation
or direct-XIP rollback is demonstrated by this harness. Crypto remains host-
provided and flash is modeled storage. Target peripherals, signed boot/recovery,
live gateway commands and deployment are still absent. Static analysis passing
does not establish absence of defects or automotive qualification.

## Principle

Prefer testing the actual implementation through complete workflows over testing
a second implementation that merely agrees with itself. Report each layer
separately; no number of host tests establishes physical CAN or driving safety.

## Implemented test layers

1. Python wire/parser/authentication/adaptive-budget unit and fault tests.
2. The portable C authority gate compiled and executed natively, with real
   P-256 signatures supplied to a public-only host crypto backend. Cross-language
   records, signature rejection, replay, phase separation, expiry and CPU-work
   rate limits are checked. This is not target crypto/RNG validation.
3. The portable C observation engine under AddressSanitizer/UndefinedBehaviorSanitizer:
   256 ID entries, 512 captured frames, 32 subscriptions, saturation, overflow,
   changed bytes/DLC, standard/extended distinction, stale dropping and coalescing.
   No CAN transmission primitive exists in this core.
4. Integrated operator -> routine-key relay -> ISO-TP/MAC -> native C authority
   -> update transaction -> inactive-slot memory adapter. Tests include both
   application and loader authorization, dropped ACKs, corrupted/missing/reordered/
   duplicate fragments, no-operator attacks, and interruption at each modeled
   erase/program/trial boundary. Host processes/CAN controllers/real flash are
   not emulated by this test; the relay and verifier are logical components.
5. Cross-compilation of the portable C cores for Cortex-M4. This checks compiler
   compatibility and component sizes, NOT a linked release image or MCU timing.
6. Pinned upstream MCUboot simulator: actual bootutil C, signature backend and
   simulated flash. Record features and tests actually exercised, not just a
   green summary. Some upstream cases return success when a feature is disabled.
7. Unicorn Cortex-M4 CPU emulation executes ARM/Thumb instructions from a linked
   test ELF containing the actual portable C authority and observation engines.
   Checks include valid/bad signatures, replay, wrong phase, lease expiry,
   observation/capture, changed-byte statistics and subscription-handle lifetime.
   ECDSA/SHA256 use explicit public-key-only host hooks; this does not test target
   cryptography or STM32 peripherals. The ELF lacks a boot vector table and is
   named `NEVER_FLASH-core-harness.elf`; it must not be installed on a device.

Run project tests: `.venv/bin/python -m pytest tools/volt_gateway -n 0`.
Run lint: `.venv/bin/ruff check tools/volt_gateway`.
`python -m tools.volt_gateway.build_core --output NEW_DIRECTORY` produces Cortex-M4 object/stack reports.
`python -m tools.volt_gateway.mcuboot_test --repo PINNED_SOURCE --output NEW_DIRECTORY` preserves upstream
test logs and source hashes; production keys/devices are never involved.
Optional CPU-emulation dependencies are pinned in `requirements-emulation.txt`
(Unicorn 2.1.4, pyelftools 0.32). Without them, pytest explicitly skips the two
emulation tests. A skipped test is not a pass. Neither dependency is needed on
the comma. The installed Unicorn wheel download was approximately 15.7 MiB.

## Emulation research — 2026-09-16

QEMU's current STM32 documentation lists F405-based boards, not this F413 White
Panda, and lists CAN, flash interface, RNG, USB and watchdog as missing. It is
not a turnkey full-system proof for this project.
[Official QEMU STM32 documentation](https://www.qemu.org/docs/master/system/arm/stm32.html).

Renode is the stronger candidate: its generic STM32F4 platform declares STMCAN
CAN1/CAN2, flash controller, GPIO, RNG and watchdog models. But that platform
declares 2 MiB flash and 256 KiB SRAM and uses an F40x SVD. It is not the measured
1.5-MiB Panda configuration, does not declare CAN3 there, and does not model the
White Panda SWCAN PHY/mux. Inspect model behavior and provide an explicit Panda
platform before claiming compatibility. An unimplemented-register warning must
not be silently treated as a passing hardware test.
[Renode platform source](https://github.com/renode/renode/blob/master/platforms/cpus/stm32f4.repl).

Renode has not been installed/run in this increment. QEMU is not being used as
a misleading substitute. Windows C: has only approximately 1.1 GiB free; avoid
large emulator/toolchain installation until sufficient backing-disk headroom is
available. Upstream simulator compilation used disabled debug/incremental output.

Unicorn supplies CPU emulation, not an STM32 board. Its Cortex-M mode and explicit
CPU selection allow executing our Cortex-M4 test ELF without pretending the
missing peripherals exist.
[Unicorn API](https://github.com/unicorn-engine/unicorn/blob/master/include/unicorn/unicorn.h).

### Upstream test-harness findings

The initial broad swap/revert matrix was stopped after approximately 294 seconds
without a complete result; it is not a passing run. The upstream CLI rejected
its documented `--align 4` argument. Its default-alignment STM32F4 run then
reported an interrupted-revert failure at operation 404. Source inspection shows
that CLI reuses a `permanent=true` image for interrupted-revert testing, whereas
`sim/tests/core.rs` constructs that case with `permanent=false`. This is evidence
of a test-setup mismatch, not proof of a Panda or bootloader vulnerability.

`mcuboot_targeted.py` compiles `mcuboot_stm32_test.rs` against the unchanged pinned
upstream library. It selects STM32F4 with 1/4/8-byte alignment, uses separate
trial/permanent fixtures matching upstream core tests, and runs bad-signature,
interrupted-revert and interrupted-permanent-update checks. Preserve failed and
successful reports separately; never silently relabel the earlier failure.

### Recorded validation — 2026-09-16

- Gateway pytest suite: **322 passed**, no skips, in 27.72 seconds; Ruff passed.
- This includes 82 integrated/fault-injection cases and two actual Cortex-M4
  instruction-emulation cases (valid and invalid update authorization).
- Correctly constructed upstream STM32F4 fixtures passed bad-signature rejection,
  interrupted trial revert and interrupted permanent updates at alignments 1, 4
  and 8. Evidence: `mcuboot-targeted-20260916-02/report.json` under the external
  diagnostics builds directory. This is swap/scratch simulation, not direct-XIP
  revert or physical Panda validation.
- No Panda flashing, vehicle CAN transmissions or production comma changes were
  performed for these tests. Full gateway firmware is not yet deployable.

Invoke Python tools with `python -m tools.volt_gateway.MODULE` from the checkout,
not by directly executing their file paths: the local `operator.py` name otherwise
shadows Python's standard-library `operator` module during interpreter startup.

## Still required before release/vehicle deployment

Subsequent real-route integration: **344 tests passed** in 24.77 seconds, no
skips; Ruff and tracked diff whitespace checks passed. The suite now includes
parked/active-highway object-bus load fixtures and seeded synthetic GMLAN/fault
replays. See [real traffic findings](volt-gateway-real-traffic.md). These are
load-driven models, not full CAN-controller emulation or vehicle validation.

- Actual F413 ELF execution with the chosen crypto, RNG, CAN, watchdog and flash
  adapters. No cryptographic-success stubs in a release build.
- Chosen flash geometry and image layout in bootloader simulations; direct-XIP
  trial/confirm/revert and both-slots-invalid CAN recovery.
- Physical power interruption, supply thresholds, CAN error/retransmission and
  bus-off behavior, switch/mux routing, hardware TX silence and RX timing under load.
- Measured MCU crypto latency, stack/RAM high-water marks, streaming/control
  contention, watchdog response and update duration.
- Actual comma optional-service failure tests and engagement/disengagement tests;
  actual vehicle bus-ID/load evidence. Never restart driving software while moving.

The second Panda can provide an isolated bench peer once identified/preserved.
Hardware tests remain bounded and disconnected from the vehicle until appropriate.
