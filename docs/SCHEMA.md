# Hermes Widget Layout Schema (v2)

## Contract

Every layout pushed to a device must validate against `hermes-plugin/hermes-widget/layout.schema.json` (canonical). The renderer treats unknown fields as no-ops (forward compatibility); required fields must be present. The backend enforces 64 KB / 100-node caps, rate limits, and action allowlists. Transport stays `/v1/`; layout `version` is `2`.

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
- `updatedAt` — optional but recommended; renderer shows stale indicator when past `ttlSeconds`.
- `title`, `ttlSeconds`, `accentColor` — optional envelope fields.
- `root` — exactly one node; must have `type`.

## Nodes

Every node requires `type`. Nodes are recursive for container types (`column`, `row`, `box`, `list`). Optional on all nodes: `id`, `weight`, `padding`, `visibleIf` (⚠️ FUTURE: not yet implemented).

### Layout containers

| Type | Required fields | Description |
|---|---|---|
| `column` | `children[]` (≤100) | Vertical stack; `spacing` (px, ≤64), `alignment`. |
| `row` | `children[]` (≤20) | Horizontal row; `spacing`, `alignment`. |
| `box` | `children[]` (≤100, overlay) | Overlay container; `alignment`. |
| `list` | `children[]`, `maxItems` (≤50) | Scrollable container (`LazyColumn` equivalent). |

### Content nodes

| Type | Required fields | Description |
|---|---|---|
| `text` | `value` (≤500 chars) | `style`: `title` / `body` / `label` / `caption`; `maxLines`; `color`. |
| `divider` | — | `thickness` (≤16, default 1), `color`. |
| `spacer` | `size` | Empty vertical spacer (px, 1–256). |
| `badge` | `text` (≤50) | Highlight badge; `color`. |

### Data

| Type | Required fields | Description |
|---|---|---|
| `stat` | `label`, `value` (≤50 chars) | `delta`, `deltaDirection` (`up`/`down`/`flat`), `weight`. |
| `progress` | `value` (0–1) | `label`; `showPercent`; `max`. |
| `calendar` | `events[]` (≤50) | Mode: `agenda` only (month removed); `maxItems`. Each event: `title`, optional `start`/`end`, `color`. |

### Interactive nodes

| Type | Required fields | Description |
|---|---|---|
| `button` | `label` (≤200) | `style`: `filled` / `tonal` / `outlined`; `action`. |
| `list_item` | `title` (≤200) | `id` (stable for dismiss/review); `subtitle`; `leadingIcon`; `trailingText`; `action`. |

### Removed in v2

`chart`, `image`, `icon`, `toggle`, `calendar` month mode, `url`/`open_app` actions. See `fixtures/legacy-v1.json` → `fixtures/brief-v2.json` for migration. Invalid v2 layouts using removed types are rejected; legacy v1 layouts are auto-migrated on push/read with a pre-migration backup and unsupported sections replaced by a visible "pull to refresh" placeholder.

## Actions

Every interactive node carries an `action` object with a `kind` and type-specific fields:

```json
{"kind":"event","event":"refresh_briefing","payload":{"itemId":"item-1"}}
{"kind":"refresh"}
{"kind":"dismiss","itemId":"item-2"}
{"kind":"review","itemId":"item-1"}
```

- `kind` enum: `event` | `refresh` | `dismiss` | `review`.
- `event` requires `event` (string ≤200) and optional `payload` (object) and optional `itemId`.
- `refresh` triggers an immediate re-fetch (no extra fields).
- `dismiss` / `review` require `itemId` (stable item to hide/mark) and optional `payload`.
- Removed: `url` (https://) and `open_app` (hermes://) — no deeplink/url rendering.

Backend validates `kind` against allowlist before storing any layout.

## Security and guardrails

- **Schema validation before push.** Any layout that fails `layout.schema.json` (v2) is rejected.
- **Payload size cap.** Reject payloads > 64 KB.
- **Node cap.** Reject layouts with > 100 total nodes. Per-container `maxItems` is a secondary guard.
- **Rate limit.** Max 30 pushes per widget per hour.
- **Action allowlist.** Only `event`, `refresh`, `dismiss`, `review` (v2).
- **No raw arbitrary code.** Nodes are a closed enum; actions are a closed enum. Unknown types are rejected in validation and skipped by the renderer.

## Constraints (enforced server-side + by renderer)

- `version`: exactly `2` (integer).
- `widgetId`: ≤128 chars.
- `updatedAt`: `date-time` format.
- `accentColor`: hex only (`#RGB` or `#RRGGBB`).
- Node count per container: `column`/`box` ≤100; `row` ≤20; `list` ≤100.
- `text.value`: ≤500 chars; `button.label`: ≤200 chars; `badge.text`: ≤50 chars.
- `calendar.events`: ≤50.
- `spacing`: 0–64 px.
- `divider.thickness`: 1–16 px (default 1).

## Forward compatibility

Unknown node types are rejected in validation (server) and skipped by the renderer (device). Unknown fields inside known nodes are ignored. Never crash on a newer schema version; validate strictly on the server.

## Shared fixtures

Canonical fixtures live in `fixtures/` at the repo root; both `hermes-plugin/hermes-widget/tests/` (Python) and `android/app/src/test/` (JVM) read them as the single source of truth for the contract. See `fixtures/brief-v2.json` (full), `fixtures/brief-v2-minimal.json` (minimal), and `fixtures/legacy-v1.json` (migration source).
