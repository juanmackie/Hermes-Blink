# Hermes Widget — Release notes

## v3.2.1 — honest legacy scope, wake visibility, and preview ergonomics

- Legacy `widget_update`/`hermes widget publish --layout-file` results now declare
  `scope: "legacy_layout"`, `publicationCreated: false`, `storedAt`, and a
  `legacy_layout_not_published` warning so a compatibility write cannot look phone-visible.
- `widget_status`/`hermes widget status` now report revision-history gaps and per-device
  UnifiedPush state; `hermes widget wake-test` sends one content-free wake and prints its
  receipt chain without creating a publication.
- Canvas guidance is now a range and points to reported `widget_instances` geometry; modern
  launchers can provide substantially larger dp canvases than the legacy nominal boxes.
- Preview output writes PNGs when `--out` is supplied even with `--json`, accepts local
  `filePath` sources, and reports missing local SVG rendering explicitly.
- Android `versionCode` is now 2, widget resize reports immediately, and diagnostics show
  UnifiedPush registration state.

## v3.2.0 — proactive, cache-safe agent behavior

- Aligned the plugin with Hermes' cache-safe `register_system_prompt_section` contract:
  proactive widget guidance is now one bounded section rendered once per session after memory,
  instead of repeated `pre_llm_call` text on every turn. Older hosts retain the legacy hook
  fallback.
- Tightened the skill description and added an explicit proactive decision rule: publish only
  real glanceable changes, check status before replacing a working surface, preview visuals at
  registered sizes, and resolve queued action intents.
- Simplified the recurring cron prompt to use its attached skill directly rather than loading it
  a second time, keeping unattended runs cheaper and less noisy.
- Fixed Pillow-only raster previews on hosts without libcairo and added a Pillow text fallback;
  SVG rasterisation remains the only preview path that needs CairoSVG.
- `hermes widget publish` now supports publication-mode CLI arguments including `--priority high`.
- `widget_status` and `hermes widget status` now surface `revision_history_gap` with the
  current/max recorded revisions instead of presenting a truncated or emptied history as truth.
- Relative JSON fixture paths resolve from the current directory, checkout root, or the
  fixture copy inside the installed plugin; missing paths report an actionable search list.

## v3.1.0 — priority wake, previews, and queued actions

- `widget_publish` now accepts `priority: "normal" | "high"`. High-priority revisions use a
  device-registered UnifiedPush endpoint and send only a content-free `fetch` wake; the phone
  pulls over the existing private HTTPS path. The high lane is limited to six per hour and thirty
  per day, supports UTC quiet hours, and records soft-limit degradation rather than losing a
  revision.
- Delivery receipts now distinguish `nudge_sent`, `fetched`, `downloaded`, `render_submitted`, and
  an explicit `rendered` pass. The Android app persists poll/fetch/render diagnostics, offers a
  user-initiated battery-optimisation review, and uses expedited WorkManager plus an exact-alarm
  fallback when a UnifiedPush wake arrives.
- Every hosted widget instance is reported with its current size class and bounds. The new
  `widget_preview` tool, `POST /v1/widgets/<id>/preview`, and publication-mode CLI render exact
  current/proposed text/SVG/raster content to bounded PNGs at registered sizes. Capacity findings
  are advisory warnings, never silent truncation. Raster previews use Pillow independently of
  CairoSVG; text has a built-in Pillow fallback, while SVG still requires CairoSVG.
- Publications and v2 action nodes carry stable `itemId`s. `approve`, `snooze`, and `open` taps
  create durable, idempotent, allowlisted intents and audit rows; the agent reads and resolves
  them explicitly with `widget_read_intents` and `widget_resolve_intent`. The HTTP server never
  executes agent work, and sensitive classes require confirmation.

## v3.0.2 — delivery truthfulness, device identity, and discoverable contracts

- The publication store now retains every revision. A revision replaced before a device fetched
  it is recorded as `superseded` and listed in that device's `skippedRevisions`, so `status`
  distinguishes "not polled yet" from "lost". `status` also reports `lastPollAt` and
  `lastFetchedRevision` per device and the nominal `pollIntervalSeconds` (900).
- `maxAgeSeconds` is a per-publication freshness window: once exceeded the server answers
  `410 publication_stale` and reports `state: stale` instead of letting the phone render it late.
- `capabilities` now exposes `render.lastRendered` and `recommendedAspectRatio` (from render
  acknowledgements), the machine-readable `svg.allowedElements`/`allowedAttributes` allowlist
  with rejected constructs, the event vocabulary and emission points, and named TTL ceilings
  (`layoutMaxTtlSeconds` vs `publicationMaxTtlSeconds`).
- `GET /v1/widgets/<id>` echoes a `publication` pointer so the two stores are no longer
  confusable by a raw API consumer.
