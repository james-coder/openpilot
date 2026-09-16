# Volt gateway implementation status and gates

## Actual MCUboot C direct-XIP/revert integration — 2026-09-16

The [new boot port](volt-gateway-boot-port.md) now tests the real MCUboot loader
with both direct-XIP and revert enabled, real C crypto, and memory-backed flash.
It adds latched storage errors/readback verification, signed device/layout
binding, vector checks and confirmation limited to the selected slot. This
advances beyond the Python boot oracle; it is still not a flashable board port.
ARM startup/execution, hardware flash/RNG/watchdog, independent CAN recovery,
release packaging and exact hardware geometry remain open. No devices changed.

## Target cryptographic backend — 2026-09-16

The ARM harness now has a pinned Mbed TLS 3.6.7 backend executing P-256 verification,
SHA256 and HMAC without host crypto hooks. Native update/transport/lifecycle tests
also exercise this C backend, including modeled interruption and rollback.
See [implementation and remaining obligations](volt-gateway-target-crypto.md).
This closes the host-crypto shortcut for those tests, **not** the complete target
firmware gate: RNG/drivers, actual loader/flash/boot/recovery and scheduling
remain unimplemented or unverified. No device flash or production Tres change.
Earlier sections describe earlier milestones; they do not supersede this update.

## Integrated lifecycle work — 2026-09-16

The native transport harness now advances a virtual clock during fragment
delivery and exercises jitter, a timeout-inducing delay and burst loss in
addition to corruption/reordering/duplicate/lost-response cases. The C updater
is connected to persistent two-slot model storage and a journal model. Tests
reset the modeled loader, select a verified trial, confirm or revert it, and
interrupt each modeled persistent update/boot-transition mutation.

This **does not implement the production loader**. `boot_model.py` is a Python
oracle with its own illustrative journal, not MCUboot metadata or an approved
F413 flash allocation. It does not execute the selected application's instructions.
Passing it cannot authorize flashing the CPU harness or existing unsigned builds.

Generated malformed-transport/jitter tests supplement fixed cases; they are not
coverage-guided native fuzzing. Clang/libFuzzer is absent in this environment.
The Windows backing disk has approximately 1.1 GiB free; do not install a large
toolchain blindly. Existing GCC analysis/native sanitizer tests remain available.

The exact attached Panda was queried through USB IN-only requests. Firmware
reported silent safety mode (0), controls disallowed, both ignition indicators
off, zero CAN errors/faults, and power-save enabled. Supply reading was 4776 mV;
the current field is an uncalibrated raw value, not milliamps. These reports do
not prove electrical silence, disconnection or SWCAN routing. Nothing was flashed.

