# Userspace parser staged validation — 2026-09-16 UTC

This follows the successful temporary firewall trial. Persistent firewall and
kernel changes are not part of this userspace trial.

## Before promotion

Fresh ignition-off/controls-disabled telemetry gates passed. Device checkout
was clean at `216a5aa12a6acd8f4991d2c3c934a513ed6a4b46`.
The existing deployment-branch versions, not replacement copies from the
development branch, were patched to preserve device-specific changes.

Baseline source backup on device:
`/data/modem-security-stage-20260916/baseline.tgz`

SHA256: `96c601fc00c9843dc5cc01874cc1e4868eff702e624714f92865e17f716bfbae`.
It contains the prior modem.py, lpa.py, modemdiag.py and qcomgpsd.py.
modem_input.py is new and had no baseline file.

Staging: `/data/modem-security-stage-20260916/candidate`.
`tools/security/validate_modem_stage.py` loads candidates only in its own
process, shares the AT lock, checks offroad telemetry, forbids reset escalation,
and prints no SIM identifiers/raw responses.

Actual staged checks passed:

- AT+CREG?, AT+CGREG?, AT+CSQ, AT+QGPS?, AT+QCCID: successful bounded reads;
  results match the legacy parser on the same captured response bytes.
- Read-only DIAG range query: 75-byte response, maximum log mask 2562 bits;
  candidate opcode, header and allocation bounds match the real response.
  No log-mask writes or GPS start commands in this check.
- Read-only eSIM profile list: one profile, one enabled; no profile changes
  or modem reset. Logical channel closed afterward.

Review caught a compatibility change in GPS error handling before promotion:
legacy qcomgpsd tolerates ERROR/CME responses to some startup commands. The
shared bounded reader now has an explicit legacy policy for qcomgpsd only;
modem/LPA keep rejecting error responses. Regression fixtures cover both.

Local combined suite: 87 passed. Syntax compilation of staged files and diff
checks passed before source installation.

## Promotion / pending validation

The helper was installed first, followed by atomic replacements of the four
source files with their original file modes. Only these files changed.
After a second fresh offroad gate, `DoReboot` requested a normal managed
reboot. This is a **warm reboot**, not a physical cold-boot test.

Manager preimports modules and the modem process is not independently
auto-restarted on exit; killing only that process is not a safe reload method.
No watchdog exemptions or lifecycle-policy changes were made.

Warm reboot succeeded; new boot ID
`77c27e96-4417-4180-add4-61f9d9764d86`. LTE returned CONNECTED/roaming,
LTE-bound HTTPS returned 200, and uncached DNS succeeded through ppp0.
Fifteen seconds of fresh manager telemetry showed no missing expected
processes; modem was running as expected and device remained offroad.

Controlled modem restart passed after another fresh offroad gate, using the
existing `lte.service` stop/start path. A separate observer recorded USB/AT
device disappearance at 2 seconds, INITIALIZING at 3 seconds, device return
at 17 seconds, SEARCHING/CONNECTING, and connected PPP at 23 seconds. The
observer then verified ten seconds of stable connection. Wi-Fi SSH survived.

After restart, the installed sources (not just staging) passed all five AT
parity queries, the DIAG range query, and read-only SIM profile parsing again.
LTE-bound HTTPS returned 200 and uncached DNS succeeded through ppp0.

## Ignition-on stationary GPS check

The user confirmed the vehicle on, outdoors and in Park. Device observation
was read-only: no process restarts, AT queries, DIAG configuration changes or
software writes on the device while on. Normal manager startup launched
qcomgpsd (PID 55511), publishing `gpsLocation` with source `qcomdiag`.

Five-minute observation completed:

- 300 received GPS updates, all 300 valid with hasFix true.
- Zero invalid updates, fix losses or timestamp regressions.
- Maximum observed receive gap 1.11 seconds; final update age 0.95 seconds.
- Same GPS PID throughout; fresh manager telemetry, no missing expected
  process observed. Periodic carState samples showed Park and 0.0 m/s.
- LTE-bound HTTPS returned 200 with GPS active.

