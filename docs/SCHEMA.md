# Hermes Widget Layout Schema (v2)

## Contract

`hermes-plugin/hermes-widget/layout.schema.json` is the single registry. Every layout pushed
to a device must validate against it, and every other copy of the contract mirrors it:

| Mirror | What it holds |
|---|---|
| `layout.schema.json` | The registry: node types, per-type fields, caps, enums. |
| `validate.py` | Dependency-free equivalent, runs inside the Hermes venv. |
| `LayoutParser.kt` | Android parser, rejects removed types and actions. |
| `Renderer.kt` / `Typo.kt` | What each field actually does on the device. |
| `skills/widget/SKILL.md` | The agent-facing promise: which fields render. |
| `docs/SCHEMA.md` | This file. |

`python scripts/check-contract-parity.py` compares all of them and exits non-zero on any
disagreement — node types, action kinds, text styles, per-type fields, and the envelope. It
runs in CI. Add a field to the schema and the check will fail until every mirror mentions it;
that is deliberate, because the alternative is a field an agent can set that silently does
nothing.

The renderer treats unknown fields as no-ops (forward compatibility); required fields must be
present. The host plugin enforces 64 KB / 100-node caps, rate limits, action allowlists, and
the hex colour rule. Transport stays `/v1/`; layout `version` is `2`.

## Root structure

```json
{
  "version": 2,
  "widgetId": "string",
  "updatedAt": "ISO-8601",
  "title": "optional title",
  "ttlSeconds": 1800,
  "accentColor": "#7C3AED",
  "root": <node>
}
```

- `version` — integer `2`. Mismatch → device shows "Update Hermes app".
- `widgetId` — stable identifier (≤128 chars).
- `updatedAt` — optional but recommended; the renderer shows a stale banner once `updatedAt` is
  older than `ttlSeconds`, and keeps showing the last good layout rather than going blank.
- `title`, `ttlSeconds`, `accentColor` — optional envelope fields. `accentColor` paints
  `progress` fills and `filled` buttons.
- `root` — exactly one node; must have `type`.

## Nodes

Every node requires `type`. Nodes are recursive for container types (`column`, `row`, `box`,
`list`). Optional on **all** nodes, and honoured by the renderer: `id`, `weight` (shares
leftover space between siblings in a `row`/`column`), `padding` (an object of `top`/`bottom`/
`start`/`end`; an omitted edge is 0), and `alignment` (`start`/`center`/`end`/`fill` — aligns a
text's ink, or a container's children).

There is no conditional visibility. `visibleIf` was reserved in earlier drafts and removed in
v2: it was never validated and never rendered.

### Layout containers

| Type | Required fields | Description |
|---|---|---|
| `column` | `children[]` (≤100) | Vertical stack; `spacing` (px, ≤64). |
| `row` | `children[]` (≤20) | Horizontal row; `spacing` (px, ≤64). |
| `box` | `children[]` (≤100, overlay) | Overlay container. |
| `list` | `children[]`, `maxItems` (≤50) | Bounded container; no scrolling on the device, so `maxItems` is the overflow guard. |

### Content nodes

| Type | Required fields | Description |
|---|---|---|
| `text` | `value` (≤500 chars) | `style`: `title` / `body` / `label` / `caption`; `maxLines`; `color`. |
| `divider` | — | `thickness` (1–16, default 1), `color`. |
| `spacer` | `size` | Empty vertical gap (px, 1–256). |
| `badge` | `text` (≤50) | Pill; `color` is the fill, and the ink flips automatically on a saturated fill. |

### Data

| Type | Required fields | Description |
|---|---|---|
| `stat` | `label`, `value` (≤50 chars) | `delta`, `deltaDirection` (`up`/`down`/`flat`), `color`. |
| `progress` | `value` (0–1) | `label`; `showPercent`. Rendered with the native progress indicator, filled with `accentColor`. |
| `calendar` | `events[]` (≤50) | Mode: `agenda` only (month removed); `maxItems`. Each event: `title`, optional `start`/`end`, `color`. |

