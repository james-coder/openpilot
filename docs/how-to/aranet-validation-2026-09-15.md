# Aranet repair validation — 2026-09-15

## Installed behavior

The recorder runs as comma under its own optional systemd unit. A separate root
service owns only Bluetooth initialization/passive reception and forwards the
selected sensor over a bounded, receive-only local socket. Neither is registered
with openpilot manager or required for driving startup. The earlier exact-name
aranetd watchdog exemption remains; no other driving checks were weakened.

The complete feature, UI integration, installer, services and tests are tracked
on both development and device deployment branches. The device updater's clean
staging copy was inspected and contains the feature; it no longer depends on
untracked recorder/menu files. Prior device-local Aranet files were preserved in
the Git stash named `aranet-before-service-migration`.

Validated runtime code: device commit `18a1a42b1`, development `e02e67335`.
Subsequent validation-report commits do not change runtime code.

## Checks actually performed

- **127 host tests and 127 device tests passed**, including the actual selfdrived
  event path/state machine with optional failures, and preservation of camera,
  CAN and other driving-process failure behavior. Lint and diff checks passed.
- Device service definitions passed systemd verification. Both units are enabled.
  AGNOS root was restored to read-only after the narrowly scoped unit installation.
- Firmware/tool assets were checksum-verified. No kernel/Panda flash occurred.
- Real Aranet readings were stored: **740 → 695 → 676 ppm**, about 120 seconds
  apart, RSSI **−61/−67/−62 dBm**. These were distinct measurements, not duplicate
  advertisements. Existing history was retained, with comma-owned storage files.
- The actual graph widget was rendered on-device against the live database and
  visually inspected. It displayed 676 ppm, 41.9°C and 16% RH with contrasting
  traces and gaps preserved. Synthetic normal/error screen renders were inspected
  separately; they are not substitutes for the live-data check.
- Pause stopped the scanner, retained the same HCI attachment process, and
  displayed paused status. Resume restarted scanning and reception.
- The recorder was deliberately killed while safely offroad. Systemd reported
  one automatic restart; recording resumed. Manager remained alive, had no failed
  expected processes, and had no Aranet process entry.
- A subsequent **warm device reboot** changed the boot ID. Both services started
  without an SSH startup command. History grew from **39 to 40 rows**, with a new
  **688 ppm / −67 dBm** reading. The device retained the expected code commit and
  its root filesystem remained read-only.
- Deployments, installation and reboots were gated on fresh valid offroad/Panda
  telemetry, ignition off and controls disallowed. Panda reported noOutput during
  the pre-deployment check. No vehicle commands or cruise-behavior changes were made.

## Resource evidence and limits

A 10-second steady-state sample measured approximately **0.1% of one CPU core**
for recording and **1.2%** for the Bluetooth helper plus children. Memory was
about **7.3 MB** and **20.9 MB**, respectively. Every observed feature process
used Linux SCHED_IDLE (policy 5) and nice 19. Kernel memory.max files confirmed
the **96 MiB / 128 MiB** limits.

Important exception to the initial plan: this 4.9 kernel exposes only the memory
controller through cgroup v2. Although the units request 5% CPU quotas, cpu.max
does not exist and those quotas are **not enforced**. We did not alter the kernel
or cgroup hierarchy to force them. Idle scheduling and bounded work remain active;
the short usage sample is not an endurance guarantee or proof of zero driving impact.

## Additional startup problems found and corrected

- The original recorder failed on a root-owned collector lock; history/status
  ownership was also wrong for its comma user.
- An unprivileged collector could not initialize the Bluetooth power device.
- BlueZ btmgmt hung with systemd's /dev/null stdin. A read-only comparison
  reproduced a timeout with /dev/null and immediate success with an EOF pipe.
  Production tool launches now use that EOF pipe and have regression coverage.
- HCI presence alone can mean DOWN INIT, not readiness. Startup now verifies
  UP/non-INIT before the LE-enable step.
- Unit boot ordering was corrected to avoid an After/WantedBy target cycle.
- Boot-time clock correction and malformed/null history/status values are handled
  so future samples do not look fresh or plot beyond the graph.

## Still unverified / outside this repair

- A physical full-power-disconnection cold boot (the performed reboot was warm).
- Post-repair driving engagement and hours-long Bluetooth/Wi-Fi/GPS coexistence.
- Every physical fault combination; tests and the performed fault injection are
  explicitly scoped above, not a general safety certification.
- CruiseMismatch/ACC observations, HVAC control and EEG were intentionally unchanged.

Installation, rollback and storage details: [Aranet history](aranet-history.md).