Fixes were already available when observation began; 0.057 seconds to the
first observed fix is **not** a measured cold-start acquisition time. No
coordinates were printed or saved. This establishes stationary live-parser
operation and sustained fixes, not GPS accuracy/authenticity or driving
engagement validation.

Physical cold boot still requires user involvement. Persistent firewall and
kernel changes remain uninstalled. Next: ignition off, fresh offroad gate,
clean shutdown, then user-assisted power removal/reconnection.

The user subsequently confirmed ignition off. Fresh device/panda telemetry
verified ignition off, controls disallowed and device not started. Manager
reported no missing expected processes; qcomgpsd had stopped normally and was
no longer expected to run. After another fresh offroad gate, DoShutdown was
set through the normal manager path. Physical power removal and cold-start
verification remain pending user action; shutdown request acceptance alone
does not prove that the hardware has powered off.

## User-assisted cold boot

The user reported disconnecting/reconnecting power and powering back on.
The observed boot ID changed to
`6a895d54-fd16-4a8b-8036-024f9a991840`, with uptime about one minute.
Installed parser helper hash remained
`bf49dc7bbe37618973b6c9d6ba037c741eab06b8cb97dfd55f62d206b073a087`.

Verified after this boot:

- Wi-Fi SSH returned; LTE CONNECTED/roaming, LTE-bound HTTPS 200.
- Uncached DNS through ppp0 returned network results.
- Installed AT parser passed all five real-response parity checks.
- Installed DIAG parser accepted the 75-byte range response and 2562-bit
  maximum mask; read-only SIM query found the same one enabled profile.
- Fifteen seconds of fresh manager telemetry: no missing expected processes;
  modem running, device offroad. GPS was stopped and not expected while off.

This closes offroad startup/connectivity checks after user-reported physical
power cycling. An ignition-on GPS check after this cold boot remains pending;
the prior five-minute GPS run preceded power removal. No permanent firewall
installation or kernel change was performed during these checks.

## GPS after physical power cycle

After the user reported ready with ignition on again, a read-only three-minute
observation on the same cold-boot ID completed:

- 180 received GPS updates, all valid with hasFix true; source qcomdiag.
- Zero invalid updates, fix losses or timestamp regressions.
- Maximum observed receive gap 1.11 seconds; final update age 0.54 seconds.
- One qcomgpsd PID (55371) throughout; fresh manager telemetry and no missing
  expected process observed. All received carState samples reported Park and
  speed below 0.1 m/s.
- LTE-bound HTTPS returned 200 while GPS was active.

The first observed fix arrived after 0.50 seconds of observation, not a
measured cold-start acquisition time. GPS was already starting before the
observer attached. No device writes, restarts, or modem queries occurred in
this observation. Cold-boot offroad checks and post-power-cycle stationary
GPS checks now pass. Driving engagement/road validation is not claimed.

Next deployment work: persistent firewall installation and boot-order
verification, only after a fresh offroad gate. Kernel changes remain off-device.

## End of user-assisted validation

The user turned ignition off again and asked to minimize further manual
cycling. The fresh offroad gate passed. The physical cold-boot and subsequent
stationary GPS checks above complete this round of user-assisted validation;
do not repeat those cycles routinely for the same parser changes.

Read-only boot-path inspection found root mounted read-only, root-owned
`/usr/local/lib` and `/etc/systemd/system`, and a comma-owned `/data` parent.
Do not execute a persistent root callback from the user-writable checkout or
assume a root-owned child under `/data` makes its whole path trusted.
`comma.service` launches the manager through tmux after local-fs.target;
`netfilter-persistent.service` runs before network-pre.target. Current legacy
IPv4/IPv6 filter tables retain their original empty ACCEPT policy: the earlier
timed firewall trial was reverted, not made persistent.

Persistent firewall deployment remains gated on a tested **reboot-surviving**
rollback and boot ordering after the existing firewall loader but before
cellular startup. The earlier transient `/run` timer does not meet that gate.
No additional reboot or permanent firewall change was made in this check.
