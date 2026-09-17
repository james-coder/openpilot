# Parked HVAC experiment, 2026-09-17

Experimental, not validated recirculation control. Repeated manual recirc
presses correlated with extended SWCAN `0x10AD6080`, DLC8,
`0a0707022b000000`, then `0a07070200000000`. The same action accompanied ON
and OFF. This is a candidate button toggle, not an absolute state command.
Other-button specificity and physical flap movement remain unverified.

`board_build --hvac-experiment` opts application slots into SWCAN normal mode
(including CAN acknowledgments). Default builds and the recovery loader keep
SWCAN silent. No production Tres/openpilot change is required.

Authenticated application op17 requests exactly one press/release pair; op18
reports version/state/attempts/interlock readiness. Neither accepts payload
parameters. Capability bit0x40 identifies this build. LIST_RULES byte1 denotes
this experimental policy. Access is local USB only, through the existing paired,
replay-resistant session. No generic CAN transmit interface is added.

The board independently requires fresh existing RUN/Park/zero-speed inputs,
five seconds of stable 12.5–15.5V power and safe state, healthy CAN, live session
and telemetry lease, and no receiving firmware update. Four attempts maximum
per boot; ten-second cooldown. Exactly one press and, after acknowledged CAN
completion plus 2500ms, one release. No automatic retransmission. Each mailbox
has a 20ms completion limit. Errors, session/interlock loss or missed release
deadline latch a trial fault and abort the mailbox. In particular, losing the
interlock after the press suppresses the release too; it does not justify
continuing transmission. CAN completion alone does not prove HVAC acceptance.

The application will ACK SWCAN traffic even before a trial. Injecting a frame
with the original center-stack ID can collide with an OEM transmission; this
remains a parked supervised experiment, not a driving feature. Feedback is the
separately observed `0x10B02099` selector `0006070d`, value0/1, plus the actual
button LED/user observation. Do not equate it with measured flap position.

CLI: `device_cli ... hvac-trial-status`, then explicit `hvac-button-trial`.
Connecting, booting, or querying status never requests the button action.

Validation before first flash: 16 native trial/MMIO tests, 59 existing CAN and
physical-interlock tests, 90 authenticated application/recovery tests passed.
ARM loader/A/B images build with no undefined symbols; trial code absent from
loader symbols. These are software tests, not vehicle or physical TX validation.
No change to normal driving manager/process dependencies.

Prepared artifacts are private under `bench-hvac01`; unsigned build/evidence
is in `/home/james/diagnostics/volt-gateway/builds/hvac-trial-20260917-01`.
Known-good rollback remains `bench-object02` and its full readback, SHA256
`9b275b8dc8b3458a2565eb896366ca1cda4dff4d82d67aa51ea8aaa683afbb5c`.
Flash and vehicle results must be recorded separately; preparation is not
deployment. No signing or pairing secrets belong in this document or Git.

## First physical flash and USB startup

After the user disconnected OBD and cold-replugged USB, the exact-target
recovery listener entered ROM DFU. The live full predecessor readback matched
Object02. Programming completed and the full 1MiB readback matched SHA256
`9d614e02c6662395f247b27f567a3d8b5e6ffc4e4ae2056d07a0be61cd6fa4a2`.
Evidence: private `flashing/370022000651363038363036-20260916/hvac01`.

The physical device booted slot A, running/error none, build
`f2199cef62a2d9a725f6672e4850474f674c2df34a7815e84371b0bcdf7af9e2`.
Authenticated INFO reported capabilities127, SWCAN3/HSCAN-mask3/backhaul2;
LIST_RULES reported experimental policy1. HVAC status was version1, idle,
zero attempts, interlock false as expected without the vehicle connected.
All three bus reports showed zero RX/TX/errors/overflows/software drops.
Only read-only queries were issued; op17 was not sent. Physical HVAC actuation
and in-vehicle behavior remain unverified. No production Tres change was made.

## First vehicle connection: trial blocked, no actuation

After reconnecting OBD, Windows still enumerated the Panda but WSL required
software USB reattachment. Authenticated queries reported `rx_degraded` and
one Powertrain hardware FIFO overflow, already present at the first sample.
Its occurrence time/cause is unknown; it must not be attributed to the connector
or boot without further evidence. Over the following ~20s the count remained1.
Object/SWCAN hardware overflows, all software drops, and all TX counters were0.
RX increased on all three buses. Queue peaks were24/22/2, maximum ages5/6/6ms.
HVAC status remained idle/attempts0/interlock false. Host did not issue op17.

The existing global TX-inhibit latch also prevents the physical safety gate
from granting permission. Installed application has no software reboot or
clear-latch operation; authenticated session close does not reset the device.
No intentional crash, malformed command, or bypass was attempted. This trial
does not establish whether the captured action controls recirculation.

Second cold boot again showed one Powertrain FIFO overflow already present at
the first query; the count stayed constant and op17 was not issued. Inspection
found an unserviced startup interval: CAN1 receives while later controllers
wait for bus synchronization, before RX interrupts are enabled. The candidate
fix drains already-running controllers into the existing bounded queues during
those waits, retaining all overflow reporting and TX fault behavior. A delayed
synchronization regression test stages48 frames with no loss. CAN/trial tests:
58 passed. This addresses a real code gap, but the cause of the physical
overflow is not yet conclusively localized or the fix vehicle-verified.

Candidate build `hvac-trial-20260917-02` and signed private `bench-hvac02`
prepared against the exact hvac01 readback. Deployment still requires entry
into the installed cold recovery loader; preparation is not a successful flash.