Production remains gated on actual target crypto/RNG, linked trusted boot and
recovery, board adapters, transport enforcement and bus/hardware verification.
The existing MCUboot simulator's TinyCrypt backend must not be implicitly chosen
as the production crypto dependency: Intel archived/ceased maintenance of that
project. Review a supported backend separately.
[Upstream maintenance notice](https://github.com/intel/tinycrypt).

## Practical hardening implementation — 2026-09-16

Implemented since the real-traffic pass:

- Python update reference now obtains fresh environment samples before/after
  storage operations, validates 30-ms sample freshness, and erases one adapter-
  supplied region at a time. Expiry/gate loss stops subsequent operations.
- Portable `firmware/update.c` now implements the same bounded transaction with
  the C authority gate, streaming hash callbacks, duplicate-chunk handling and
  independent readback verification. The transport integration suite now uses
  this C updater rather than Python for storage sequencing.
- Cortex-M4 emulation executes this updater through simulated trial commit.
  Cryptography remains host-backed, storage is a tiny RAM model, and no new
  image is actually booted. This does not close the reboot/revert gate.
- A deterministic CAN behavioral model tests priority, non-preemption, missing
  ACKs, same-ID conflicts, bounded retries/mailboxes/RX queues and injected
  bus-off. It is not an electrical or register-accurate controller emulator.
- `python -m tools.volt_gateway.validate --output NEW_DIRECTORY` runs tests,
  lint, Cortex-M4 builds and GCC static analysis, with hashed reports and explicit
  skipped/hardware-unverified status. It never claims production readiness.

USB IN-only identity inspection confirmed the backed-up White Panda is still
running `v1.7.3-EON-unknown-RELEASE`, type 01, serial
`370022000651363038363036`. No flashing, mux changes or vehicle transmissions.

**The complete plan is not finished.** Target crypto/RNG, flash/boot adapter,
boot/confirm/revert emulation, live command transport/dispatcher/host service,
shared on-device rate enforcement, provisioning and parked validation remain.
The current CPU harness is NEVER_FLASH, not a gateway firmware image.

Acceptance policy follows the owner's revised scope: practical automated
engineering checks plus benign parked checks, no dedicated bench rig, required
external review or certification claim. Physical power-cut/fault testing is
not performed on the vehicle; its absence stays an explicit residual limitation.

## Active full implementation — two-key separation and executable test layers

The deliverable remains the complete gateway, not a succession of reduced-scope
products. Acceptance tests are internal gates. New implementation now includes:

- Distinct operational HMAC versus operator ECDSA authority; interactive local
  encrypted signing tools, signed manifests, fresh ENTER/PROGRAM grants and
  public-only release packaging. No real keys generated/provisioned.
- Portable Cortex-M4 C authorization and bounded observation cores (ID stats,
  capture, subscriptions/coalescing/stale drops); native sanitizer tests and
  Cortex-M4 instruction emulation, with explicit host crypto hooks.
- Integrated fragmented/MAC transport tests into the native C authority and
  inactive-slot memory adapter, including 74 interruption-boundary cases.
- Pinned MCUboot v2.4.0 simulator research and targeted upstream C recovery tests.
  See [test layers, gaps and upstream harness findings](volt-gateway-testing.md).

These are not yet a bootable production firmware or a deployed host daemon.
Still missing: vetted target crypto/RNG and board drivers, linked trusted loader,
actual flash/journaling/CAN recovery, full command dispatcher/host integration,
provisioned bus/IDs/keys, physical recovery and vehicle independence validation.
Do not flash the core objects or CPU-emulation ELF. Production Tres is unchanged.
The original Panda application was confirmed again over read-only USB queries.

## Latest milestone: verified full backup, original application running

Two identical 1.5-MiB main-flash reads completed on 2026-09-16; application digest
and signature match the prior read. See [inventory](white-panda-inventory.md)
and [evidence](evidence/volt-gateway/readback-success-20260916.json).
A tiny native WinUSB handoff caught the legacy softloader in 552 ms and entered
ROM DFU. WSL `dfu-util` performed both reads; a non-programming DFU jump returned
to the original application, verified by live USB identity/version queries.
No CubeProgrammer/IDE installation, erase, programming, protection changes or
vehicle CAN transmissions were needed. No background attachment loops remain.

The backup portion of gates 1–2 below is complete. Numeric protection state,
exact chip revision/RAM, restoration testing and deployable firmware remain
unverified. Earlier attempt/setup sections below are historical, not current
blockers. Prefer the second Panda for subsequent recovery/development tests.

## Current host choice: usbipd passthrough, Linux tools in WSL

Owner superseded the Windows-native tool plan: use usbipd-win and direct Linux
USB access instead of installing STM32CubeProgrammer. The earlier 5-GiB
CubeProgrammer installation reserve is no longer a prerequisite for this path.

usbipd-win 5.3.0 installer was downloaded through winget and its SHA-256 verified:
`1c984914aec944de19b64eff232421439629699f8138e3ddc29301175bc6d938`.
The first install failed with MSI 1925/1603 (insufficient administrator rights).
A standard Windows UAC elevation was then requested with automatic reboot
disabled. That initial prompt was canceled. The owner subsequently installed
the package; version 5.3.0 was confirmed, and attachment is now completed.

The existing WSL `vhci_hcd` module was loaded successfully using the distribution's
root account. Linux USB root hubs now appear. No WSL restart/kernel rebuild was
needed. The Panda now appears at Linux bus 1/address 2 (these numbers are not
persistent identity).

Completed: verified labeled serial at Windows bus `2-2`; restricted the existing
USB/IP firewall rule from `LocalSubnet` to `172.21.244.169`; bound only this Panda
without force/auto-bind; attached it to Ubuntu. Host address is `172.21.240.1`.
Direct IN-only libusb inspection confirmed hardware type 01 and original
`v1.7.3-EON-unknown-RELEASE`. The first inspection exposed unsupported context
manager usage on USBDeviceHandle; helper and mocks now use explicit closing.

Installed `99-voltgw-white-panda.rules` in `/etc/udev/rules.d/`, restricting
application-mode access to this VID/PID/serial and group `plugdev`, mode 0660.
Read-only inspection succeeds as ordinary user james without sudo. No wildcard
DFU permission. Four inspection tests and the full 165-test suite pass.

The elevated sharing helper initially stopped after narrowing the firewall due
to a scalar/array verification bug; that was corrected before bind succeeded.
No Panda boot-mode/firmware/CAN operation was sent in this setup session.

At that setup stage, next was the separately gated Linux DFU backup. Mode transitions may require
explicit reattachment; do not assume an application-mode attachment proves DFU
recovery. No flash/erase/protection changes to obtain USB access.

Rollback is targeted `usbipd detach` then `unbind` for this device; do not stop
unrelated devices/services. WSL's client IP can change after restart: revalidate
the restricted firewall scope instead of opening the service to the LAN.

## Superseded Windows-native plan (historical context)

**Earlier failed readback attempt:** Linux `dfu-util` installed (118 KB installed size).
Recovery-only AutoBind rules for port 2-2 + BBAA:DDEE / 0483:DF11 were added;
they do not share arbitrary USB devices. After a single D1/value1 reset, the
softloader appeared briefly, then disappeared from Windows and WSL. USB/IP
logged a missing-device error while claiming it. This repeats the earlier
enumeration problem, not a completed backup. Both auto-attach processes were
stopped; owner was asked to unplug/replug once. Green blinking is consistent
with softloader waiting. No ROM entry, flash read/write or protection change
occurred. Do not repeat the same mode transition until its failure is understood.
See [attempt evidence](evidence/volt-gateway/readback-attempt-20260916.json).

The following Windows-native plan is retained only for historical context:

Use Windows-native WinUSB for Panda identification/mode transitions and official
STM32CubeProgrammer CLI for ROM DFU readback/verification and later explicit
programming. CubeProgrammer does not implement the Panda proprietary softloader.
Keep compilation, tests and Git in WSL. No USB/IP installation or ownership
switch mid-operation. Do not substitute `PandaDFU.recover()` for reading.

`windows_preflight.ps1` performs PnP/driver/disk/tool inventory only. It makes no
device changes. On this host its unsigned WSL-path script requires a process-only
PowerShell execution-policy override; no persistent policy was changed.

2026-09-16 preflight: labeled Panda present/OK with WINUSB; CubeProgrammer absent
from the expected installation path and PATH; winget search returned no package.
C: had approximately 1.46 GB free. Chosen installation headroom gate is 5 GiB
(project reserve, not a claimed ST minimum). Do not install a large package or
delete user files to work around this. Obtain the official Windows installer
and sufficient Windows staging space first. Pin executable version/hash before
device access, preserving vendor signature/provenance information.

References: [ST tools](https://www.st.com/en/development-tools/stm32cubeprog),
[ST CLI](https://dev.st.com/stm32cube-docs/prog/2.23.0/en/docs/markup/CubeProg_Command_Lines.html),
[WSL USB setup](https://learn.microsoft.com/en-us/windows/wsl/connect-usb).

## Implemented off-device

- Bounded codecs/auth envelope, telemetry alternatives, fixed queue model.
- Adaptive load admission and bounded chunk-retry model.
- Two-peer update simulation with FC/ACK costs, real MAC checks, duplicate-result
  cache and congestion/loss/expiry/power/reconnect fixtures.
- Read-only Windows preflight; offline allowlisted read-command builder and
  double-read/vector/hash verifier; explicit `readback.py` runner and
  non-programming `dfu_leave.py` bootstub jump. No firmware programming command.
- Historical source preservation and reproducible nonmatching candidate builds.
- Nine investigation documents, with explicit unknowns rather than invented
  MCU geometry, wiring or CAN-ID assignments.

## Ordered next gates

1. USB/IP application access is complete. Prepare a read-only Linux DFU tool and
   explicit reattachment/identity checks before one coordinated USB-only
   transition session. Target exact serial and
   port; no automatic first-device selection. On enumeration failure stop,
   restore the application via the documented available path, and report state.
2. Establish MCU/flash extent and protection without modifying them; read twice
   and preserve privately. Stop on RDP. Verify restoration separately on a spare.
3. Identify the second Panda. Prefer it for bench development. Build a minimal
   receive-only board image from the historical F4 reference only after geometry
   and recovery gates; retain existing bootstub where compatible. No image is
   currently approved to flash.
4. Prove signed-loader layout, verification, both-slots-invalid recovery and
   power-cut behavior off-vehicle. Do not remotely replace the loader.
5. Confirm harness continuity and simultaneous HSCAN/SWCAN routing, then parked
   receive-only capture. Select bus/IDs from evidence; implement only exact
   primary safety additions after separate review.
6. Add optional host arbiter/CLI, authenticated controls, bounded discovery and
   capture; validate process independence and rate/error behavior on bench.
7. Parked staged vehicle validation: identity/status, utilization, replay
   rejection, local ID table, small subscriptions, manual recirc correlation.
   HVAC transmission remains a later separately reviewed experiment.

## Acceptance commands

```
.venv/bin/python -m pytest tools/volt_gateway -n 0
.venv/bin/ruff check tools/volt_gateway
.venv/bin/python -m openpilot.tools.volt_gateway simulate-update
```

Passing these does not prove MCU resource use, electrical bus health, flashing,
power-loss recovery or driving independence. Remaining physical gates are not
waived by the user's conditional approval to flash after backup.

## Validation result — 2026-09-16

161 offline tests passed; Ruff passed; archived scenario report matches the
current simulator. See [simulation evidence](evidence/volt-gateway/transfer-simulation-20260916.json).
An 8-KiB synthetic transfer completed in 34.080 s on the quiet/60%-loaded
fixtures, and 45.594 s with the congestion fixture. The hard ceiling dominates
both steady-load cases. Lost ACKs replayed cached responses with exactly 32
chunk effects, not duplicate writes. Persistent ACK loss exhausted three retries;
power loss, session expiry and reconnect aborted. Those are model outcomes only.

During that earlier increment the Windows preflight was run against the USB device; no
mode transition, driver install, backup, firmware build for deployment, flashing
or vehicle test was performed during this increment. Original application
remained present. The subsequent USB/IP setup above supersedes its host-tool
blocker. The subsequent full binary backup is complete as recorded above;
restoration/recovery validation remains outstanding.
