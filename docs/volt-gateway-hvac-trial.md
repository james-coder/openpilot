# Parked HVAC experiment, 2026-09-17

## Configurable USB candidate 1: ODI event

After installing the configurable SWCAN image, the owner explicitly requested
one `0x10AC0099` extended DLC4 frame, payload `000e0700`, while watching the
screen/LED. Fresh Park/RUN/zero-speed inputs were present (ages80/76/16ms).
Authenticated opcode21 accepted it; state reached done, attempts1. SWCAN TX
counter rose0 to1; RX rose from0x67e6 to0x6bba; overflow,
malformed, arbitration-loss, TX-error, and ESR fields remained zero.
During five seconds afterward, the subscribed stream returned two
`0x10AD6080` frames containing `0a07070200000000`, at MCU times156305000 and
156335000us. No subscribed `0x10B02099` recirc-state report was captured in the
two-second before/five-second after windows. Host observation drops were zero.
Those clear messages do not prove causation or recirculation movement. Awaiting
owner observation; no second candidate or repeat was sent.

Owner then reported candidate 1 made the infotainment display show recirc while
the physical button still indicated outside air. This supports display/event
behavior, not successful recirculation actuation.

## Candidate 2: attempted three-second action pair

Owner requested candidate 2. Fresh interlock inputs were37/14/51ms old. The
first `0x10AD6080` DLC8 `0a0707022b000000` request was accepted (attempts2),
but the state had faulted before the planned release at three seconds. The host
therefore did **not** send `0a07070200000000`. Subsequent SWCAN counters still
showed transmitted1 (the candidate1 frame only), malformed0, overflow0,
arbitration_lost0, tx_errors0, ESR0. This does not confirm successful wire
transmission of candidate2. The fault reason is not distinguished by the current
status record; do not infer bus collision, timeout, or vehicle rejection from it.
No automatic reset or retry was performed.

### Owner-requested retry after USB power cycle

Owner rebooted the White Panda and requested candidate2 again. Initial state
idle/attempts0, fresh interlock, SWCAN TX0. The first payload
`0a0707022b000000` completed successfully: state done, TX1. Approximately three
seconds after acceptance, with the interlock still ready, the host requested
`0a07070200000000`. That request was accepted, but state faulted and TX remained1.
Thus only the first frame has confirmed completion; the clear frame does not.
RX rose8458 to9180, malformed/overflow/arbitration_lost/tx_errors/ESR remained0.
No capture subscriptions were active in this retry; user-visible effect awaits
owner observation. No further automatic retry/reset was performed. The repeated
fault still lacks a diagnostic reason; aggregate zero error counters do not
establish that this was a bus error or exclude a local interlock/deadline fault.

Owner reported no screen/LED effect from candidate2 and requested candidate1
again. The fault was still latched. Authenticated parked software reboot
succeeded with OBD connected, without unplugging or flashing; USB reattached.
One candidate1 frame (`0x10AC0099`, DLC4, `000e0700`) then completed: idle to
done, attempts0 to1, TX0 to1, RX5469 to5641, malformed/overflow/arbitration_lost/
tx_errors/ESR all zero. Owner-visible result pending. This verifies another
successful candidate1 transmission, not the cause of the intermittent fault.

## Candidate 3: explicit provisional raw-value experiment

After the owner requested trying candidate3 values15s apart despite absent
enumerations, the host sent two extended DLC5 frames at **provisional** ID
`0x1045A080`: `0000000000`, then `0200000000` about15s later. These encode raw
recirc values0 and1 in byte0 bits3..1, leaving all other bits zero. The ID's
priority/source and payload value meanings are hypotheses, not observed
request definitions. Zero is **not** established as "no change" for other HVAC
fields; this uncertainty was stated before sending. No claim of ON/OFF semantics.

Both frames completed, one TX-count increment each, attempts1 to2 to3, state
done after each, fresh interlocks throughout. Reported malformed/overflow/
arbitration_lost/tx_errors/ESR remained zero (SWCAN error-accounting limitations
still apply). No resets, flashes, retries, or subscriptions were used. Physical
HVAC effects await owner observation.

## Latest physical result: first completed TX pair, hvac05

On September17, with OBD/USB connected and fresh Park/RUN/zero-speed evidence,
one authenticated op17 was accepted. State progressed press_pending →
release_wait → done, attempts1. SWCAN transmitted counter increased0→2;
RX increased97632→98628. Malformed, overflow, arbitration-loss, TX-error and
ESR fields remained0 in the before/after samples. No additional trial requested.
Subscriptions to `0x10B02099` and `0x10AD6080` produced no observations in the
two-second pre-trial / approximately four-second post-trial window. Therefore
CAN transmission completed, but HVAC acceptance, recirc LED change, and physical
flap movement remain unconfirmed pending user observation. No success claim for
recirculation control follows from the CAN TX counter.

Owner subsequently confirmed **no recirc-state change** during our TX trial.
The infotainment popup occurs during manual button presses; the owner did not
notice one during our injection (explicitly uncertain). Do not report the
injection as successful popup control either.

