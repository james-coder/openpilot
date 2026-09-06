# comma3 reliability investigation and release candidate

Investigation date: September 6, 2026. Baseline openpilot `ca32282dc` and
opendbc `4700fb1a0`. Candidate branches: `fix/comma3-reliability` and
`fix/gm-diagnostic-fingerprint`. These changes are not installed on the device.

## Evidence and changes

| Issue | Evidence | Change |
| --- | --- | --- |
| Low memory | September 4, 18:20:02 MDT: 91% memory, Low Memory alert, disengagement; radard RSS rose from 80 to 1,015 MiB in 125 minutes. UI stayed near 176 MiB. | Populate radar lead structs through scalar setters instead of assigning dictionaries. |
| Earlier reported low memory | Retained logs show 88% memory before an earlier reboot. Earlier route data has been deleted. | Same allocation fix addresses the reproduced growth mechanism; the second exact alert cannot be independently confirmed. |
| Car unrecognized | Route `00000016--8c632ce9b9` selected MOCK. Two bus-0 frames at address `0x7e3`, payload `02 1a b0 00 00 00 00 00`, eliminated GM candidates. Next boot identified Volt. | Exempt only bus-0, eight-byte, ISO-TP length-2 service-0x1a requests at `0x7e3` from eliminating GM candidates. Keep raw fingerprint evidence. |
| UI scheduling | Startup requested CPU 0, but render-loop affinity recovery moved UI to CPU 5, shared with radar/planning. | Use CPU 0 consistently, including hotplug recovery. |
| Brief cruise fault banner | Repeated roughly one-second startup alerts. | Delay the GM permanent banner by one second. No-entry and immediate-disable alerts retain immediate behavior. |
| Misleading communication logs | Ignored debug subscriptions were included in failure diagnostics. | Log services using the same validity/liveness/frequency exclusions as the actual checks. |
| Sensor probe noise | The first supported sensor probe can return ENODEV before the other sensor succeeds. | Debug-level log for this exact expected probe failure. Other ioctl errors and failure of all probes remain errors. |
| Startup model skips | Frame-drop filter already treats the first ten iterations as warmup. | One informational warmup summary; subsequent dropped frames remain error logs. Inference skipping is unchanged. |
| Optional API failures | Repeated DNS/request failures for Prime/firehose UI queries. | Keep cached status, bound requests to ten seconds, retry with capped exponential delay, log first failure/recovery, reset delay when network type changes. |

The memory mechanism was reproduced with pycapnp 2.2.2: dictionary-to-struct
assignment creates schema reference cycles. Realtime setup disables cyclic GC.
12,000 iterations of the former assignment added approximately 74 MiB and
432,000 unreachable objects; scalar setters did not exhibit this growth.
The fix preserves decoded lead values and default values in fresh radar messages.

The origin of the diagnostic CAN requests is still unknown. They were observed
in received CAN, not the retained `sendcan` stream. This change does not force a
Volt fingerprint or ignore arbitrary traffic. Other addresses, buses, lengths,
services, and non-GM candidates retain their previous behavior.

## Local validation

- Minimal full host build passed using system clang 18. The workstation's
  user-installed clang could not load `libtinfo.so.5`; no source change was needed.
  Git LFS model/font objects were restored before building.
- 51 tests and 27 subtests passed: radar allocation and decoded-field equivalence,
  GM fingerprint negatives, all six radar degradation flags, clean recovery,
  CAN failure behavior, cruise alert timing, existing alert/state-machine tests,
  CAN diagnostic data, API retry behavior, and offline study evaluation.
- Ruff and whitespace checks passed for changed Python files.
- Recorded radar input repeated for **four simulated hours, accelerated**:
  1,200 recorded snapshots; zero measured RSS growth; zero unreachable objects.
  This is not four hours of wall-clock device operation or a thermal test.
- A second accelerated four-hour run with 2,400 snapshots from two segments
  grew 0.16 MiB, slope 0.057 MiB/hour, and also left zero unreachable objects.

Reproduce the focused suite from the repository environment:

