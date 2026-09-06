# Local road review server

The active workstation URL is **http://192.168.99.189:8088/**. It serves a built
React/Vite application with an Express backend. All browser assets are bundled;
the interface does not depend on a CDN or the comma device being reachable.

The workspace includes prediction-blind frame annotation, model and sampling
comparisons, the interactive radar/video example, and the written reliability
and study findings. Reports retain their original relative image references.

## Review and saving

Open **Annotate frames**, drag a box around each visible plate, and enter an
encounter ID. Reuse that ID for the same vehicle within a route. Mark a plate
readable only when you can independently transcribe its text. Check the whole
frame before marking it reviewed; empty reviewed frames are negative examples.
Zoom up to 400% to inspect original image pixels.

Edits autosave after a short pause to `/mnt/algo14/comma3-alpr/labels.json` in the
existing evaluator format. The preceding saved document is kept in
`labels.previous.json`. Atomic replacements and revision checks prevent stale
browser tabs from overwriting another save. Failed saves remain visible; use
**Download labels** to preserve your current draft before reloading a conflict.
A browser-local draft provides recovery when the last save did not finish.
The image paths, frame identity and tuning/test assignments cannot be changed
through the save API.

## Running

Use Node 22.12 or newer and keep dependencies on the USB volume. From this
folder:

```sh
npm ci --cache /mnt/algo14/npm-cache
npm run build
npm start
```

Defaults: `REVIEW_HOST=192.168.99.189`, `REVIEW_PORT=8088`,
`REVIEW_DATA_DIR=/mnt/algo14/comma3-alpr`. The data directory must already contain
`labels.template.json`, `study-results.json`, the reports, and `runs/` images.
Incoming connections are limited to loopback and `192.168.98.0/23`. This is the
local review service; it has no internet-facing deployment or external login.
Only selected reports and image assets are served, not the model cache, raw
recordings, repository files, or arbitrary directory listings.

The installed user service is `road-review.service`. It starts independently of
the terminal, restarts after failures, and is enabled at boot through user
lingering. Its unit is in `~/.config/systemd/user/road-review.service`.

```sh
systemctl --user status road-review.service
systemctl --user restart road-review.service
systemctl --user stop road-review.service
journalctl --user -u road-review.service -n 30
```

Rebuild after frontend changes. Restart the service after backend changes.
The URL uses this workstation's current LAN address; if that address changes,
update `REVIEW_HOST` in the service and its documented URL.

## Verification

```sh
npm test
npm run build
npm run test:browser
```

Browser checks require Playwright Chromium. Set `REVIEW_CHROMIUM` to an existing
compatible Chromium executable, or install Playwright's browser into a USB cache
using `PLAYWRIGHT_BROWSERS_PATH`. `REVIEW_TEST_ARTIFACTS` controls screenshot
output (defaults to the September 6 diagnostic directory on USB).

The browser suite uses isolated labels and never writes annotations into the
study. It checks report images, annotation coordinates at native zoom,
autosaving, reload, download, concurrent edits and a fresh narrow browser.
Backend tests cover persistence, backups, conflicts, annotation validation,
fixed dataset identity, file boundaries and evaluator-compatible export.
The live endpoint was also reached over SSH from another host on the subnet.

## Assisted review (default)

`/#review` now opens **Confirm close examples**. `/#manual` retains the original
independent annotation workflow and all existing human labels. Both have
persistent browser-local shadow lift, brightness and contrast controls. The
pixel transform changes display tones only; original PNG/video data is retained.

The initial assisted queue contains 45 proposed visual vehicle encounters from
505 selected frames. Vehicle YOLO11s and plate detection/OCR ran on the RTX 4090.
404 plate observations were grouped with local visual tracks. The default view
contains two close/large-plate candidates, approximately 5.6 m and 4.0 m in their
selected views, with up to eight alternate frames per encounter. Distance is an
approximate horizontal radar-to-camera match, not a calibrated association.
More large-plate candidates and the full prepared queue are available in the
selection menu. Empty sampled video frames are excluded from this default flow.

Users confirm or correct a plate box and transcript, reject a non-plate, or mark
it unreadable. Vehicle IDs are automatic; grouping and lighting overrides are
optional. Lighting uses recorded local time (America/Denver) and image brightness
as a heuristic, not a sunrise/sunset or exposure measurement. Candidate ranking
uses pixel width and sharpness, not OCR confidence as a proxy for accuracy.
A separate action records the user's explicit clear-baseline confirmation.