### Interactive nodes

| Type | Required fields | Description |
|---|---|---|
| `button` | `label` (≤200) | `style`: `filled` / `tonal` / `outlined`; `action`. |
| `list_item` | `title` (≤200) | `id` (stable for dismiss/review); `subtitle`; `trailingText`; `action`. |

### Removed in v2

`chart`, `image`, `icon`, `toggle`, `calendar` month mode, `url`/`open_app` actions. Invalid v2
layouts using removed types are rejected. There is no v1 compatibility path: `version` must be
`2`, and anything else is rejected rather than migrated.

## Typography

`style` is a closed four-step scale, defined once in `Typo.kt` and documented for the agent in
`skills/widget/SKILL.md`. The device does not accept a free-form font size.

| `style` | Size | Weight | Color |
|---|---|---|---|
| `title` | 18sp | bold | `#000000` |
| `body` | 14sp | normal | `#000000` |
| `label` | 12sp | medium | `#000000` |
| `caption` | 11sp | normal | `#8E8E93` |

An absent or unknown `style` renders as `body`. Glance ships only `Normal`, `Medium` and
`Bold`, so the scale uses those three and no others.

## Color

Every colour field — `accentColor`, `text.color`, `divider.color`, `badge.color`,
`stat.color`, `calendar.events[].color` — accepts a 3- or 6-digit hex only (`#RGB` /
`#RRGGBB`). Named colours, `rgb()`, and 8-digit alpha hex are **rejected at push time**. The
device ignores an unparseable colour rather than failing to paint, so the rejection is the
guard against a push that quietly renders the wrong colour.

Semantic defaults: secondary label `#8E8E93`, hairline/track `#E5E5EA`, delta up `#34C759`,
delta down `#FF3B30`.

## Actions

Every interactive node carries an `action` object with a `kind` and type-specific fields:

```json
{"kind":"event","event":"refresh_briefing","payload":{"itemId":"item-1"}}
{"kind":"refresh"}
{"kind":"dismiss","itemId":"item-2"}
{"kind":"review","itemId":"item-1"}
{"kind":"approve","itemId":"item-1","actionClass":"reversible"}
{"kind":"snooze","itemId":"item-1","actionClass":"reversible"}
{"kind":"open","itemId":"item-1","actionClass":"read_only"}
```

- `kind` enum: `event` | `refresh` | `dismiss` | `review` | `approve` | `snooze` | `open`.
- `event` requires `event` (string ≤200) and optional `payload` (object) and optional `itemId`.
- `refresh` triggers an immediate re-fetch (no extra fields).
- `dismiss` / `review` / `approve` / `snooze` / `open` require a stable `itemId`.
- `approve` / `snooze` / `open` enqueue an allowlisted intent; they do not execute an operation
  on the widget server. `actionClass` is bounded to the documented policy classes; destructive,
  external, and irreversible classes require an explicit agent confirmation.