- The Android app sends a real device label at pair time, supports per-device rename
  (`PATCH /v1/device`, device token only), handles the `hermeswidget://pair?url=...&code=...`
  deep link, shows a pairing-code expiry countdown, and posts a `review` event plus an immediate
  refresh when the publication opens.
- `hermes widget pair` prints a copy-paste `URL  code` line; `hermes widget status` shows
  revision history and per-device poll/skip detail.
- CI installs `cmdline-tools` directly (no deprecated Android setup action), caps Gradle
  workers/heap for constrained runners, and runs the suite on Ubuntu 3.11/3.13 and Windows.
  `docs/APK_RELEASE.md` documents building without Android Studio.

## v3.0.1 — truthful binding and pairing guidance

- Binding precedence is consistent across the bootstrap, `setup`, `up`, `serve`,
  the `widget_setup` agent tool, and `status`: an explicit `--host`/`--port` wins,
  an omitted flag keeps the saved `widget/server.json` value independently, and a
  first install defaults to `127.0.0.1:8788`. A binding update preserves the saved
  Hermes executable and home, and a malformed `server.json` is reported instead of
  silently replaced.
- A binding change never restarts the server or claims the new bind is live. `up`
  and `status` report `restart_required`/`restartRequired`, and `status` prints the
  configured bind separately from the address its health probe actually used, in
  both text and JSON.
- `setup` and `up` no longer infer an HTTP phone URL. Operators are directed to
  their private HTTPS proxy URL and a short-lived code. `pair` now requires an
  explicit, usable HTTPS `--server-url`, prints the URL and code for manual entry,
  and no longer offers an unsupported QR/same-phone link or `--qr` option.
- `up` keeps `pairing_url_hint` `null` unless a usable HTTPS URL was supplied, and
  returns actionable pairing instructions.
- The Windows launcher test patches a platform decision inside the gateway hook
  instead of mutating Python's process-wide `os.name`; both branches are covered on
  Linux, and CI runs the suite on Windows and on Ubuntu with Python 3.11 and 3.13.
- Public guides now state the container rule precisely: direct hosts bind loopback,
  while a container binds `0.0.0.0` inside the container behind a host-side loopback
  port publish, with paired Compose settings and a required host-side port check.

## v3.0.0 — personal visual channel

- Added the authenticated `widget_publish` path for accessible text, constrained static SVG,
  and bounded local PNG/JPEG/WebP assets. Publications are immutable revisions with required
  title/summary, optional expiry, conditional fetches, and honest delivery stages.
- Android now prefers publications while preserving the existing `hermes-brief` widget and
  legacy v2 layout endpoints, renders visuals without cropping, supports zoom, reports usable
  dimensions, and keeps the last successful publication offline.
- Installation is capability-based and idempotent: it detects Hermes/profile/home/toolset
  capabilities, installs the plugin/skill/gateway hook, and uses a persistent server fallback
  when systemd is unavailable.
- Ordinary Hermes turns receive a concise publishing reminder, and the one six-hour routine
  uses `widget_publish` only when a useful update exists.
- Android release signing accepts the existing personal identity through protected Gradle
  properties or environment variables. No signing secret is stored in this repository; the
  release artifact must still be built and verified on the owner’s signing host.

## v2.1.1 — bloat audit: cut what nothing used

A repo-wide over-engineering pass (`/ponytail-audit`). Nothing here changes what the widget
renders; it removes code and dependencies that had no caller.

**Dependencies.** Four declared Android dependencies were never imported — verified by removing
them and building: `retrofit` (HTTP is `HttpURLConnection`), `kotlinx-serialization-json` (JSON is
`org.json`), `datastore-preferences` (storage is SharedPreferences, and datastore still arrives
transitively via `glance-appwidget`), and `glance-material3`. Measured with a clean build both
ways: the debug APK goes from 10,697,969 to 6,951,623 bytes — **-3,746,346 bytes (35%)**.
`appcompat` is kept: it is the only parent of `AppTheme`.

**Deleted.**

- `docs/hermes-notes/` — 4,925 lines of copied upstream Hermes docs, unreferenced (see
  CONNECTION.md §10 for why the install is the authority instead).
- `hermes-plugin/hermes-widget/pairing.py` — never imported; its `from hermes_widget import store`
  could not resolve, and it duplicated the QR payload already built in `tools.py`.
- `scripts/bootstrap.sh` — unreferenced; re-implemented `hermes widget up` in bash.
- `store.delete_widget`, `Layout.kt formatEventTime` — no callers.
- `backend/{SECURITY,TAILSCALE}.md` and `.env.example` — notes for a service whose `app/` is gone.
  `backend/DEPRECATED.md` now names the owner of each rule they used to describe.

