# CAN debugger usability

The comma 3 CAN panel now has wider bus buttons and the shorter Radar label,
reserves space for decoded values and Graph, and exposes complete message/signal
names when a row is tapped. Units and named states come from the three matched
GM DBCs; numeric values remain separate for graphing. Unknown traffic stays raw.

Graphs use a thick cyan line, bright axes, a current-value readout and explicit
Freeze/Resume and Close buttons. Plot taps do not close the graph. The window is
30 seconds, constant values receive padded bounds, and history is retained during
CAN interruptions. The buffer is bounded to 6,000 finite samples.

Parked inspection gets a five-minute inactivity timeout: offroad, or with fresh
valid car state below 0.1 m/s and disengaged. Movement, engagement, unavailable
onroad vehicle state, panel exit or a panel fault restore the normal timeout.
The existing onroad-transition navigation is preserved.

This update changes CAN inspection only. It includes no braking controller tuning,
personalized learning, braking-analysis scripts or braking-review web pages. It
requires no additional driving recordings. These files are identical to the
previously tested CAN implementation on the diagnostics investigation branch.
Host validation covers decoding, numeric graph values, timeout selection, bounded
buffers, desktop rendering, and Close/Freeze/plot-tap behavior. On-device runtime
validation has not been performed; publishing the branch does not establish that
the device has installed it.

Run the CAN tests from a configured checkout:

```sh
.venv/bin/pytest -q selfdrive/ui/tests/test_can_diagnostics_data.py \
  selfdrive/ui/tests/test_can_diagnostics_usability.py
```

Generate illustrative previews using the changed widgets and an existing desktop
display, without a CAN subscription:

```sh
DISPLAY=:0 BIG=1 SCALE=1 OFFSCREEN=1 PYTHONPATH="$PWD" \
  .venv/bin/python -m tools.profiling.render_can_diagnostics \
  --output /mnt/algo14/comma3-alpr/diagnostics-ui
```
