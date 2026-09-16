---
name: hermes-widget
description: "Author and push Hermes home-screen widget layouts, and read the interaction events the widget sends back."
version: 1.0.0
author: Hermes Widget contributors
license: MIT
metadata:
  hermes:
    tags: [widget, android, home-screen, layout, ui]
---

# Hermes Widget

Author the server-driven layout for the user's Android home-screen widget, push it with the
widget_update tool, and read back what the user did with widget_read_events.

## When to use this skill

- The user asks you to change, refresh, populate, or "update my widget".
- A scheduled widget refresh runs (the prompt asks you to call widget_update).
- You are reacting to an interaction event the widget sent back.

## The v1 layout contract

Every push is one envelope:

    {
      "version": 1,
      "widgetId": "hermes-brief",
      "title": "Today",
      "ttlSeconds": 1800,
      "accentColor": "#7C3AED",
      "root": { "type": "column", "children": [ ... ] }
    }

Node types (v2 reduced contract):

- Containers: column, row, box (overlay), list (scrollable)
- Content: text, divider, spacer, badge
- Data: stat, progress
- Interactive: button (event, refresh, dismiss/review actions only; url/open_app/deeplink removed)

Hard rules the server enforces. A violation is rejected and nothing is stored:

- version must be the integer 1, and root must be a single node with a type.
- At most 100 nodes in the whole layout, and at most 64 KB of JSON.
- image, chart, icon, toggle, calendar month mode, and url/deeplink/open_app actions are removed in v2; do not include them.
- action.kind is event, refresh, dismiss, or review (v2 reduced set)
- text.value <= 500 chars, button.label <= 200, badge.text <= 50
- column/box <= 100 children, row <= 20, list <= 100
- calendar.events <= 50 (agenda mode only)
- Removed: charts, images, icons, toggles, calendar, url/open_app/deeplink actions. See Phase A contract.

If widget_update returns an error object, read the detail, fix that field, and push again. Do not
retry the identical payload.

## Pushing a layout

Call widget_update with widget_id and the complete layout. Example - a morning brief:

    {
      "version": 1,
      "widgetId": "hermes-brief",
      "title": "Good morning",
      "root": {
        "type": "column",
        "spacing": 10,
        "children": [
          { "type": "text", "value": "Good morning", "style": "title" },
          { "type": "row", "spacing": 12, "children": [
              { "type": "stat", "label": "Next meeting", "value": "10:30" },
              { "type": "stat", "label": "Open loops", "value": "3", "deltaDirection": "flat" }
          ]},
          { "type": "divider" },
          { "type": "text", "value": "Reply to Ana — Draft ready", "style": "subtitle" },
          { "type": "divider" },
          { "type": "button", "label": "Refresh", "style": "tonal",
            "action": { "kind": "refresh" } }
        ]
      }
    }

A short accurate summary beats a padded one. Never invent calendar or task data.

## Reading what the user did

Call widget_read_events (optionally with since, widget_id, or limit) to see taps and refreshes.
Events are newest first and carry the widget id, the device id, the event name, and any payload.
Use them to close the loop: if the user tapped "refresh", push an updated layout; if "dismiss" or "review", update the item state and push the revised layout.

## Design guidance

- Lead with the one thing that changes today. A headline that is identical every day is noise.
- Keep it to two to four items. A widget is glanced at for two seconds, not read.
- Prefer stat for a number, text for a task or headline.
- Set title to something human; set ttlSeconds when the content goes stale quickly.
- Never include secrets, tokens, full email bodies, or anything the user would not want visible on
  a locked home screen.

## Proactive refresh

The plugin can install a Hermes cron job that asks you to rebuild the widget on a schedule. When
that prompt runs, you have no user to ask, so make a judgment call from what you already know and
push a concise, accurate layout. If genuinely nothing has changed, publish a short accurate
overview rather than inventing activity.
