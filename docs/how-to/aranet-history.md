# Aranet4 cabin history

Settings → Device → Cabin air / Aranet4 → GRAPH (offroad menu).
CO2 is the large cyan trace; temperature is amber and humidity magenta, each with its own
scale. Windows: 2 hours, 24 hours, 7 days, 30 days. Pause / Resume stops/restarts passive
scanning without deleting history. Missing/stale samples are labelled and outages break lines.
There are no HVAC commands, vehicle transmissions, pairing, or GATT connections.

Selected device: owner's previously identified Aranet4 2954E, CE:24:29:74:F2:C2. Another
sensor is never selected automatically. Integrations advertisements must be enabled. Current
observed measurement interval is 120 seconds; repeated advertisements are deduplicated using
their sample age. Receipt time minus advertised age is an estimate, not a sensor clock.

Storage: `/data/aranet/history.sqlite`, outside uploaded routes. At most 21,600 rows and
30 days of samples; at most one reading approximately per minute. SQLite max_page_count=1024
with default 4096-byte pages limits the database to 4 MiB; rollback journal can temporarily
use roughly another 4 MiB. Tiny status/lock/pause files are additional. No raw BLE recordings.
Pressure, battery and RSSI are retained with each sample but only CO2/temperature/humidity
are graphed. Fixed row slots reuse space rather than accumulating history indefinitely.

The collector sets Linux SCHED_IDLE, nice 19 and idle I/O priority before scanning, independently
of driving processes. The scanner inherits these settings. Both manager and systemd launches
apply them; failure to set the policy stops startup rather than scanning at normal priority.
SCHED_IDLE is a very-low-priority CPU policy, not an isolation guarantee for kernel Bluetooth
interrupts, radio coexistence, memory or shared locks. Idle I/O effectiveness depends on the
storage scheduler. Missing samples under load are preferable to competing with driving tasks.
The collector uses bounded
packets, database and graph rendering. Manager starts `aranetd` on TICI only where the prior
Bluetooth helper `/data/bluetooth-test/probe.py` is installed. This is installation-specific,
not general stock-AGNOS Bluetooth support. That validated helper handles firmware initialization
only when fresh telemetry confirms ignition off. If the controller is missing while driving,
the collector waits rather than resetting it. Reception can continue during a drive.

For initial deployment without restarting manager, `system/manager/aranet.service` can be
installed in `/run/systemd/system/` and started. This is a temporary launcher; after reboot,
openpilot manager owns the collector. A nonblocking lock prevents two collectors. The runtime
service is limited to 96 MiB memory / 10% CPU; manager launches use the collector's own bounded
data structures and idle scheduling, not those systemd limits. Do not run other BLE experiments
concurrently with the collector; pause it first.

Remote pause: create `/data/aranet/paused`; resume: remove that specific marker. The directory
must be writable by the UI user (`comma` on this installation). Retained history stays available
if Bluetooth fails. BlueTooth cold-boot/long-drive endurance remains to be established; a working
short capture is not proof of hours-long reliability. CO2 is not CO detection or a safe-driving
assessment. There is no automatic ventilation yet.

Validation: `pytest selfdrive/car/tests/test_aranet.py`; synthetic UI preview:
`BIG=1 SCALE=1 OFFSCREEN=1 python -m tools.profiling.render_aranet /tmp/aranet-preview.png`.
