# Dashcam on the device (first slice)

Settings -> Device -> **Dashcam** opens a full-screen view of the events saved by `eventd` (see
`dashcam-event-saving.md`) and plays the recording around the one you pick. Optional, offroad only, and **not yet run on
the comma 3X**: it has been exercised only by unit tests and by the real widget on a workstation display with generated
footage.

## What it does
- **Event list** (newest first, in `events.jsonl` order, so a wrong device clock cannot reorder it): stored time exactly as
  written, kinds, peak decel / horizontal g, segment name, and whether the footage is available, being recorded, or deleted,
  and whether it is flagged (`user.preserve`). A time before 2025 is labelled "(clock unset?)", one more than a day ahead
  "(clock ahead?)", and a missing one "Time unknown". A missing, empty or corrupt events file shows an empty state.
- **Player** for the selected event, from the segment's `qcamera.ts` (526x330). If the event is within 15 s of the start
  or end of its segment, the neighbouring segment is joined on. It opens paused 5 s before the event.
  Segments being written (a `*.lock` file) or without video are never opened.

| Control | Does |
|---|---|
| Play / Pause (or tap the picture) | start or hold playback; at the end, Play starts again from the beginning |
| `< frame` / `frame >` | exactly one frame back / forward (pauses) |
| `-5 s` / `+5 s` | jump in video time, clamped to the clip |
| 0.1x 0.25x 0.5x 1x 2x | playback rate |
| Scrub bar | drag to seek (playback is held while the finger is down and resumes if it was running). Red tick = the event |
| Protect | sets `user.preserve` on the event's segment (the deleter keeps it and the two before it, within its 15 GiB budget); the flag is read back before it says "Protected" |
| Refresh | re-reads the event file and footage state |
| Close | back to Settings |

A strip above the scrub bar shows speed, brake and blinkers from the qlog `carState` (about 10 Hz) when the segment has
a readable qlog. The event marker needs the qlog's `qRoadEncodeIdx`; without it the clip opens at the start with no marker.

## Limits of this slice
- Low-resolution qcamera only (no full-resolution road/wide/driver view, no zoom, no graph beyond the speed strip).
- Not validated on the device: GPU texture upload on the 3X, real decode speed, touch feel, the screen timeout override
  (300 s, kept alive while playing), and engagement behaviour with the screen present.
- "Protected" is the deleter's flag, not a guarantee: beyond the 15 GiB preserve budget the oldest flagged footage is
  released again.

## How it fails safe
The UI process is a driving process, so the screen is built not to be able to hurt it.
- **Entry**: `device.py` imports the screen lazily inside `try/except Exception`; any failure shows "Dashcam unavailable.
  Driving is unaffected." The button is enabled only offroad.
- **Offroad only**: the screen closes itself on the first frame where `ui_state.started` is true, frees its texture, stops
  its worker and restores the screen timeout.
- **Never raises into the main loop**: `render`, `_update_state` and `_render` each catch `Exception`, log with
  `cloudlog.exception`, and close the view.
- **No blocking on the render thread**: reading the event file, checking segments, indexing and decoding video, reading the
  qlog and setting the flag all run on one worker thread that calls `drop_realtime()` (so it does not inherit the UI's
  `SCHED_FIFO`) and runs at nice 10. The two threads exchange whole objects with no locks; decoded frames live in a cache
  capped at 60 MB.
- **Bad files**: missing, zero-length, truncated, corrupt or oversize video and qlog become a message in the video area. A
  damaged stretch becomes a gap that playback skips. A decoder that stops answering pauses playback after 6 s.
- **Heat**: decoding and playback stop while `deviceState.thermalStatus` is not `ok`.
- No new Params keys, no cereal changes, no native code, nothing written except the `user.preserve` flag you ask for.

## Tests
- `system/review/tests/test_player_*.py`: event parsing, clock and stepping math, frame cache bounds, decoding a generated
  H.264/MPEG-TS clip (seek, step, corrupt, empty, truncated), the session and worker, layout and touch routing.
- `selfdrive/ui/tests/test_dashcam_entry.py`: the Device-panel entry survives the screen failing to import or construct;
  player logic imports no UI code; the widget keeps its guards.
- `selfdrive/ui/tests/test_dashcam_native.py` (needs `DISPLAY`; run with `DISPLAY=:0 BIG=1 SCALE=1 OFFSCREEN=1`): the real
  widget against generated footage on a workstation, including touch, texture upload, close on `started`, and exceptions.
- `python -m openpilot.tools.profiling.render_dashcam <dir>` writes **synthetic** preview PNGs (made-up footage and events,
  stamped as such). They show layout and wording only.

## Before it goes on the car
Parked, ignition off, per `AGENTS.md`. Open the screen with no events, with a deleted segment, and with a real event;
check it closes when the car is started; then confirm a normal drive and engagement afterwards.

## Next slice
Full-resolution road/wide frames (decode `fcamera.hevc`/`ecamera.hevc` with the same index-and-cache design, reading
`roadEncodeIdx`), zoom and pan, and a graph of the incident (speed, decel, accelerometer) from the rlog.
