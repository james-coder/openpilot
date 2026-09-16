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
