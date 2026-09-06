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
