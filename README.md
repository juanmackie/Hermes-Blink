# Hermes personal widget

Hermes can send one-way, accessible visual updates to one persistent Android
home-screen widget. The existing `hermes-brief` identity uses the publication
API; updates use `widget_publish` with text, safe
static SVG, or a bounded local PNG/JPEG/WebP.

Current product version and compatibility are listed in [CHANGELOG.md](CHANGELOG.md).

## Host setup

The full Linux, Windows, and TrueNAS setup, private HTTPS, pairing, persistence, and
troubleshooting guide is [docs/SETUP.md](docs/SETUP.md).

Quick start on the Hermes host:

```sh
bash scripts/bootstrap-linux.sh --json
hermes widget status
```

On Windows, run `python scripts/bootstrap.py --json` from the repository root.

## Android

The app is under `android/`. Build a debug APK with a JDK 17+ toolchain:

```sh
cd android
./gradlew testDebugUnitTest lintDebug assembleDebug
```

The release APK must be signed with the existing personal signing identity;
never commit that identity or its passwords. Until that release is built and
verified, the debug APK is for local testing only. The widget preserves the
last successful publication offline, renders visuals with fit scaling, and
opens a larger pinch-zoom view on tap. It reports `published`, `downloaded`,
`render_submitted`, and the higher-level `fetched`/`nudge_sent` receipts
separately; none of those states claims that the user saw or understood an update.

### Priority wakeups, previews, and action intents

`widget_publish` accepts `priority: "normal" | "high"`. A high publication sends only a
content-free `fetch` wake to a device-registered UnifiedPush endpoint (ntfy and other
self-hostable distributors work); the phone then pulls over the existing private HTTPS path.
The high lane is limited to six wakes per hour and thirty per day, with over-limit requests
visibly degraded to normal. A per-widget quiet-hours window can be set with
`widget_set_quiet_hours` (UTC by default; optionally set an IANA timezone).
`hermes widget wake-test` sends one content-free wake and prints the per-device receipt chain
without creating a publication revision. Android requests
battery-optimisation exemption only from the
user, then uses expedited WorkManager with an exact-alarm fallback where permitted. The app's
**Delivery diagnostics** screen shows last poll, fetch, render, and exemption state.

`widget_preview` and `POST /v1/widgets/{id}/preview` rasterise the exact proposed or current
publication at the device's registered `2x2`, `4x2`, `2x4`, `4x4`, or custom sizes. Modern
launchers may report 4×4 geometry around 300–420dp per side; the reported inventory, not the
legacy 270dp nominal box, is authoritative. Capacity
findings are warnings, never silent truncation. Raster previews use Pillow without requiring
libcairo; text has a built-in Pillow fallback, while SVG rasterisation still requires CairoSVG.
The CLI writes local PNGs:

```sh
hermes widget preview --sizes 2x2,4x2,4x4 --out ./widget-previews
hermes widget preview --publication-file proposal.json --out ./widget-previews
hermes widget publish --title "Market open" --summary "Brief" --text "..." --priority high
```

Publication files are resolved from the current directory, checkout root, or installed plugin
copy, so the command does not require a particular working directory.

Publication actions may carry stable `itemId`s. `approve`, `snooze`,
and `open` taps are durably queued as allowlisted intents; they never execute on the HTTP
server. The phone keeps a bounded retry outbox when the private path is unavailable, and the
agent reads intents with `widget_read_intents` before recording a terminal decision with
`widget_resolve_intent`. Destructive/external actions wait for explicit confirmation.

The widget has a small **Request update** action. It records a generic poke and triggers the
existing refresh routine; it does not prescribe content to the agent, which decides from its own
current context whether a new publication is warranted.

Low-stakes updates can be published as an independent `ticker` while the hero is retained.
Standing `widget_watch` rules, bounded `widget_ask` questions, provenance labels, dark
palettes, and size-keyed text variants are also available. Attention status contains
aggregate-only dwell/tap/supersession numbers, not content.
