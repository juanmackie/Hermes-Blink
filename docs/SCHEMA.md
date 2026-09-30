# Widget publication contract

Hermes Blink publishes one accessible update per widget. The Android app fetches and renders
publications from `/v1/widgets/<id>/publication`; widget IDs and settings are stored separately.
The former tree-layout API, parser, validator, and JSON schema have been removed.

Publish from the agent with `widget_publish`. Content has exactly one source: plain `text`,
static inline `svg`, or a local `file_path` containing PNG, JPEG, or WebP. `summary` is required
for accessibility. The API validates size, media type, expiry, action IDs, and SVG elements before
it stores a revision. `widget_preview` renders the proposed or current publication at registered
sizes. Delivery status distinguishes publication, wake, fetch, download, and render submission.

The publication API and safety limits are described below. The Android color tokens and preview
size bands are checked against their Kotlin counterparts by `scripts/check-contract-parity.py`.

## Retention

The server runs a retention pass at startup and every 24 hours. It keeps the newest 100 revisions
per widget and delivery, fetch, render-ack, nudge, attention, render-build, and action-audit records
from the last 90 days. Orphaned asset rows and files are removed; assets referenced by the current
publication or a retained revision stay available. SQLite's WAL is checkpointed after a cleanup.
Older history is intentionally unavailable. `widget_status` returns a recent bounded window by
default; pass `summary: false` and a `limit` up to 100 to request a larger window. Its
`resultLimits` field reports the returned window and whether records were truncated.

## Publication channel

Recent revisions are retained in `publication_revisions`. A revision replaced before a device
fetched it is marked `superseded` and appears in that device's `skippedRevisions`, so
"waiting" and "lost" are different states. If the current `publications.revision` is newer
than the maximum recorded history revision, `widget_status` and `hermes widget status` return a
`revision_history_gap` warning instead of presenting a truncated history as complete. Each
publication requires `title` and `summary`
and exactly one of `text`, inline `svg`, or a local PNG/JPEG/WebP `file_path`. Optional
`priority` is `normal` or `high`; optional `itemId`, `actions`, `provenance`, `dark_palette`, and
`variants` provide stable queue-only interaction, honest evidence labels, and size-aware
presentation. An independent `ticker` region may be published without replacing the hero.
The effective priority and any degradation reason are returned in the envelope.

### Freshness vs expiry

| Field | Channel | Meaning |
| --- | --- | --- |
| `expiresAt` / `ttl_seconds` (≤31536000) | publication | Server-side expiry; an expired revision is not delivered. |
| `maxAgeSeconds` (≤31536000) | publication | Freshness window since `publishedAt`. Once exceeded the server answers `410 publication_stale` and `status` reports `stale`, so content is dropped rather than rendered late. |
| `priority` (`normal`/`high`) | publication | Requests a content-free wake. The effective lane, requested lane, and any quiet-hour/rate-limit degradation are reported. |

`capabilities.publicationMaxTtlSeconds` exposes the expiry ceiling.

### Render surface and fit

The most recent render acknowledgement supplies `render.lastRendered` (`width`, `height`) and
`render.recommendedAspectRatio`; before the first acknowledgement both are `null`. Images and
SVG are drawn with `ContentScale.Fit`: letterboxed inside the widget bounds, never cropped or
stretched. Size visual content near the reported aspect ratio.

### SVG allowlist

`capabilities.svg.allowedElements` and `allowedAttributes` are the exact permitted sets.
Anything else is **rejected** with an error naming the element or attribute, never silently
dropped; `ignoredAttributes` is currently empty. Rejected constructs include `script`,
`foreignObject`, `image`, `use`, `animate`, `style`, `DOCTYPE`/`ENTITY`, `http(s)`/`ftp`
URLs, and event-handler attributes. `text` is allowed; only generic `font-family` values
(`sans-serif`, `serif`, `monospace`) are guaranteed on device.

### Events and polling

The event vocabulary is `refresh`, `dismiss`, `review`, `event` (a caller-named event with its
own `payload`), the generic `request_update` poke, and the queue-only action kinds `approve`,
`snooze`, and `open`. Emission points:

- `refresh` — tapping the publication fetches now.
- `dismiss` / `event` — publication actions.
- `approve` / `snooze` / `open` — publication actions that enqueue an intent.
- `review` — opening the publication zoom view.
- `request_update` — a widget tap that records a generic poke and triggers the existing refresh
  routine; the agent decides whether and what to publish.

The phone polls on a `PeriodicWorkRequest` of 15 minutes (`capabilities.pollIntervalSeconds`
= 900), on app open, and after a tap. Android WorkManager batches and defers work, so the
observed gap between revisions can be longer than nominal; `status` exposes `lastPollAt`,
`lastFetchedRevision`, and `skippedRevisions` per device so waiting and lost are distinct.

