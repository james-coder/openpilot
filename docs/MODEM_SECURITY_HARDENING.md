# EG25 containment: audit and staged implementation

Status: 2026-09-15, **candidate changes only, not deployed**. This is an
incomplete security audit, not a claim that a compromised modem is safe.
The only production-device operation in this implementation stage was a
read-only backup, gated by a fresh offroad check. No kernel, USB authorization,
Panda safety, manager watchdog, routing or service configuration was changed.

For subsequent native-VM packet tests, rollback candidate, parser changes and
remaining gates, see [Stage 1 validation follow-up](MODEM_STAGE1_VALIDATION.md).
The observations and test counts below describe the initial audit stage.

## Provenance and rollback

Observed device: AGNOS 18.3, custom Bluetooth-enabled 4.9.103+ kernel. Local
kernel source is `commaai/agnos-kernel-sdm845` at
`c368754c26c7b9659de187addc6cccedc6cfb0a0`, with Bluetooth DTS/config changes.
Runtime compressed config and local build config have matching SHA256
`2c1248037df31728e47d41430b80d51a8ccc3a8e728401af074ca9f85ab049f4`.
This establishes a config match, not reproducible binary provenance.
Builder checkout `ec1cf237a84565a056dbbc6f433b1b1c20c07a2c` declares 18.2;
do not silently equate it with the installed 18.3 root filesystem.

`tools/security/backup_boot.py` saved both current 64 MiB boot partitions at
`/home/james/diagnostics/modem-security-20260915/known-good-boot`, comparing each
local digest with a fresh digest on the device:

| Slot | SHA256 |
| --- | --- |
| a | `3ade1f5da238dc4747827db5204f1dcfc0f53a6d418f50bdd47569dfd09504b1` |
| b | `bf0dd9ff2393131dfa7c6dac40af7ae709a858f588755076502e4b9996b6afd1` |

These preserve the current boot artifacts; they are not a complete rootfs/data
backup or a tested recovery procedure. Keep the manifest and images together,
off the device. Do not overwrite them with experimental builds. No flash
procedure is authorized by passing these software tests.

## Observed attack surface

Physical modem path:
`/sys/devices/platform/soc/a800000.ssusb/a800000.dwc3/xhci-hcd.0.auto/usb1/1-1`.
USB2 bus 1 and USB3 companion bus 2 share that host controller. The external
gadget controller is separately `a600000.dwc3`. Bus numbers alone are not
trust anchors; policy must use the controller and physical downstream port.

Normal device: `2c7c:0125`, EG25-G, firmware EG25GGBR07A08M2G, USB 2.0,
device class ef/02/01, bcdDevice 3.18, EP0 64 bytes, no serial string.
One configuration, value 1, total length 209, five alternate-setting-0
interfaces, attributes a0, 500 mA. All bulk endpoints below are 512 bytes;
interrupt endpoints use interval 9.

| Interface | Class/subclass/protocol | Endpoints | Binding/use |
| --- | --- | --- | --- |
| 0 | ff/ff/ff | bulk 81/01 | option, ttyUSB0; qcomgpsd DIAG/GNSS, required here |
| 1 | ff/00/00 | bulk 82/02, interrupt 83 (10 B) | option, ttyUSB1; NMEA, no production reader observed |
| 2 | ff/00/00 | bulk 84/03, interrupt 85 (10 B) | option, ttyUSB2; shared AT port |
| 3 | ff/00/00 | bulk 86/04, interrupt 87 (10 B) | option, ttyUSB3; PPP data |
| 4 | ff/ff/ff | bulk 88/05, interrupt 89 (8 B) | qmi_wwan/cdc-wdm; bound, but observed cellular transport is PPP |

Class-specific descriptors on interfaces 1–3 are also part of the baseline.
No interface can be called unnecessary solely because no reader was observed.
The installation has no available u-blox: DIAG GPS must not be removed.
Removing interface 1 can renumber ttyUSB devices; stable physical-interface
aliases and permission rules must precede any interface removal.

Current device and interface authorization defaults are permissive. Kernel
`drivers/usb/core/hcd.c` initializes the defaults; `message.c` propagates
interface authorization; `driver.c` checks authorization at probe/claim.
`hub.c` enumerates/parses descriptors before policy can inspect the device.
A late udev unbind rule is **not** race-free default-deny.

