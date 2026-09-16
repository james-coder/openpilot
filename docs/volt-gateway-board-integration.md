# Integrated board firmware — 2026-09-16

This supersedes earlier component-only status, without superseding physical
release gates or claiming vehicle validation.

Final clean validation: **1590 passed, zero failures/skips**; lint, ARM/native
crypto/MCUboot, all three full board links and 19 selected GCC analyses passed.
See [hashed validation evidence](evidence/volt-gateway/complete-board-20260916.json).
This is distinct from the [actual RAM-only hardware probe](evidence/volt-gateway/ram-probe-20260916.json).

An independent pin-map review corrected CAN1 PB8/PB9 to **AF8** for F413
(not AF9 used by other STM32 families). The historical White STM32F4 branch
and [ST DS11581, table 11](https://www.st.com/resource/en/datasheet/stm32f413rh.pdf)
agree. Tests now assert all selected CAN pins against a separate explicit map.

## Complete composition

`board_build.py` links the actual loader plus separate slot-A/slot-B
applications. Each includes board startup, watchdog, CAN, entropy, crypto,
safety sampling, storage and the command stack. CPU tests start at the loader
reset vector with poisoned SRAM and execute signed boot, MSP/VTOR handoff,
application revalidation and confirmation. Confirmation follows mandatory
application/session initialization. Outputs are `NOT_RELEASED`; emulator
harnesses and signing code are excluded from these images.

P-256 verification uses Mbed TLS restartable operations with bounded CAN and
watchdog work between slices. Flash-busy service stays in RAM. Maximum crypto
slice latency still requires silicon measurement.

`white_safety.c` reads PC2 supply voltage using an explicitly provisioned White
divider and decodes verified primary-HSCAN speed, Park and RUN broadcasts.
Updates require fresh samples and five seconds of stable 12.5–15.5 V supply.
OEM broadcasts are operational interlocks, **not authenticated evidence**
against a malicious CAN peer. Confirmed images may boot read-only on USB power
or while moving; trial metadata writes require the physical interlock.

## Observation and control

`application.c` implements authenticated info/status, bus statistics, bounded
ID discovery/pages, subscriptions and capture/pages. Logical bus **3 means
SWCAN**, independent of whether CAN2 or CAN3 supplies its controller; 0–2 name
available high-speed controllers. Three eight-byte tunnel frames carry one
observation. A 100-ms assembly limit, stale-record dropping, coalescing and
control priority bound work. Unsigned observations are for UI/logging/reverse
engineering only. There are **no permitted vehicle-control TX messages**.

The pinned historical White USB driver has explicit boundedness patches and
none of its old unrestricted callbacks. `device_cli.py` refuses original Panda
firmware and authenticates the same command protocol. USB ownership is latched
until reset; it cannot inherit a CAN session. There is no raw CAN/flash USB API.
Sessions expire after 20 seconds without authenticated activity, or at their
hard one-hour limit. Telemetry requires refreshed ten-second liveness.

`provisioning.py` creates owner-only, never-Git artifacts for the first
USB-commanded **all-CAN-listen-only** profile. It requires an existing public
signing key and explicit controller/divider choices. It has no hardware access.

`bench_image.py` checks the board ELF origins/load segments and recorded build
digests, then assembles a private 1-MiB initial image using the owner's local
signing key. A starts at version 2 and B at version 1; both initial slots have
confirmed trailers so USB-only first boot performs no metadata writes. The
packager accepts only canonical CAN-listen-only provisioning. Its
`factory-flash.bin` contains the routine pairing secret and is forbidden from
public release packaging/Git; it must never be sent to the comma. The packager
does not flash or waive hardware validation gates. Full-image CPU tests execute
the actual generated image, including fallback to B when A is invalid.

`update_device.py` verifies a public release against the locally trusted key,
unlocks the signing key only in an interactive development terminal, and
transfers independently authorized chunks. Unlocking precedes session creation,
so typing time does not exhaust a live challenge. Exact retries do not repeat
writes. Private signing material never crosses USB/CAN. The tool cannot write
the loader/provisioning and never requests a reboot. The current implementation
uses PROGRAM directly in the application or standalone recovery loader; it
does not claim an intermediate ENTER/reset phase has been deployed.

The loader's ten-second cold USB recovery window precedes CAN enable and
software-watchdog start. Its fixed vendor request may enter ROM; it is absent
from the application/CAN API. ROM mode is for USB-only bench recovery: its
peripheral initialization is outside our vehicle-side safety policy.

## Actual hardware check

The SRAM-only probe established **DEV_ID 0x463, REV_ID 0x1000, 1024 KiB
physical flash and White revision-C strap**. The ROM's generic 1536-KiB
descriptor is not the chip's capacity. The implemented layout is now:

| Region | Address | Size |
| --- | --- | --- |
| Loader | `0x08000000` | 128 KiB |
| Fixed provisioning | `0x08020000` | 128 KiB |
| Application A | `0x08040000` | 384 KiB |
| Application B | `0x080a0000` | 384 KiB |

The conservative 128-KiB SRAM envelope is unchanged. Images have distinct
linked addresses; this is signed direct-XIP/revert, not a single-bit pointer.

RAM probe 09 (`cac28f25fb138a2c19abb6920d6626a0cc43f9495827c8ef5a43f935288cf908`)
successfully enumerated through Windows and WSL as the exact labeled serial.
Its read-only report showed 4767 mV, valid ADC sampling, and all three CAN
controllers held in reset. This confirms startup/clock/USB/ADC operation on
silicon, **not** complete gateway/CAN/update operation. The probe contains no
CAN driver or flash-programming driver and automatically resets after 120 s.

Setup-packet traces exposed a historical USB endpoint-zero defect: Windows
requested 255 bytes of a 64-byte BOS descriptor, which needs a terminating
zero-length packet. The corrected driver supplies it, uses one packet for
zero-length transfers, and advances long descriptors on transfer completion.
Regression coverage executes the actual ARM USB code for these cases.

The labeled device was non-programmatically taken from its original application
through its legacy softloader into ROM DFU and back. ROM vectors read at
`0x1fff0000`: SP `0x20002f50`, reset `0x1fff58cb`, matching the historical White
entry. RDP remains `0xAA`/level 0. No option bytes or flash were written.
The original firmware returned with safety mode 0, ignition flags 0 and zero
reported CAN faults. This checks the **old** recovery path, not the new USB
window on silicon. ROM refused the alternative `0x1ff00000`, flash-size-register
and debug-ID queries. The SRAM-only probe subsequently read the registers
directly without flash or option-byte changes; no readout protection was bypassed.

## Release boundaries

No production signing/pairing keys have been created by the agent. Deployment
requires owner-controlled keys, first-image signing, verified divider/routing,
and validation of the new USB recovery/timing on this board. Hardware loader
write protection is not enabled: update API bounds are not hardware protection.
No physical CAN peer is available; modeled recovery is not physical recovery.

Shared-bus CAN IDs and Tres safety compatibility remain independent gates.
The bench provisioning tool does not select IDs. The adaptive transfer model
is distinct from the driver's conservative 100-frame/s maximum, two-frame burst
and 50% observed-window stop thresholds; it is not deployed adaptive control.
No Panda was flashed. Production comma, Tres and driving software were untouched.

USB/CPU/peripheral simulation does not establish electrical behavior, physical
power-loss recovery, silicon timing, installed wiring or automotive certification.

## Owner-key and initial-image workflow

From an ordinary local terminal in this checkout (not the comma), create the
encrypted owner key once. Enter its passphrase only at the terminal prompt:

```
.venv/bin/python -m tools.volt_gateway.operator keygen --device 370022000651363038363036
```

The resulting private directory must be backed up encrypted off-device with
decryption material kept separately. Do not send its contents or passphrase to
chat. The agent has not generated production secrets or selected a passphrase.

The now-measured revision-C board supports the explicit `8862` divider choice.
For USB-commanded bench discovery, `provisioning --swcan-controller 3
--verified-divider 8862` keeps CAN1/CAN2 available as high-speed listeners and
uses logical bus 3 for SWCAN. This is not a measurement of the installed harness.
Supply the recorded device ID, owner's public DER key and a new private output
directory. This tool does not enable any HSCAN backhaul transmission.

Then `bench_image --build VALIDATED_BOARD_DIRECTORY --provisioning
PRIVATE_PROVISIONING_FILE --output NEW_PRIVATE_DIRECTORY` prompts locally to
sign both application slots. It verifies the matching signing public key and
refuses noncanonical/TX-enabled initial provisioning. Neither command programs
the Panda. Preserve the known original backup before a separately validated
USB-only flash; never use a `NEVER_FLASH` emulator/probe image for that operation.
