# Using radar to prioritize ALPR footage

Radar is useful for choosing intervals from the video already recorded by
openpilot. Start with a nearby vehicle ahead, then evaluate adjacent-lane
targets. The experiment should operate offline before adding any on-device
processing or upload selection.

## Evidence in this recording

In segment `00000004--cfc3880dd5--106`, the camera shows a white pickup ahead
and a red pickup in the lane to the left. The recorded healthy radar tracks are
consistent with both vehicles' positions and motion:

| Video frame | Front track 29 range | Front plate detection width, original pixels | Left track 28 range | Left track lateral offset |
| --- | ---: | ---: | ---: | ---: |
| 1060 | 19.25 m | 41 px | 25.62 m | 3.73 m left |
| 1100 | 8.50 m | 86 px | 15.38 m | 3.23 m left |
| 1120 | 5.88 m | 111 px | 13.38 m | 2.89 m left |
| 1160 | 5.62 m | 107 px | 19.25 m | 3.18 m left |
| 1180 | 7.50 m | 82 px | 24.50 m | 3.46 m left |

This supports the proposed use: the nearby vehicle's plate occupies more image
pixels as the radar range decreases. The left target at frame 1120 is about
12.5 degrees off-axis. Another retained segment has right-side tracks around
15 m range and 5.4 m right offset. Off-axis points alone do not identify an
object as a vehicle; the camera association still needs visual validation.

Use `liveTracks`, which contains the radar points, rather than only the fused
`radarState.leadOne`/`leadTwo`. The GM interface parses twenty object slots with
range, azimuth, relative speed and track ID. It suppresses points while radar
reports a degradation condition. A fused lead with `radar=false` is a vision
estimate, not an independent radar measurement.

The GM parser puts measured range directly into `dRel` and computes
`yRel = range * sin(azimuth)` (positive left). For geometry away from the
centerline, treat this as radial range and reconstruct forward distance from
range and lateral offset. The numeric angle encoding in the DBC is not the
sensor's physical field of view.

## What coverage does this establish?

There is direct evidence of coverage extending into adjacent lanes ahead of
the car. It does not establish coverage of a vehicle directly alongside or
behind, nor guarantee detection through occlusion or on every curve.

The source comment identifies a Continental C1A-ARS3-A interface. For context,
[published ARS300 research](https://rybski.net/paul/papers/its2009_darms.pdf)
lists approximately 56 degrees of near-range coverage and 18 degrees of
far-range coverage (Table I). Those family-level specifications do not verify
the installed module's part number, firmware mode or calibrated coverage.
The observed tracks are the stronger evidence for this particular recording.

## Proposed first experiment

Across the thirty downloaded segments, 1,111 of 23,977 radar snapshots (4.6%)
contained a point at 5–25 m with lateral offset within 1.8 m. Expanding the
lateral corridor to 5.5 m selected 3,354 snapshots (14.0%). These are raw
snapshot fractions, not unique vehicle counts or measured compute savings:
persistence, pre/post buffers, camera fallback and readable-plate recall have
not yet been evaluated. Radar reported no errors in these sampled messages.

1. Select healthy radar tracks around 5–25 m away that persist for at least
   0.3–0.5 seconds. Begin with a narrow central corridor, then test side targets
   separately. These are exploratory thresholds, not established optima.
2. Retain a short interval before and after each encounter from existing video.
   Relative speed indicates whether the object is approaching or leaving the
   useful range. Include stopped vehicles; absolute motion is not a requirement.
3. Run plate detection on the selected frames. Rank crops by actual plate size,
   sharpness, exposure and viewing angle, then run OCR and combine observations.
   Radar measures object position and motion; it cannot establish plate visibility.
4. Compare the selected intervals with an ungated video baseline. Measure
   processing/upload reduction and independently labeled readable encounters
   lost by the selection rule. Retain background samples to expose misses.
5. Keep a camera-based fallback for absent, stale or degraded radar. The prior
   radar faults make an exclusive radar requirement unsuitable without evidence.

Radar-to-image projection could later identify useful horizontal regions, but
accurate projection needs camera intrinsics, radar/camera alignment, mounting
offsets and timestamp synchronization. Radar generally has no plate-height
measurement; a radar return should not be treated as the exact plate location.

The current code still analyzes the full selected videos. No radar gate has been
added to the driving stack, logger, uploader or ALPR inference pipeline. The
local audit is saved as `/mnt/algo14/comma3-alpr/radar-audit.json`, with its
reproduction script in the September 6 diagnostic directory.
An interactive five-frame example is saved as
`/mnt/algo14/comma3-alpr/radar-example.html`; its lane spacing is illustrative,
and the radar map is not a calibrated overlay on the camera image.
