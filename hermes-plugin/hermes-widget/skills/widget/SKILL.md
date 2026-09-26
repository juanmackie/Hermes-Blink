---
name: hermes-widget
description: "Proactive widget publishing, previews, delivery, actions."
version: 3.2.0
author: Hermes Widget contributors
license: MIT
metadata:
  hermes:
    tags: [widget, proactive, android, home-screen, layout, ui]
    category: productivity
---

# Hermes Widget

Publish one useful update to the user's Android home-screen widget with `widget_publish`.
Use text for a concise readable note, inline SVG for a static chart/diagram, or a local
PNG/JPEG/WebP for a photo. The legacy structured layout flow remains available through
`widget_update` when a v2 layout is genuinely the better representation.

## When to use this skill

- The user asks you to change, refresh, populate, or "update my widget".
- A scheduled widget refresh, heartbeat, or routine runs and context contains a genuinely
  useful change for the user.
- The user asks for a glanceable status, brief, decision surface, or a compact visual that
  belongs on the home screen rather than buried in chat.
- You need to distinguish stored, fetched, downloaded, and render-submitted delivery state,
  or you need to respond to a queued widget action.

## Proactive decision rule

This is an ambient surface, not a chat log. Before publishing, ask: **is there a real change
the user would want to see at a glance?** If yes, publish one useful revision. If not, do
nothing and do not spend a rate-limit slot. Never turn an ordinary coding turn into a widget
update merely because the plugin is available.

Use `widget_status` before replacing a working publication when freshness or delivery is
uncertain. Use `widget_preview` before publishing a visual that has not already been checked
at the phone's registered sizes. Use `priority: "high"` only for time-sensitive content; a
normal publication is the default. After a user taps an action, read `widget_read_intents`,
handle the allowlisted work in the agent, and record the outcome with `widget_resolve_intent`.

## Publish one accessible communication

Call `widget_publish` with a short `title`, a non-empty plain-text `summary`, and exactly one
source:

- `text`: accessible plain text.
- `svg`: inline static SVG for charts, diagrams, or simple shapes.
- `file_path`: one local PNG, JPEG, or WebP file on the Hermes host.

Add either `expires_at` (timezone-aware ISO-8601) or `ttl_seconds`, never both. Set
`priority: "high"` only for a genuinely time-sensitive update: the phone receives a
content-free UnifiedPush wake, pulls over the existing private HTTPS path, and the server
rate-limits the high lane (six per hour, thirty per day) with visible degradation to normal.
Use `widget_set_quiet_hours` for a UTC quiet window when the user does not want wake noise.
Do not put publication content in a push payload. A successful tool result means the host
stored a revision; it does **not** mean the phone downloaded or rendered it. Use
`widget_status` for `nudge_sent`, `fetched`, `downloaded`, and `render_submitted` separately.

### Visual guardrails

- Title: at most 512 UTF-8 bytes. Summary is required and at most 4 KiB.
- Text: at most 32 KiB. SVG: at most 256 KiB. Raster: at most 5 MiB.
- Raster dimensions: at most 16,384 per side and 16 million decoded pixels.
- PNG, JPEG, and WebP are identified by file signature, not extension alone.
- SVG is a closed static subset. Scripts, event handlers, external URLs/resources,
  `href`, `foreignObject`, animation, filters, DOCTYPE/entity declarations, processing
  instructions, and unsupported elements/attributes are rejected.
- The previous successful display remains current when publication validation fails.

Do not retry an identical rejected payload. Correct the source first. Prefer a static SVG
with a text summary over a dense unreadable chart. For a photo, write it to a bounded local
file first; do not send a remote URL.

## Legacy v2 layouts

Use `widget_update` only when a structured v2 layout is the appropriate representation. It
remains supported for compatibility, but it is not required for ordinary visual publishing.
Always dry-run that legacy layout before you push.

`widget_validate` takes the same `layout` and returns the node count, byte size, the text
styles you used, and any design warnings — without publishing anything and without spending a
push from the 30-per-hour budget. A rejected push wastes a slot; an accepted bad one replaces
a widget that was working. Iterate on `widget_validate`, then call `widget_update` once.

### Dry-run a legacy layout

`widget_validate` takes the same `layout` and returns the node count, byte size, the text
styles you used, and any design warnings — without publishing anything and without spending a
push from the 30-per-hour budget. A rejected push wastes a slot; an accepted bad one replaces
a widget that was working. Iterate on `widget_validate`, then call `widget_update` once.

## The v2 layout contract (transport stays /v1/)

