# Thermal history and parked fan trial

Local implementation only. No deployment or change to the device's running fan
curve has been performed. Physical-device fault, cold-boot and cooling validation
remain required before deployment.

Local verification: 216 tests passed across thermal/fan/power monitoring,
selfdrived and existing Aranet suites. Native Params compilation, targeted Ruff
and diff checks passed. Populated/empty history screens rendered at comma 3 size;
text bounds were checked and the populated screenshot inspected. These are local
tests and synthetic previews, not device/cooling/noise validation.

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

For a future verified-offroad deployment, create `/data/thermal-history` owned by
comma:comma, mode0755, install `system/hardware/thermal-history.service` in systemd's
persistent unit directory and enable it. AGNOS root is normally read-only: preserve
and restore that state during installation. Do not install while moving. Compile
the new Params key before running the modified hardwared/UI. Normal reboots preserve
history; OS replacements require separate verification of service installation.

Before deployment test optional recorder absent/stopped/crashed/permission denied
and cold-boot unavailable, engagement independence, native build, rendered menu
layout, and battery/onroad regressions. For cooling validation compare sunny parked
sessions using the original versus trial curve, recording temperature/RPM/watts,
and listen for noise. A temperature fall alone does not isolate fan effectiveness
from a cooler cabin or declining sunlight. Never deliberately heat-soak or cause
OOM on a vehicle to validate the feature.
