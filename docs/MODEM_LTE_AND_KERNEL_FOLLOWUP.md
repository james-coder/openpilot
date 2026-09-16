# LTE recovery and kernel containment follow-up

## Deployment durability finding

On inspection, production was clean at `216a5aa12a6acd8f4991d2c3c934a513ed6a4b46`;
the previously installed uncommitted parser edits and `modem_input.py` were
absent. The historical validation reports describe checks at those earlier
times, not a guarantee that those files remained installed.

The device reflog and finalized updater checkout show the old committed
image. `launch_chffrplus.sh` swaps in finalized updates when its Git-mtime
guard permits; source-only edits do not reliably invalidate that image.
This is consistent with updater replacement of uncommitted changes.
The root-owned persistent firewall remains installed and confirmed.

Remedy: commit the scoped parser/recovery changes on the actual deployment
branch, push normally, deploy that commit while freshly verified offroad,
and verify the commit AND file hashes after restart. Do not disable the
updater, reset unrelated work, or treat source-copy success as persistence.

## LTE implementation

- Wait inside the modem worker for `lte.service` active/exited/success.
  Its Type=simple service is active/running before hardware initialization
  finishes; active alone is not a valid handoff signal.
- CONNECTED requires an assigned IPv4 address plus successful route and DNS
  configuration. It does not prove Internet reachability.
- Bound retry cadence (5/15/60 seconds), PPP negotiation (120 seconds),
  command timeouts and progress-log memory. Preserve failure history across
  worker state transitions; clear it after five stable minutes.
- Hardware recovery requires repeated registered, authenticated, early PPP
  hangups with no address. Lost coverage alone is not a reset trigger.
- Optional fixed-operation root broker has a once-per-host-boot budget,
  consumed before requesting restart. It independently requires fresh,
  continuously safe offroad telemetry and GPS stopped/not expected.
- Missing/denied/failed broker leaves ordinary retries available. It is not
  a manager process or engagement requirement. No watchdog exemptions.
- Broker success means restart requested; only a subsequent configured PPP
  link changes status to reconnected. Neither means HTTPS was verified.

The root broker runs the openpilot safety observer as `comma`, never imports
user-writable Python as root, and accepts no variable commands/arguments.
Existing unrestricted comma sudo is NOT removed by this change. A check of
current safe state cannot guarantee ignition stays off after the observation.

Rollback: use the preceding committed deployment revision for application
files; remove/rename only the broker installation to disable escalation.
Do not roll back the independently installed firewall to roll back LTE code.

## Kernel provenance corrections and remaining work

Pinned vendor base: `c368754c26c7b9659de187addc6cccedc6cfb0a0`.
Stable inventory is through 4.9.337, not a list of confirmed vulnerabilities.

| Stable candidate | Classification / evidence |
| --- | --- |
| feab6c8 / a5c051b BOS bounds | Missing; second depends on first; candidate 0001/0002 |
| d2a6d705 descriptor sysfs locking | Missing; candidate 0003 |
| 2679c223 xHCI lifetime | Not relevant to pinned vulnerable operation: vendor c847383 reverted prerequisite 48701a8; do NOT apply archived 0004 |
| ecaaef6 duplicate endpoints across interfaces | Missing: pinned config parser checks only the current interface; prerequisite/follow-up review pending |
| 35531ec maximum-packet bits | Missing: pinned parser masks bits before validation; dependency review pending |
| 994d611 bitmask cleanup | Dependent on preceding maximum-packet correction; not independently counted as a security fix |
| 3cbc886 zero-endpoint skipping / 135878c correction | Do not apply skipping alone: upstream corrected it for compatibility; pinned no-skip behavior partially matches final behavior, warning absent |
| 6969767 cleanup limits / 0f8d02d revert | First superseded upstream; pinned final behavior equivalent to revert |
| c795e16 endpoint blacklist | Dependent/device-specific follow-up requiring further relevance review |
| 7bbbb95 usb_string termination | Missing: message.c returns on index zero before clearing buf[0]; not yet in candidate series |
| fe26b8d extra descriptor bounds | Missing: usb.c lacks maximum/minimum checks; requires atomic signature/header/macro and both direct-caller changes (hub.c, hwa-hc.c); not yet applied |
| d90419b nested reset guard | Missing: no reset_in_progress flag/guard; evaluate callback prerequisites and interaction with forced re-enumeration before applying |
| 2e7c5ff root-hub status bounds | Missing: hcd.c copies length without transfer_buffer_length check; host-local usbfs trigger differs from direct modem descriptor parsing |
| ff4c63f DWC3 teardown ordering | Missing: pinned dwc3_remove calls debugfs exit before mode exit; vendor teardown differs, needs adapted review |
| 054ace8 DWC3 runtime-PM removal | Missing: pinned remove uses put_sync before disable; vendor glue/lifecycle prerequisites still under review |
| 49bdc6b interface-claim error cleanup | Missing: driver.c returns without restoring driver/data/PM state on bind failure; not yet applied |
| 56d298a interface-claim LPM removal | Missing; review/apply with preceding claim cleanup, not independently inferred from textual applicability |

