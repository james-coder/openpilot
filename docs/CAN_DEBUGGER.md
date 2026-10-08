# CAN inspection on comma 3

Open **Settings → CAN Bus**. This full-screen inspector reads the installed car's
powertrain, radar and chassis DBCs. It does not transmit CAN, change panda bus
configuration or affect driving controls. For the Volt, the installed DBCs contain
113 message definitions and 651 signal definitions. A definition does not mean
the car actually sends that message. The inspector also loads a Volt-only
odometer extension, bringing its catalog to 114 messages and 653 signals.

## Odometer

The header shows whole miles and the age of the latest reading. Search
**odometer** in Browse or the DBC catalog to see **OdometerMiles** and
**OdometerKm**, save either to Favorites, graph it, or inspect its raw bytes and
bit definition. Fractional signal values retain one decimal place; the header
truncates to completed miles. Freeze holds the reading and age along with the
rest of the screen.

On this Volt, bus 0 broadcasts CAN ID **0x120** every approximately five seconds.
The first four bytes form an unsigned big-endian counter in **1/64 km** units;
miles use the exact conversion of 1.609344 km per mile. This matches the passive
decoder in [OVMS's Volt implementation](https://github.com/openvehicles/Open-Vehicle-Monitoring-System-3/blob/master/vehicle/OVMS.V3/components/vehicle_voltampera/src/vehicle_voltampera.cpp).
The local check covered 652 frames in 55 recorded segments: no decreasing
readings, and distance increments agreed with integrated vehicle speed to about
1%. A current dashboard comparison remains the check on the absolute reading.
The historical mileage supplied by the owner is not used as an offset or to
generate an estimate.

The inspector-only `volt_odometer.dbc` supplies the definition; driving DBCs and
parsers are unchanged. No diagnostic request or GMLAN switching is needed.
The five-byte message's last byte remains undefined because its meaning has not
been verified. Unexpected lengths and an all-one counter show raw/unavailable
data. Odometer readings become stale after 15 seconds, allowing for their slower
broadcast; stale readings remain visible with their age. No previous-session
reading is substituted when the inspector opens without traffic.

## Finding signals

**Browse** shows received signals with units and named states. **Find** accepts
message/signal names, units, bus names and hexadecimal or decimal CAN IDs
(`0x135`, `0135`, `309`). Space-separated terms must all match. Submit an empty
search to clear it. The keyboard is available when parked.

Use **Bus** to narrow the list and **Filter** to choose:

- **Live:** received signals and raw messages. Data older than one second is
  explicitly marked stale.
- **Changed since reset:** signals that changed at least once after resetting
  the baseline, including changes that returned to the original value. Signals
  first received after reset are marked **New**.
- **Raw:** messages without a matching definition or valid payload length.

Tap **Save** beside a signal to add it to **Favorites**. Up to 24 signals persist
in selection order across inspector exits and reboots, validated against the
current car and DBC. Saving another car's favorites replaces the previous car's
list. Favorites are independent of Browse's search and filters.

Tap a row for signal details, **Graph**, **Message**, or **Decoding details**.
Back restores the previous Browse list and scroll position. **More → DBC
definitions** includes unsampled messages; **Signals** lists every definition
in a message, with **Not seen** until received. Decoding details show byte order,
start bit, width, signedness, scaling and named states.

## Freeze and compare

**Freeze** holds one timestamped snapshot across values, message counts, raw
bytes, bit activity, coverage and graphs. The receiver continues collecting
bounded live data. **Resume** returns to current values. Baseline/activity resets
and graph selection are disabled while frozen.

**Compare** displays up to two vertically stacked graphs with independent units
and scales, a shared cursor and a 10- or 30-second window. Choose Signal 1/2;
favorites appear first, followed by the remaining DBC signals. Unsampled choices
wait for incoming data. Enum signals use step plots. Gaps longer than one second
are not connected (15 seconds for the slowly broadcast odometer). Tap a plot
to inspect real samples and their ages; while frozen, **Previous sample / Next sample** moves the shared cursor precisely.

## Messages and bits

Message details include received count, approximate frame rate, sample age,
received/expected payload length, last successful decode and raw bytes.

**Bits** shows four bytes per page, each with eight large touch cells. Previous /
Next supports payloads through 64 bytes. Cyan outlines identify DBC-defined bits,
including signals crossing bytes and Motorola byte order. The selected signal's
bits are highlighted. Amber underlines mark recent changes. **Dim defined**
helps locate unexplained activity. Tap a bit for its signal association and
change count; undefined bits explicitly say they are undefined in the installed
DBC. **View signal** opens the associated definition. Activity tracking starts
when the live Bits view opens; it cannot recover earlier changes from a frozen
snapshot. Reset activity starts a new baseline for the selected message.

## Diagnostic traffic

At boot a factory tester sweeps module diagnostics on bus 0. Frames on the diagnostic IDs below show a
label and a one-line decode instead of only raw bytes: in the raw row's subtitle, in the message screen's
**Diagnostic ID**, **Diagnostic note** and **Diagnostic frame** rows, and in Find (try `hmi`, `tester`, `bcm`). No DBC is changed
and nothing is transmitted.

| IDs | Meaning | Basis |
| --- | --- | --- |
| 0x241 BCM, 0x242 PSCM, 0x248 HVAC A26 (low-speed bus), 0x251 HVAC K33 (low-speed bus), 0x252 HMI, 0x254 Amplifier, 0x7E0 ECM, 0x7E1 HPCM, 0x7E4 HPCM2 (hybrid battery module), 0x7E5 EBCM, 0x7E7 BECM (battery energy control module) | tester request | owner's GDS2 analysis (address and module only) |
| 0x541 BCM, 0x552 HMI, 0x5E8 ECM, 0x5E9 HPCM, 0x5EC HPCM2, 0x5ED EBCM, 0x5EF BECM | module reply, unsegmented (`81` DTC report frames) | **derived**, shown as `(derived)` |
| 0x641 BCM, 0x648 HVAC A26, 0x652 HMI | module reply, segmented (ISO-TP, e.g. `$22` answers and negative replies) | **derived** |
| 0x651 HVAC K33 | module reply, segmented | answered on the car in the owner's HVAC testing; the only reply not marked derived |
| 0x242 / 0x542 / 0x642 | request, unsegmented reply, segmented reply: the Long Range Radar Sensor Module (`Radar`) on bus 1 (object detection), otherwise the Power Steering Control Module (`PSCM`; the same ID also reaches a Keyless Entry module on the low-speed bus) | request GDS2 analysis, replies **derived** |
| 0x7E8-0x7EF | OBD-II response (request + 8); the module is named only when that request ID is in the table (0x7EA, 0x7EB, 0x7EE stay `unknown ECU reply`) | standard, module derived |
| 0x101, 0x7DF | tester broadcast (target byte first, 0xFE = all modules), OBD-II functional request | GDS2 analysis / standard |

Reply IDs for diagnostic-report (`$A9`) frames use the unsegmented 0x5xx IDs, while `$22` replies use the
segmented 0x6xx IDs. That split is inferred from the recorded sweep and the request/reply pairing, not confirmed
for every module.

Any other ID stays unlabeled until the owner confirms it, however close it is to a listed one (for example 0x249,
0x24B, 0x553, 0x649). Every ID in the recorded boot sweep is now labeled. Examples:

- `HMI <- tester: read DTCs by status (A9 81 9A)`
- `HPCM2 <- tester: read data by ID (22 43 56)`
- `BCM (derived) -> tester: DTC report: C0750, type 03, status 19` (GMLAN `81` report frames on 0x5xx; the code
  uses the usual P/C/B/U letters, type and status stay raw hex)
- `HMI (derived) -> tester: negative reply to read diag info: response pending (7F A9 78)`
- `unknown ECU reply: positive 0x22 read data by ID (62 43 2F)`

The decoder reads ISO-TP single, first, consecutive and flow-control frames and names GMLAN/UDS services, OBD-II
modes and common negative-response codes. It does not decode data identifiers, PIDs, status bits or DTC
meanings. Malformed, short or oversized frames get a plain description, never an error. It is a constant-time
table lookup per frame (about 1 microsecond) with no cache, and it runs only for undefined frames, so the
8 ms update budget and the 256-unknown-ID-per-bus cap are unchanged. The cap is still first come, first served.
The code is `selfdrive/ui/layouts/settings/can_diag_decode.py`; to label another ID, add it to its tables once the
owner has confirmed it.

**More → Bus coverage** separates observed IDs, matching definitions, successful
decodes and unknown traffic. Counts cover this inspection session. A successful
decode count does not mean every later frame passes validation.

## Resource limits and lifecycle

The inspector reads at most 32 CAN batches per UI update, checking an 8 ms budget
between batches, and rejects invalid batches or timestamps outside the previous
second. Unknown IDs are capped at 256 per bus; coverage reports omitted frames.
Only two selected graph histories are retained (6,000 samples each), along with
one frozen snapshot. Bit activity is tracked only for the selected message.
Dynamic text measurements bypass the shared permanent text cache.

Parked inspection gets a five-minute inactivity timeout: offroad, or with fresh
valid car state below 0.1 m/s and disengaged. Movement, engagement, unavailable
onroad vehicle state, exit or a fault restore the normal timeout. Normal onroad
navigation is preserved, including exiting the embedded search keyboard. Leaving
the inspector releases its CAN subscription, session and histories. The hidden
Settings sidebar does not receive touches behind the full-screen inspector.

There is no on-device webserver or QR handoff. Desktop previews below are
illustrative renders of native widgets with synthetic data, not vehicle footage.

## Validation and previews

Run data and native touch checks from a configured checkout with a desktop display:

```sh
DISPLAY=:0 BIG=1 SCALE=1 OFFSCREEN=1 PYTHONPATH="$PWD" .venv/bin/pytest -n0 -q \
  selfdrive/ui/tests/test_can_diagnostics_data.py \
  selfdrive/ui/tests/test_can_diagnostics_usability.py \
  selfdrive/ui/tests/test_can_inspection.py selfdrive/ui/tests/test_can_touch.py \
  selfdrive/ui/tests/test_can_odometer.py selfdrive/ui/tests/test_can_diag_decode.py
```

`test_can_diag_decode.py` is pure Python and also runs without a display. It replays the recorded boot sweep
(`opendbc_repo/opendbc/car/tests/gm_diag_sweep_fixture.json`); it does not validate the on-device panel or any
live vehicle traffic.

Generate previews and run layout, control and text-cache checks:

```sh
DISPLAY=:0 BIG=1 SCALE=1 OFFSCREEN=1 PYTHONPATH="$PWD" \
  .venv/bin/python -m tools.profiling.render_can_diagnostics \
  --output /tmp/can-touch-preview
```

Run the synthetic decode/memory profile (opens no CAN socket):

```sh
PYTHONPATH="$PWD" .venv/bin/python -m tools.profiling.profile_can_inspection --iterations 24000
```

The synthetic checks establish bounds and interaction behavior, not coverage of
all live vehicle traffic or human evaluation of the physical touchscreen.
