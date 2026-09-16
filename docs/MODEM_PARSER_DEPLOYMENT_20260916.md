# Guarded parser-kernel trial — 2026-09-16

Preparation: full incremental ARM64 Image.gz-dtb build succeeded with the
existing configuration, Bluetooth support, 0100+0101 policy and 0102+0103 parser
fixes. Source config.c was restored to its pre-build contents afterward; build
outputs now describe #5, not #4. The previous signed images are preserved.

- Expected uname version: `#5 SMP PREEMPT Wed Sep 16 00:08:05 MDT 2026`.
- Image.gz-dtb SHA256: `3ad9d5b09c4a442fa0ee3497eba9de771f525a6204d4aff9003e00fb2e5da02d`.
- Signed candidate SHA256: `1925912dee9410b807436c089f5d8a49b293ad188e48b7743616eba6f61486d3`.
- Signed candidate size: 17,745,920 bytes. Original and new signatures verified.
- Rollback is the complete current #4 boot_a: `46fd613ff1b1147ad0212d205fc96181fb7b25dbdcb273210882a64efb56cdbd`.
- boot_b remains pinned to `bf0dd9ff2393131dfa7c6dac40af7ae709a858f588755076502e4b9996b6afd1`.

`kernel_parser_trial.py` is a separate, narrowly pinned successor to the prior
operator-only trial tool. Its root-owned directory, timer and service do not
replace the existing confirmed trial. It snapshots #4, checks partition
readback against the actual #4 tail, and arms an independent eight-minute timer
before writes. Confirmation additionally requires a changed boot ID and exact
new kernel version. Restored-boot tracking prevents the policy marker shared
by #4/#5 from causing repeated reboots after recovery.

Root space admission accounts for the full 64 MiB rollback partition, candidate
size and a 64 MiB remaining-free reserve. Existing recovery records are not
deleted to make space. At preflight root had about 190 MiB free, /data 9.1 GiB;
fresh Panda voltage was 13.805 V, ignition/controls false, no faults. Active slot
and all baseline partition/image hashes matched. Windows C: stayed above 1 GiB
free; this build reused existing outputs and did not duplicate the full tree.

101 tests passed across parser, authorization/root-hub/installer, both trial
profiles, reports, DIAG and evidence checks. Ruff passed. No new manager process,
engagement dependency, Panda change or application update is part of this trial.

Deployment results will be recorded separately below. Preparation and these
tests are not proof of a successful target boot, driving safety, or malicious
USB containment. A pre-userspace failure still requires physical recovery;
the timer cannot repair a kernel that never runs userspace. Malicious USB and
fuzz testing are excluded from this actual vehicle.

## Actual deployment and parked validation

Flashed only boot_a after the independent timer was armed and a fresh offroad
check passed. Complete readback matched
`1f8135dd9f5004744ab634a0d705b2a25b99e4ae831859fcf8f6a6e244c4b0c9`.
This differs from packaging's original-baseline-tail calculation: installation
correctly computed the partition hash using the actual #4 tail. boot_b stayed
unchanged. One managed reboot was performed; SSH returned on kernel #5 with
about 52 seconds uptime. No ignition cycles or modem resets were requested.

- Both protected root hubs bind hub, authorized=1; device and interface defaults
  remain 0. Modem interfaces bind four option drivers and one qmi_wwan.
- LTE-bound HTTPS returned 200; uncached DNS over ppp0 returned records with
  network as the data source. These are separate checks, not inferred from the
  passive report's connected state.
- One enabled SIM profile was read without changing profiles. Five AT queries
  passed bounded-parser/legacy parity checks. No DIAG command was sent.
- Bluetooth hci0, both Aranet services and the audio card remained present.
- 150 seconds of passive observation: 149 fresh manager/Panda samples, no
  missing expected processes or Panda faults, 150 connected modem samples,
  zero observed retries, two advancing Aranet write timestamps.
- No BUG/Oops/WARNING/panic/Call-trace matches in the inspected boot dmesg;
  authorizer journal showed startup without rejection messages.
- Existing application revision remained 10f71636d. No application pull,
  manager/watchdog changes or Panda update occurred.

The validator was initially invoked with an incorrect Python import context,
then incorrect CLI flags. Both launcher errors occurred before any query;
module invocation with positional arguments succeeded. They were not modem
or kernel faults.

After all parked checks, the guarded confirmation rechecked safe state,
partition hashes, authorizer activity, changed boot ID and exact running kernel.
Trial phase is confirmed; rollback timer is disabled/inactive. Root returned
read-only, with about 109 MiB free. Full #4 rollback is verified both root-owned
on-device and locally at kernel-parser-trial/kernel4-rollback.img. The original
Bluetooth recovery baseline and prior trial records are untouched. Local trial
artifacts total about 115 MiB, below the 256 MiB additional-artifact budget.

The sanitized passive report is committed as
`tools/security/evidence/parser-kernel5-parked-20260916.json`.
It deliberately leaves HTTPS unobserved because that check was external to the
passive stream; the separate successful test is recorded above.

Still unobserved: physical cold boot, sustained GPS, fresh vehicle recognition,
actual driving engagement and adversarial USB behavior. qcomgpsd was stopped
and not expected offroad; absence of GPS was not counted as failure. This
confirms parked compatibility, not completion of the broader security audit.
