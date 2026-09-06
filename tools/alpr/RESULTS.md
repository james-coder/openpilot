# Initial local study — September 6, 2026

Implementation is ready for further experiments. Accuracy validation and device
deployment are not complete.

## Retained sample

All thirty selected segments completed export after the device returned home:
120 files, 4,287,737,290 verified bytes. These include both road cameras and
qlog/rlog. The export resumed from its frozen manifest after the earlier LAN
interruption. Further workstation analysis does not require device connectivity.

Visual inspection includes highway daylight, low-sun glare, dusk and a short
night segment, plus an intersection with close vehicles. This is not yet a
balanced evaluation set. Driver-camera footage was not exported.

## Executed comparisons

Hardware: RTX 4090, 24 GiB VRAM. Both ONNX models reported CUDA execution
providers; PaddleOCR ran on CPU. Each row below is a different run scope.

| Run | Scope | Sampled frames / crops | Detections | Candidate tracks | Provisional accepted tracks | Processing time |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| S detector + CCT-S, 5 fps | 60 camera clips | 15,994 frames | 486 | 156 | 2 | 1,369 s |
| T detector + CCT-XS, 5 fps | Same 60 clips | 15,994 frames | 476 | 128 | 1 | 1,334 s |
| PaddleOCR v6 medium | Same S detector crops | 486 crops | Shared | 156 | 1 | 62 ms/crop mean OCR |
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
Resumed jobs overlapped with export/indexing, and the final T batch overlapped
with CPU Paddle recognition. These timings are descriptive, not an isolated
hardware benchmark. The expanded Paddle run is `paddle-complete-study`;
`paddle-shared-crops` preserves the earlier eighteen-segment comparison.

## Artifacts and remaining work

Local USB artifacts under `/mnt/algo14/comma3-alpr/`:

- `comparison.html`: S, T and Paddle predictions on matched primary crops.
- `comparison-close-vehicle.html`: tiled/native/full-rate observations of the
  same scene. Extra tiled candidates need full-context review.
- `radar-example.html` and `radar-audit.json`: synchronized recorded radar
  examples and an exploratory selection audit; see
  [RADAR_PRIORITIZATION.md](RADAR_PRIORITIZATION.md).
- `review.html` and `labels.template.json`: 646 prediction-blind review frames,
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

The export is complete. Label independently, tune using the designated tuning
routes, and evaluate on the test routes as described in [README.md](README.md).
Evaluate radar selection against the same ungated videos before treating it as
a reliable way to reduce processing or uploads.

Keep the current study on the workstation. Nothing in these results establishes
sufficient memory, latency or thermal margin for running ALPR alongside driving
on the comma3. No cloud service or on-device ALPR job was installed.

## Assisted close-range review

After the first human review, the original uniform frame sampling was replaced
as the default UI by a prepared encounter queue. Original annotations remain in
`labels.json`; assisted confirmations have a separate file and provenance.

A new RTX 4090 pass processed 505 radar/plate-selected frames from 14 camera
clips. YOLO11s detected vehicles, plate detection was repeated on vehicle crops,
and OCR compared original crops with a shadow-lifted view. It produced 404 plate
observations grouped into 45 proposed local vehicle tracks in about 247 seconds.
Two selected views combine 3–8 m radar range with at least 75 original plate
pixels: approximately 5.6 m / 107 px and 4.0 m / 139 px. Each encounter offers
up to eight selected views. These are candidates for a human-confirmed clear
baseline; close range or high model confidence does not establish readability.

The UI supplies vehicle IDs and estimated lighting, and saves shadow lift,
brightness and contrast settings in the browser. Review actions confirm/correct,
reject, or mark unreadable. Optional grouping corrections handle fragmented
visual tracks. See [web/README.md](web/README.md) for reproduction and storage.

Vehicle 2 also has a classical multi-frame fusion comparison. Seven of eight
views aligned; the eighth touches the image edge and was excluded. Registered
averaging and frequency-weighted combination reduce noise, but the characters
remain uncertain. This is not a successful recognition-accuracy result. A
recorded IMU audit found substantial angular motion during the burst, supporting
further gyro/exposure calibration work. Current derivatives use visual
registration; inertial deblurring has not been applied. See the
[burst experiment notes](web/README.md#vehicle-2-burst-experiment).
