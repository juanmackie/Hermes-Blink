# Hermes Widget — Release notes

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