Other USB-core/xHCI/DWC3, option/TTY/PPP, networking and memory-corruption
candidates remain unclassified; no complete stable-security coverage claim.
The candidate worktree is separate from the known-good Bluetooth tree/image.

Early policy is physical-controller scoped, not VID/PID identity. It retains
the full five-interface compatibility set (four option + one qmi_wwan),
pending evidence supporting further removal. Root hubs/external USB remain
unchanged. No global USB class driver removal has been justified yet.

Residual risks: USB core/controller parsing occurs before authorization;
permitted option/QMI/PPP/DIAG/AT transports still parse hostile data; a
compromised modem can spoof every allowed descriptor byte. Userspace isolation,
global driver reduction and remaining kernel fixes are separate unfinished
defenses. This is not a claim that a compromised modem becomes safe.

## Validation boundaries

Local tests cover retry/error paths, fresh-state gate, one-shot reset budget,
real engagement event code under synthetic process state, descriptor mutation
fixtures and a native C harness of the proposed kernel policy. The existing
modem process remains subject to normal blocking/disabling health checks.

USB core, xHCI object and tizi DT cross-compiled. No experimental kernel has
been flashed. No hostile USB testing on the production device; Raw Gadget
belongs on disposable modern kernels/hardware, not this vendor 4.9 kernel.
Cold boot, sustained GPS and driving validation of the NEW LTE changes must
not be inferred from older parser checks or local tests.

## Actual application deployment — 2026-09-16 UTC

Application commit `2ec5aef00` and off-device artifact commit `44690543f`
were pushed to `deploy/volt-gm-egr` and fast-forwarded on the device after
fresh offroad checks. No manager/watchdog edits and no experimental kernel
flash. Root-owned broker installed separately at `/usr/local/lib/comma-modem`;
root filesystem returned read-only. Its unprivileged safety dry run passed.

Before restart, 114 parser/recovery tests passed on the comma, plus live
read-only AT parity, DIAG range and SIM profile checks. Local combined suite
passed 197 tests. Six synthetic real-event-path engagement tests subsequently
passed on the comma too; this is NOT actual driving/engagement validation.

Managed warm reboot produced boot ID
`0253a5d5-852a-467f-9dd6-c9881d36d9e8`. SSH took about two minutes to return.
Device Git HEAD remained `44690543f850ed0856ccdf5c89792b872bbe6c42`, clean.
Parser helper SHA256 remained
`bf49dc7bbe37618973b6c9d6ba037c741eab06b8cb97dfd55f62d206b073a087`.
New runtime retry/recovery fields were present, confirming new worker code.
Thirty fresh offroad manager samples had no missing required process.
LTE-bound HTTPS returned 200; uncached DNS returned network results via ppp0.

