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
  selfdrive/ui/tests/test_can_odometer.py
```

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