Built-in exposure includes HID, storage, USB audio, ACM, FTDI, option/WWAN,
USBNET, RNDIS, CDC Ethernet/NCM, QMI/WDM, RTL8152, ASIX, NET1080, ZAURUS and
other network drivers. MBIM, generic USB serial, UAS and USB Bluetooth are
disabled in the observed config. Built-ins cannot be secured by a module
blacklist. Modules are enabled, signature enforcement is off, module loading
is not locked; udev modalias autoload exists despite an empty installed module
directory. Root automount rules also process newly attached partitions.

Touchscreen is I2C, Wi-Fi platform ICNSS, Bluetooth UART ttyHS1. Preserve those,
external gadget/update support, and other essential hardware when reducing
host class drivers. Full dependency/boot testing remains pending.

Modem management runs as comma, but comma has unrestricted passwordless sudo;
PPP runs as root. AT ports are shared using `/dev/shm/modem.lock`. ModemManager
is runtime-masked by modem.py, not permanently eliminated from boot exposure.

Legacy IPv4/IPv6 runtime filter policies were ACCEPT without rules. Saved
IPv4 rules targeted wwan0 only, while cellular used ppp0. The persistence
service reported success despite a rules-load failure with the unsupported
nft backend. SSH listens on wildcard IPv4/IPv6 port 22; Avahi on wildcard UDP.
Private carrier addressing is not a firewall. Forwarding was off.

## Stable security-fix audit: initial findings, not completion

`tools/security/kernel_fix_inventory.py` compares complete histories through
v4.9.337 (`87a72e81764d2fc5411706c551462d61cdb97660`), not a blind rebase.
The selected USB, TTY, PPP, networking, memory and related paths yield **3961
candidate commits**, not 3961 vulnerabilities. Inventory entries start
unresolved. Commit-ID absence alone does not prove a missing equivalent fix.

Source comparison against the pinned vendor tree found these old code paths:

| Stable commit | Vendor evidence | Initial classification |
| --- | --- | --- |
| d2a6d7054d134bbe5051548697ac1aa524df18be | `drivers/usb/core/sysfs.c`, read_descriptors lacks added device lock | Missing locking change; concurrent descriptor reads relevant |
| feab6c8cfc3d3ec003de54aaed12ea915d1eff35 | `drivers/usb/core/config.c:937`, BOS header size not checked | Missing BOS out-of-bounds-write fix |
| a5c051b6503c0ba543e993cfc295b64f096e0a29 | same BOS parser retains old traversal and uncorrected stored total length | Missing device-reset BOS bounds fix; review predecessor above |
| 2679c2231bc3fb260f74e1faf7d6810427b1fc6e | `drivers/usb/host/xhci.c:3624`, xhci_free_dev lacks udev pointer clearing | Missing change from xHCI lifetime fix; dependency review pending |

These are code comparisons, not exploit demonstrations. Already-backported,
equivalent and partial fixes throughout the rest of the tree remain to be
classified. TTY/PPP/netfilter and full transitive prerequisites remain open.
Do not treat this table as permission to cherry-pick isolated fixes into a
production kernel. The generated full inventory is an audit artifact, not a
finished backport series.