Every push is one envelope:

    {
      "version": 2,
      "widgetId": "hermes-brief",
      "itemId": "optional-stable-item",
      "title": "Today",
      "ttlSeconds": 1800,
      "accentColor": "#7C3AED",
      "root": { "type": "column", "children": [ ... ] }
    }

Containers: `column`, `row`, `box` (overlay), `list` (bounded, no scroll).
Content: `text`, `divider`, `spacer`, `badge`.
Data: `stat`, `progress`, `calendar` (agenda only), `list_item`.
Interactive: `button`.

Hard rules the server enforces. A violation is rejected and nothing is stored:

- `version` must be the integer 2, and `root` must be a single node with a `type`.
- At most 100 nodes in the whole layout, and at most 64 KB of JSON.
- `text.value` <= 500 chars, `button.label` <= 200, `badge.text` <= 50, `stat.label`/`stat.value` <= 50.
- `column`/`box` <= 100 children, `row` <= 20, `list` <= 100, `calendar.events` <= 50.
- `spacer.size` is required (1–256). `spacer` is the only node with a required size.
- `action.kind` is `event`, `refresh`, `dismiss`, `review`, `approve`, `snooze`, or
  `open`. `event` needs a non-empty `event`; the item and action kinds need an `itemId`.
  `approve`/`snooze`/`open` enqueue an allowlisted intent; they never execute work on the
  server. Sensitive classes wait for an authenticated agent decision.
- Removed in v2, and rejected: `image`, `icon`, `toggle`, `chart`, calendar `month` mode, and
  `url`/`open_app`/deeplink actions. There is no deeplink rendering.
- There is no conditional visibility. `visibleIf` was reserved in an earlier draft and is gone;
  any field not in the table below is ignored, so setting it is a silent no-op.

If widget_update returns an error object, fix that one field and push again. Never retry an
identical payload.

## Only these fields render

The device honours the fields below and ignores everything else. Writing a field that is not
here is a silent no-op, so build the design out of this list rather than inventing keys.

| Node | Fields that render |
| --- | --- |
| `column` | `children`, `spacing`, `alignment` |
| `row` | `children`, `spacing`, `alignment` |
| `box` | `children`, `alignment` |
| `list` | `children`, `maxItems` |
| `text` | `value`, `style`, `color`, `maxLines`, `alignment` |
| `stat` | `label`, `value`, `delta`, `deltaDirection`, `color` |
| `progress` | `value`, `label`, `showPercent` |
| `badge` | `text`, `color` (the fill) |
| `divider` | `thickness`, `color` |
| `spacer` | `size` |
| `calendar` | `events`, `mode`, `maxItems` |
| `list_item` | `title`, `subtitle`, `trailingText`, `action` |
| `button` | `label`, `style`, `action` |

Every node also takes `id` (stable identity you can reference from an action), `itemId`
(stable identity for an action round-trip), `weight` (share leftover space between siblings in
a `row` or `column`), `padding`, and `alignment`
(`start`/`center`/`end`/`fill`): on `text` it aligns the text, on a container it aligns the
children. `padding` is an object — `{"top":12,"bottom":12,"start":12,"end":12}`; an omitted
edge is 0.

## Typography — four steps, pick at most three

`style` is a closed scale. `title` and `caption` are the pair you will use most; the device
does not accept a free-form font size.

| `style` | Size | Weight | Color | Use for |
| --- | --- | --- | --- | --- |
| `title` | 18sp | bold | `#000000` | The hero: one number, one word, a headline |
| `body` | 14sp | normal | `#000000` | Task text, list item titles |
| `label` | 12sp | medium | `#000000` | Short identifiers, event titles |
| `caption` | 11sp | normal | `#8E8E93` | Labels, units, timestamps, context |

An absent or unknown `style` renders as `body`. At most three of these four per widget, and at
most two active weights — the contrast between `title` and `caption` is what makes a layout
readable at a glance, and adding a third size flattens it.

## Color

- **6-digit hex only.** 8-digit alpha hex, named colors, and `rgb()` are rejected at push
  time (`accentColor` has always been checked this way; `color` now is too).
- Primary text: `#000000` or omit.
- Secondary labels: `#8E8E93`.
- Hairline / progress track: `#E5E5EA`.
- Deltas: `#34C759` up, `#FF3B30` down — the `deltaDirection` you set picks the color, so do
  not also set `color` on the same `stat`.
- **One accent hue per widget.** The envelope `accentColor` paints `progress` fills and
  `filled` buttons. `badge.color` and `divider.color` are per-node overrides.

## Spacing rhythm

