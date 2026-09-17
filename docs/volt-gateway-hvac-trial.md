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