`assisted-v1/reviews.json` stores these assisted decisions, with revision checks,
an atomic update and a previous-copy backup. It does not replace `labels.json`
or turn machine suggestions into independent ground truth. Pending corrections
are retained in browser storage against the saved decision version. Original
annotations are never regenerated by the preprocessing commands.

Reproduce preprocessing from the repository root (bulk outputs stay on USB):

```sh
UV_CACHE_DIR=/mnt/algo14/uv-cache uv sync --project tools/alpr --extra paddle --extra assisted
PYTHONPATH="$PWD" .venv/bin/python -m tools.alpr.prepare_assisted \
  /mnt/algo14/comma3-alpr --output /mnt/algo14/comma3-alpr/assisted-v1/candidates.json
PYTHONPATH="$PWD" tools/alpr/.venv/bin/python -m tools.alpr.preprocess_assisted \
  /mnt/algo14/comma3-alpr --output /mnt/algo14/comma3-alpr/assisted-v1
```

The second step caches per-clip proposals. Use a new output directory for a
changed model or selection experiment, and copy that experiment's candidate
manifest there before running. Do not replace a live queue while it has reviews.
[YOLO11s](https://docs.ultralytics.com/models/yolo11/) weights come from Ultralytics' official assets release; its code/model
license is AGPL-3.0. Existing plate detector/OCR models retain their separately
recorded provenance. Queue metadata records versions, CUDA device and the vehicle
weight checksum. No inference is installed on the comma device.

## Vehicle 2 burst experiment

Vehicle 2's **Compare combined frames** panel compares the reference frame with
aligned average/median, frequency-weighted fusion, and mild sharpening. Seven
of its eight displayed views passed alignment; the clipped frame was excluded.
The same display controls apply to both sides. These are separate derivative
images, not replacement recordings or machine-confirmed transcripts. Existing
human decisions and the live encounter queue are unchanged.

The classical experiment uses projective ECC registration, bounded tone
matching and a conservative implementation of the idea in
[Fourier burst accumulation](https://openaccess.thecvf.com/content_cvpr_2015/papers/Delbracio_Burst_Deblurring_Removing_2015_CVPR_paper.pdf).
It uses no learned reconstruction and no OCR-guided fitting. It reduces visible
noise in this example, but the uncertain characters remain unresolved. Alignment
correlation is a registration diagnostic, not character-reading accuracy.

Both inertial streams are retained for this interval: 152 gyro and 152
accelerometer readings over the 1.4-second burst plus 30 ms margins (about
107 Hz). The uncalibrated gyro norm integrates to roughly 0.44 radians / 25° of
angular travel. This supports investigating gyro-aided motion correction; it
does not establish a calibrated camera trajectory. Accelerometer measurements
include gravity and do not directly yield image displacement.

Road-camera metadata identifies OX03C10 and records integration lines and gain.
The 14.697 ms SOF-to-EOF duration is sensor readout, **not exposure duration**.
Exposure/IMU timing, camera-to-IMU calibration and HDR readout need to be
established before an exposure-aware deblurring kernel is defensible. Gyro data
now drives a separate, explicitly uncalibrated sensitivity comparison. Relevant prior work:
[Image Deblurring using Inertial Measurement Sensors](https://www.microsoft.com/en-us/research/publication/image-deblurring-using-inertial-measurement-sensors/).

Reproduce from the repository root:

```sh
PYTHONPATH="$PWD" .venv/bin/python -m tools.alpr.audit_burst_imu \
  /mnt/algo14/comma3-alpr --encounter 00000011--c81f40f6a9--16/fcamera/vehicle-086
PYTHONPATH="$PWD" tools/alpr/.venv/bin/python -m tools.alpr.fuse_burst \
  /mnt/algo14/comma3-alpr --encounter 00000011--c81f40f6a9--16/fcamera/vehicle-086
```

Source hashes, accepted/rejected alignment details, the inertial audit and
native-resolution derivatives live in `assisted-v1/fusion/7d594befef6e/`.
`fusion/catalogue.json` exposes available comparisons through a read-only API.
Synthetic checks verify registration direction, identical-input preservation,
and the average limit of Fourier weights. Browser checks verify both images and
switching combination methods without altering independent annotations.


## Plate context and tentative readings

Assisted review accepts an optional issuing state, plate design, vehicle type,
alternate transcripts and an uncertainty note. The chosen text remains verbatim.
Tentative readings cannot enter the clear baseline; older browser submissions
preserve this context. These annotations remain separate from independent labels.

Format hints currently cover Utah's standard Skier and Arches designs only:
letter + three digits + two letters, per the
[Utah DMV Skier page](https://dmv.utah.gov/plates/license-plates/skier/) and
[Arches page](https://dmv.utah.gov/plates/license-plates/arches/), checked 2026-09-06.
Other states can be recorded without imposing an unverified pattern. Unknown
designs receive a conditional hint; personalized and other designs bypass it.
No character is inserted, O/0 substituted, or certainty raised by a format match.
State-based OCR reranking has not yet been evaluated or connected to inference.

## Gyro-guided sensitivity comparison

After the audit and fusion commands above, run:

```sh
PYTHONPATH="$PWD" tools/alpr/.venv/bin/python -m tools.alpr.gyro_deblur \
  /mnt/algo14/comma3-alpr --encounter 00000011--c81f40f6a9--16/fcamera/vehicle-086
```

The recorded local gyro averages and nominal narrow-camera geometry predict
roughly 738–1029 px/s of horizontal camera-induced motion at these plate positions.
For each usable frame, the script constructs a local motion kernel with an
**assumed** 1, 2, 4, 8 or 12 ms exposure, applies a regularized inverse filter,
then reuses the original image alignment and combines frames. The sweep is fixed
in advance and uses no transcript or OCR score. Source image and IMU hashes,
flow estimates and assumptions are retained in the report. Rerun this command
following any regenerated fusion report to restore these comparison methods.

This is not calibrated recovery: HDR exposure weights/timing, gyro bias, exact
camera-to-IMU axes, rolling shutter, radiometric response and target translation
are unresolved. The low-duration variants show little change; stronger variants
introduce doubled/ringing edges without reliably resolving the characters.
No improvement in reading accuracy has been established. Synthetic tests check
zero-motion preservation, yaw projection direction and recovery of known blur;
these tests do not establish real-plate accuracy.


## Additive review batches

The default selection is **Recommended**, with a thumbnail browser and automatic
resume at the first unreviewed recommendation. The narrow close-radar filter is
optional. Encounter numbers follow the persistent queue, so changing filters
does not rename a previously discussed vehicle. Saved decisions and original
sample IDs remain intact when new encounters are appended.

To look beyond the original radar/prior-detection selection, prepare a separate
batch. `--scan-fps 2` also visits the narrow road camera twice per second across
all downloaded segments, regardless of radar state. It retains the original
near-radar and previous plate candidates. Keep the batch under `assisted-v1`
for the image-only server route, and choose a unique ID prefix:

```sh
PYTHONPATH="$PWD" .venv/bin/python -m tools.alpr.prepare_assisted \
  /mnt/algo14/comma3-alpr \
  --output /mnt/algo14/comma3-alpr/assisted-v1/expansion-01/candidates.json \
  --scan-fps 2 --id-prefix expansion-01
PYTHONPATH="$PWD" tools/alpr/.venv/bin/python -m tools.alpr.preprocess_assisted \
  /mnt/algo14/comma3-alpr --output /mnt/algo14/comma3-alpr/assisted-v1/expansion-01
```

Screen the resulting vehicle/plate views for duplicate encounters, false boxes,
clipping and severe blur before recommending them. This screening is not a human
transcript confirmation. Save a selection JSON with `append_ids` from the new
batch and `recommended_ids` referring to retained or new IDs. Then run:

```sh
PYTHONPATH="$PWD" .venv/bin/python -m tools.alpr.append_assisted \
  /mnt/algo14/comma3-alpr/assisted-v1/queue.json \
  --source /mnt/algo14/comma3-alpr/assisted-v1/expansion-01/queue.json \
  --selection /mnt/algo14/comma3-alpr/assisted-v1/expansion-01/selection.json
```

The append command rejects colliding IDs, preserves existing entries and order,
backs up the prior queue, and atomically publishes the new queue. It never writes
either human annotation file. Batch hashes and scan counts are recorded separately
because sampled frames can overlap the original run. Do not rerun an already
appended selection; use another unique batch for further preprocessing.
