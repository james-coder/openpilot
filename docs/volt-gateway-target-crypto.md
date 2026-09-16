# White Panda target crypto integration

Final validation: **686 tests passed, zero failed/skipped**, with lint, ARM/native
builds, ARM byte reproducibility and selected GCC static analysis passing.
[Evidence](evidence/volt-gateway/target-crypto-20260916.json).
The combined harness uses 22,596 bytes text, 8 data and 60,656 BSS; no complete
bootloader, board drivers or runtime stack/interrupt budget is included.

## Implementation

The gateway now has a C adapter using pinned **Mbed TLS 3.6.7**, independently
of MCUboot's older simulation dependency. The upstream
[release and checksum](https://github.com/Mbed-TLS/mbedtls/releases/tag/mbedtls-3.6.7)
identify the official 5.2-MiB archive, SHA256
`a7e8bcbec0e6f761b4af24f25677626b35f762f68eef79c08677a363212d11f6`.
That release includes security fixes, including ECC-related fixes; this does
not mean every upstream advisory affected our subset.

Every build verifies the digest and extracts fresh headers/library sources.
Tests never download dependencies or modify the existing upstream checkout.
Reports retain source/header/object hashes, compiler version, stack-usage files,
map, image hash and sizes. Retain the upstream licenses with redistributed code.

The adapter supplies:

- P-256/SHA256 verification for existing manifest/operator domains, including
  their terminating NUL bytes. Signatures are fixed 64-byte `r || s`; the trusted
  public key is a validated 65-byte uncompressed SEC1 point.
- SHA256 for manifests and streaming received-image/readback verification.
- HMAC-SHA256 with a 32-byte key and messages bounded to 512 bytes.
- A **24-KiB static allocation arena**, no fallback to libc heap. Exhaustion
  rejects the operation; repeated verification tests exercise allocation reuse.
- Rejection of malformed/off-curve keys, invalid scalars, bad signatures/domains/
  lengths, invalid stream transitions and oversized input. An internal failure
  through the legacy void hash callback latches verification closed. Invalid
  peer signatures do not permanently disable the verifier.

Only P-256, SHA256/HMAC and prerequisites are enabled. No TLS, X.509, PEM,
filesystem, sockets or entropy fallback. ARM section garbage collection removes
private-key signing/keygen code; the builder rejects unexpected signing,
network/parser or old host-trap symbols. ASN.1 prerequisites are enabled for
upstream ECDSA configuration, but the wire adapter does not parse ASN.1.

There is no private firmware-signing key in this backend. All test credentials
are disposable fixtures. No production keys were generated/provisioned.

## Test execution

The Cortex-M4 harness now executes this backend's actual ARM instructions with
**no host cryptographic interception**. The host supplies public fixtures and
observes completion, not cryptographic answers. Cases cover an authorized update,
bad operator signature, bad image signature and wrong verification key, plus an
HMAC known-answer test. Inputs occupy a read-only page separate from the stack.
The old host-hook harness remains a separate test layer.

Execution has instruction/wall-time limits; exceeding either fails. The initial
100-million-instruction allowance did not cover two software verifications;
the bounded allowance is now 300 million. Neither that limit nor host wall time
measures physical Panda latency. No limit failure was relabeled as a pass.

Native update integration uses direct C verification/hash function pointers. It
tests framed updates, lost replies, corruption, delay, reordering, duplicates,
burst loss, separate application/loader authority, all 81 modeled persistent
update cut positions and trial confirm/revert under nine transport cases.
**Storage and boot selection are still models**, not actual flash or executed
application startup. GCC analysis also runs on the native adapter compilation.

## Reproduction

Download the official archive explicitly or use the preserved local copy. Set
`VOLTGW_MBEDTLS_ARCHIVE` to its absolute path; the default location is
`~/.cache/voltgw/mbedtls-3.6.7.tar.bz2`. Missing dependency means explicit skipped
tests and an **incomplete**, not passing, unified gate.

```
python -m tools.volt_gateway.target_crypto --archive /path/to/mbedtls-3.6.7.tar.bz2 --output NEW_DIRECTORY
python -m tools.volt_gateway.target_crypto --archive /path/to/mbedtls-3.6.7.tar.bz2 --output OTHER_NEW_DIRECTORY --native
python -m pytest tools/volt_gateway/test_target_crypto.py -n 0
python -m tools.volt_gateway.validate --output NEW_VALIDATION_DIRECTORY
```

These commands never open USB, sign production firmware or flash. The ELF remains
**NEVER_FLASH**: no board startup/vector table, peripheral drivers, physical
flash layout, trusted boot/recovery or provisioned transport IDs.

## Remaining board obligations

- Confirm identity/geometry, link real loader/application, select sector-aligned
  A/B storage and validate physical confirmation/revert/recovery.
- Implement healthy hardware entropy; fixed test sessions/nonces are not a RNG.
- The backend is synchronous, single-owner and not ISR-safe. Measure actual
  verification latency; ensure CAN RX, clocks and watchdog continue to run.
- Wire fatal errors to optional-TX shutdown/watchdog. The panic loop is only a
  fail-stop primitive, not a board fault handler or electrical-silence proof.
- Budget whole-firmware memory and nested interrupts. Arena bounds and
  per-function stack reports alone cannot establish the complete requirement.
- Integrate actual authenticated command/session handling. A tested HMAC
  primitive does not itself implement the target protocol dispatcher.

A read-only query on 2026-09-16 confirmed the original EON firmware, White type
01, serial `370022000651363038363036`, reported silent safety mode, zero reported
faults/CAN errors and ignition flags off. This is not an electrical test.
No Panda flash or production comma/Tres configuration changed.
