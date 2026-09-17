# White Panda inventory

## Actual flash-size correction — later 2026-09-16 RAM probe

The labeled device executed a volatile SRAM-only probe that read DBGMCU DEV_ID
`0x463` and the actual flash-size register **1024 KiB**. Its preserved diagnostic
marker was `0x14630400` (tag `0x10000000`, DEV_ID shifted 16, size in KiB).
The earlier ROM DFU descriptor advertised 1536 KiB: that describes the ROM's
family-wide address map, **not this chip's installed flash capacity**.

This supersedes the earlier 1.5-MiB assumption below. Do not program beyond
`0x080fffff`. Both original 1.5-MiB readback files remain preserved unchanged;
their extra 512 KiB must not be used as evidence of physical flash. The first
1 MiB of the first backup has SHA-256
`b34e88c141a128ad6505fb88260c158da9df59fdfec4b7a75c3a155638ff6279`.
No flash or option bytes were written during the probe. Power/reset returns to
the original firmware. The gateway is being linked for two 384-KiB A/B slots,
with the existing 128-KiB loader and 128-KiB provisioning regions retained.

The subsequent successful USB probe read raw DBGMCU_IDCODE `0x10006463`
(DEV_ID `0x463`, REV_ID `0x1000`) and the historical PA13 revision-C strap.
It enumerated through Windows/WSL and reported valid 4767-mV supply sampling.
See [hardware evidence](evidence/volt-gateway/ram-probe-20260916.json).
Earlier unknown-revision/geometry statements below are retained as history,
not current findings. The package marking/suffix remains unobserved.

## Follow-up chip/protection inspection — 2026-09-16

Owner reconfirmed the labeled unit is USB-only, disconnected from any vehicle.
ROM DFU option-byte reads succeeded; matching copies/complements give RDP byte
`0xAA` (level 0). Raw option bytes were preserved without writing them. The ROM
rejected the flash-size-register and DBGMCU_IDCODE addresses; no bypass was
attempted. The main-flash descriptor still reports 1.5 MiB. Original application
was restored by a non-programming jump and its serial/version/silent mode checked.
See [evidence](evidence/volt-gateway/chip-inspection-20260916.json).

The owner cannot open the glued enclosure and explicitly approved using the
historical chip assumption. The recovered forwarding branch's `board/Makefile`
at `4e85803018ba8cfe20d7e1eb47bc7171ee38faf4` specifies `STM32F413xx` and the
F413 startup file. Proceed with that **candidate**, retaining an on-target
family/flash-size check and recording the unknown silicon revision. Do not
represent the build target as a physical chip-ID measurement. Earlier unknown
numeric RDP statements below are superseded by this read.

## Labeled unit — do not conflate with the second Panda

Label (owner confirmed): `VOLT CAN FORWARDING`; USB-only during discovery.
Serial `370022000651363038363036`; VID/PID `BBAA:DDCC`; hardware query type `01`
(White Panda); application `v1.7.3-EON-unknown-RELEASE`. Application version was
read successfully again after the owner's USB reconnect. Windows preflight
finds it present with status OK and WINUSB service. No flash has been written.

Public raw evidence: [inventory](evidence/volt-gateway/inventory-20260916.json).
That file describes the initial read-only pass; the later softloader-entry and
reconnect incident is separately recorded in [adaptive transfer](volt-gateway-adaptive-transfer.md).

ROM DFU reports 1,572,864 bytes (1.5 MiB) of main flash; two complete reads succeeded.
Unknown: exact PCB revision, STM32 suffix/revision, numeric RDP setting,
installed bootstub provenance, current forwarding/mux configuration, populated
ESP/PHY part numbers. The second Panda's identity is unknown.

Historical F413 source is NOT a measurement of this MCU. Candidate family sizes
are 1 MiB/1.5 MiB flash and 320 KiB SRAM; the historical linker used only
128 KiB flash/128 KiB RAM. Its app starts at `0x08004000` after a 16-KiB stub.
Do not select a new linker layout until geometry is verified.

## Preservation gate

Two complete identical main-flash backups are now preserved privately at
`/home/james/diagnostics/volt-gateway/backups/370022000651363038363036-20260916`.
SHA-256: `5b51369e41ce51c80129d4089c7499e6d0c671e1914d130856fef1bee586e5e9`.
Boot vectors passed plausibility checks; application digest and signature match
the earlier application-mode reads. A historical rebuild did not match that digest.
See [readback evidence](evidence/volt-gateway/readback-success-20260916.json).
OTP and option bytes are not included; never write them during inspection.
Treat full dumps as potentially secret-bearing. No RDP bypass/unprotect/erase.

The owner selected USB/IP to WSL instead of Windows CubeProgrammer. Direct Linux
identity reads now work as the normal user; see [USB/IP evidence](evidence/volt-gateway/usbipd-20260916.json)
and [implementation plan](volt-gateway-implementation-plan.md).
An offline verifier exists in `tools/volt_gateway/backup.py`; it cannot authorize
flashing or prove a restoration works. Restoration on a spare remains a separate
gate. Do not use current `PandaDFU.recover()` as an inspection operation.

The successful route used a small native WinUSB helper to catch the old
softloader after 552 ms, enter ROM DFU, then attach ROM DFU to WSL for two
`dfu-util` uploads. A non-programming DFU jump returned to the original bootstub.
Live USB inspection then confirmed the original serial, type 01 and application
version. No erase, programming, protection change or CAN transmission occurred.
Returning to the existing application is not a tested restoration from backup.
