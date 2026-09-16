# Protected root-hub fix and second parked trial

## Correction

Commit `3edcf510a` adds patch 0101 after the archived complete 0100 candidate.
`usb_set_configuration()` now authorizes the host-created parentless root hub
on the protected controller even when interface defaults are denied. The
exception uses host-owned topology, not a peripheral-advertised hub class.
Downstream devices/hubs retain default-deny. Other controllers are unchanged.

The regression compiles the actual initialization assignment extracted from
USB core, testing protected/unprotected controllers, both default states, and
root/direct-child/behind-hub topology. Previous tests covered the independent
profile predicate but missed this initialization. The regression is still
not a live USB enumeration/probe test.

37 focused tests passed; broader parser, recovery, synthetic engagement,
USB-policy, installer and rollback suite: 159 passed. Ruff passed. Full ARM64
kernel/three-DTB build succeeded with the existing compiler/configuration.
The failed trial's root-owned artifacts were archived recoverably, not used
as the new rollback baseline.

## Second image

- Image.gz-dtb SHA256: `01ee9d2f4ff727e3a46cb6fc5d2defe24619e6d68498c27c29805bb5cf414feb`.
- Signed image SHA256: `9734b68021ed8b7fe46217b0bb7261ee757e161f0d65ebfecfa2567666f23fdf`.
- Full boot_a SHA256: `46fd613ff1b1147ad0212d205fc96181fb7b25dbdcb273210882a64efb56cdbd`.
- Running build: `4.9.103+ #4 SMP PREEMPT Tue Sep 15 23:01:32 MDT 2026`.
- Original Bluetooth baseline remains the rollback; boot_b unchanged.

Signature verification and full partition readback passed. Fresh offroad
gates preceded installation, flash and managed reboot; timed rollback armed.

## Live observations after reboot

- Both internal root hubs bind `hub`, authorized=1. Their device and interface
  defaults remain 0. Modem is authorized; interfaces 0–3 bind `option`, 4 binds
  `qmi_wwan`. Authorizer active, no rejection messages observed.
- LTE CONNECTED, no recovery retries; LTE-bound HTTPS 200 and uncached ppp0 DNS
  passed. Wi-Fi SSH remains reachable.
- Five read-only AT queries/legacy parity passed. First DIAG range probe
  returned unexpected opcode 19/17 bytes; two subsequent probes passed
  (opcode 115/75 bytes). The first-query anomaly remains unexplained; a similar
  anomaly was documented before this kernel trial. Do not count it as a pass.
- Read-only SIM profile parsing passed, one enabled profile, no modifications.
- Fresh offroad telemetry, Panda tres/no faults/controls disallowed, no missing
  expected manager processes. Bluetooth hci0 present; Aranet services active
  and recent advertisements/database writes reported by recorder status.
- Root filesystem read-only; no BUG/Oops/WARNING/panic/Call-trace matches in
  the inspected boot dmesg. This is not proof of absence of kernel defects.

## Guarded normal modem restart

The existing once-per-boot, freshly offroad-gated broker restarted lte.service.
USB nodes disappeared at approximately 2 seconds, returned at 17 seconds,
and LTE reached CONNECTED at 24 seconds and remained connected through the
55-second observation. Wi-Fi SSH stayed up. Re-enumeration restored the same
five pinned bindings; both root-hub device/interface defaults remained zero.
LTE HTTPS 200 and uncached cellular DNS passed again.

An initial ad-hoc process-health observer asserted before its subscriptions
had all received initial messages. Rerunning with an explicit bounded initial
message wait passed ten fresh samples: expected manager processes running,
Panda fault-free, ignition/controls false. Do not classify that observer
startup assertion as a verified device/process failure.

After these checks, the guarded confirm operation reverified the image,
untouched second slot, policy marker, authorizer and fresh offroad state.
The corrected trial was confirmed and its rollback timer disabled. The
known-good Bluetooth baseline remains saved locally and root-owned on-device.
This accepts parked compatibility, not completion of the security audit.

## Remaining limits

No actual driving engagement, sustained GPS acquisition, physical cold boot,
malicious USB personality, or fuzz testing was performed in this trial.
USB-core/controller parsing before authorization and legitimate modem driver
traffic remain attack surfaces. Broader stable-fix provenance remains open.
Neither descriptor matching nor these compatibility tests establish that a
compromised modem is safe. Physical recovery is still required for a failure
before userspace can run the guarded rollback.
