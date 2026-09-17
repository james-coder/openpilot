# Thermal history and parked fan trial

Installed on the comma 3X (device branch `deploy/volt-gm-egr`) on 2026-09-15,
after live ignition-off/no-output verification. Thermal changes: `f9d097791`;
Bluetooth boot-permission fix: `605091505`. No Panda firmware or safety changes.

Local verification: 216 tests passed across thermal/fan/power monitoring,
selfdrived and existing Aranet suites. Native Params compilation, targeted Ruff
and diff checks passed. Populated/empty history screens rendered at comma 3 size;
text bounds were checked and the populated screenshot inspected. These are local
tests and synthetic previews, not device/cooling/noise validation.

Device verification: native Params build; 169 tests across thermal/fan/power,
selfdrived and Aranet; actual optional thermal service SIGKILL/restart with fresh
manager health remaining normal; and a live-data offscreen UI render. History
was observed accumulating, with bounded storage and idle scheduling. These tests
do not establish physical driving engagement, cooling performance or fan noise.

The first deployment reboot exposed a pre-existing Bluetooth startup-order bug:
the root helper could create deviceState/pandaStates msgq files as root:comma 0644,
preventing comma-user manager/UI from opening them read/write. Its systemd unit
now uses UMask=0002 with Group=comma. Three isolated on-device cross-UID tests
reproduced the old failure and verified both startup orders with the fix. The
repair changed only group-write permission on the two affected shared files;
no global driving health exemption or Panda configuration change was made.
This demonstrates why optional-process health tests alone are insufficient:
shared-resource permissions and real boot ordering also require verification.

The repeat reboot passed (boot e5e6e23d-7bb2-46a9-8571-29a4794e3d4a): root-first
IPC files were group-writable, manager/device/Panda/peripheral telemetry was fresh
and valid, no expected manager process was missing, and the thermal service had
zero restarts and new timeline samples. Cellular-bound HTTPS returned HTTP 200
through ppp0 after reboot with Wi-Fi left connected for SSH. No modem changes were
made; the earlier failed-start boot had repeated PPP hangups, so one successful
reconnect is not a long-term cellular reliability guarantee. This was a software
reboot, not a physical power-removal/cold-soak test. Real-road engagement and
hot-parked cooling/noise comparisons remain unverified.

## Existing versus new recording

Existing driving route logs contain component temperatures, requested fan output,
and measured RPM. Offroad STATUS_PACKET logs normally provide ten-minute snapshots.
Neither is a permanent lifetime exposure summary.

The independent `thermal-history.service` consumes existing device/peripheral
messages, never CAN or hardware controls. Every five seconds it updates lifetime
statistics for CPU maximum, GPU maximum, RAM, PMIC maximum and modem maximum,
separately for openpilot onroad/offroad state. Offroad is not a vehicle-speed or
ignition measurement. There is **no OLED temperature sensor** in this dataset.

Persistent statistics include peaks with dates/context, worst complete 10-minute
and one-hour time-weighted means, sampled exposure and longest stretches strictly
above 50/55/60/65/70/75C, monitored seconds, sessions and continuity interruptions.
Those thresholds are reporting bins, not validated damage limits. Gaps over ten
seconds, absent data, mode transitions and process restarts break continuity.
Powered-off exposure cannot be observed. Lifetime means since recording began;
there is no automatic backfill from old logs. Wall-clock dates before 2024 are
unavailable; elapsed durations use monotonic time. Samples between endpoints are
estimates, not continuous physical measurements.

Additionally a 30-second snapshot timeline records all five temperatures, fan
request, available RPM and voltage, device watts and screen brightness. It retains
20,160 snapshots (seven days of uninterrupted recording). Session identifiers and
timestamps permit gap-aware analysis. Missing values are NULL, not invented zeroes.

Storage: summary <=64 KiB plus one fixed <=64 KiB staging file, saved once a minute;
SQLite <=8 MiB plus its bounded rollback journal (budget roughly 16 MiB), no WAL
or growing raw archive. Abrupt power loss can lose the latest minute of summaries.
Corrupt summaries are preserved, not silently reset. The recorder then stops/retries
slowly; the menu shows missing/stale/invalid history rather than blocking driving.

Settings > Device > Thermal exposure history displays the lifetime statistics.
The timeline is an SQLite analysis/export source; it is not yet graphed in the UI.

## Fan experiment

Replaces the earlier **uninstalled** 55C-start proposal. Original behavior below
70C is retained when there is no extra cooling to ramp down. Above 70C, additional
headroom increases smoothly toward 40% at 75C, with extra output changing at most
two percentage points per second. On cooling, that extra output ramps down.
The original controller is still evaluated, and cooling is never reduced below it.
This targets only comma 3/3X; comma four and all ignition-on behavior are unchanged.
The 40% cap is a test setting, not proof of quietness or OLED protection.

`ParkedCoolingDisabled=true` restores the original parked controller immediately.
The menu exposes that preference. Existing ignition-on 100% overheat override,
power saving, voltage/energy limits and shutdown logic are unchanged. Do not disable
battery shutdown to run the fan longer. No Panda firmware/safety changes are needed.

## Installation and verification

The service is intentionally NOT registered with openpilot manager or readiness
checks. It runs as comma, SCHED_IDLE/nice19, idle I/O, memory cap64MiB, with no
capabilities and only local message sockets. Its absence, crash or storage failure
must not inhibit engagement. The service does not depend on Aranet/Bluetooth.

For a verified-offroad deployment, use `system.hardware.install_thermal_history`:
it creates `/data/thermal-history` owned by comma:comma, mode0755, installs
`system/hardware/thermal-history.service` in systemd's persistent unit directory
and enables it. AGNOS root is normally read-only: the installer preserves and
restores that state. Do not install while moving. Compile
the new Params key before running the modified hardwared/UI. Normal reboots preserve
history; OS replacements require separate verification of service installation.

Before deployment test optional recorder absent/stopped/crashed/permission denied
and cold-boot unavailable, engagement independence, native build, rendered menu
layout, and battery/onroad regressions. For cooling validation compare sunny parked
sessions using the original versus trial curve, recording temperature/RPM/watts,
and listen for noise. A temperature fall alone does not isolate fan effectiveness
from a cooler cabin or declining sunlight. Never deliberately heat-soak or cause
OOM on a vehicle to validate the feature.
