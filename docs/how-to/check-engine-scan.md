# Check-engine scanner (Volt)

The newer, locally tested [GM diagnostics extension](gm-diagnostics.md) adds a separate limited-coverage module fault survey and engine context. It has not yet been deployed.

Update: the scanner-only feature was subsequently deployed on the car's installed baseline and successfully read P0401. See [deployment and live-test results](check-engine-live-test.md). The development-branch build notes below describe the original local implementation, not the separate deployment branch.

The CAN Bus panel now has **Signals** and **Check engine** tabs. Check engine reads emissions diagnostic information from the supported Chevrolet Volt; it is not a full GM module diagnostic tool.

## Use

1. Park the car, keep it stationary, turn the ignition on, and disengage openpilot.
2. Open the CAN Bus panel, select **Check engine**, and press **Scan**.
3. Wait approximately eight seconds. **Cancel** stops an active scan. Leaving Park, moving, engaging controls, or losing fresh vehicle data also stops it.
4. Review the lamp status and stored, pending, and permanent codes, grouped by responding ECU address.

The screen shows **Unknown** or **Not read** for missing information, never an empty-code result inferred from silence. Partial results are labeled. The last successful or partial scan is saved with its time and vehicle identity and remains available after restarting. A failed or cancelled scan does not overwrite it.

`card` also scans automatically, under the same gates, once per ignition cycle and on every shift into Park (at most once per 30 s). The startup alert summarizes the saved scan: `MIL ON: <codes>` only when the lamp read says the light is on, `MIL off, codes: <codes>` for codes left on file after the light went out (stored and permanent codes outlive the light), and the scan's age when it is over 12 hours old. While the last scan read the light as on, a `CHECK ENGINE <codes>` badge shows on the driving view, above the wind badge.

Descriptions are bundled offline, with an explicit fallback for unknown codes. A code identifies a reported condition, not a confirmed failed part or a determination that the car is safe to drive. In particular, P1E00 may require additional GM-specific module diagnostics unavailable through these generic emissions queries. There is no code-clearing feature.

## Implementation and limits

- The UI uses the `ObdScanRequest` mailbox. Only `card` publishes diagnostic CAN traffic; parameter reads/writes run on its existing background thread.
- `ObdScanController` requires the supported non-passive Volt configuration, ignition, initialization, fresh control/panda/gear/wheel data, Park, speed below 0.1 m/s, and disengaged controls. Replay is disabled.
- `ObdScanner` is nonblocking, with two seconds per service, a ten-second overall deadline, at most eight responders, 512 bytes per reply, and bounded input processing.
- Queries are standard service 01 PID 01 (lamp), 03 (stored), 07 (pending), and 0A (permanent), on bus 0 at functional address 0x7DF. Responses are 0x7E8–0x7EF. Multi-frame replies receive only fixed flow-control packets at the corresponding 0x7E0–0x7E7 address.
- The matching panda firmware adds `GMSafetyFlags.READ_ONLY_OBD` (8), enabled only for this Volt fingerprint. It retains normal GM safety mode and existing actuator checks. Diagnostic transmission requires the EV/ASCM configuration, controls disallowed, standstill, and wheel data younger than 500 ms. Query transmission is limited to once per 500 ms; flow control to once per 100 ms per address.
- Firmware permits only these exact eight-byte payloads: `02 01 01 00 00 00 00 00`, `01 03 00 00 00 00 00 00`, `01 07 00 00 00 00 00 00`, `01 0A 00 00 00 00 00 00`, and physical flow control `30 00 0A 00 00 00 00 00`. Code clearing, physical diagnostic requests, security access, and session changes are not permitted by this feature.

Both the application and modified `opendbc_repo` safety sources are required. Installing only the Python/UI files does not add firmware support. The panda safety parameter alone is not a firmware-version attestation; deploy the matching firmware and application together, with the vehicle parked. This working tree has not been deployed to the car.

The safety changes are saved locally as submodule commit `34427888c607b8aedd6578efec51f38fde957d19` on `feat/volt-read-only-obd`, based on `11a5edfd7c257b604e129a0b55d5a3486ff83b32`. This commit has not been pushed: publish or otherwise transfer it before expecting another checkout to fetch the updated submodule reference. Application changes remain uncommitted in the parent working tree.

## Local validation

From the repository root, with the testing environment installed:

```sh
uv run --no-sync pytest -q selfdrive/car/tests/test_obd_scan.py selfdrive/ui/tests/test_obd_diagnostics.py selfdrive/ui/tests/test_can_diagnostics_data.py selfdrive/ui/tests/test_can_diagnostics_usability.py
uv run --no-sync python -m unittest discover -s opendbc_repo/opendbc/safety/tests -p 'test_*.py'
uv run --no-sync scons -j8 --minimal common cereal/messaging msgq_repo/msgq selfdrive/pandad
```

Build panda firmware from the `panda` directory with `uv run --project .. --no-sync scons -j8`. This builds local artifacts; it does not flash hardware.

The scanner tests cover multi-ECU and multi-frame replies, malformed input, timeouts, cancellation, permission gates, saved-result identity, background persistence, and real Cap'n Proto enum/configuration compatibility. UI tests cover manual request/cancel IDs, blocked requests, expired requests, and interrupted-scan fallback. All 46 scanner/UI tests and 14 subtests passed locally. GM firmware tests include exact byte allowlists, forbidden mutations, freshness, rate limits, timer wraparound, and existing actuator safety regressions. The full safety suite completed 3,149 tests successfully (391 skipped). MISRA analysis still reports the same pre-existing Toyota `misra-c2012-10.3` finding as the untouched submodule baseline; the changed GM code has no findings under that configuration.

For simulated UI screenshots (requires a working display):

```sh
DISPLAY=:0 BIG=1 SCALE=1 OFFSCREEN=1 uv run --no-sync python -m tools.profiling.render_obd_diagnostics --output /tmp/obd-ui-preview
```

## Parked vehicle validation — still required

1. Deploy the application and matching safety firmware together while parked, preserving a rollback version. Confirm normal vehicle identification and no new panda/safety errors.
2. Confirm Scan is unavailable with ignition off, stale data, or controls enabled. Do not perform diagnostic testing while driving.
3. With ignition on, in Park and disengaged, scan and compare lamp/codes against a trusted OBD reader. Confirm which Volt ECUs respond; generic emissions requests may not expose the underlying hybrid-module fault.
4. Inspect logged CAN traffic for only the four allowed requests and any required flow-control packets. Verify Cancel stops further requests.
5. Restart and confirm the timestamped saved report remains visible. Confirm a failed scan preserves it and that Signals still works normally.

No live fault codes or vehicle behavior have been validated by the local tests.
