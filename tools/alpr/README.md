# Offline comma video ALPR study

This is a workstation experiment, separate from driving processes. It copies
completed recordings, runs local open-source models, and produces reviewable
predictions. Nothing here enables on-device ALPR, cloud uploads, or a plate
database. Keep data, environments, and model caches on attached USB storage.

## Reproduce

Run commands from the openpilot repository root. The export uses ordinary
Python; indexing uses openpilot's environment for the cereal schema. Recognition
uses the separate locked environment in this directory.

```sh
UV_CACHE_DIR=/mnt/algo14/cache/uv uv sync --project tools/alpr --extra paddle --frozen

.venv/bin/python -m tools.alpr.export \
  --host comma@192.168.98.187 --output /mnt/algo14/comma3-alpr/2026-09-study

PYTHONPATH="$PWD" .venv/bin/python -m tools.alpr.index /mnt/algo14/comma3-alpr/2026-09-study

PYTHONPATH="$PWD" tools/alpr/.venv/bin/python -m tools.alpr.run \
  /mnt/algo14/comma3-alpr/2026-09-study --output /mnt/algo14/comma3-alpr/runs/s-native-5fps

PYTHONPATH="$PWD" tools/alpr/.venv/bin/python -m tools.alpr.run \
  /mnt/algo14/comma3-alpr/2026-09-study --output /mnt/algo14/comma3-alpr/runs/t-native-5fps \
  --detector yolo-v9-t-640-license-plate-end2end --ocr cct-xs-v2-global-model

PYTHONPATH="$PWD" tools/alpr/.venv/bin/python -m tools.alpr.run \
  /mnt/algo14/comma3-alpr/2026-09-study --output /mnt/algo14/comma3-alpr/runs/s-tiled-subset \
  --tiled --limit-segments 3 --limit-frames 200

PYTHONPATH="$PWD" tools/alpr/.venv/bin/python -m tools.alpr.paddle_compare \
  /mnt/algo14/comma3-alpr/runs/s-native-5fps --output /mnt/algo14/comma3-alpr/runs/paddle-shared-crops

PYTHONPATH="$PWD" .venv/bin/python -m tools.alpr.review \
  /mnt/algo14/comma3-alpr/runs/s-native-5fps --output /mnt/algo14/comma3-alpr/review.html

PYTHONPATH="$PWD" .venv/bin/python -m tools.alpr.evaluate \
  /mnt/algo14/comma3-alpr/runs/s-native-5fps /mnt/algo14/comma3-alpr/labels.json \
  --split test --output /mnt/algo14/comma3-alpr/evaluation-s.json
```

Use `--host-key-alias` only for a previously verified identity when an existing
SSH tunnel name already identifies this device. The exporter requires strict
host-key verification and does not trust an unexpected replacement key.

## Export and timing

The first export freezes thirty evenly spaced retained segments in a manifest,
covering thirty nominal minutes from both narrow (`fcamera`) and wide (`ecamera`)
road cameras, plus qlog/rlog. No driver camera is copied. Selection spans the
retained time range; it is not a guarantee of a balanced lighting/traffic sample.
Review the health summary and expand deliberately if a condition is missing.

Only completed segments without lock files qualify. The device must be offroad;
transfer checks this before files and every five seconds during a file, and stops
on a failed check or lost connection. SSH uses keepalives and remote checks have
a thirty-second deadline, so stop detection is not instantaneous. Each copied file is checked
against the source size and SHA-256 before the manifest marks it verified.
Re-run the same command to resume partial transfers. Deleted or changed sources
fail visibly. No source files are removed or protected from the normal deleter.

At the measured bitrate, both cameras consume about 145 MiB/minute plus logs:
budget about 4.5 GiB for thirty minutes. Raw footage stays on the LAN even when
Prime cellular connectivity is available. The exporter accepts an explicit SSH
host, so transport remains under the operator's control.

Indexing maps encode indices to original frame IDs and monotonic timestamps.
Consistent synchronized clocks supply wall time; missing/discontinuous clocks
are marked unavailable. Truncated logs produce warnings. The video decoder uses
nominal 20 fps for sampling/offsets; records carry original timestamps when
available. These nominal offsets are not a substitute for recorded timestamps
when diagnosing frame gaps. Rerun indexing after an export completes.

Inference checks input hashes and records package versions, model hashes,
provider configuration and per-clip completion markers. Re-running identical
configuration resumes completed clips. Changed configuration requires a new
output directory; updated timing indices cause affected clips to be recomputed.
Do not run two writers against the same output directory.

## Models and comparison

