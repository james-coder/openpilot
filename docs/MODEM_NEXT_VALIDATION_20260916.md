# Follow-up validation and security work — 2026-09-16 UTC

Production remains on confirmed kernel #4 and application revision 10f71636d
during this pass. No flash, reboot, modem reset, service installation, GPS
start, profile change, firewall change or driving-software restart occurred.
New tools are operator-only; nothing is added to manager or boot dependencies.

## Current device observation and next ordinary startup

A 15-second passive subscription run showed fresh device/Panda/manager data,
offroad state, no Panda faults, no missing expected processes, connected modem
state with zero retry count, and advancing Aranet advertisement timestamps.
qcomgpsd was stopped AND not expected to run. GPS/carState messages were
absent: this is not a failed GPS or vehicle-detection test while offroad.

`tools/security/observe_startup.py` is a bounded manual observer (1 Hz, at most
600 seconds, nice 19/SCHED_IDLE). It subscribes only to existing messages and
reads bounded status files. It sends no CAN/DIAG commands and writes no params.
It emits GPS fix/accuracy, not coordinates; modem identifiers are omitted.
When started with fresh carState it also reports loaded CarParams brand/model,
not VIN. Loaded parameters alone are NOT proof of a new successful fingerprint.

At the next ordinary startup, run from the development computer without
installing or changing anything on the comma:

```sh
ssh -o BatchMode=yes -o HostKeyAlias=192.168.0.106 comma@192.168.98.187 \
  'cd /data/openpilot && /usr/local/venv/bin/python - --seconds 300' \
  < tools/security/observe_startup.py
```

Review the offroad→started transition, fresh carState, loaded GM/Volt model
(not MOCK), expected processes, GPS fix acquisition/continuity and accuracy,
modem state, and advancing Aranet timestamps. Connected modem state is not an
HTTPS test. A changed boot ID plus owner-confirmed full power cycle is needed
to call a test a physical cold boot, not merely a warm startup.

**Pending:** no new ordinary vehicle startup or actual driving engagement was
observed in this pass. The observer is not installed as a continuous service
and is not left running indefinitely. No special ignition cycling is needed.

## DIAG anomaly investigation

Quectel's own [QLog command definitions](https://github.com/quectel-official/QLog/blob/main/diagcmd.h)
identify opcode 19 as invalid command, 20 as invalid parameter, 21 as invalid
length and 24 as invalid mode. Thus the earlier opcode 19/17-byte response was
a protocol-level negative reply, not proof of a USB CRC error or memory fault.
The recorded opcode/length cannot establish which request it concerned.
Its payload was not retained; do not invent its contents or exact cause.

The existing HDLC reader checks CRC; send_recv returns the first non-log frame
without correlating its command to the request. Potential causes still include
a genuinely rejected first request or an unrelated response. Neither is proven.
GPS setup already has a bounded ten-attempt retry. This investigation does not
silently discard errors or change production GPS retry/parser behavior.

`tools/security/diag_probe.py` sends only the existing 0x73 range-read request,
at most five times, with existing receive deadline/frame bounds. Each query
requires fresh offroad telemetry and fresh manager evidence that qcomgpsd is
neither running nor expected; serial ownership is exclusive. It reports each
failure separately, known error names, and booleans indicating whether an error
payload starts with/exactly equals the request. No unknown raw response or
identifier is printed. An echo match would be evidence, not authentication.

Executed three read-only queries on the parked device: all returned opcode
115, 75 bytes, operation 1/status 0, valid mask bounds; about 1 ms each.
The anomaly did not recur. Its cause and impact on fresh sustained GPS remain
open; do not call this fixed. No reset was induced to try to reproduce it.

## Additional provenance review (off-device only)

Pinned vendor base: c368754c26c7b9659de187addc6cccedc6cfb0a0. Semantic inspection
used vendor config.c and stable history through v4.9.337; this is a small
additional tranche, NOT completion of the 3,957-candidate inventory.

| Stable fix | Classification/evidence | Dependencies and disposition |
| --- | --- | --- |
| ecaaef6b50a7873d495f9cc69fc4c4edfc792635, duplicate endpoints | Genuinely missing: vendor usb_parse_endpoint checks only endpoints already in the current alternate setting; no cross-interface/control-direction helper exists. | Existing same-alt check is present semantically; exact upstream prerequisite SHA is not available in vendor history, so no ancestry claim. Unmodified 0005 applies to isolated vendor config.c after deployed BOS fixes. Not compiled/deployed. |
| 35531ec82046dc072bb841c452b7cea193af42f3, wMaxPacketSize | Genuinely missing correction: vendor initializes maxp with the already-masked usb_endpoint_maxp(), then tries to inspect transaction bits that were stripped. Earlier aed9d65ac327 is an ancestor. | Upstream 0006 does not apply verbatim: surrounding zero-maxpacket changes differ. Needs reviewed contextual adaptation, not blind application of intermediate regressions. Not compiled/deployed. |
| 994d611bfe38762a30ed60db49af8cdeaf7586dc | Existing BIT(12)/BIT(11) expression is equivalent to the named mask; not an additional substantive security correction. | Cosmetic follow-up to maxpacket fix; vendor mask definition exists. Not queued as a security fix. |
| 135878c0b1e0d3135f72392c44f531322594987c | Not relevant to the harmful zero-maxpacket-skipping regression at this vendor base: the preceding 3cbc8867d76 skip was never present. | Extra warning text is absent, but don't add the harmful predecessor merely to satisfy 0006 patch context. |

References: [duplicate endpoints](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/commit/?id=ecaaef6b50a7873d495f9cc69fc4c4edfc792635),
[maxpacket validation](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/commit/?id=35531ec82046dc072bb841c452b7cea193af42f3).
Both parse device-supplied configuration descriptors before interface policy.
Later endpoint enabling is additionally restricted by the current exact-profile
gate; that does not eliminate earlier parser exposure. Reachability is not a
claim of a demonstrated exploit or assigned CVE.

Review-only patch exports live under `kernel_backports/review/`. They are NOT
part of the deployed 0100+0101 series. The deployed build tree was unchanged.

## Verification of this work

61 local tests passed across evidence classification, stale/missing telemetry,
GPS ownership refusal, lab-log failure detection, USB policy, installer,
rollback and DIAG bounds. Ruff and shell syntax checks passed. The lab gadget
compiled statically with Wall/Wextra/Werror; committed serial evidence passes
the log checker. Local mocks/fixtures are not target driving validation.

## Disposable USB lab

See [lab procedure/results](MODEM_USB_LAB_20260916.md). Actual modern-kernel
enumeration tests are now available, but vendor-policy and adversarial reset/
lifetime testing remain gates before further production kernel changes.
