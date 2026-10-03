---
name: hermes-widget
description: "Proactive widget publishing, previews, delivery, actions."
version: 3.4.0
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
PNG/JPEG/WebP for a photo.

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

`widget_publish` returns advisory capacity warnings keyed to the smallest registered band
(`BODY_HIDDEN_IN_XS`/`_S`, `BODY_MAY_SCROLL_M`/`_L`, `TITLE_MAY_CLIP`, `SUMMARY_HIDDEN_IN_XS`).
They are estimates (~34 chars per line at 245dp); heed them instead of hoping text fits.

Use `widget_set_quiet_hours` for a quiet window (UTC by default, optional IANA timezone)
when the user does not want wake noise. High-priority wakes degrade visibly to normal
during quiet hours and past the rate limits (6/hour, 30/day).
Do not put publication content in a push payload. A successful tool result means the host
stored a revision; it does **not** mean the phone downloaded or rendered it. Use
`widget_status` for `nudge_sent`, `fetched`, `downloaded`, and `render_submitted` separately.

### Visual guardrails

- Title: at most 512 UTF-8 bytes, single line. Summary is required and at most 4 KiB.
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

## Canvas sizes

The widget is resizable from 2x1 (110x56dp) up through tablet and unfolded-foldable sizes
(provider allows 110x56 to 1600x1600dp), and modern launchers can give the same cell
substantially more dp than the old nominal preview boxes. Treat these as **ranges**, not
fixed canvases. The authoritative geometry is the `widget_instances` inventory reported by
the phone; at density 3.0 a 4x4 instance can be about 407x412dp (1221x1236px), not
270x270dp. Sizes beyond the canonical guide ranges report size class `custom` and take
their band from the height.

### The band ladder the device actually renders

The phone groups every instance into one of four bands by **height**, and applies a width
guard on top. Write for the band your smallest registered instance lands in — the phone
warns you when a publication does not fit it.

| Band | Height | Canonical shapes | What the device renders | Publication variant key |
| --- | --- | --- | --- | --- |
| `xs` | < 130dp | 2x1, 4x1 | Header (mark + status dot), provenance + one hero line | `2x2` |
| `s` | 130–184dp | 2x2, 4x2 floor | `xs` + one summary line | `2x2` |
| `m` | 185–299dp | 4x2, 2x3 | `s` + scrollable body (≈3 lines visible) + status line + Request update | `4x2` |
| `l` | ≥ 300dp | 4x3, 4x4 | `m` + ticker line, ≈8 body lines visible | `4x4` (falls back to `4x2`) |

Two consequences worth planning for:

- **Below 185dp the body is not rendered at all.** A `xs`/`s` instance shows the title (and,
  in `s`, the summary) and nothing else. If the smallest thing you publish must be readable
  at 4x1, it has to live in the title.
- **`widthDp < 245` is single-column.** No ticker, no question, no split row, and the hero
  is one line. Write the ticker and the question for wide instances only.

The status line and the 48dp Request update pill are **pinned**, not scrolled: a long body
never pushes the action below the fold.

### Shape table

| Shape | Typical dp range | What fits |
| --- | --- | --- |
| 2x2 | ~110–306 x 115–276dp | Band `s`/`m`: one hero title + one summary line. |
| 4x2 (default) | ~245–624 x 115–276dp | Band `m`: hero + summary + a few body lines, or a compact photo. |
| 2x4 | ~110–306 x 185–422dp | Band `l`, single-column: title, then body (no ticker). |
| 4x4 | ~245–624 x 300–422dp | Band `l`: hero + body + ticker. |

Density caveat: `px = dp × device density`; the same dp class can produce very different pixel
counts across phones. `widget_preview` and the publication endpoint use the phone's reported
`widthDp`/`heightDp`/`widthPx`/`heightPx` when inventory is available, so do not hand-tune to
the legacy nominal 120/270dp constants.

Because the user can resize, never let the publication depend on a fixed height. Long body
text scrolls instead of clipping, and the pinned footer keeps the status line and the action
visible; that is what stops a 4x4 design from overflowing a 4x2 slot.

## The one-hero rule

Every size has exactly one focal point. Ask what the single takeaway is before publishing;
if two things are equally loud, the widget says nothing.

- Small: one headline, or one number with its unit in the summary.
- Medium: hero title, supporting detail in the summary and first body lines.
- Large: hero, then supporting body and an independent ticker for low-stakes updates.

A headline that is identical every day is noise. Lead with the one thing that changed.

## Typography — four steps on the MD3 scale

The device renders a closed four-step scale (no free-form sizes):

| Style | Size | Weight | Use for |
| --- | --- | --- | --- |
| `title` | 16sp | bold | The hero: one headline |
| `body` | 14sp | normal | Body text |
| `label` | 12sp | medium | Short identifiers |
| `caption` | 11sp | normal | Summary line, status, timestamps |

Keep the contrast between `title` and `caption`; adding emphasis everywhere flattens it.

## Color

- **6-digit hex only** for any authored accent. 8-digit alpha hex, named colors, and
  `rgb()` are ignored, never rendered.
- The device pairs ink with its surface for contrast; do not fight it with low-contrast
  authored colors.

## Reading what the user did

`widget_status` also names the app build each device last reported (`devices[].appVersion`,
`appBuildCode`) and which build rendered the current revision (`delivery[].renderedBy`), so a
user report of "it looks wrong on my phone" starts with the build rather than a guess.

Call `widget_status` with `consume_update_requests=true` on every proactive run. A non-empty
`newlyConsumedUpdateRequests` list means the user tapped **Request update** on the widget and
asked for something fresher: publish what you already know now instead of treating the run as
an ambient refresh. Call widget_read_events (optionally `since`, `widget_id`, `limit`) to see
taps and refreshes. Events are newest first and carry the widget id, device id, event name,
and payload.

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

Standing watches are separate from ordinary publishes. Use `widget_watch` with
`operation=create` for a durable condition → publication rule, `operation=tick` to evaluate
it on the host (with any bounded source snapshot), and `operation=pause`/`list` for control.
A watch publishes only on a false→true transition, honors cadence/quiet hours/max-per-day,
records `watchId`, and self-clears when its condition resolves.

A low-stakes update can be sent as a `ticker` with `widget_publish`; the current hero is retained.
Tickers can carry independent TTL, priority, provenance, pinned/rotating items, and a bounded
question. `widget_ask` opens a question for the user; answers are read with
`widget_read_questions` and never execute work. Delivery receipts and aggregate-only attention
metrics are available in `widget_status`; do not infer attention from a render acknowledgement.

After an app-open or scheduled publication, `widget_status` can report the stored publication,
successful device download, and render submission separately. None of those states proves the
user noticed or understood the content.

## Pre-submit checklist

- [ ] Publication has a useful title, a truthful accessible summary, and exactly one source
- [ ] Text/SVG/raster is bounded, supported, and contains no secret or unwanted lock-screen data
- [ ] Scheduled refresh made no call when nothing useful changed
- [ ] Capacity warnings from `widget_publish`/`widget_preview` either fixed or deliberately accepted
- [ ] Every color is 6-digit hex
- [ ] Content fits the smallest registered band, and degrades if the user resizes
- [ ] `widget_preview` used at the registered sizes for a new visual
- [ ] Never a secret, token, full email body, or anything unwanted on a locked home screen

## Talking to the user about design

Describe the hierarchy in plain language — "the next meeting time large at the top, with how
long until it in grey underneath". Offer one alternative when it is genuinely useful. If the
user says it looks plain, shorten the title and sharpen the one-hero contrast. Do not add more colors.