Primary references: [4.9.337 changelog](https://cdn.kernel.org/pub/linux/kernel/v4.x/ChangeLog-4.9.337),
[pinned vendor source](https://github.com/commaai/agnos-kernel-sdm845/tree/c368754c26c7b9659de187addc6cccedc6cfb0a0),
[USB authorization](https://docs.kernel.org/6.3/usb/authorization.html).

## Tainted-data / sensitive-sink audit

This matrix records examined paths and open work; it is not an exhaustive
closure claim. Resource bounds mitigate denial of service, not injection or
the trustworthiness of navigation/network information.

| Modem-controlled source | Consumer / sink | Existing protection, changes, remaining work |
| --- | --- | --- |
| AT lines, URCs, errors | modem.py, lpa.py, qcomgpsd.py parsers, logging | Candidate shared total deadline/byte/line limits; no raw modem error interpolation; normal response compatibility still needs hardware validation |
| CCHO logical-channel reply | interpolated CGLA/CCHC AT commands | Candidate exact ASCII 1–19 validation; prevents command delimiters, not malicious SIM semantics |
| SIM APDU/TLV, ICCID, profile names, notifications | LPA TLV processing, UI, profile requests | Full nested-length/count/type audit pending; do not infer safety from AT bounds |
| SIM notificationAddress / activation address | HTTPS ES9P URL and requests redirects | GSMA trust bundle present; authority validation, redirect policy, response-size limits and SSRF/DNS analysis remain OPEN |
| AT DNS, local/peer IPv4 | sudo ip route/rule, resolvectl | IPv4Address validation and argv execution observed; addresses still control privileged network policy; DNS trust and broker isolation pending |
| PPP stream | kernel PPP/TTY, root pppd, hooks | Security-fix provenance and privilege audit pending; framing bounds in Python do not protect this path |
| DIAG HDLC/log masks | CRC decode, allocation, GNSS unpacking | Candidate frame/CRC/deadline/mask bounds; per-message field validation and spoofed GPS/time integrity remain OPEN |
| Modem status/temperature/identifiers | /dev/shm/modem JSON, hardware.py, UI/registration | Writer uses fixed directory/random tempfile/atomic rename; consumer JSON size/schema/type audit remains OPEN; values not trusted for authenticity |
| Descriptors/re-enumeration | USB core, host controller, built-in class probes, automount | No early deny policy implemented yet; strings/VID/PID cannot authenticate modem |

Inspected subprocess paths use fixed executable/argv, not modem text through
a shell. This is not proof that every downstream shell, PPP hook, formatting,
path or privileged configuration sink has been audited. Reducing privileges
requires removing unrestricted sudo from a dedicated worker and providing a
small fixed-operation broker; simply adding NoNewPrivileges would break
current management. Unsupported cgroup/namespace settings are not isolation.

## Implemented candidate code and validation

- Shared AT parser: 16 KiB line, 64 KiB total, 256 lines, whole-transaction
  timeout; serial write deadlines; prefix-only response matching.
- SIM logical-channel sink validation.
- DIAG bounded encoded frame, explicit CRC/length failures, bounded setup
  response wait and 12-bit log-item mask allocation. Ordinary GPS waiting is
  not given a new idle timeout. Malformed data fails visibly, not as fake GPS.
- Explicit legacy IPv4/IPv6 owned-chain firewall candidate. Only ppp+/wwan+
  ingress/forwarding is constrained; unrelated rules, Wi-Fi and output policy
  are preserved. No automatic installer or manager dependency.
- Read-only boot backup and reproducible kernel-history inventory tools.

42 local tests passed for AT/DIAG parsing, channel injection rejection,
firewall rendering/verification and preflight/inspection failures. The WSL namespace integration attempt failed
IPv6 preflight because its ip6_tables module was unavailable; candidate apply
did not proceed to commit either family. **No successful dual-stack packet or
normal cellular/GPS compatibility test yet.** No real engagement, cold-boot,
crash-recovery or driving validation is claimed.

## Remaining deployment gates / next stages

1. Complete normal/error AT, DIAG and LPA fixture coverage and validate benign
   parked operation. Preserve driving-process health enforcement; no blanket
   exemption for GPS/modem processes. Test failure/absence/resource competition.
2. Test firewall in isolated dual-stack namespaces with packets, outbound
   HTTPS/DNS, reconnect and unrelated-rule preservation. Add boot ordering and
   an independently armed, tested timed rollback before any host installation.
   A two-family apply is not atomic; preflight is not a rollback mechanism.
3. Complete priority kernel provenance and tainted-sink audits. Prepare reviewed
   dependency-aware backports and off-device builds with measured costs.
4. Implement early default-deny for both companion root buses of the physical
   modem controller, exempt root hubs, pin allowed drivers and validate the
   full configuration on every enumeration before permitting minimum tested
   interfaces (initial target 0/2/3). Deny 1/4 only after compatibility proof.
5. Remove unnecessary host drivers in an off-device kernel candidate; review
   available mitigations and userspace privilege separation. Preserve the
   known-good Bluetooth image and all required update/diagnostic hardware.
6. Raw Gadget tests belong only on a disposable modern kernel/VM or separate
   gadget hardware. Raw Gadget is not assumed available in this 4.9 kernel.
   Test normal composite, extra HID/storage/network/serial and malformed
   combinations there—not on the driving device. See the
   [kernel documentation](https://docs.kernel.org/5.10/usb/raw-gadget.html).

## Residual risks by boundary

A. Class-driver removal / early interface denial can eliminate those driver
probe paths, but this stage has not yet deployed either measure.

B. Privilege isolation can reduce userspace compromise impact; current comma
sudo and root PPP mean that boundary is not yet established.

C. Necessary option/TTY/PPP/DIAG/AT (or QMI if retained) parsing remains
attacker-facing, including legitimate-looking malicious responses.

D. USB core and xHCI/DWC3 enumeration parse attacker-controlled information
before an interface authorizer can decide. Descriptor allowlisting cannot
eliminate that attack surface or authenticate the modem.
