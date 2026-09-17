# Bench debugging

True ICE/SWD halt, single-step and hardware breakpoints require a physically
connected debug probe. USB memory access is not an equivalent CPU debugger.
The current tool provides arbitrary **aligned 32-bit address reads and writes**.
Use ELF symbols/DWARF to locate variables; RAM probe builds retain `-g3` symbols.

## Deliberately unsafe DEBUG memory monitor

Build `ram_probe --debug` to define `DEBUG=1`. The monitor refuses compilation
unless `VGW_RAM_PROBE` is also defined. Normal board builds have neither the
memory implementation nor its USB dispatch. Board-build and first-image
packaging reject `vgw_debug_*` symbols. The monitor is RAM-loaded, not flashed;
it uses the CAN-disabled probe and cannot be combined with `--listen` or
`--recovery`. No signing/provisioning secrets are loaded by the probe.

This is an **unauthenticated local debug backdoor**, intentionally excluded
from installed firmware. Only use with the vehicle connector physically
disconnected. Arbitrary writes can override any software safety restriction,
enable peripherals, damage flash/protection configuration or make the device
unrecoverable. A bad read can fault/reset the CPU; peripheral reads can have
side effects. No fault containment or successful recovery is promised for
arbitrary addresses. Do not access option bytes, flash programming controls,
OTP, secret regions or CAN transmit registers during ordinary diagnosis.

The host verifies exact device serial and `voltgw-DEBUG-MEM-v1` before every
operation. There is no scanning, recursive dump, automatic retry or CAN endpoint.
Do not dump flash/RAM indiscriminately: installed flash still contains the
pairing credential even though the debug probe itself has no keys.

```
.venv/bin/python -m tools.volt_gateway.debug_memory --usb-only-bench 0xe0042000
```

`--write VALUE` performs one 32-bit write. The response acknowledges the issued
write; it is not an automatic readback (which could itself have side effects).
Readback, if appropriate for that particular address, must be explicit.
The monitor normally resets after 120 seconds; writes affecting clocks, reset,
USB or RAM can disrupt that behavior. The usual one-shot RGB introduction and
blue heartbeat indicate the probe, not production readiness.

## Wire format

USB bulk endpoint 2 accepts one 16-byte little-endian request:
`VGM1`, opcode (`0` read / `1` write), address, value.
Endpoint `0x81` returns `VGR1`, status (`0` accepted / `1` malformed), echoed
address, read value or requested write value. Only one reply is buffered;
additional requests while a reply is pending are dropped, never queued.
Malformed lengths/opcodes/alignment do not access memory.

## Actual bench evidence, 2026-09-16

RAM debug image SHA-256:
`7568035b0296cbd5f157353ab2939e99fd2b3134dafa56b77c4a60e90317f7e8`.
It was loaded through ROM DFU into SRAM, not flash. The USB tool read
`0xe0042000 = 0x10006463`. A scratch word at `0x2001c400` was saved privately,
written with a known pattern, read back, restored, and checked again.
Original scratch contents were not printed. No option-byte, flash, CAN or
other peripheral writes were used for this validation.
The initial DEBUG/parser/build-exclusion plus passive-probe suite passed
26 tests. Separate full-board exclusion tests check that the DEBUG symbol
family is absent and its write command cannot modify RAM in a normal image.
The combined updated DEBUG, normal-board execution and initial-image packaging
suite subsequently passed **39 tests, none skipped**, with lint passing. After
the physical memory test, the probe timed out and the installed loader's USB
recovery command returned the device to ROM DFU. The outstanding CAN startup
failure is not resolved by these debugging-tool tests.