**The v1→v2 migration is gone.** `migrate_layout`, the v1 node-type stripping, the pre-migration
DB backup and `fixtures/legacy-v1.json` are removed: no `widget.db` exists on the host, nothing
has shipped a v1 database (versionCode 1), and the 6-hourly agent push would rewrite the layout
anyway. A layout that is not `version: 2` is now rejected with a clear error instead of silently
rewritten. If you somehow have a v1 row, `git show <this commit>^:hermes-plugin/hermes-widget/validate.py`
has the old migrator, and one `widget_update` from the agent replaces it.

**Two pairing tools became one.** `widget_pair` minted the same code as
`widget_mint_pairing_code`; the QR payload and same-phone link now come back from
`widget_mint_pairing_code` when you pass `server_url`. The agent tool surface is 7 tools.

**Two correctness fixes found by the audit.**

- `box` ignored its own `alignment` and always overlaid children at center-start, while both
  SKILL.md and SCHEMA.md documented `alignment` as rendering for `box`. `BoxNode` now maps it.
- `widget_setup` set `HERMES_WIDGET_FORCE_ENV=supported` at runtime to talk its own
  environment check past the guard. Removed: an unsupported host now returns the honest
  `needs_user_action` payload.

**Recovery kept honest.** `_backup_db` used to be reachable only through migration. It is now
`store.backup_db()`, called by `hermes widget upgrade` — the one command that can replace the
database — so `hermes widget rollback` has a real restore point. `verify-cli-local.py` no longer
plants a fake backup; it exercises the real upgrade→backup→rollback path.

---

## v2.1.0 — the layout contract actually renders

v2 introduced a layout contract the device did not honour: `style`, `color`, `spacing`,
`thickness`, `alignment`, `padding` and `weight` were declared in the schema, accepted by the
validator, and ignored by `Renderer.kt`, which painted a bare black `Text` whatever you sent.
This release makes the contract real and adds a guard against it drifting again.

- **Typed design tokens** (`Typo.kt`): the `style` enum is now a four-step scale that renders —
  `title` 18sp bold, `body` 14sp normal, `label` 12sp medium, `caption` 11sp normal/secondary.
- **Every declared field is applied**: `color`, `maxLines`, `alignment`, `padding`, `weight`,
  `spacing`, `divider.thickness`, `calendar` times as clock times instead of raw ISO strings,
  `badge`/`button` fills, native `LinearProgressIndicator`, and a stale banner from
  `ttlSeconds` + `updatedAt`.
- **`widget_validate`** (new tool): dry-run a layout — node count, byte size, text styles, and
  design warnings — without publishing and without spending a rate-limit slot.
- **Hex-only colours enforced** on every colour field, not just `accentColor`.
- **Golden layouts** in `fixtures/golden/`, asserted valid *and* warning-free in both
  languages, and referenced by the design skill.
- **Offline preview**: `hermes widget preview <layout.json>` renders the tree to HTML in every
  widget shape, with the widget bounds outlined, so a design can be seen without building and
  installing an APK. It draws only from the registry's typography and palette.
- **`scripts/check-contract-parity.py`** (in CI): compares schema, validator, Android parser,
  renderer tokens, skill and docs — including the type-scale sizes, palette and spacing — and
  fails when they disagree. The typography scale and palette now live in
  `layout.schema.json` (`definitions.typography` / `definitions.palette`), so there is one
  registry rather than six copies.
- **Removed dead fields**: `visibleIf` (never validated, never rendered), `list_item.leadingIcon`
  (v2 has no icons), `progress.max`, and duplicated `stat.weight` / `list_item.id`.
- **Skill rewritten** with the typography scale, colour rule, spacing rhythm, canvas sizes,
  the one-hero rule, a reference-layout list, and an explicit "only these fields render" table.

---

## v0.2.0 — connects to your own Hermes agent

The widget now talks to **your own Hermes agent**, not a developer-hosted backend. The
`hermes-widget` Hermes plugin starts a small HTTP server on your machine (`hermes widget serve`)
that serves the same REST paths the app already uses, with bearer-token auth.

> Historical note: the commands below describe the old pre-pairing flow and are not current
> setup instructions. Use [docs/HERMES_AGENT_SETUP.md](docs/HERMES_AGENT_SETUP.md). The current
> app pairs with a short-lived code and device token, and the phone must never receive the
> operator token.

- Default widget id is now `hermes-brief` (was `fixture-morning-brief` / `demo-widget`).
- User-facing copy now says "Hermes agent" instead of "backend".
- **`backend/` is deprecated** for shipping and kept only as a local fixture. The shipped
  transport is the Hermes plugin.

---

## v0.1.0

Historical release notes only; do not use these pre-pairing instructions for the current app.
The current setup uses the capability-based bootstrap, Tailscale Serve HTTPS, and short-lived
pairing codes. See [docs/HERMES_AGENT_SETUP.md](docs/HERMES_AGENT_SETUP.md) and
[docs/APK_RELEASE.md](docs/APK_RELEASE.md).
