# Update-to-boot integration — off-device only

Validation: **1,232 passed, zero failed/skipped**; lint, Cortex-M4 core builds,
native/ARM crypto and loader builds, and selected GCC analysis passed.
[Evidence](evidence/volt-gateway/update-boot-integration-20260916.json).

The C updater now has an integration test against the actual pinned MCUboot
loader, not only the illustrative Python boot model. A test-only storage adapter
connects their flash views. Host callbacks still implement transport/environment
and outer signature/hash services; MCUboot performs inner-image verification
using its real Mbed TLS backend. This is not a complete board image.

## Implemented behavior

- `vgw_boot_validate_candidate` checks inner signature, target/layout, slot-local
  link address, vector bounds, exact image length/padding and manifest version.
  It cannot write flash or select an application.
- `vgw_boot_commit_candidate` repeats validation, requires an erased trailer,
  then writes and reads back trial magic last. It refuses the selected slot.
  Recovery into either slot is allowed after boot selection found no image.
- These are **trusted internal APIs**, not new CAN commands. The caller must
  enforce PROGRAM authorization, fresh safe power/offroad conditions and the
  complete outer-image readback hash. The production adapter is still pending.
- The native integration fixture exercises the C authorization/updater followed
  by this commit API, real trial selection, confirmation and revert.
- Candidate versions use `0.0.0+uint32`. Integration exposed that MCUboot ignored
  the build number by default. `MCUBOOT_VERSION_CMP_USE_BUILD_NUMBER` now makes
  those versions participate in selection; tests cover both slots.

Signing an older payload with a deliberately higher release version remains a
possible authorized recovery strategy. There is no irreversible anti-rollback
counter. A lower/equal release version must not be assumed to displace a higher
confirmed image; operator tooling must address that before deployment.

## Fault coverage

The new tests cover both-invalid recovery, inactive-slot updates in both
directions, duplicate chunks, invalid inner signatures despite a valid outer
signature, wrong version/slot, truncated/extra data, uncommitted partial images,
and failure at every modeled update mutation: five erases, four programming
chunks and the final marker write. Faults include before-write failure, partial
write, false-success and modeled power cuts before/after mutation.

An acknowledgement can be lost after the marker is completely written. The
device may then boot a verified trial even though the host observed failure.
The host must query boot state after reconnect rather than interpreting a failed
transfer response as proof that nothing changed. An unconfirmed trial reverts;
with no fallback it returns to no-image recovery.

## F413 flash driver candidate

`white_flash.c` implements bounded single-slot erase/program/readback and exact
MCUboot metadata writes, without option-byte or mass-erase operations. The active
image can be opened metadata-only. Busy paths are placed in `.ramfunc.vgw_flash`;
ARM initialization checks that callbacks and state are SRAM-resident. This does
not establish transitive callback/vector/linker placement, which still needs a
complete-image audit. Register-model tests are not physical flash validation.

The ARM flash-driver object is 1,828 bytes of code/constants; its largest local
stack frame is 552 bytes, not a whole-call-chain stack bound. Relocation inspection
shows a `.rodata` reference in the RAM section (the pre-program metadata check),
so a RAM section attribute alone must not be treated as proof of flash-independent
execution. Full linked-image placement and busy-path reachability remain gates.

Cache/prefetch handling, lock/readback failures and frozen-clock/busy timeouts
fail closed. The test-only `VGW_FLASH_TEST` escape from a fatal callback must
never be enabled on the board. Program/erase timeout budgets are provisional,
not measured worst-case timings. A hardware erase already in progress cannot be
cancelled just because update permission expires.

## Remaining release gates

1. Bind the trusted updater, loader and flash driver in one production image,
   with fresh permission checked immediately before persistent mutations.
2. Implement/audit clocks, RAM-safe service paths, hardware watchdog, entropy,
   reset handoff and actual CAN recovery; prove loader independence from apps.
3. Prove full-image memory fit, flash-stall behavior, interrupt/RX servicing and
   physical power-loss recovery. Native storage tests do not establish these.
4. Provision fixed recovery transport/identity/keys without putting secrets in
   source or logs; resolve harness/bus/ID choices before vehicle transmission.
5. Bench flash only the complete gated image. Then perform parked receive-only
   vehicle validation. No production Tres changes are included.

The original firmware may be replaced when these gates pass; preservation is no
longer a blocker. Backups remain intact. No Panda was flashed for these tests.
