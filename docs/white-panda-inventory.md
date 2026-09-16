# White Panda inventory

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
