# Aranet4 cabin history

Settings → Device → Cabin air / Aranet4 → GRAPH (offroad only).
CO₂ is cyan and dominant; temperature is amber and humidity magenta, on separate
scales. Missing/stale samples are labeled and gaps are not connected.

## Ownership and driving independence

Two optional systemd services replace the experimental manager/root launchers:

- `aranet-bluetooth.service` owns the dedicated Bluetooth UART, HCI attachment,
  and passive scan. It runs privileged but never writes cabin history.
- `aranet.service` runs as `comma`, with no capabilities and only Unix sockets.
  It consumes a versioned receive-only local feed and writes bounded history.

Neither service is a dependency of manager, selfdrived, or driving engagement.
Manager no longer imports/starts the recorder. The exact-name `aranetd`
watchdog exemption remains for compatibility; all other process checks retain
their existing behavior. Opening the optional menu lazily imports its code;
missing optional modules must not break the main settings screen.

The helper accepts no device commands, pairing, GATT, or remote connections.
Only the selected Aranet4 2954E (`CE:24:29:74:F2:C2`) is forwarded, at most
once per second. Backpressure drops telemetry instead of blocking other work.
No CAN, Panda, HVAC, Wi-Fi, GPS or modem configuration changes are involved.

## Startup, failure and resources

Initialization uses the previously validated WCN3990 UART/firmware sequence:
only `/dev/btpower`, `/dev/ttyHS1` and Bluetooth rfkill entries are eligible.
Device-supplied firmware hashes, ROM/product identity, acknowledgement sequence,
and resulting patch version are checked. No ROM-only fallback or partition
flashing is performed. The existing H4 transport/sleep settings are retained;
Bluetooth idle-power/IBS optimization is not part of this repair.

Power/reset/firmware initialization requires fresh, valid device/Panda telemetry:
ignition off, offroad and controls disallowed. The gate is checked throughout
initialization. Occupied UARTs and pre-existing externally owned controllers are
not stolen. If the radio is absent onroad, logging waits for a safe offroad
opportunity. Already-initialized passive reception can continue during a drive.
A helper crash can therefore leave a gap until the next safe initialization.

Both services use nice 19, SCHED_IDLE, idle I/O priority, 30-second failure
backoff and 5% CPU quotas each. MemoryMax is 128 MiB for the Bluetooth helper
(including children) and 96 MiB for recording. These limits do not eliminate
kernel interrupt, radio coexistence or other shared-resource effects.

The SQLite database stays at `/data/aranet/history.sqlite`, outside route uploads.
Its existing schema/history are preserved: 30 days, at most 21,600 rows, 4 MiB
database (page-size aware), and approximately another 4 MiB worst-case rollback
journal. Helper logs rotate at 256 KiB with two backups (under 1 MiB total).
No continuous raw BLE or firmware-transfer dump is retained.

Status retains `time`/`message` compatibility and adds `state`,
`last_advertisement`, and `last_write`. Listening is not recording. The UI shows
safe-initialization waits, failures, paused/stale/offline states and sample age.
Local packets are bounded to 4096 bytes and independently validated by the
recorder. Errors include a bounded stage/exit/error summary instead of suppressing
all subprocess diagnostics.

Pause/resume creates/removes only `/data/aranet/paused`. Pause disconnects the
recorder, which stops passive scanning without resetting the controller. History
remains readable. CO₂ is not CO detection or a safe-driving assessment.

## Reproducible installation and rollback

The existing Bluetooth-enabled kernel is a prerequisite. No new flash is needed.
Ordinary application updates use tracked code; systemd unit installation and
root-owned assets persist outside the checkout. An OS/kernel replacement must
be checked separately; do not silently reflash it.

The installer verifies all assets before installing them in
`/data/aranet-bluetooth`:

- BlueZ 5.72 Ubuntu arm64 tools, each SHA-256 pinned in the installer. Original
  package: `bluez_5.72-0ubuntu5.5_arm64.deb`, SHA-256
  `ced50bcaee2c563ba965ff5faa70ae54fc3e22b8ebc09ccf9474beed63eda9d4`.
- Device-supplied `crbtfw21.tlv` and `crnv21.bin`, pinned in the firmware module.
  Preserve proprietary firmware on the device, not in the public git repository.
- The default source is the previously provisioned `/data/bluetooth-test`;
  `--assets DIRECTORY` accepts the same `usr/bin` and `firmware` layout.

On a safely parked, ignition-off device, after installing the tracked code and
restarting manager so its former collector is no longer running:

```sh
cd /data/openpilot
sudo env PYTHONPATH=/data/openpilot /usr/local/venv/bin/python -m openpilot.system.aranet.install
```

The installer refuses stale/unsafe telemetry, an old live manager collector,
unverified assets and symlink targets. It migrates only named history files to
comma ownership, retaining their contents. It installs/enables both persistent
units. There is no privileged or blocking hook in manager startup.

For an application release changing unit definitions, run the installer again
offroad. A clean checkout must contain the recorder, UI integration, helper,
tests, services and installer. Verify updater staging too; dirty/untracked files
are not a release mechanism.

Feature-only rollback (offroad) retains history and the engagement fix:

```sh
sudo env PYTHONPATH=/data/openpilot /usr/local/venv/bin/python -m openpilot.system.aranet.install --disable
```

## Incident and verification

The initial integration treated a dead recorder as a driving-process failure.
Recorder/storage tests missed the connection to engagement. Device logs later
confirmed root-owned `collector.lock` caused PermissionError under manager's
comma user. The database and status file were root-owned too. Correcting those
files and exempting only the optional recorder removed that engagement block,
but did not initialize Bluetooth after reboot.

The subsequent review found zero fresh samples, no HCI adapter, insufficient
Bluetooth permissions, hidden helper errors, and Aranet files missing from clean
deployment staging. This repair addresses those separate failures; service
liveness alone is never evidence of successful recording.

Tests cover firmware parsing and safety gates, protocol/measurement validation,
bounded storage/retries, missing helpers, duplicate collectors, file ownership,
optional screen imports, and actual selfdrived event/state-machine behavior.
Run:

```sh
pytest -n0 selfdrive/car/tests/test_aranet.py selfdrive/car/tests/test_aranet_service.py selfdrive/selfdrived/tests
BIG=1 SCALE=1 OFFSCREEN=1 python -m tools.profiling.render_aranet /tmp/aranet-preview.png
```

Deployment acceptance requires three distinct new sensor measurements visible in
SQLite and the graph, pause/resume, service restart, and reboot verification.
Record true cold power-up and subsequent drive/endurance results separately;
host tests or a warm reboot do not establish those results.
