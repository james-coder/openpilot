# Initial local study — September 6, 2026

Implementation is ready for further experiments. Accuracy validation and device
deployment are not complete.

## Retained sample

Thirty segments were selected. Eighteen completed segments (72 files,
2,808,270,230 verified bytes) were copied before the device stopped responding
on the LAN. These contain roughly 17.5 minutes from each road camera, plus
qlog/rlog. The remaining twelve segments are frozen in the resumable manifest.
The device rebooted once at the user's suggestion, returned on the LAN, and
subsequently became intermittently reachable before connectivity was lost.

Visual inspection includes highway daylight, low-sun glare, dusk and a short
night segment, plus an intersection with close vehicles. This is not yet a
balanced evaluation set. Driver-camera footage was not exported.

## Executed comparisons

Hardware: RTX 4090, 24 GiB VRAM. Both ONNX models reported CUDA execution
providers; PaddleOCR ran on CPU. Each row below is a different run scope.

| Run | Scope | Sampled frames / crops | Detections | Candidate tracks | Provisional accepted tracks | Processing time |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| S detector + CCT-S, 5 fps | 36 camera clips | 10,486 frames | 109 | 34 | 1 | 905 s |
| T detector + CCT-XS, 5 fps | Same 36 clips | 10,486 frames | 114 | 25 | 0 | 874 s |
| PaddleOCR v6 medium | Same S detector crops | 109 crops | Shared | 34 | 0 | 56 ms/crop mean OCR |
| S tiled, 5 fps | One minute, narrow camera | 300 frames | 135 | 43 | 1 | 103 s |
| S full frame, 20 fps | Same one minute, narrow camera | 1,200 frames | 257 | 2 | 1 | 53 s |

The untiled S run on that same one-minute narrow clip took about 26 seconds.
Tiling increased processing time about fourfold and generated many more
candidates. That alone does not show higher recall or accuracy: candidates can
include false detections and fragmented tracks. Full-rate sampling gave more
observations of the same two candidate tracks in this clip.

The provisional acceptance rule uses three agreeing observations and a mean
score >= 0.85. Scores differ between recognizers and are not calibrated against
one another. These counts **must not rank recognition accuracy**. Tune each
recognizer's acceptance threshold on independent tuning labels before comparing
test accuracy, false accepted reads and abstentions.

Peak process RSS was approximately 1.3 GiB in the ONNX runs. The WSL driver
reported per-process GPU memory as unavailable; no per-model VRAM peak is
claimed. Timing includes decoding and output writes, and is a workstation
measurement rather than evidence of comma3 real-time feasibility.

## Artifacts and remaining work

Local USB artifacts under `/mnt/algo14/comma3-alpr/`:

- `comparison.html`: S, T and Paddle predictions on matched primary crops.
- `comparison-close-vehicle.html`: tiled/native/full-rate observations of the
  same scene. Extra tiled candidates need full-context review.
- `review.html` and `labels.template.json`: 420 prediction-blind review frames,
  initially unreviewed. The zoom control exposes original pixel detail.
- `study-results.json`, `hardware.json`, per-run configs and stats: counts,
  timings, provider information, exact package versions and model checksums.
- `2026-09-study/manifest.json`, per-segment `index.json`, and `health.json`:
  verified source checksums, original frame timing and retained health events.

Independent plate transcripts have not been established. A close-plate visual
check showed that even an apparently legible plate can retain ambiguous
characters in shadow; guesses must not become ground truth. The planned
200-encounter evaluation remains pending, with actual readable counts and
condition coverage to be reported after review. No accuracy winner is claimed.

Restore device connectivity, resume the remaining export, rerun indexing and
the same S/T commands to complete their existing outputs. Paddle comparison
uses an empty output directory, so give the expanded comparison a new name.
Then label independently, tune using the designated tuning routes, and evaluate on the
test routes as described in [README.md](README.md).

Keep the current study on the workstation. Nothing in these results establishes
sufficient memory, latency or thermal margin for running ALPR alongside driving
on the comma3. No cloud service or on-device ALPR job was installed.