- Removed: `url` (https://) and `open_app` (hermes://) — no deeplink/url rendering.

The host plugin validates `kind` against its allowlist before storing any layout.

## Pre-publish preview and inventory

The Android app reports every hosted widget instance with its current size class and pixel
bounds to `PUT /v1/device/instances`. `widget_preview` and `POST /v1/widgets/<id>/preview`
render the exact current or proposed publication at those registered sizes. The response
contains bounded base64 PNGs; the CLI writes them with:

    hermes widget preview --sizes 2x2,4x2,4x4 --out ./widget-previews
    hermes widget preview --publication-file proposal.json --out ./widget-previews

Capacity findings (`TEXT_MAY_CLIP_2X2`, `LAYOUT_MAY_CLIP_2X2`, and image letterbox warnings)
are advisory. A warning never silently truncates a publication; the device remains the final
render authority. A missing local SVG rasterizer is reported as an explicit renderer note,
not as a successful-looking placeholder. Relative fixture paths resolve from the checkout or
installed plugin copy.

## Dry run and preview

`widget_validate` (and `store.inspect_widget`) validate a layout, report its node count, byte
size, the text styles it uses, and any design warnings — **without storing anything and
without consuming a rate-limit slot**. It accepts the same `layout` argument as
`widget_update` and returns the same error codes, so a green dry run means the push would have
succeeded.

Design warnings are advisory: the push still succeeds. They are `ROOT_PADDING_LOW` (root
padding below 12 on an edge), `TOO_MANY_TEXT_STYLES` (more than three of the four steps),
`NEAR_NODE_CAP` (over 80 of the 100 allowed nodes), and the registered-instance capacity
warnings described above.

`hermes widget preview <layout.json>` renders the tree to a standalone HTML file — every widget
shape Android can give it, with the widget's real bounds outlined so clipping is visible. It is
how a design gets looked at without building, installing and screenshotting an APK:

    hermes widget preview fixtures/golden/large-brief.json
    # -> fixtures/golden/large-brief.preview.html

Every number and colour it draws comes from this registry (`definitions.typography`,
`definitions.palette`, `definitions.spacing`), and it refuses a layout that does not validate.
It is an approximation of Compose, not a simulator: it approximates RemoteViews sizing and
cannot show real font metrics. The device stays authoritative.

## Security and guardrails

- **Schema validation before push.** Any layout that fails `layout.schema.json` (v2) is rejected.
- **Payload size cap.** Reject payloads > 64 KB.
- **Node cap.** Reject layouts with > 100 total nodes. Per-container `maxItems` is a secondary guard.
- **Rate limit.** Max 30 pushes per widget per hour. Dry runs do not count.
- **Hex-only colours.** Enforced for every colour field.
- **Action allowlist.** Only `event`, `refresh`, `dismiss`, `review`, `approve`, `snooze`, and
  `open` (v2); the latter three enqueue intents and never execute on the HTTP server.
- **No raw arbitrary code.** Nodes are a closed enum; actions are a closed enum. Unknown types are rejected in validation and skipped by the renderer.

## Constraints (enforced server-side + by renderer)

- `version`: exactly `2` (integer).
- `widgetId`: ≤128 chars.
- `updatedAt`: `date-time` format.
- Colours: `#RGB` or `#RRGGBB` only.
- Node count per container: `column`/`box` ≤100; `row` ≤20; `list` ≤100.
- `text.value`: ≤500 chars; `button.label`: ≤200 chars; `badge.text`: ≤50 chars.
- `stat.label` / `stat.value`: ≤50 chars; `list_item.title` / `subtitle`: ≤200 chars.
- `calendar.events`: ≤50.
- `spacing`: 0–64; `divider.thickness`: 1–16 (default 1); `spacer.size`: 1–256 (required).
- `action.payload`: an object; `action.itemId` ≤128 chars. `itemId` is stable and required
  for item actions.

## Publication channel (separate store)

`/v1/widgets/<id>/publication` is a different store from `/v1/widgets/<id>` (the v2
layout). Publishing to one does not update the other. `GET /v1/widgets/<id>` echoes a
`publication: {revision, publishedAt, kind, state}` pointer so a raw API consumer can see
the current publication instead of concluding a publish was lost. `hermes widget status`
and `widget_status` report both channels.

Every revision is retained in `publication_revisions`. A revision replaced before a device
fetched it is marked `superseded` and appears in that device's `skippedRevisions`, so
"waiting" and "lost" are different states. If the current `publications.revision` is newer
than the maximum recorded history revision, `widget_status` and `hermes widget status` return a
`revision_history_gap` warning instead of presenting a truncated history as complete. Each
publication requires `title` and `summary`
and exactly one of `text`, inline `svg`, or a local PNG/JPEG/WebP `file_path`. Optional
`priority` is `normal` or `high`; optional `itemId` and `actions` provide stable queue-only
interaction. The effective priority and any degradation reason are returned in the envelope.

### Freshness vs expiry

| Field | Channel | Meaning |
| --- | --- | --- |
| `ttlSeconds` (≤86400) | v2 layout | Device-side stale banner; the last good layout keeps rendering. |
| `expiresAt` / `ttl_seconds` (≤31536000) | publication | Server-side expiry; an expired revision is not delivered. |
| `maxAgeSeconds` (≤31536000) | publication | Freshness window since `publishedAt`. Once exceeded the server answers `410 publication_stale` and `status` reports `stale`, so content is dropped rather than rendered late. |
| `priority` (`normal`/`high`) | publication | Requests a content-free wake. The effective lane, requested lane, and any quiet-hour/rate-limit degradation are reported. |

`capabilities.layoutMaxTtlSeconds` and `capabilities.publicationMaxTtlSeconds` expose the two
ceilings explicitly; they are intentionally different windows, not a bug.

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
own `payload`), plus the queue-only action kinds `approve`, `snooze`, and `open`. Emission points:

