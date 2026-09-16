# Stage 1 validation follow-up — production unchanged

2026-09-15. No SSH operation, modem interruption, reboot, deployment or kernel
flash was performed on the production comma during this follow-up.

## Isolated packet lab

Docker Desktop was not running and the WSL integration was unavailable.
Instead, Ubuntu-packaged QEMU was downloaded/extracted into
`/tmp/modem-native-lab.qUdl4W`, without installing host packages. The disposable
guest boots Alpine 3.22.5's native 6.12.94-0-virt kernel, 768 MiB RAM, no disk,
no host block devices or USB pass-through, user-mode networking. The ISO's
SHA256 matched the checksum from the [official release directory](https://dl-cdn.alpinelinux.org/alpine/v3.22/releases/x86_64/).

`tools/security/firewall_packets.py` creates three isolated network namespaces:
device-under-test, cellular peer, and unrelated Wi-Fi peer. Actual legacy
IPv4/IPv6 rules run in the device namespace. Initial-namespace firewall/routing
are not changed. Requires Python, iproute2, legacy iptables/ip6tables, iputils
and OpenSSL. Run only in a disposable guest:

```sh
python3 tools/security/firewall_packets.py --disposable-vm /absolute/path/to/cellular_firewall.py
```

The first packet run exposed blocked IPv6 neighbor discovery after cache
flush. The candidate now permits only neighbor solicitation/advertisement
(135/136), hop limit 255, on cellular ingress. It does not permit router
advertisements, redirects or arbitrary unsolicited ICMPv6. Kernel support
for this match still needs checking on the actual target before deployment.

The initial passing run covered unsolicited echo and DNS blocking, forwarding
attempts, outbound echo/DNS/certificate-verified HTTPS, both address families,
unrelated Wi-Fi ingress, repeat apply and interface rename/link-down/up. The
expanded harness also **passed** full interface deletion/recreation,
RELATED ICMP errors and unsolicited TCP SYN. Live rollback/two-family failure
is still separate. A passing packet subset does not close the full gate.

Final guest result:

```text
PASS: dual-stack unsolicited echo/DNS/TCP and forwarding deny; outbound echo/DNS/HTTPS; RELATED ICMP; Wi-Fi; repeated apply; interface recreation
NOT COVERED: live timed rollback/two-family failure, target compatibility
```

## Independently armed rollback candidate

`tools/security/firewall_trial.py` is lab-only, not installed on the comma.
It snapshots both filter tables and preflights restoration before arming a
PID-1-owned systemd timer. It checks timer activation before calling apply.
The timer invokes a separate restore process and retries incomplete restores.
An immediate partial-apply failure also attempts both restorations. Explicit
confirmation checks rules but **must follow separate connectivity checks**.

Code paths and snapshot directories must be root-owned and non-writable by
other users. Full production packaging/import-path trust audit is pending.
The restore uses complete filter snapshots: no concurrent firewall
administrators are permitted during a trial. NAT/mangle/other tables are not
modified. No persistent boot policy is installed.

Unit tests exercise arming failure, arm-before-apply ordering, partial-family
failure and failed-restore retry state. They mock systemd/netfilter and do not
prove independent timer execution. The current Alpine guest uses OpenRC;
a systemd guest is still required to test expiration, SSH/caller death,
confirmation races, restore failure/retry, and reboot behavior. Do not run
this candidate on production to substitute for that missing test.

The combined local parser/firewall/rollback suite passed **83 tests**, with
Ruff and `git diff --check` passing. This count includes mocked failure-path
tests and synthetic fixtures, not 83 real-device checks.

## Parser / tainted-sink changes

- Added synthetic normal AT registration, signal, SIM channel/APDU, DNS and
  GPS transcripts. These are fixtures, not captured hardware qualification.
- TLV parsing now bounds input bytes, element count, tag length and length
  encoding; rejects truncated/indefinite values instead of silently stopping.
  `find_tag` validates trailing TLVs too. Tests cover nested and long values.
- ES9P accepts hostname authorities and named operations, not arbitrary URL
  authorities/paths/credentials/ports; HTTP redirects are rejected while the
  GSMA certificate trust bundle remains in use. Carrier compatibility needs
  validation. DNS rebinding/private DNS answers and bounded streamed HTTP
  response handling are **not solved** by these changes.

Additional sink findings/open work:

- PPP's `connect` argument is a shell-interpreted chat command, but currently
  assembled from fixed strings and fixed DIAL_CID, not modem response text.
  The installed pppd hooks/options/environment and package provenance still
  need auditing; source checkout alone cannot establish their installed state.
- Builder `lte.sh` quotes descriptor-derived sysfs strings when constructing
  mmcli arguments; its modem selection trusts VID/PID and is not a security
  boundary. Modem reset loops are unbounded. The script invokes GPIO controls
  as root and requires separation from a future unprivileged parser.
- DNS and route calls use argv and IPv4 validation, but modem-selected DNS/
  peers remain privileged network-policy inputs. A fixed-operation broker
  and restricted worker privileges are still required.
- qcomgpsd's outer/inner DIAG lengths and versions use assertions; satellite
  count drives allocation before complete record validation. Some size checks
  use floor division rather than exact length, and OEMDRE lacks an equivalent
  complete-record check. Frame bounds do not close these consumers' audit.
- SIM field decoders still index some byte fields without exact field-length
  checks, and some text decoding ignores malformed UTF-8. TLV envelope
  validation does not establish semantic validity or authenticity.

## Kernel candidate

Four original stable patches are stored in `tools/security/kernel_backports`,
with source classification, order and the BOS prerequisite relationship.
They apply in sequence to extracted files from the exact pinned vendor tree.
No edits were made to the Bluetooth-enabled kernel checkout. No compilation,
boot, sanitizer or malicious USB validation is claimed. Further equivalent/
partial/not-relevant classifications and transitive dependency/follow-up
review are still needed before this is a complete backport proposal.

## Production gates remain closed

After all offline gates pass, benign parked validation must separately record:
registration; LTE-bound data; DNS; GPS acquisition and sustained GPS; read-only
SIM/eSIM state; Wi-Fi SSH; manager/process health; controlled modem reconnect;
and cold boot. Reconnect/cold boot are intentionally deferred under the
instruction to keep production unchanged for now. No experimental kernel is
part of Stage 1 deployment.