- **Primary:** FastALPR 0.4.0, YOLOv9-S 608 detector, CCT-S v2 OCR.
- **Speed comparison:** YOLOv9-T 640 detector, CCT-XS v2 OCR.
- **Independent OCR:** PaddleOCR 3.7.0 / PaddlePaddle 3.3.1,
  `PP-OCRv6_medium_rec`, on the exact crops from the primary detector.

ONNX models use the workstation GPU; CUDA provider failure is an error rather
than an unreported CPU fallback. `--provider cpu` is available. The locked Linux
environment includes ONNX Runtime GPU and its CUDA/cuDNN dependencies. Paddle
uses CPU with four threads, so its latency is not a GPU speed comparison.

The default detector downsizes a full native frame. `--tiled` uses overlapping
640-pixel windows with duplicate suppression to retain more small-plate detail.
Initial sampling is 5 fps; use `--sample-fps 20` on a bounded subset to assess
temporal loss. Neither upscaling nor OCR can recover plate characters absent
from the source pixels.

Predictions include per-frame boxes/text/scores, PNG crops, track summaries,
JSONL, CSV and a local HTML report. Tracks use short-lived box overlap, with no
identity association across cameras or segments. An accepted candidate requires
three agreeing observations and a mean score of at least 0.85. This is an
exploratory rule, not an accuracy guarantee or a calibrated probability.

Per-clip metrics include elapsed time, mean/p95 processing latency and peak
process RSS. Processing includes crop/context output, and the first frame may
include runtime warmup. Run comparisons without competing GPU workloads for
meaningful timing; these desktop results cannot establish comma3 feasibility.

## Independent labels and accuracy

Open `review.html` locally. It presents one-second context frames sampled every
five seconds by default (`--stride 1` for every context frame). It does not show
predictions. Draw all plate boxes, transcribe readable text, mark lighting and
blur, and assign the same human encounter ID to a vehicle across adjacent
frames/cameras in the same route. Include unreadable plates and frames with no
plates; mark a frame reviewed only after examining it completely. Download
`labels.json` to save; import it into the page to resume. Closing the page without
downloading loses unsaved labels. For finer temporal review, inspect source
frames directly and extend the same JSON schema.

The target is at least 200 independently labeled readable encounters, with
day/night, narrow/wide, small/large, and sharp/blurred examples. If the retained
sample lacks those examples, report the actual counts and collect more footage.
Never fill labels with model output. This repository does not claim a measured
accuracy until those labels exist.

Route hashes assign entire routes to tune (20%) or test (80%), preventing adjacent
segments from leaking across splits. Tune thresholds only on the tuning split;
freeze them before test evaluation. With few routes the split can be imbalanced,
which calls for more data rather than moving examples based on their results.

The evaluator reports detection recall (IoU >= 0.5, one-to-one matches), exact
OCR given detection, end-to-end exact frame reads, false detections, conservative
encounter-level exact reads, false accepted tracks, and abstentions. Separators
and case are ignored; O/0 and I/1 are never substituted. Unreviewed frames are
excluded, not assumed negative. An unreadable or conflicting track match is
reported as excluded. Wilson intervals accompany rates; repeated frames and
recurring vehicles violate independence, so frame intervals are descriptive.

## Prior work, sources, and licenses

No maintained, integrated openpilot ALPR subsystem was found in the research.
[platescan](https://github.com/ryjones/platescan) is adjacent prior work worth
inspecting, rather than an established openpilot solution.

[FastALPR](https://github.com/ankandrew/fast-alpr),
[fast-plate-ocr](https://github.com/ankandrew/fast-plate-ocr), and
[open-image-models](https://github.com/ankandrew/open-image-models) publish the
detector/OCR implementations and model registries used here. Their installed
package metadata declares MIT. These package licenses do not automatically
settle every weight's training-data or upstream licensing provenance; retain
the individual model cards/release licenses when selecting redistribution or
deployment artifacts. `config.json` records the exact downloaded weight hashes.

[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) uses Apache-2.0; see the
[PP-OCRv6 documentation](https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-OCRv6/PP-OCRv6.html)
and [medium recognition model card](https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec).
The latter is a general text recognizer; only measured results on these plate
crops can determine whether it improves over CCT.

## Future deployment gate

Choose a preferred model from independently labeled test results, false-read
rates and abstention tradeoffs. Only then evaluate a separate offroad or server
job with explicit storage/retention limits. Cloud processing would need an
explicit upload choice and cellular budget. Any on-device experiment needs
separate profiling of CPU, RAM, thermals and accelerator contention, with the
driving stack stopped initially. A different CPU core does not isolate ALPR's
memory or GPU load from driving functions.
