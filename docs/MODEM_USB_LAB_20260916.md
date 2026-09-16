# Disposable Raw Gadget lab — 2026-09-16

The prior Alpine virt kernel lacked both required modules. A diskless QEMU VM
now boots Ubuntu 6.8.0-90-generic with Raw Gadget and a separately built dummy
HCD/UDC. No kernel module was loaded on WSL or the comma. No network, writable
disk, shared directory or physical USB passthrough is attached to the VM.

## Reproducibility

Local lab: `/tmp/modem-usb-lab-v2.ZjSWlB`. Packages were downloaded, not installed
on the host, from Ubuntu's apt repositories. Kernel packages share version
6.8.0-90.91~22.04.1; hashes:

| Artifact | SHA256 |
| --- | --- |
| linux-image | 26566b977d86105903d8f4ca8e9d807b4cc7791f2ad9065e634a6dcb687fe129 |
| linux-modules | 45a8de2e4378172fde2579cc69df36df236880b88d2f9ac2b69126b51decbc6d |
| linux-modules-extra | c29bdf993d051e25e3e1b1e978c69e47ad81e3df1c11fd06715333d9be339ce5 |
| arch headers | 98e243b9d24b396381af629d3ee47f30c14da525aec7182928c95aae48862432 |
| common headers | 13361117e0822892511a049afeda590a7eaffc7faf13b376c7fac9a87f5de669 |

The packaged config enables USB_RAW_GADGET=m but not USB_DUMMY_HCD.
Built only upstream Linux v6.8
[dummy_hcd.c](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/gadget/udc/dummy_hcd.c)
against those extracted Ubuntu headers with an external-module Makefile
`obj-m := dummy_hcd.o`. Source SHA256:
6892efeaca7d13e0f8ba172fac6b9607f9ac5c60de1a5f1812a57eb7998fd993.
GCC 12.3 from extracted gcc-12/cpp-12/libgcc-12-dev packages was used; its Ubuntu
packaging revision differs from the original kernel compiler (.3 versus .2).
Module built/loaded successfully; this is a disposable lab, not a production
module recommendation. BTF generation was skipped because no vmlinux existed.

Initramfs contains static BusyBox, udc-core.ko/raw_gadget.ko from the kernel
package, built dummy_hcd.ko, the statically compiled tracked usb_lab_gadget.c,
its expected profile emitted with `--profile`, and tracked usb_lab_init.sh as
executable /init. Build with `gcc -static -Wall -Wextra -Werror -O2`, then
`find . -print0 | cpio --null -o --format=newc | gzip -1` from ramroot.
The C program refuses gadget operations without the VM marker. The init
requires the explicit `comma_disposable_usb_lab=1` kernel command line.

QEMU invocation (paths adapted to extracted QEMU package):

```sh
qemu-system-x86_64 -machine q35,accel=tcg -cpu max -m 768 \
  -nodefaults -no-reboot -nographic -serial stdio -monitor none -nic none \
  -bios /path/to/seabios/bios-256k.bin \
  -kernel /path/to/vmlinuz-6.8.0-90-generic -initrd initramfs.cpio.gz \
  -append 'console=ttyS0 rdinit=/init panic=1 loglevel=3 comma_disposable_usb_lab=1'
```

For relocated Ubuntu QEMU, set QEMU_MODULE_DIR and LD_LIBRARY_PATH to extracted
libraries and `-L` to its usr/share/qemu directory. Missing these initially
caused a host QEMU initialization assertion, then missing boot ROMs; corrected
before the successful run. These were lab launcher failures, not C3X faults.

## Tests and limits

Boot exposed dummy_udc.0 and /dev/raw-gadget. Ten enumerate/disconnect cycles:
normal profile, added keyboard-class, mouse-class, mass-storage-class,
CDC-network-class, vendor-serial-class, hub-class, changed interface numbering,
keyboard-class again, normal again. All remained authorized=0 with zero
interface driver bindings; normal descriptors matched exactly (227 bytes),
all modified profiles differed. VM reached LAB_DONE and powered off normally.

Use `check_usb_lab_log.py serial-validation.log` to reject incomplete runs,
wrong profile decisions, unexpected configuration requests or nonzero binding/
authorization. Test success requires the full ordered case sequence.

The second complete run's serial output from LAB_KERNEL through power-down
is committed at `tools/security/evidence/usb-lab-20260916.log` (CR normalized).
The checker passed all ten cases. Both successful runs powered down normally.

**Scope:** this exercises actual modern USB-core descriptor enumeration and
device-default-deny, plus exact descriptor comparison. Added class interfaces
are descriptor-only (no functional HID/storage/network endpoint emulation).
The normal profile intentionally stays unauthorized too; modem operation and
positive option/QMI authorization are NOT emulated. Generic class drivers are
not loaded in this small initramfs, so zero driver binding alone is not proof
of resistance to their probe paths. Unauthorized state and no SET_CONFIGURATION
are the useful independent observations.

The stock lab kernel does not contain the vendor physical-port/driver-pinning
patch. This does NOT close its full containment gate, prove early root-hub
initialization safety on 4.9, or test malformed BOS/reset/lifetime races.
No fuzzing or adversarial USB traffic was sent to the actual comma.