Priority wake is opt-in per publication. `priority: "high"` sends only `{"message":"fetch"}`
to the device's UnifiedPush endpoint; no title, summary, widget id, or publication content
enters the push. The app pulls over the existing authenticated HTTPS path, using expedited
WorkManager and (when permitted) an exact-alarm fallback. The high lane is six per hour and
thirty per day; over-limit and timezone-aware quiet-hours requests degrade to normal and record the
reason. `widget_status` exposes the ordered `nudge_sent → fetched → downloaded →
render_submitted` receipts. A `rendered` receipt means a render pass completed, not that a
human saw the content.

Receipt history is assembled from its owning records: nudge delivery, publication fetch/download,
and render acknowledgements. The database no longer writes a second copy of those states; schema
version 7 migrates prior render confirmations into `publication_acks` before removing the duplicate
receipt table.

Transient wake delivery failures are retried by the server with exponential backoff, up to five
attempts. Invalid endpoints and endpoints reported as expired are not retried.

Action taps are queue-not-authorise: `approve`, `snooze`, and `open` create durable,
idempotent intents with an audit row. `widget_ask` opens a bounded question and `answer` is an
authenticated device event; neither path executes work. Standing `widget_watch` rules publish
only on condition transitions and self-clear when resolved. Attention status contains only
aggregate dwell/tap/render/supersession counts. The agent consumes intents with
`widget_read_intents` and records `applied`, `declined`, or `held` with `widget_resolve_intent`.
Sensitive intents remain pending until the paired device confirms them at
`POST /v1/device/action-intents/{intentId}/confirm`; the agent cannot assert device confirmation.
A revoked device cannot report inventory, telemetry,
or enqueue an intent, and unactioned intents expire.

### Client build reporting

A device that renders the wrong thing is usually a build problem, so the phone names the
build it is running on **every** request, including the GET poll:

| Header | Example | Meaning |
| --- | --- | --- |
| `X-Hermes-App-Version` | `0.2.0` | `versionName` of the installed package |
| `X-Hermes-App-Build` | `200` | `versionCode` / `longVersionCode` |
| `X-Hermes-Os-Sdk` | `35` | Android API level |
| `X-Hermes-App-Sha` | `719cb8139b07` | the commit this APK was built from (`-dirty` when the tree was not clean) |

The combined form `0.2.0 (200)` in the version header is also accepted. The same values
travel in a `client` object on `PATCH /v1/device` (`{"appVersion","appBuildCode","osSdk"}`)
and as `clientVersion` / `clientBuildCode` on the render acknowledgement.

The server stores the latest report per device (`device_client_info`, first and last seen)
and, per `(widget, device, revision)`, which build rendered it
(`publication_render_builds`). The SHA is the precise answer: `app_build_code` says which *release* a phone is on, and
four different APKs once shared one code, so the commit is what actually identifies a build.
`widget_status` surfaces all of it as `devices[].appVersion` / `appBuildCode` /
`appBuildSha` and `delivery[].renderedBy` / `delivery[].client`.

Deliberately narrow, and deliberately lenient:

- only an app version, a build code and an OS API level — nothing that identifies a person,
  a place or an account;
- values are bounded (32 characters, build code ≤ 2^31-1, API level ≤ 100) and a value that
  fails validation is **ignored, not stored and not raised** — build metadata must never be
  able to fail a publication fetch;
- an unchanged build writes nothing, so the 15-minute poll does not become a write per wakeup;
- a device that reports nothing (an app predating this) keeps working; its `client` and
  `renderedBy` fields are `null` rather than a guess, and a revoked device gets no row.

### Tap attribution and failed-request visibility

A press of a widget button has to leave a trail on both sides, because a tap that
produces no row is ambiguous by nature. Three things make it answerable:

| What | Where | Notes |
| --- | --- | --- |
| `instanceId` on every event and update request | `events.instance_id`, `widget_update_requests.instance_id` | null when the client did not report one, never guessed |
| `instanceId` + `appBuildSha` on the render receipt | `publication_render_builds` | which instance drew which revision, and from which commit |
| Refused device requests | `event_rejections` (bounded, pruned oldest-first) | method, path, status, error code, request id; no token, no payload |
| One access line per device request | `hermes_widget.server` log | `access POST /v1/widgets/x/events -> 403 req=… device=… event=… code=…` |

`widget_status` surfaces `rejections` (with `byCode`, `byDevice`, `lastCode`,
`lastEvent`, `lastStatus`) next to `delivery[]`, so "the tap produced no row" has
an answer instead of six possibilities: refused at auth, refused as a non-device
principal, malformed, rate limited, server error, or never left the phone.

On the phone the same trail is local: `Config.actionOutcomes` keeps the last ten
(button, instance, HTTP status, code, message, time) and Diagnostics renders them.
`Outcome.kt` turns an `HttpResult` into one honest sentence, so 401, 403, 404, 429,
5xx and "no connection" are no longer the same toast — and the two silent exits of
the tap path (no server URL, no device token) now say which one they were.

Attribution added in this round starts empty by construction: revisions that were
already on a device were rendered by a build nobody recorded, and they are reported
as `renderedBy: null` rather than back-filled with a guess.

## Forward compatibility

Unknown publication kinds and malformed fields are rejected by the server before storage. The
Android client ignores optional metadata it does not use and keeps its last valid cached content
when a network fetch fails.
