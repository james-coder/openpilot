# GM read-only diagnostics extension

For the current Mode 06/PID 69/6B/readiness implementation and its pending vehicle
validation, see [GM EGR evidence](gm-egr-evidence.md). The notes below are the
historical initial deployment record; their "not yet performed" statements refer
to that initial rollout, not the later September 12 GM scan.

## Historical initial deployment record

This extends the emissions scanner with a **GM details** tab, a one-shot GM current/history fault survey, engine freeze-frame context, a parked engine snapshot, and a text/JSON report exporter. The scanner-only port was deployed September 12, 2026, with matching firmware flashed and verified. Both scanners report ready. **An expanded live GM scan has not yet been performed**; actuator control is not implemented. The previous saved P0401 result remains intact.

## What it reads

- GM faults from responding modules on the connected high-speed bus (bus 0), with base DTC, failure-type suffix, raw GM status byte, and raw reply.
- Current/history status using GM's definitions, not UDS status-bit definitions. These encodings differ.
- An explicit end-of-report marker and status-availability mask per responder. A partial stream or unsupported requested status is not an empty fault list.
- Engine freeze frame 0: triggering code, load, coolant temperature, fuel trims, manifold pressure, RPM, speed, intake temperature, airflow, commanded EGR, and EGR error, when returned by ECU 0x7E8.
- Separately labeled parked readings of coolant temperature, RPM, commanded EGR, and EGR error. These are sequential readings, not simultaneous samples or a test under road load.

For P0401, the first useful questions are whether other modules report related faults, which failure-type/status bytes they report, and what conditions the engine captured when the freeze-frame trigger code set. A freeze frame belonging to a different code must not be attributed to P0401. Zero RPM, zero commanded EGR, or a parked EGR error value is not proof of a restricted cooler or failed valve.

## Coverage is deliberately explicit

This is **not all-module coverage or a full GDS2 replacement**. It does not access low-speed CAN, wake sleeping modules, route through gateways, identify every module by name, or establish a known inventory of installed modules. Module names are not guessed from CAN addresses. A silent module may be absent, asleep, inaccessible, unsupported, or have failed to respond.

The overall GM report is always labeled limited/partial, even if every observed responder finished its list. Each module has its own completion state. The survey recognizes the standard GM UUDT address ranges (39 possible responders) and requests only faults matching current/history status; it does not request every supported-but-never-failed DTC.

Manufacturer-specific failure-record decoding, module identification, additional networks, live charts while driving, and bidirectional tests are not implemented. This version does not read Mode 06 monitor-test results or GM proprietary failure records; standard engine freeze frame 0 is the historical context currently supported.

## Safety and timing

- Manual Scan/Cancel only, with the same initialization, fresh CAN/control/panda/gear/wheel data, ignition, Park, standstill, and disengagement checks as the emissions scanner.
- Emissions and GM scans are mutually exclusive. `card` remains the sole diagnostic CAN publisher; the UI only submits a mailbox request.
- The firmware requires both `READ_ONLY_OBD` (8) and `READ_ONLY_GM_DIAGNOSTICS` (16), EV/ASCM mode, controls disallowed, standstill, and wheel data younger than 500 ms. The intended Volt safety parameter is 28.
- Exactly one additional functional frame is allowed: bus 0, ID `0x101`, payload `FE 03 A9 81 12 00 00 00`. Broadcast requests are limited to once per five seconds.
- Engine context uses bus 0, physical ID `0x7E0`, service 02 with frame number fixed at zero and the enumerated PIDs, or service 01 with PIDs 05/0C/2C/2D. No arbitrary PID/address entry is exposed. These reads share the existing 500 ms emissions-query limit. The old flow-control allowlist remains unchanged.
- Module collection lasts five seconds; each of sixteen context reads gets two seconds. Expected duration is about 37 seconds, with a hard 50-second overall deadline, 128 diagnostic frames per tick, and 128 stored records per module.
- Unsupported replies, missing data, truncation, and missing completion markers are explicit. Cancel stops further requests but cannot recall responses an ECU already scheduled.
- No clearing, programming, security access, ECU reset, tester-present subscriptions, communication control, EGR actuation, or other output control.

Safety parameters are not a firmware-version attestation: the application and matching verified firmware must be installed together. Do not deploy this development branch wholesale over the car's different driving-control branch. Port just the diagnostic changes onto `deploy/volt-obd`, as was done for the first scanner.

The original development safety changes are in opendbc commit `c65c6038` (following `34427888`). The device-compatible port is opendbc `e3cff012`, pinned by application commit `a1137316f`. Both ports were pushed to the respective james-coder GitHub repositories on branch `deploy/volt-gm-diagnostics`. The unrelated development-branch changes were not deployed.

The scanner-only port is in `/tmp/volt-obd-deploy.TUFdeg`, preserving the installed touch-first inspector and driving controls. Native params/pandad and H7 firmware builds pass. Port regression testing passes all 137 scanner/UI tests and 14 subtests, including the existing native CAN touch tests. The remaining UI failures were resolved by building visionipc and restoring/building the checkout's Git LFS font and icon assets. The touch test fixture addresses `CanSignalsLayout`, the inspector inside the diagnostic tab wrapper. GM safety tests pass (277 tests, 21 skipped); the full port safety suite also passes (3,194 tests, 392 skipped). The simulated GM-details screen renders and was visually inspected.

During deployment, fresh vehicle state confirmed Park, zero speed, controls disabled, and a 12 V supply above 13 V. The previous manager was stopped cleanly. Native params/pandad and H7 firmware were built on the comma, then firmware was flashed through the standard pandad helper and its complete signature verified. Firmware SHA-256: `c2603a85c25958fcd33f78d5131489656622968f4de857eaa86d0d00aa1c0c2c`.

