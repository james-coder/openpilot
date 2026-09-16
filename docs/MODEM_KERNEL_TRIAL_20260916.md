# Kernel trial: rejected and rolled back

On 2026-09-16 UTC, the user explicitly requested the experimental flash after
the remote pre-userspace recovery limitation was explained. This was a benign
offroad boot/compatibility trial, not hostile-USB validation.

## Image and preparation

- Tooling committed/pushed as `7db665547`; device fast-forwarded using an exact
  Git bundle after a normal fetch began downloading unrelated history.
- Full kernel/three-DTB build succeeded. Image.gz-dtb SHA256:
  `7220ce806bd91ae3fb4bb1cdf4ec7d00ddc03fcb4785073f07494ddc09594310`.
- Signed candidate image SHA256:
  `ed717a16e1fdf704a8af7072f6b9caaf04bf86b8ed1873f649431a3701c29563`.
- Expected full boot_a trial SHA256:
  `bcbb16ba48036ea1e98c18d6c6923c40da5dfb2e0904eaaa3b705db43d25a762`.
- Baseline and candidate signatures verified. Preserved current Bluetooth-enabled
  full boot_a backup locally and in root-owned device storage; boot_b untouched.
- 14 mocked rollback tests and Ruff passed. A separate systemd timer invoked
  the callback before flashing and cancelled the unwritten trial without
  rebooting or changing either partition. This was not a simulated kernel hang.
- Fresh offroad telemetry gates passed before installation, flash and reboot.
  Full flashed-partition readback matched; root filesystem returned read-only.

## Actual result

Candidate booted: `4.9.103+ #3 SMP PREEMPT Tue Sep 15 22:48:44 MDT 2026`.
Wi-Fi SSH returned, UI and expected offroad manager processes were running,
Panda reported no faults with ignition/controls false, and Bluetooth hci0
existed. This did not test driving engagement or BLE data reception.

The DT policy marker was present, authorizer active, and both internal USB
root hubs reported device/interface default authorization zero. However,
their root-hub interfaces themselves were unauthorized and the modem did not
enumerate. No modem serial nodes or ppp0 existed; LTE HTTPS/DNS could not run.
The read-only AT validator failed because the modem lock/device was absent;
DIAG/SIM checks were not reached. **The trial failed compatibility.**

The cause is the interaction between HCD-wide interface default-deny and
`usb_set_configuration()` initializing every interface directly from
`HCD_INTF_AUTHORIZED(hcd)`, including the host-created root hub. Excluding the
root hub from the separate modem profile predicate was insufficient. Existing
policy-helper/fake-sysfs tests did not exercise actual hub initialization and
therefore missed this. An active authorizer service alone is not proof that
the modem exists or works.

Invoked the guarded rollback explicitly. It restored and verified the full
Bluetooth-enabled baseline, checked boot_b unchanged, rechecked offroad state,
and requested reboot. No relaxed USB authorization or global watchdog bypass
was used to make the failed trial appear successful.

## Required before another trial

- Correct root-hub interface initialization using host-owned topology, not
  peripheral-provided class/VID/PID. Downstream hubs and functions must remain
  default-denied; do not exempt arbitrary devices advertising hub class.
- Add an integration regression that boots/probes the protected root hub,
  discovers a downstream device, and verifies it remains unauthorized until
  explicitly approved. Policy-helper tests alone do not cover this path.
- Complete the outstanding disposable hostile-USB tests and retain an honest
  distinction between bench tests, parked compatibility, and driving tests.
- Preserve the known-good image and remote-recovery limitation. The timer
  cannot recover a pre-userspace hang, or reboot without fresh safe telemetry.

This candidate must not be described as deployed hardening. Broader stable-fix
provenance, sustained GPS and cold-boot validation remain open.

## Verified after rollback

- Running known-good `4.9.103+ #1 SMP PREEMPT Mon Sep 14 16:07:10 MDT 2026`.
- Full boot_a hash again matches `3ade1f5da238dc4747827db5204f1dcfc0f53a6d418f50bdd47569dfd09504b1`;
  boot_b remains `bf0dd9ff2393131dfa7c6dac40af7ae709a858f588755076502e4b9996b6afd1`.
- Modem connected, zero recovery retries; LTE-bound HTTPS returned 200 and
  uncached ppp0-bound DNS succeeded.
- All five read-only AT/legacy-parity queries passed; DIAG range response
  passed (75 bytes); SIM profile read passed (one enabled profile, no changes).
- Fresh offroad telemetry, Panda tres with no faults, controls disallowed,
  no missing expected manager processes, and Bluetooth hci0 present.
- Disabled the trial rollback timer after verifying the restored baseline.
  Root-owned images/tooling remain for evidence; no trial accepted/confirmed.