- `refresh` — tapping the publication fetches now; also a v2 `button` with `kind=refresh`.
- `dismiss` / `event` — v2 button actions.
- `approve` / `snooze` / `open` — v2/publication actions that enqueue an intent.
- `review` — opening the publication zoom view.

The phone polls on a `PeriodicWorkRequest` of 15 minutes (`capabilities.pollIntervalSeconds`
= 900), on app open, and after a tap. Android WorkManager batches and defers work, so the
observed gap between revisions can be longer than nominal; `status` exposes `lastPollAt`,
`lastFetchedRevision`, and `skippedRevisions` per device so waiting and lost are distinct.

Priority wake is opt-in per publication. `priority: "high"` sends only `{"message":"fetch"}`
to the device's UnifiedPush endpoint; no title, summary, widget id, or publication content
enters the push. The app pulls over the existing authenticated HTTPS path, using expedited
WorkManager and (when permitted) an exact-alarm fallback. The high lane is six per hour and
thirty per day; over-limit and UTC quiet-hours requests degrade to normal and record the
reason. `widget_status` exposes the ordered `nudge_sent → fetched → downloaded →
render_submitted` receipts. A `rendered` receipt means a render pass completed, not that a
human saw the content.

Action taps are queue-not-authorise: `approve`, `snooze`, and `open` create durable,
idempotent intents with an audit row. The agent consumes them with `widget_read_intents` and
records `applied`, `declined`, or `held` with `widget_resolve_intent`; a sensitive class
requires confirmation. A revoked device cannot report inventory or enqueue an intent, and
unactioned intents expire.

## Forward compatibility

Unknown node types are rejected in validation (server) and skipped by the renderer (device).
Unknown fields inside known nodes are ignored. Never crash on a newer schema version; validate
strictly on the server.

## Shared fixtures

Canonical fixtures live in `fixtures/` at the repo root; both
`hermes-plugin/hermes-widget/tests/` (Python) and `android/app/src/test/` (JVM) read them as
the single source of truth for the contract.

- `fixtures/brief-v2.json` (full), `fixtures/brief-v2-minimal.json` (minimal),
  `fixtures/invalid-*.json` (rejection cases: chart, image, toggle, url action, calendar month).
- `fixtures/golden/*.json` — the reference layouts the design skill points an agent at
  (`small-hero-stat`, `medium-split-strip`, `medium-agenda`, `large-brief`). They are asserted
  valid **and warning-free** in both languages, so the skill cannot teach a layout that no
  longer validates.

Both `assets/fixture_layout.json` and `android/app/src/main/assets/fixture_layout.json` must
stay byte-identical to `fixtures/brief-v2.json`; `test_contract.py` enforces that.
