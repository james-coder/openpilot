# Off-device candidate series — NOT production approved

Base: vendor `c368754c26c7b9659de187addc6cccedc6cfb0a0`.
Patches are unmodified `git format-patch` exports from upstream 4.9 stable,
retaining original authorship and upstream commit references.

For the stable-only series, apply only 0001–0003 in filename order. 0002 follows 0001 in the same BOS parser and its
context includes 0001's minimum-header validation. 0003 is the separate
descriptor sysfs locking change. 0004 is retained for audit history, NOT in the
candidate series: vendor commit `c8473833537b11c54042e51addd3af73f09f7f4b`
reverted the dereference introduced by `48701a8f4adbaa04a046edb68871b76f809fd57b`.
That revert is an ancestor of the pinned base. The vulnerable
`dev->udev->slot_id = 0` operation is absent from `xhci_free_virt_device()`.
This corrects the earlier classification based on clean patch application.
All four applied in sequence to the exact vendor versions of the affected files
(verified with `git apply --check` followed by apply in a disposable extracted
tree). This is **source applicability only**, not a compile/boot/security test.

| Patch | Classification at pinned base | Reachability / remaining review |
| --- | --- | --- |
| 0001 | genuinely missing | BOS header parsing before interface authorization |
| 0002 | genuinely missing; sequence after 0001 | changed BOS descriptors on reset, before interface policy |
| 0003 | genuinely missing | concurrent sysfs descriptor reads; lifetime/locking review required |
| 0004 | not relevant to this pinned code path | prerequisite dereference was reverted; other xHCI lifetime paths remain under review |

These candidates are not the complete security-fix set. No claim is made that the
other 3957 inventory candidates are absent, irrelevant, or covered. Full
prerequisite/follow-up review, semantic equivalence audit, cross-compilation,
sanitizer/VM testing, and target compatibility remain open. The dirty
Bluetooth-enabled kernel checkout and production device were not modified.

## Complete off-device candidate

`0100-complete-offdevice-candidate.patch` is an ALTERNATIVE complete diff
against the pinned vendor base, not another patch to apply after 0001–0003.
It includes the known-good local Bluetooth changes, 0001–0003, and the
experimental physical-modem-host policy. Do not apply 0004.

The policy denies devices/interfaces on the DT-marked internal controller
before enumeration, pins permitted driver names, checks the exact captured
descriptor profile and direct physical port, suppresses interface driver
autoload aliases, blocks usbfs ioctls on this host, and forces changed/reset
devices through re-enumeration. These are proposed defenses, not tested
production guarantees. The external controller is excluded. The original
root-hub exclusion was incomplete; see the correction below.

Cross-compiled USB core, xHCI object and tizi DT; native C policy logic and
Python sysfs fixtures passed. This does not test USB enumeration, probe,
reset, suspend/resume or hostile traffic on a running candidate kernel.
The full Image.gz subsequently compiled and linked successfully; exact
configuration is saved in candidate.config and build hashes in
docs/MODEM_LTE_AND_KERNEL_FOLLOWUP.md. Modern disposable gadget/VM tests,
boot and hardware compatibility remain deployment gates. No candidate was
flashed at that stage. Compile success is not evidence that hostile USB is contained.

## Root-hub correction after failed parked trial

The first candidate was subsequently flashed, failed modem compatibility,
and was rolled back to the verified Bluetooth baseline. See
`docs/MODEM_KERNEL_TRIAL_20260916.md`. The host-created root-hub interface
inherited interface-default-deny, so its hub driver could not enumerate the
modem. Testing the separate profile predicate had missed this initialization.

Apply `0101-protected-root-hub-interface.patch` AFTER the complete 0100 patch.
It exempts only the parentless host-created root on the protected controller
from interface default-deny, preserving policy on downstream devices and all
other controllers. It does not exempt peripheral-supplied hub class IDs.
The regression compiles the actual assignment from `usb_set_configuration`
and checks root/direct-child/behind-hub topology with both controller policy
and default authorization states. This is not live USB enumeration testing.

## Next parser-only candidate (not deployed)

Apply 0102 then 0103 **after 0100+0101**, not after the standalone 0001–0003
series. 0102 is the unmodified stable duplicate-endpoint patch; 0103 preserves
the upstream one-line wMaxPacketSize correction with vendor-specific context.
The review/0005 and review/0006 exports remain audit references; do not apply
both those and 0102/0103.

The original deployed source/build and Bluetooth rollback images are untouched.
Source-derived regressions and ARM64 config.o cross-compilation passed; no full
kernel image, flash or hardware test was performed for this new candidate.
See docs/MODEM_PARSER_CANDIDATE_20260916.md for provenance, compatibility
follow-ups, reproducible tests and remaining gates.
