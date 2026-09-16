# Board/release integration progress — not a flashable release

Offline regression: **1,097 passed, zero failed/skipped**, with lint, ARM
builds and selected GCC analysis passing. This includes 27 image-packaging,
five chip-inspection and 19 MMIO tests. See
[validation evidence](evidence/volt-gateway/board-release-20260916.json).

## Approved five-step implementation status

1. **Hardware identification:** exact Panda and its 1.5-MiB ROM flash geometry
   rechecked; numeric RDP level 0 now read. Chip-ID register access is rejected
   by ROM. Owner approved using the recovered F413 historical build target
   because the enclosure cannot be opened. Exact revision remains unknown.
2. **Board startup/flash:** White-specific early quiescence and runtime identity
   primitives implemented/tested, but not integrated into actual reset startup.
   Real flash operations, clock/watchdog/RNG integration and application handoff
   remain unfinished. There is no claim of a complete board firmware here.
3. **Standalone CAN recovery:** remains unfinished. Existing authority/update
   cores are not a CAN driver or independently bootable recovery service. No
   production recovery IDs or provisioning material have been installed.
4. **Signed-image pipeline:** implemented inner MCUboot image construction,
   strict verification and outer signed release packaging; tested against the
   actual native C loader. Target updater integration still must verify the
   inner image and commit the trailer last.
5. **Complete-image validation and flashing:** not performed. The original
   labeled Panda remains unchanged in flash. Existing offline tests cannot
   justify replacing its bootstub with an incomplete recovery implementation.

## Read-only chip inspection

`chip_inspect.py` exposes only three fixed reads: flash-size register,
DBGMCU_IDCODE and option bytes. It requires the exact previously verified ROM
DFU serial. Its only DFU download payload is SET_ADDRESS; it has no erase,
unprotect, option-byte programming or arbitrary-address API. ROM transaction
errors are cleared, not circumvented. The device returned a 104-ms pointer
operation poll interval, supported by a bounded one-second maximum and ten
polls. The first attempt's 100-ms bound was too strict and was corrected.

System/peripheral reads were refused; option-byte reads succeeded. USB device
feature bytes `00ffffff` were also preserved but **not interpreted as chip ID**.
The `dfu-util` descriptor-bounded size-register attempt did not read memory;
the subsequent fixed-address ROM request was explicitly rejected by ROM.

DFU read/pointer semantics follow [ST AN3156](https://www.st.com/resource/en/application_note/cd00264379.pdf).
The helper's scope is inspection only; no user action needs to bypass protection.

## White-specific early safe state

`firmware/white_board.c` uses trusted MMIO callbacks, never addresses received
over CAN. Register and pin definitions come from the preserved historical
`board/inc/stm32f413xx.h` and `board/boards/white.h`.

It preloads inactive output levels, disables all three HSCAN transceivers and
ESP/GPS power controls, leaves LEDs off, holds CAN1/2/3 in peripheral reset,
and makes shared CAN2/CAN3/GMLAN mux pins inputs. USB PA11/12 and debug PA13/14
are untouched. Register readback validates the expected output levels, modes,
pulls and reset state. This is **not electrical proof of TX silence**.

The identity primitive reads device/revision and flash-size registers while
running on the MCU. It accepts family ID `0x463` with 1536 KiB as the candidate;
revision/errata policy remains to be integrated. None of these functions have
been run on the attached Panda, and no production MMIO binding is installed.

## Inner and outer signed images

`boot_image.py` accepts a raw payload already linked for slot A or B. It checks
the initial stack/reset vectors, builds the exact MCUboot header and protected
device/layout TLV understood by the C boot port, and signs it with P-256.
Versions are encoded as `0.0.0+version`, retaining the full uint32 version.
The signing key also signs the existing independently verified outer manifest.
The routine pairing key is never an input to either signature operation.

No MCUboot trial trailer is distributed in the image; unused slot bytes must
be erased, then the trusted updater writes the trial marker after complete
readback/hash/inner-signature validation. Download completion alone is not a
commit. A slot A binary cannot be relabeled as slot B by the tool.

The existing local interactive signer gains `boot-sign --slot A|B`; it emits a
public release archive. It requires the encrypted local private key and a
terminal passphrase, never an environment variable or a secret on the comma.
No production signing or pairing key was generated during this work.

The actual C-loader integration tests use ephemeral keys, insert the trailer
as a simulated trusted commit, boot either packaged slot, confirm and reboot.
Corruption, wrong slot/device/layout/version/key, extra/truncated bytes and
an outer signature wrapping a corrupt inner image are rejected. These test
payloads are not complete hardware firmware and must never be flashed.

## Remaining sequence

Finish reset/clock/watchdog/RNG and SRAM-resident flash servicing with hardware
timeout/error tests; integrate authenticated CAN recovery independently of both
slots; connect actual MCUboot inner verification and last-write trailer commit
to the updater; provision reviewed transport IDs and keys; then produce and
validate the complete board release. Do not change option bytes automatically.
Only after those gates should USB programming and physical A/B/recovery tests
replace the preserved original application.