On-device tests passed: 113 scanner/UI tests plus 14 subtests, and 277 GM safety tests (21 skipped). Manager restarted in tmux window `comma:gm-manager`. Post-start checks showed live valid CAN, Park, zero speed, disabled controls, GM safety parameter 28, no panda faults, zero safety-blocked transmissions, and UI/card/controlsd/pandad running. Both scanner statuses were ready; no request was pending. `ObdLastScan` retained its original timestamp, `2026-09-12T07:09:52.034213+00:00`.

Rollback binaries are in `/data/gm-deploy.bqmyBl/rollback-binaries.tar.gz` (params, pandad, signed H7 firmware, and bootstub). Previous application `0c9cb558b` and opendbc `eca95a16` remain on `deploy/volt-obd`. Roll back only while parked with stable power: stop manager, restore both previous commits and their matching binaries (or rebuild), flash/verify matching firmware, then restart manager. Do not mix application and safety versions.

No codes were cleared, no expanded diagnostic requests or actuator commands were transmitted, and no road test or full-device reboot was performed. Await confirmation of an outdoor parked setup before the first expanded live scan.

## Reports and export

`ObdScanRequest` accepts command `scan_gm`; live status is `GmScanStatus` and saved results are `GmLastScan`. Reports are bound to vehicle identity. They are independent of `ObdLastScan`; failed/cancelled attempts do not replace a previous saved result.

Run on the device after a successful GM scan:

```sh
python -m tools.car_porting.export_gm_diagnostics
python -m tools.car_porting.export_gm_diagnostics --json
```

The exporter reads saved data only; it neither starts a scan nor opens CAN. VIN is omitted unless `--include-vin` is supplied. JSON includes raw responses for technical review. Unsupported manufacturer-code descriptions remain explicitly unavailable.

## Validation

Locally: 72 scanner/UI tests and 14 subtests passed; the full safety suite completed 3,194 tests successfully (392 skipped). Changed GM firmware has 130/130 executable lines covered. Panda H7 firmware builds successfully. MISRA analysis reports only the previously documented unchanged Toyota finding (`misra-c2012-10.3`).

```sh
uv run --no-sync pytest -q selfdrive/car/tests/test_obd_scan.py selfdrive/car/tests/test_gm_diagnostics.py selfdrive/ui/tests/test_obd_diagnostics.py selfdrive/ui/tests/test_can_diagnostics_data.py selfdrive/ui/tests/test_can_diagnostics_usability.py
uv run --no-sync python -m unittest discover -s opendbc_repo/opendbc/safety/tests -p 'test_*.py'
DISPLAY=:0 BIG=1 SCALE=1 OFFSCREEN=1 uv run --no-sync python -m tools.profiling.render_obd_diagnostics --output /tmp/gm-diagnostics-preview
```

Still required before claiming expanded vehicle support: captured request/reply comparison against these formats, identification of actual responders, comparison with a GM-capable service tool, and physical UI/full-reboot/persistence checks for GM reports. Scanner-only deployment, firmware-signature verification, and live parked readiness checks are complete. No expanded live scan has been performed.

## Requested EGR active test: evidence still required

EGR actuation is not implemented or permitted by the diagnostic firmware allowlist. Public-source research on September 12, 2026 did not establish a verified 2017 Volt L3A actuator command, actual-position identifier/scaling, or an EGR-specific service-bay routine. Do not infer these from another GM engine or from generic GMLAN/UDS service numbers. Reading commanded EGR and EGR error is not commanding the valve, and is not a verified actual-position measurement.

Before implementing an active test, obtain the applicable GM Service Information procedure and verified protocol definitions, or a supervised GDS2 request/reply capture on the matching ECM/calibration. A capture alone does not establish safe preconditions. Required evidence includes ECU identification, session requirements, exact requests and replies, target bounds, feedback scaling, permissible engine/temperature/voltage conditions, duration limits, and stop/release behavior on cancellation, disconnect, process failure, or ignition changes. Do not brute-force control identifiers against the vehicle.

Any eventual implementation needs a separately gated, explicitly armed parked service mode, narrowly allowlisted firmware, watchdog/timeout validation, and supervised vehicle testing in a suitable location. Do not add automatic engine start or code clearing to this test. Valve movement alone does not establish adequate cooler flow. GM bulletin 24-NA-080 remains the verified repair-context reference for this fault.

GM provides Service Information and GDS2 through [ACDelco TDS](https://www.acdelcotds.com/subscriptions); access to the service manual does not itself guarantee access to raw proprietary command definitions. No subscription has been purchased and no actuator command has been sent.

## Sources

- GM **GMW3110 (February 2010)**, sections 4.4, 8.18, and Appendix E: functional read request, UUDT response layout, completion marker, address ranges, and status definitions. [Publicly hosted copy of GM specification](https://studylib.net/doc/26162849/gmw3110-2010).
- [Scapy's GMLAN implementation](https://github.com/secdev/scapy/blob/master/scapy/contrib/automotive/gm/gmlan.py): independently identifies read service A9 and status-mask subfunction 81. No Scapy runtime dependency was added.
- [python-OBD command tables](https://python-obd.readthedocs.io/en/latest/Command%20Tables/): standard engine PID meanings and freeze-frame command family.
- [GM bulletin 24-NA-080](https://static.nhtsa.gov/odi/tsbs/2024/MC-11010577-0001.pdf): mechanical diagnostic context for P0401; not a CAN protocol specification and not an automatic repair diagnosis.
