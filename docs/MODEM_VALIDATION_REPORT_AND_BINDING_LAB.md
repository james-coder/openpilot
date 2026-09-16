# Validation report and stronger USB lab — 2026-09-16

Production remains on kernel #4 and application 10f71636d. This pass made no
device configuration, service, firmware, kernel, or application changes. No
modem reset, active DIAG query, GPS restart, or ignition cycling was requested.
Known-good Bluetooth-enabled rollback images were not modified.

## Offline evidence reporting

`tools/security/modem_validation_report.py` accepts explicit local route-log
segments and passive-observer JSONL. Example from the development checkout:

```sh
.venv/bin/python tools/security/modem_validation_report.py \
  --log /local/route--0/qlog.zst --log /local/route--1/qlog.zst \
  --live-jsonl /local/observation.jsonl --output /local/report.json
```

Keep original route segment directories. Inputs are bounded to 64 MiB each,
including decompressed data; incomplete frames/JSONL are rejected. Output
refuses to overwrite an existing file. It excludes coordinates, VIN, modem
identifiers and arbitrary log payloads. Source/route/boot identities are hashed.
Kernel build, revision and route/boot boundaries prevent older evidence being
silently reused. This branch logs `CurrentBootlog` in initData params rather
than populating bootlogId; both forms are supported.

Each check is pass/fail/not_observed. A pass describes only supplied samples:
it is not proof of all-day reliability or driving safety. Corrupt input prevents
positive claims. GPS continuity requires at least 120 seconds with no >=3-second
sample gaps. Identification requires route-local carParams plus started and
carState evidence, not cached CarParams. Engagement is reported separately.
Cellular connected state is not an HTTPS test; cold boot is never inferred.

The manual passive observer now emits kernel/boot/revision metadata and status
file age. No background service or engagement dependency is added. It remains
bounded, nice 19/SCHED_IDLE, and read-only.

Actual observations this pass:

- A 15-second current-kernel observation supplied 14 healthy manager/Panda
  samples and 15 fresh connected-modem samples. No startup or GPS was observed.
- Only one distinct Aranet write timestamp was observed; this short interval
  does **not** prove recording progression or indicate a fault.
- The newest saved route's inspected segment used kernel #1. The report
  correctly excludes it from kernel #4 validation.
- Fresh ordinary startup, sustained GPS, vehicle identification and actual
  engagement on #4 remain unobserved in this pass. No special cycle is needed;
  collect these during normal use.

## DIAG evidence, not a speculative production fix

Fixtures exercise negative/unrelated opcodes, shared timeout across log frames,
and CRC failure. They demonstrate that current send_recv returns the first
non-log frame, without request correlation. The prior anomalous response remains
unexplained. Production GPS parsing/retry behavior is unchanged. The optional
read-only probe now rechecks offroad state after waiting for manager telemetry;
a regression test verifies refusal if that state changes.

## Actual driver-binding lab

This extends [the initial lab](MODEM_USB_LAB_20260916.md), using the same
disposable Ubuntu 6.8.0-90 VM, dummy HCD and Raw Gadget. It does not assume
Raw Gadget exists in the vendor 4.9 kernel. No physical USB passthrough, network,
shared directory or writable disk is attached.

The new initramfs uses `usb_lab_bindings_init.sh` as /init and
`usb_lab_bindings.py` as /usb_lab_bindings.py. It contains Python 3.10 and its
standard library/runtime dependencies; the unchanged production authorizer is
copied as /authorize.py. Its SHA256 is
`afa972c5cab2b091a41dd35ea6996bb18a79b988a96196d44c54b1d6bd7fdeaf`.
Only the adapter passes a fixed dummy-controller path. Module list and load
order are explicit in the init script. The gadget is compiled statically with
`-Wall -Wextra -Werror -O2`; `--allow-config` supplies fixed control ACK/zero
replies and enables endpoints, not AT/QMI or cellular data emulation.

Using the prior QEMU command with this initramfs:

- Default boot: 10 normal→modified→normal cycles, followed by absent-authorizer,
  crash-after-device-authorization and disconnect-during-authorization cases,
  each with recovery. **36 cases passed.** Normal profiles bind four option
  interfaces and one qmi_wwan, expose four ttyUSB nodes, one cdc-wdm and a wwan
  interface. Repeated authorization is idempotent. Modified profiles stay
  device-unauthorized with no bindings. After authorizer crash, all five
  interfaces remain unauthorized/unbound before recovery.
- Separate boot with `comma_lab_controls=1`: permissive dummy bus, kernel
  configfs functional keyboard, mouse, read-only RAM-backed mass storage and
  CDC Ethernet gadgets. **Four positive controls passed**, binding usbhid,
  usb-storage and cdc_ether. No input events or external packets were sent.
- Both VM runs shut down normally. Logs are committed under tools/security/evidence.

```sh
.venv/bin/python tools/security/check_usb_binding_log.py \
  tools/security/evidence/usb-bindings-20260916.log \
  tools/security/evidence/usb-positive-controls-20260916.log
```

The checker requires all 40 ordered cases and expected bindings, rejects
missing completion markers and kernel/test failures. Mutation fixtures verify
that weakened or incomplete evidence cannot pass. The source-derived vendor
USB-interface initialization test additionally substitutes the former buggy
root-hub expression and verifies that the regression harness fails.

Limits: modified composite profiles remain descriptor-only extra interfaces;
functional class controls are separate gadgets, not full EG25+class emulation.
This does not test QMI data transfer, malicious bulk traffic, malformed BOS,
concurrent lifetime races, or the complete vendor physical-port/driver-pinning
patch. Modern driver-binding evidence plus a source-derived regression is not
equivalent to booting and testing the complete vendor candidate. That remains
a gate before further production kernel changes.

Lab storage increased from about 459 MiB to 537 MiB (under the 256 MiB growth
budget); Windows C: retained about 1.44 GiB free, above the 1 GiB floor.
No additional kernel image was built or flashed in this pass.

Verification: 63 local tests passed across reporting, DIAG fixtures/ownership,
USB policy/installation and evidence checking. Ruff, shell syntax and the
strict C compilation passed. These are in addition to the 40 actual VM cases;
they are not target driving validation.