Pick one rhythm and stay in it: `spacing` 8 (tight), 12 (default between sections), 16 (airy,
large widgets). Root padding is 12 on every edge when you do not set it — leave it at 12 or
more or content sits against the widget border (`ROOT_PADDING_LOW` is a warning, not a block).

## Canvas sizes

The widget is resizable. Android sizes cells in dp, so these are approximate content boxes
after the safe inset — design for the shape, not a fixed pixel height.

| Shape | Roughly | What fits |
| --- | --- | --- |
| 2×2 | ~120 × 120dp | One hero (`stat` or `title` text) + one `caption`. Nothing else. |
| 4×2 (default) | ~270 × 120dp | Hero + two or three supporting lines, or a hero `row` of two stats. |
| 2×4 | ~120 × 270dp | One stacked column: title, then 3–4 `list_item`s. |
| 4×4 | ~270 × 270dp | Hero row + divider + `list` of 3–5 items + a footer `caption`. |

Because the user can resize, never let the layout depend on a fixed height. `list` with
`maxItems` and `calendar` with `maxItems` are the safety valves; that is what stops a 4×4
design from overflowing a 4×2 slot.

## The one-hero rule

Every size has exactly one focal point. Ask what the single takeaway is before writing JSON;
if two things are equally loud, the widget says nothing.

- Small: one `stat` value, or one large `title` line.
- Medium: hero left or top, supporting detail opposite or below.
- Large: hero row, then 2–4 supporting metrics or one content block.

A headline that is identical every day is noise. Lead with the one thing that changed.

## Reading what the user did

Call widget_read_events (optionally `since`, `widget_id`, `limit`) to see taps and refreshes.
Events are newest first and carry the widget id, device id, event name, and payload. Close the
loop: if the user tapped "refresh", push an updated layout; if "dismiss" or "review", update
that item's state and push the revised layout.

Action taps are a queue, not an authorisation. Publications may carry stable `itemId`s and
`approve`/`snooze`/`open` actions. Read them with `widget_read_intents`, perform only the
allowlisted reversible work in the agent, then record `applied`, `declined`, or `held` with
`widget_resolve_intent`. Destructive/external classes require explicit confirmation and are
still only recorded as intent state. Duplicate `clientEventId`s collapse to one intent.

## Proactive refresh

The plugin installs an idempotent Hermes cron job every 6 hours. On an unattended run, use
context you already know and call `widget_publish` only when there is a genuinely useful,
supported update. Never invent calendar, task, metric, chart, or image data. If nothing useful
changed, do nothing and leave the current publication untouched. Never republish an identical
revision merely because the routine ran.

After an app-open or scheduled publication, `widget_status` can report the stored publication,
successful device download, and render submission separately. None of those states proves the
user noticed or understood the content.

## Reference layouts

Adapt these rather than starting from a blank tree. They are validated in CI by both the
Python validator and the Android parser, so they are guaranteed to render.

- `fixtures/golden/small-hero-stat.json` — 2×2, one hero stat plus context.
- `fixtures/golden/medium-split-strip.json` — 4×2, two-column split with a divider and progress.
- `fixtures/golden/medium-agenda.json` — 4×2, agenda plus a refresh button.
- `fixtures/golden/large-brief.json` — 4×4, hero row, list with dismiss/review actions, footer.

## Pre-submit checklist

- [ ] Publication has a useful title, a truthful accessible summary, and exactly one source
- [ ] Text/SVG/raster is bounded, supported, and contains no secret or unwanted lock-screen data
- [ ] Scheduled refresh made no call when nothing useful changed
- [ ] Exactly one hero element when using a legacy v2 layout
- [ ] At most three `style` values from the four-step scale
- [ ] Every color is 6-digit hex
- [ ] Root padding >= 12 on all four edges
- [ ] `maxLines` set on any text that must not wrap (`maxLines: 1` on titles and hero lines)
- [ ] `maxItems` set on every `list` and `calendar`
- [ ] At most one accent hue
- [ ] Content fits the size you designed for, and degrades if the user resizes
- [ ] No `image`, `icon`, `chart`, `toggle`, calendar `month`, or `url` action — and no field
      outside the "only these fields render" table
- [ ] `widget_preview` used at the registered sizes (or deliberately accepted a capacity warning)
- [ ] `widget_validate` run, and every warning either fixed or deliberately accepted
- [ ] Never a secret, token, full email body, or anything unwanted on a locked home screen

## Talking to the user about design

Describe the hierarchy in plain language — "the next meeting time large at the top, with how
long until it in grey underneath". Offer one alternative when it is genuinely useful. If the
user says it looks plain, add structure (a divider, a badge, a progress bar) or typographic
contrast. Do not add more colors.
