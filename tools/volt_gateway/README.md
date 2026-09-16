# Volt gateway off-device work

`mcuboot_port.py --arm` and `boot_emulation.py` execute the real loader and
crypto on Cortex-M4 emulation and run signed slot-local Thumb probes. They do
not implement hardware startup, a flash peripheral or independent CAN recovery.
`firmware/status_led.c` adds optional nonblocking startup/status/error patterns;
see [LED meanings and limitations](../../docs/volt-gateway-led-status.md).

`mcuboot_port.py` now builds the actual pinned MCUboot direct-XIP/revert C loader
against memory-backed flash and the real crypto backend. Tests cover trial,
confirmation, revert and storage failures; it is **not flashable firmware**.
Set `VOLTGW_MCUBOOT_CHECKOUT` as well as the crypto archive for complete offline
validation. See [boot port](../../docs/volt-gateway-boot-port.md).

`target_crypto.py` builds a pinned Mbed TLS 3.6.7 C backend. The ARM harness can
execute real verification, SHA256 and HMAC without host crypto hooks; native
update/lifecycle tests exercise the same backend. This still is **not a flashable
image**. Set `VOLTGW_MBEDTLS_ARCHIVE` to the verified official archive before
validation; tests do not fetch it and missing dependency is reported as skipped.
See [target crypto](../../docs/volt-gateway-target-crypto.md) for details and gates.

Current lifecycle additions: `boot_model.py` and `test_lifecycle.py` join the C
updater to persistent model storage and simulated reset/boot/confirm/revert.
This is NOT a flashable loader or an actual booted application. The transport
harness now injects virtual-time jitter/delay/burst-loss faults. Generated-input
tests supplement deterministic scenarios; no exhaustive-condition claim.

`usb_inspect --serial SERIAL --health` additionally reads the reviewed historical
41-byte health packet; it never sets a safety/mux mode or transmits CAN. Current
readout is raw, not interpreted as milliamps. Unknown layouts are rejected.

Research prototype only. Nothing in this directory is registered with the
openpilot manager, sends CAN or participates in driving readiness. The explicitly
invoked USB inspection helper reads Panda identity/version only.

- `protocol.py`: bounded experimental codecs and authentication envelope.
- `simulation.py`: bounded control/telemetry queue and fixed rate model.
- `adaptive.py`: adaptive update admission and chunk retry state models.
- `transfer_sim.py`: two-peer authenticated update/fault simulation, including
  paced reverse ISO-TP flow-control and cached retransmission responses.
- `benchmark.py`: native Python measurements and synthetic wire-cost models.
- `provenance.py`: build-only historical source comparison; no flashing.
- `authority.py`: verifier-only operator authorization, distinct from routine MACs.
- `operator.py`: development-only interactive encrypted key generation/signing;
  private keys never go through the comma. No production keys have been generated.
- `release.py`: public-artifact-only packaging and explicit staged-secret checks.
- `update_engine.py`: inactive-slot transaction reference, NOT a hardware updater.
- `firmware/authority.*`, `firmware/observe.*`: portable bounded C cores, not a
  complete bootloader/board firmware; no CAN/flash write driver is included.
- `native_harness.py`, `test_system.py`: native-C/integrated transport fault tests.
- `build_core.py`: Cortex-M4 object/stack size reports, not a bootable image.
- `m4_emulation.py`: Cortex-M4 instruction tests using explicit host crypto hooks;
  the emulator ELF must NEVER be flashed. No STM32 peripherals are modeled.
- `mcuboot_test.py`, `mcuboot_targeted.py`: pinned upstream bootloader simulator
  checks. See the testing document for its CLI fixture/argument issues and the
  difference between swap recovery and direct-XIP revert coverage.
- `windows_preflight.ps1`: read-only Windows PnP/driver/tool/disk inventory.
- `usb_inspect.py`: exact-serial, IN-only Linux libusb identity/version inspection
  after USB/IP attachment. Does not construct Panda or reset it.
- `usb_mode.py`: separately invoked, exact-serial/version-gated non-programming
  mode transitions; not part of inspection. WSL softloader re-enumeration failed;
  do not repeat that path hoping to recover USB access.
- `windows_rom_handoff.ps1`: read-only by default; explicit `-AttemptHandoff`
  catches the legacy softloader through native WinUSB and enters ROM DFU.
- `readback.py`: explicit two-read Linux DFU backup into a new private directory;
  no erase/program/unprotect. Successfully used on the labeled Panda.
- `dfu_leave.py`: explicitly jump from ROM DFU to the preserved bootstub without
  programming. This is not a restoration-from-backup test.
- `backup.py`: offline read-command construction and double-read verification;
  deliberately has no command execution or programming entrypoint. Its Windows
  command builder belongs to the superseded host-tool plan; verification is portable.
- `windows_share_panda.ps1`: elevated, exact-device USB/IP sharing and narrow
  firewall scope; no Panda commands. `99-voltgw-white-panda.rules` grants normal
  Linux group access to only the identified application-mode device.

Run all tests with `.venv/bin/python -m pytest tools/volt_gateway -n 0`.
Run scenarios with `.venv/bin/python -m openpilot.tools.volt_gateway simulate-update`.
Add `--output NEWFILE` to preserve JSON; existing files are never overwritten.

See [implementation status and gates](../../docs/volt-gateway-implementation-plan.md)
and [adaptive transfer status](../../docs/volt-gateway-adaptive-transfer.md).
This is not the finished gateway or a deployable firmware updater.

## Local operator tools (not comma commands)

Run `python -m openpilot.tools.volt_gateway.operator --help` for `keygen`, `sign`
and `authorize`. These require a local interactive terminal. `keygen --device
370022000651363038363036` creates an encrypted key in the private ignored checkout
directory; the passphrase is never accepted through an argument or environment
variable. Back it up encrypted off-device before any provisioning. The signed
manifest is a gateway envelope, not a replacement for MCUboot image signing.

Before committing: `python -m openpilot.tools.volt_gateway.release check-staged`.
This is not automatically installed as a Git hook and does not detect arbitrary
unknown binary secrets. No whole-checkout deployment/copy of the private directory.

For CPU emulation, install the off-device `requirements-emulation.txt` into the
development environment; missing dependencies produce explicit skipped tests.
See [testing evidence and limitations](../../docs/volt-gateway-testing.md).
# Practical validation entrypoint

```
.venv/bin/python -m tools.volt_gateway.validate --output /absolute/new/artifact-directory
```

Runs the offline suite, lint, Cortex-M4 object builds and GCC static analysis.
Reports skips as incomplete and missing test reports as failures. Its
`production_ready` field remains false: board integration and parked acceptance
are not performed by this command. Requires the development dependencies,
including optional emulation dependencies for a no-skips result.

The portable C update transaction now runs in native transport tests and the
Cortex-M4 harness; that harness remains **NEVER_FLASH**. Flash storage and crypto
are host models, not a bootable production loader. `can_model.py` adds CAN
behavior tests without opening USB/CAN interfaces.