```sh
PYTHONPATH="$PWD" .venv/bin/pytest -n0 -q \
  selfdrive/controls/tests/test_radar_memory.py \
  selfdrive/ui/tests/test_api_poller.py \
  selfdrive/selfdrived/tests/test_gm_cruise_fault.py \
  opendbc/car/tests/test_can_fingerprint.py \
  opendbc/car/gm/tests/test_radar_interface.py \
  selfdrive/selfdrived/tests/test_alerts.py \
  selfdrive/selfdrived/tests/test_alertmanager.py \
  selfdrive/selfdrived/tests/test_state_machine.py \
  selfdrive/ui/tests/test_can_diagnostics_data.py tools/alpr/test_study.py

PYTHONPATH="$PWD" .venv/bin/python -m tools.profiling.radar_memory \
  /path/to/completed/rlog.zst --hours 4 --output /path/to/radar-soak.json
```

Add `--real-time` for wall-clock pacing. The soak has no CAN sockets or vehicle
I/O. It checks less than 16 MiB total RSS growth and less than 1 MiB/hour slope
after warmup. Use multiple representative rlogs when memory capacity permits.

## Remaining release gates

1. Physically disconnect the comma3 from the vehicle and power it on the bench.
   Ignition off alone does not satisfy this agreed validation gate.
2. Build the candidate on the target architecture, including the Spectra camera
   path (the host build uses a different camera backend). Confirm both sensor
   selection and a real probe failure still produce appropriate diagnostics.
3. Run recorded radar replay for four wall-clock hours. Capture process RSS/PSS,
   temperature, scheduling affinity and frame/timing statistics. Confirm the
   same allocation thresholds and compare latency with the baseline.
4. Verify UI affinity on CPU 0 across offroad/onroad simulation and core hotplug;
   verify no rendering or control deadline regression. CPU affinity does not
   partition RAM, memory bandwidth, or accelerator capacity.
5. Exercise API outage, recovery, network changes, sleep/wake, and shutdown on
   the device. A request already in flight can take up to its timeout to finish.
6. Install the candidate only after these gates pass. Retain baseline commits
   and capture a short controlled vehicle validation before promoting the
   updater branch. Check fingerprints, genuine fault alerts, radar fallback,
   temperatures, frame drops, and memory trend during a longer drive.

Promotion requires the opendbc commit to be reachable remotely before the root
gitlink is promoted. Roll back the updater branch to the baseline root commit
and update submodules to that commit's recorded gitlinks; reboot only while
parked. Do not delete recordings or reset car parameters as a workaround.

## Prime and remote access

Prime supports the official `ssh.comma.ai` relay. The LAN address is not a
cellular public address. Follow [comma's SSH instructions](https://docs.comma.ai/how-to/connect-to-comma/)
and verify both the relay and device host keys. On this inspection the relay
key matched the published RSA fingerprint, but it closed connections before
authentication. LAN SSH remained operational.

A live authenticated device API request returned `is_paired: false` and
`prime_type: 0`; the device cached `PrimeType=-1` (unpaired). This is a
server-side status observation, not a conclusion about whether a subscription
was purchased. Pair this device to the subscribed account through its pairing
QR code in comma connect, then verify the server status and relay again.
At the user's suggestion, one offroad reboot was performed on the baseline
software. The fresh API check still returned the same unpaired/no-plan status.
The resumable footage export was paused for the reboot and resumed afterward.
After the device returned home, another live request still reported unpaired
status. All thirty selected footage segments then completed export: 120 files,
4,287,737,290 bytes, verified against source checksums. Subsequent workstation
analysis can continue without device connectivity. The device remains connected
to the car, with ignition off at the last inspection; the disconnected bench
validation and candidate installation remain pending.

There is no ignition prerequisite in this source: manager registers at startup,
Athena is a daemon started offroad, the pairing dialog creates a pairing token
without an ignition check, and Prime polling runs while offroad and awake.
Athena's onroad flag changes TCP keepalive settings, not eligibility to connect.

Keep retries for coverage gaps even with Prime. Export experimental bulk footage
over the LAN; no ALPR upload, paid service, or production inference is enabled.

## Local evidence

Raw logs and validation outputs are on USB at
`/mnt/algo14/comma3-diagnostics/2026-09-06/`. Recordings, checksums, model files,
and ALPR outputs are under `/mnt/algo14/comma3-alpr/`. These are local artifacts,
not repository contents. Storage pressure (~10% free on device during inspection)
is separate from RAM pressure; retain the existing deleter's free-space floor.
