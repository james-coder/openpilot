# Kernel flash preflight — 2026-09-16 UTC

The user authorized flashing and finishing validation. Authorization is not
evidence of a passed boot, USB-policy or recovery test.

## Verified without changing production

- Fresh offroad gate passed; deployed Git revision d6b947101, AGNOS 18.3,
  active slot _a, running kernel 4.9.103+.
- Both live boot partitions still match the saved known-good snapshots:
  boot_a 3ade1f5da238dc4747827db5204f1dcfc0f53a6d418f50bdd47569dfd09504b1;
  boot_b bf0dd9ff2393131dfa7c6dac40af7ae709a858f588755076502e4b9996b6afd1.
- Four modem interfaces bind option; the fifth binds qmi_wwan.
- Existing Bluetooth install_boot.py is NOT a suitable installer for this
  trial: it targets active boot_a, pins a superseded pre-Bluetooth image,
  and has no established recovery if the host cannot boot.
- launch_chffrplus.sh calls abctl --set_success during agnos_init, before
  modem/GPS validation. Do not claim A/B boot-success handling will recover
  a kernel that boots but breaks peripheral operation.
- Authorizer service was not installed at initial inspection. Flashing
  default-deny without it would leave the modem's interfaces unauthorized.

## Disposable test environment

Booted the existing Alpine 3.22.5 ISO under QEMU, native x86_64 Linux
6.12.94-0-virt, 768 MiB RAM, no writable disk, no USB/host-device passthrough.
Inside this VM, modprobe dummy_hcd and modprobe raw_gadget both report missing
modules; /sys/class/udc and /dev/raw-gadget are absent. This VM does NOT close
the hostile USB testing gate. No gadget/fuzzing commands ran on the comma.

Available host disk remains about 1.8 GB; building another full test kernel
here requires more space or another test environment. No large downloads or
additional full builds were started. The existing ARM build is not a kernel
that this x86_64 VM can boot.

## Guarded authorizer packaging

Added an operator-only installer, with root-owned path checks, refusal to
overwrite prior installations, unit verification, cleanup on install failure,
and restoration of the read-only root mount. It enables but does NOT start
the service or change USB authorization/boot partitions. The unit now has
ConditionPathExists for the candidate DT marker; on the current known-good
kernel it must be skipped, not enter a failing restart loop.

Installer mocks test normal enable-without-start, prior install/symlink
refusal, non-root/untrusted paths, verification failure, partial enable and
disable failure. These do not prove candidate-kernel USB behavior.

## Still required before calling this complete

- A verified physical recovery path/computer/cable for a pre-network boot
  failure; physical availability was requested, not yet confirmed.
- A suitable disposable modern gadget test kernel/hardware and actual USB
  personality/re-enumeration tests (native C and fake-sysfs tests are not that).
- Correct signed boot-image packaging, current partition verification,
  authorizer installation, explicit rollback procedure and fresh offroad gate.
- Actual target boot/normal modem/GPS/Bluetooth/Panda/UI/process checks;
  subsequent cold boot and sustained GPS, without unnecessary ignition cycles.
- Remaining stable-fix provenance/backports, privilege and tainted-data audit
  items listed in MODEM_LTE_AND_KERNEL_FOLLOWUP.md.

No boot partition, slot selection, Panda safety or running kernel was changed
during the preflight. Do not use this document as a completed flash report.

## Prepared on the comma (not flashed)

Committed/pushed 892ff6471 and fast-forwarded the device under fresh offroad
checks. Installed the root-owned authorizer and enabled its unit. An explicit
start request was correctly SKIPPED: ConditionResult=no, ActiveState=inactive,
SubState=dead. The current kernel has no policy marker, so the authorizer did
not execute or alter USB authorization. Root filesystem returned read-only.
LTE-bound HTTPS returned 200 afterward; no modem restart or reboot was used.

To withdraw preparation on the known-good kernel, disable the newly installed
comma-modem-usb.service; there is no manager/watchdog configuration to undo.
Do not disable the service on a future default-deny kernel and expect LTE/GPS
to work: restoring the known-good kernel is the rollback for that policy.
Physical recovery and hostile-USB testing remain open; neither boot slot has
been written.