The subsequent passive recording
`/home/james/diagnostics/volt-gateway/hvac-after-failed-tx-20260917-01.jsonl`
contains420 subscribed observations,86 discovered IDs, zero host observation
drops, and another manual ON/OFF pair. Same-bus MCU timestamps, not host arrival:

| Transition | Selection report 0x10B02099 | Candidate 0x10AD6080 | Candidate lag |
| --- | --- | --- | --- |
| ON | 756008000 us, `0006070d00000001` | 756074000 us, `0a0707022b000000` | 66 ms |
| OFF | 763255000 us, `0006070d00000000` | 763325000 us, `0a0707022b000000` | 70 ms |

Candidate clear payloads followed about3s later, with a second clear roughly
31–33ms afterward. This ordering and failed injection favor a downstream
event/display exchange, not the initiating request, but do not prove exact ODI
semantics. Another observed family was0x560/`ODIEvent_LS`, actual ID0x10AC0099,
DLC4, `000e0700`, twice; it was counted but not raw-subscribed during this pass.
The generic DBC's0x22D remote-climate request family was not observed in the
180s discovery interval, including both manual toggles. Absence is not proof
that the controller cannot accept that family.

GM's2017 Volt service-manual data-link table lists both A26 HVAC Controls and
K33 HVAC Control Module on SWCAN and LIN. Its LIN schematic connects A26 pin9
to K33 pin4 over circuit7531 GN/YE (HVAC LIN1). This makes a direct LIN button
path plausible; it does not establish a recirc payload or exclude a separate
CAN/diagnostic control interface. Do not substitute Gen1 wiring/protocols.
Sources: [GM data-link table](https://estimate.mymitchell.com/GMC/document/4/6/4/0/0/100304692_4640002_11741334.html),
[GM LIN schematic](https://estimate.mymitchell.com/GMC/document/4/2/9/1/5/4291538.html).
Local source SVG is retained under `vehicle-observations/2017-volt-lin-4290886.svg`.
Next target is an evidenced request to K33 (ordinary CAN or documented diagnostic
control), not repeat injection of the failed ODI pair. No further TX issued.

Installed hvac05 includes the later separation of the cabin-action interlock
from flash-programming voltage/dwell requirements described below. The original
12.5–15.5V/five-second cabin gate in the initial design is no longer applicable.

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

## hvac02 physical deployment

With OBD disconnected as confirmed by the user, the armed USB recovery listener
and automatic DFU watcher captured ROM enumeration. Full live predecessor
matched hvac01, then hvac02 programmed successfully. Full1MiB readback SHA256:
`4a5c71d7379eacbe1b60eaeb46eb2a3e72e91248a5a7f8773532a13c82e49021`.
Evidence: private `flashing/370022000651363038363036-20260916/hvac02`.
The first automatic USB reattach was too early; subsequent software reattach
succeeded without another physical replug. Running slot A build was checked
against the prepared ELF identity and matched exactly:
`240e6b7f86fb5d3cb7ef1eb2af50344b09b3b424e214c31d5fbe005345cf4849`.
Indicators running/error none; capabilities127; experimental TX policy1;
HVAC idle/attempts0/interlock false on USB only. This confirms installation
of the startup fix plus bounded HVAC TX support, not successful vehicle TX.

## hvac03 prepared correction (not deployed)

With hvac02 physically running, all three buses had zero hardware/software RX
overflows, but the trial stayed idle/interlock false and op17 was not issued.
Read-only subscriptions confirmed repeated primary frames309
`00001146991a1a12` (Park),497 `a21200001800007a` (RUN), and1001
`0000000000000000` (zero speed), with changing unrelated PRNDL bytes later.
The installed status does not expose VIN/sampler details, so the remaining
physical blocker is not conclusively identified.

hvac03 separates the short cabin-button trial from flash-write power policy:
fresh (<=250ms) Park/RUN/zero-speed evidence and healthy CAN are required, but
12.5–15.5V and five-second stability are no longer HVAC prerequisites. Signed
firmware updates retain their existing power interlocks. Exact two-frame policy,
authentication, session timeout, and no arbitrary raw TX remain unchanged.

It also corrects a clock inconsistency: trial sampling previously used the
CAN poll-entry epoch even though RX timestamps may be later. The new caller
uses the actual monotonic clock extension, as the storage gate already does;
driver health tolerates at most10ms since the last CAN poll. Native tests cover
that difference, future/stale timestamps, and missing/unsafe vehicle evidence.

Status op18 version2 additionally exposes VIN, sampler/power-valid/stability
flags, input seen/safe masks and ages, TX-inhibit and session state. New op19
requests a USB-authenticated secondary-gateway reboot, acknowledged before a
250ms delay and fresh parked-state recheck. It does not require valid VIN or a
cleared RX latch, does not restart the driving processes, and cannot run during
an active button pair or receiving update. Loader/default builds do not expose
this hook. CLI adds `parked-gateway-reboot` and understands both status versions.

88 native CAN/HVAC/physical-safety tests passed; lint/diff checks passed; ARM
loader/A/B build succeeded. Signed candidate `bench-hvac03` is prepared against
hvac02's exact readback. None of these results establish physical deployment or
successful HVAC control. Installed hvac02 still lacks the new reboot operation.