Actual broker test: safe reset accepted; second request refused with code 3.
USB disappeared at ~6 seconds, returned at ~21 seconds; CONNECTED at ~28
seconds, then ten stable seconds. Wi-Fi SSH survived. LTE-bound HTTPS and
uncached DNS passed again. The reset budget is intentionally consumed until
the next host boot. This tests the broker and reconnect, not an induced
registered-early-hangup cohort (that trigger is covered synthetically).

One DIAG range probe after the restart returned an unexpected opcode or
length; its original assertion did not record which. Do not invent its
contents/cause or count it as a pass. Seven subsequent read-only probes
returned opcode 115, 75 bytes, operation/status 1/0. AT parity and read-only
SIM listing passed. Existing GPS setup retries already handle parser
rejections; regression tests exercise transient success and ten-failure
exhaustion. The validation tool now reports opcode/length on failure.
This anomaly remains unexplained; fresh sustained GPS on this revision
is still unverified. No additional ignition cycling was requested.

Persistent firewall remains confirmed, active and enabled, with the expected
IPv4/IPv6 COMMA_CELL_INPUT/FORWARD policies after reboot/reconnect. Kernel
still reports 4.9.103+. The known-good Bluetooth tree and rollback images
were not changed.

## Full off-device build

Full `Image.gz` and tizi DT compiled/linked successfully with GCC 8.2.1:
`ARCH=arm64 HOSTCFLAGS=-fcommon KCFLAGS=-w PYTHON=python3 -j4`, the
existing aarch64 toolchain under volt-bluetooth-build/builder/tools.
`KCFLAGS=-w` is inherited from the working build recipe, not a warning-clean
claim. Source/base and complete patch are recorded in kernel_backports;
`candidate.config` records the exact resolved configuration, originally
copied from the known-good Bluetooth build then processed by olddefconfig.

Build outputs remain in /home/james/git/volt-modem-kernel-candidate/out:

- Image.gz SHA256: 73f1d9e751a99364b671deb36a6d541d19165cba06ee98191cc2176961e936b6
- comma_tizi.dtb SHA256: d64577ec02b2af89cd61f10e7ebaba5e557d06c2a397314426cacfff3e6e3ebf
- .config SHA256: 2c1248037df31728e47d41430b80d51a8ccc3a8e728401af074ca9f85ab049f4

No boot image was packed or flashed. Compiler/linker success and the native
C harness do not establish enumeration/reset race safety. Modern disposable
USB tests, target boot/compatibility, remaining stable fixes and further
driver reduction remain open. Windows C: fell to roughly 1.8 GB free after
the build; do not start more large builds/downloads without checking space.

## Installed PPP sink audit follow-up

The actual modem worker runs as UID/GID 1000 with no effective capabilities;
pppd and NetworkManager run as root. ModemManager is inactive/runtime-masked.
Unrestricted comma sudo remains, so worker compromise is not contained by
the UID alone. Actual pppd reports 2.4.9, root-owned mode 4754, SHA256
6cfa3341b1d75d00eecd1bfb9f54189069f45818f88253ab6ea6a79292376e43.
No dpkg package entry was found; binary/package backport provenance remains
unresolved despite the reported version.

Read installed ip-up/down, ipv6-up/down, ip-pre-up and both 0000usepeerdns
hooks. No local-hook overrides or symlinks under /etc/ppp were present.
The hooks forward positional values with quoted run-parts arguments, without
eval; fixed script directories and checked PATH directories are root-owned,
non-writable by other users. The PPP connect/chat string remains constant,
not interpolated from modem replies. Raw log content is not executed.

systemd-resolved was active, causing both legacy DNS hooks to return early;
normal DNS installation is through the validated argv-based resolvectl path.
The dormant ip-down DNS hook has unquoted expansion of its resolver-backup
path: audit provenance and input constraints before treating that fallback
as safe. DNS servers, peer routes and PPP authentication/negotiation still
cross privileged boundaries. These observations do not replace the remaining
pppd parser, configuration-option, privilege-isolation or SSRF audits.

Final local combined regression run: **231 passed**, including firewall
rollback, parser bounds, LTE recovery, optional-process safety and USB policy
logic. This count does not include additional live packet or vehicle tests.
