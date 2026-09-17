# Local USB in-circuit recovery

Implemented in candidate `hvac-trial-20260917-05` / private `bench-hvac05`.
**Not physically deployed or in-circuit validated yet.** Installed hvac02 does
not have this operation. Its final cold-loader installation is still necessary.

The new recovery path is independent of the HVAC feature switch and of the
application firmware updater's VIN, CAN freshness, parked-state, and RX-overflow
gates. It is present in both ordinary and experimental USB application builds.
The operator performs vehicle-connected maintenance while parked.

1. A paired local USB session sends authenticated, payload-free opcode20.
   Capability0x80 advertises support. CAN-originated requests, bad MACs, payloads,
   and replayed requests cannot initiate another recovery action.
2. The application acknowledges, suppresses optional telemetry/HVAC TX, then
   after250ms writes a fixed, checked one-shot SRAM intent and resets. It does
   not erase or program flash. Reset disables the vehicle transceivers.
3. Before normal initialization, watchdog start, or CAN startup, the loader
   consumes the marker and enters the fixed STM32 ROM entry directly. No cold
   USB enumeration window must be caught. Normal cold-start recovery remains.
4. The host waits for the exact ROM USB identity and reattaches it to WSL where
   necessary. It compares the entire existing flash against the saved predecessor,
   programs only the established regions, verifies the entire1MiB readback,
   boots, reattaches, and queries the running application and recovery capability.

The intent uses fixed SRAM words0x2001bfe0/4, outside BSS and below the reserved
stack/debug region. The linker limits BSS accordingly. Magic/complement and a
software-reset flag are checked; consumption clears both words. This is a reset
mailbox, not a firmware-authentication primitive or remotely supplied address.

## Host commands

Entry only, no flash writes:

```
.venv/bin/python -m tools.volt_gateway.device_cli --pairing PRIVATE_PAIRING usb-recovery
```

Complete maintenance workflow after this capability is installed:

```
.venv/bin/python -m tools.volt_gateway.in_circuit_flash \
  --pairing PRIVATE_PAIRING --prepared PRIVATE_PREPARED_DIRECTORY \
  --before PRIVATE_FULL_READBACK --evidence NEW_PRIVATE_EVIDENCE_DIRECTORY --parked
```

OBD and USB remain connected. WSL enumeration changes may require software USB
reattachment; the tool performs it for the exact device/port (`--busid`, default
2-2). It refuses old firmware before requesting a reset, and never guesses a
vendor request or silently falls back to another Panda. A preflight or readback
mismatch stops programming. Reusing an evidence directory is rejected.

ROM DFU is a **local physical-USB maintenance boundary**, with broader authority
than signed CAN application updates. It can replace loader/provisioning with the
reviewed local image. It is not exposed through the CAN command transport and
does not give a CAN peer arbitrary flash access. Application signatures still
apply on normal boot. Pairing/signing keys and full readbacks remain private.

## Validation and limits

- Native tests cover local-only authenticated dispatch, invalid MAC/payload,
  duplicate handling, operation while RX is inhibited, torn markers, one-shot
  consumption, reset-type checks, and failed marker writes.
- Whole linked ARM image execution covers the application's delayed marker/reset
  with no vehicle/update-power prerequisites; GPIO state is checked at reset.
- Whole linked loader execution reaches the fixed ROM vector with CAN PHYs
  disabled, marker consumed, and no watchdog start or USB enumeration wait.
- Host tests cover capability refusal and exact recovery opcode selection.
- These model/register tests do **not** emulate STM32 ROM USB implementation,
  real power behavior, Windows drivers, or physical CAN electrical effects.

Physical completion requires installing the candidate, leaving OBD and USB in
place, requesting recovery in software, performing an exact-readback update,
and verifying the newly booted firmware. Do not call in-circuit flashing proven
until that complete sequence succeeds on this device.
