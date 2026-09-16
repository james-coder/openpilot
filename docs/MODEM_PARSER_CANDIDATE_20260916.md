# Parser-only next candidate — 2026-09-16

Status: off-device, not deployed. Production and Bluetooth rollback images
remain unchanged. This pass only listed existing device route directories;
no reset, service/configuration change, active diagnostic command or flash.
The latest route is still 00000076, whose previously inspected initData used
kernel #1. No new ordinary-startup, sustained-GPS or engagement evidence is
available for #4. These remain separate gates, not inferred passes.

## Exact source and dependency chain

Base vendor commit: c368754c26c7b9659de187addc6cccedc6cfb0a0.
Candidate order: archived 0100 (includes Bluetooth/BOS/policy), 0101 (root-hub
correction), **0102 (duplicate endpoints), 0103 (packet-size validation)**.
Only drivers/usb/core/config.c changes in this new pair. Existing deployment
source and build outputs are never patched by the tests.

- Deployed config.c SHA256: `3bb0ac8008652da4c40a9e3aed0533c8093ca6bb41a33e86ff263a96d01b3de9`.
- Patched config.c SHA256: `9d845103c45d330995a763f21c1baf2e129e57be5714beb0b4746638bbc1e816`.
- Deployed build .config SHA256: `2c1248037df31728e47d41430b80d51a8ccc3a8e728401af074ca9f85ab049f4`.

The regression reconstructs config.c from the pinned vendor commit and archived
0100 and verifies its deployed-source hash. 0101 does not modify that file.
Each test creates an isolated temporary copy, checks/applies 0102+0103, and
verifies the deployed source stayed unchanged.

## Semantic audit through 4.9.337

| Stable commit | Classification and disposition |
| --- | --- |
| ecaaef6b50a7873d495f9cc69fc4c4edfc792635 | Missing cross-interface/control-direction duplicate detection. Existing same-alt check is present; exact earlier commit ancestry is not required for this semantic finding. Unmodified upstream export becomes 0102. |
| 35531ec82046dc072bb841c452b7cea193af42f3 | Missing raw wMaxPacketSize read. Prerequisite aed9d65ac327 is an ancestor. 0103 adapts context only; retains all bits for existing validation. |
| 994d611bfe38762a30ed60db49af8cdeaf7586dc | Existing BIT(12)/BIT(11) expression is equivalent to this named-mask cleanup; no additional correction required. |
| 135878c0b1e0d3135f72392c44f531322594987c | Vendor lacks the harmful zero-maxpacket-skipping predecessor. Preserve existing zero handling; do not import the regression just to match later patch context. |
| c795e16801aa97668f41a1354632903da5ba3c72 | Later compatibility mechanism: blacklist selected endpoints so a later valid duplicate can win. Not a prerequisite to duplicate rejection; by itself its blacklist is empty. Not included. |
| bab3f154b0e0565c49fbfcf988c68d239c8edcd7 | Uses that mechanism for Hercules audio device 06f8:b000, interface 5 endpoints 01/81. Not applicable to captured EG25 2c7c:0125. Such audio-device compatibility is not promised by this candidate; general USB compatibility still needs review before deployment. |

Reviewed local stable history for config.c from the duplicate fix through
v4.9.337 and the referenced quirk changes. This is a bounded two-fix audit,
not completion of the thousands of remaining stable-fix candidates.

Sources: the original signed-off patch exports and local upstream Git history,
including [duplicate endpoints](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/commit/?id=ecaaef6b50a7873d495f9cc69fc4c4edfc792635)
and [packet-size correction](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/commit/?id=35531ec82046dc072bb841c452b7cea193af42f3).

## Verification

67 tests passed in the combined parser, USB authorization/root-hub/installer,
DIAG, report and evidence-checking suite, with no skips. Ruff passed. This
includes four new parser-candidate tests; it is not target driving validation.

```sh
.venv/bin/python -m pytest -q -n 0 tools/security/test_usb_parser_backports.py
```

- Compiles extracted fixed duplicate-detection helpers and the actual original
  duplicate loop. Checks same-interface duplicates, cross-interface duplicates,
  valid alternate-setting reuse, opposite bulk directions, control endpoints
  in either direction, empty configurations and all 14 captured EG25 endpoints.
- Compiles the actual old/new packet validation blocks and their source tables.
  Checks unchanged ordinary EG25 values, valid HS periodic transaction bits,
  illegal transaction bits on bulk/full-/low-speed endpoints, reserved high
  bits, superspeed bulk and preserved zero handling.
- Fixed implementations pass; the original implementations fail the regression
  vectors. Native C fixtures use undefined-behavior sanitizer with fail-fast,
  Wall/Wextra/Werror, suppressing existing sign-compare/unused-parameter noise.
- Cross-compiles the complete patched config.c with the existing GCC 8.2.1
  ARM64 command and generated configuration/headers. Source, object and
  dependency destinations alone are redirected to temporary artifacts; all
  other flags (including the recorded build's warning suppression) are retained.
  Result is verified as an AArch64 ELF object. No make, relink or image rebuild.

Tests deliberately skip if required local vendor sources/history are absent;
skips must not be reported as validation passes. The extraction harness uses
minimal type/helper stubs and does not execute the whole USB parser pipeline.
Cross-compilation checks real kernel types, but is not runtime compatibility.

## Limits and rollout

No malicious USB input was sent to the comma. No claim of complete USB-core,
xHCI/DWC3, serial/TTY/PPP or legitimate-QMI containment follows from these fixes.
The modern-VM authorizer evidence and vendor root-hub tests remain separate.
Full vendor candidate runtime testing, ordinary operation evidence and a
freshly verified parked-state review remain gates before any subsequent flash.

Additional artifacts are small temporary C sources/objects, well below the
256 MiB budget; C: retained more than 1 GiB free. Original sources, .config,
kernel images and rollback baseline were preserved.
