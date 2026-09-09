# CAN inspection on comma 3

Open **Settings → CAN Bus**. The inspector is read-only and uses the installed
car's matching powertrain, radar and chassis DBCs. It does not transmit CAN or
change driving controls. For the Volt, these contain 113 message definitions and
651 signal definitions; availability in a DBC does not mean the car sends them.

- **Live** shows received signals with physical units and named states. Values
  older than one second are marked stale. Tap the name for the full message,
  signal, DBC, bit position, signedness, byte order, scaling and named states.
- **Changed** keeps signals that changed at least once during this UI session.
- **Raw** shows observed messages that could not be decoded. Messages with an
  unexpected payload length remain raw instead of displaying plausible values.
- **DBC** browses definitions even without traffic. Tap **View** for every signal
  in that message; unsampled signals say **Not seen** and cannot be graphed.
  Tap **DBC** again to return to messages.
- **Coverage** shows each bus's activity, approximate frame rate, observed IDs,
  matching definitions, messages decoded at least once, unknown IDs and available
  definitions. Counts accumulate during the current UI process's inspection
  session; a decoded count is not proof that every later frame passes validation.
- **Search** accepts message or signal names, units, bus names and hexadecimal or
  decimal CAN IDs (for example `0x135`, `0135` or `309`). Space-separated terms
  must all match. Submit an empty search to clear it. Selecting a DBC message
  clears search so all its signals appear. Search entry is available when parked.

Graphs use a thick cyan line, bright axes, a current-value readout and explicit
**Freeze/Resume** and **Close** buttons. Plot taps do not close the graph. The
window is 30 seconds and history is retained during CAN interruptions. Graphs
keep at most 6,000 finite samples. Unknown messages are capped at 256 IDs per bus;
coverage reports omitted frames. Each UI update reads at most 32 CAN batches,
checking an 8 ms work budget between batches, and drops batches older than one
second. Live value measurements bypass the shared permanent text cache.

Parked inspection gets a five-minute inactivity timeout: offroad, or with fresh
valid car state below 0.1 m/s and disengaged. Movement, engagement, unavailable
onroad vehicle state, panel exit or a panel fault restore the normal timeout.
The existing onroad-transition navigation is preserved.

Run data and usability tests from a configured checkout:

```sh
.venv/bin/pytest -q selfdrive/ui/tests/test_can_diagnostics_data.py \
  selfdrive/ui/tests/test_can_diagnostics_usability.py
```

Generate illustrative previews with the real widgets and an existing desktop
display, without a CAN subscription:

```sh
DISPLAY=:0 BIG=1 SCALE=1 OFFSCREEN=1 PYTHONPATH="$PWD" \
  .venv/bin/python -m tools.profiling.render_can_diagnostics \
  --output /mnt/algo14/comma3-alpr/can-coverage-preview
```

The renderer exercises filters, message browsing, expanded decoding details,
graph controls and text-cache stability. Its frames contain synthetic data;
these images do not establish live traffic coverage on the vehicle.
