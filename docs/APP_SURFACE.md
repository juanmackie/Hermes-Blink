# Companion app surface — what was adopted from the design system

The design system in `.pi-web/attachments/` describes a *delivery-tracking* product:
shipment cards, filter chips, switches, steppers, countdowns, tablet dual-pane. This app
is a widget plus four thin screens. Taking the document wholesale would have imported
components for features that do not exist and a font that cannot be shipped offline.

So this page records the split: what was adopted, what was deliberately left, and why.

## Adopted

| From the system | Here | Why it fits |
| --- | --- | --- |
| Tonal surface tiers, dark canonical | `res/values-night/app_colors.xml` is the design system's ladder token for token (`#131314` / `#1C1B1C` / `#2A2A2B`), with a light ladder in `res/values/app_colors.xml` | Depth by luminance, not drop shadow. Also fixes a real defect: the main screen was `#FFFFFF` on `#000000` text, and the pairing screen the same, so neither had a dark mode. |
| Elevation direction | Asserted per mode in `AppSurfaceTest` | Light raises by darkening, dark raises by lightening. The first version of the light ladder had this backwards, and the test caught it. |
| Type roles (title-lg 22, body-lg 16, body-md 14, label-md 12) | `styles/TextTitle`, `TextBody`, `TextBodySmall`, `TextLabel` | The screens were 22/16/15/13sp ad hoc. The widget's own four-step scale (`Typo.kt`) is a publisher-facing contract and is deliberately **not** shared with the app. |
| 8dp grid, 16dp margin/gutter, 4dp sub-increments | `res/values/app_dimens.xml` | 24dp everywhere was not on the grid the system describes. The duplicate `gutter` name for 16dp was removed in round 15: two names for one step is how a layout ends up half on one scale and half on the other. |
| Shape: 24dp card radii, pill buttons, 12dp fields | `res/values/app_dimens.xml` (the scale), `res/values/themes.xml` (the scale handed to components) | Concentric harmony: 16dp cards inside the 28dp widget root. Round 15: components take a step from the named scale instead of naming a radius. |
| Filled / tonal / outlined button hierarchy | `ButtonFilled`, `ButtonTonal`, `ButtonOutlined` | One primary action per screen, which is also the accessibility guidance. |
| Outline-variant hairline instead of shadow | `MaterialDivider`, card stroke | Reads on any wallpaper, which is the whole point for a launcher surface. The hand-drawn `divider_hairline.xml` is gone: it was a `View` with a colour where a component belongs. |
| 28dp widget root | `shape_widget_root`, as the *floor* | The system calls it non-negotiable; on API 31+ the widget still resolves the launcher's own radius first (`WidgetRadius`), so 28dp is only the fallback. |
| Monospace for values that change | `TextMono` in Diagnostics | Device ids, revisions and request ids should not reflow while being read. The system's monospaced Roboto Flex instances are not available offline; `monospace` is the platform equivalent. |

## Deliberately not adopted

| From the system | Why not |
| --- | --- |
| Shipment receipt cards, chips, filter bars, switches, checkboxes, progress steppers, status pills | There is no filtering, no settings toggles, no staged delivery flow and no progress to show. Inventing them would be UI for imagined features. |
| Roboto Flex as the font family | No font file, and no API-level guarantee of the variable axes. The screens use the platform `sans-serif`; the widget cannot set a family at all. |
| 40dp buttons | Below the 48dp touch-target floor. `touch_target` is 48dp and `AppSurfaceTest` fails if it drops. |
| "Dark is canonical" as a *force* | The dark set is what we author as the default, but the app follows the device's light mode like every other Android app. Forcing dark would break users who asked for light. |
| Foldable/tablet dual-pane, `1.5rem` expanded margins, 8/12-column grids | Four screens of form and diagnostics. A `ScrollView` reflows. |
| Numerals tracking, countdowns, ETAs | No countdown exists. |

## What the change fixed, beyond looks

- The publication zoom view was a hard-coded near-black screen in **every** mode. It now
  resolves the themed surface, and its body text moved off `Color.WHITE` at the same time —
  leaving it white on a light surface would have been unreadable. `AppSurfaceTest` scans
  that Kotlin file for exactly this, because a programmatic layout has no XML to gate.
- Ten `HardcodedText` lint warnings and four overdraw warnings are gone; the string table
  is now the single place the screens' words live.
- Four new screens' worth of literals (`#FFFFFF`, `#000000`, `#666666`, `Color.rgb(...)`)
  were the reason the app had no dark mode at all.

## Round 6 — "the buttons do nothing"

Reported on the Diagnostics screen, and worth writing down because the cause was not the
obvious one:

| Button | What was actually wrong |
| --- | --- |
| Add widget | `offer()` returned `false` and said nothing whenever the once-per-pairing flag was set, so after the first automatic offer the button was permanently dead *and mute*. Split into `offerOnceAfterPairing` (automatic, still once) and `offerNow` (manual, always acts and always reports: requested / already added / launcher cannot pin / system refused). |
| Review battery | Opened `ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`, which modern Android routes to a per-app screen that returns immediately for apps without a direct-exemption entitlement — and `startActivity` does not throw, so the old `runCatching` fallback never fired. Now: say "already exempt" when it is, otherwise open the battery **list** and say what was opened. |
| Close | Worked, but was last in a long scroll and gave no press feedback at all. |

The shared cause behind all three: **the custom button backgrounds were plain `<shape>`
elements with no state layer.** A tap produced no ripple and no colour change, so a button
that acted correctly still read as dead. All button backgrounds are now `<ripple>` with a
mask (the mask matters for the outlined button, whose fill is transparent), and text fields
have a focused state. `AppSurfaceTest` and `check-contract-parity.py` both fail if a button
background loses its ripple.

## Settings: a paired status that keeps up

`PairingStatus` (pure, unit-tested) renders one line plus an optional detail, polled every
2s from `onResume` and cancelled in `onPause`:

- **Unpaired** — names the next step (`hermes widget code`).
- **Pairing…** — an attempt is in flight.
- **Paired as &lt;deviceId&gt;** — with "polled 30s ago · fetched 25s ago" from local state.
- **Pairing expired** / **Connected earlier, now failing** — a revoked or errored pairing is
  a *problem* tone, not "unpaired"; the two look identical if you only check for a token.
- **Offline** — and it never claims a cached publication before one has been fetched.

The tick reads only the encrypted store and prefs: no network, no battery cost, and no way
for a 2-second cadence to hammer a private server.

## Round 7 — "Request update does not work"

The report carried the answer in one line: **"Widget actions: none recorded yet"** while
the same screen showed a healthy poll, fetch, render and a registered instance. Since
round 5, every outcome of a widget-button press is written locally before the request is
attempted, so an empty list means the press never reached our code — this was not a
network, token or server problem, and no amount of server-side work would have found it.

Two weaknesses on our side, both fixed:

1. **The scroll region was `defaultWeight()`.** A weight-constrained Glance LazyColumn is
   measured by the platform at draw time; when that resolution misses, the Column is
   taller than the cell and the launcher clips the bottom — which is exactly where the
   pinned action lives. A press then lands on the *surface*, whose target is "open the
   app", and the button looks dead. The scroll region now has an explicit height computed
   from the instance (`BandSpec.scrollHeightDp`), so the chrome is subtracted by
   arithmetic and the footer is inside the cell by construction. Four unit tests assert
   the arithmetic across 4 widths x 9 heights.
2. **The last silent exit is gone.** `parameters[eventKey] ?: return` could bail out with
   no trace; it now logs and records `missing_event_parameter`.

And the trail grew the two links that were missing, so this cannot be ambiguous again:

| Link | Where | Means |
| --- | --- | --- |
| 1. composed | `Config.setLastComposition` from the widget, shown in Diagnostics | the band, whether an action was drawn, the scroll height |
| 2. fired | `Config.recordActionFired` in the receiver, counting the broadcast *before* Glance dispatches it | a tap produced an action that reached this process |
| 3. outcome | the action list, already present | what the request got back |

`fired = 0` means the tap never produced a broadcast — the surface got it. `fired > 0`
with no outcome means the dispatch failed. `composed: NO ACTION DRAWN` means there was
no button to press. Each is a different bug with a different fix, and the screen now says
which.

## Round 8 — the deployment review

### 1 (P0) The composition was built for the wrong height

The field report carried the numbers: on a 407x270dp 4x2 cell, Diagnostics read
`composed: band L … scroll region 316dp`, and 316 + 96 of chrome = 412dp — the 4x4 height.

Cause, and it is a real misunderstanding on my part: in `SizeMode.Responsive(sizes)`, Glance
composes for the **closest sample in the set**, so `LocalSize` inside the composition
reports *that sample*, not the cell the launcher draws into. The nearest sample to
407x270 was 407x412, so the column was composed 142dp too tall, the pinned footer and its
action fell outside the cell, taps landed on the launcher, and no action broadcast was
ever produced — `fired: 0`, which is exactly what the report showed.

The order is now inverted:

1. **The launcher's own report for this instance** (`AppWidgetManager` options, refreshed on
   every resize) is authoritative. It is the same geometry the host receives and the bands
   are defined against.
2. The responsive sample is a hint only, and now only decides *which composition tree*
   Glance builds — never a dp number we lay out with.
3. A fixed fallback is the last resort.

`SizeGate.resolve` is pure and records where the geometry came from. Acceptance tests
assert what the review asked for: at 8 real cell sizes × 4 samples, `chrome + scroll` fits
the cell, the band equals the band implied by the reported geometry, and the band does not
move when the sample changes. The 56dp 2x1 floor also forced a correction — a minimum list
height *guarantees* an overflow on a cell that small, so fitting wins and the list takes what
is left, with `hasReadableBody` saying whether that is enough.

Diagnostics now shows `composed Xdp · cell Ydp · source`, so a mismatch is visible from the
device instead of inferred.

### 2 (P1) `PUT /v1/device/attention` failed 100% of the time

The route read `widgetId` for routing and then passed the *whole body* to
`store.report_attention`, whose allow-list is aggregate counters only — so every report came
back `400 attention reports must not contain content or unknown fields`. 22 rejections and
counting. The store tests passed because they never sent a realistic body through the
route. Routing fields are now filtered before the call, and `AttentionRouteRoundTrip`
sends exactly what `HermesApi.reportAttention` sends. A test also pins the *other*
direction: a body carrying `payload`, `title` or `text` is still refused, so the fix did not
weaken the content guard.

### 3 (P1) CI had scheduled zero jobs since `e1a9cf8`

`.github/workflows/ci.yml:146` had `- name: Assemble the debug APK (clean: published sizes
are clean-build sizes)`. The unquoted `: ` makes the value a mapping rather than a string,
PyYAML rejects the file, GitHub reports no jobs, and CI stays green because nothing ran.
Quoted, and verified by parsing: 5 jobs, 30 steps.

`scripts/check-workflow-yaml.py` now does a real parse when PyYAML is available and a
dependency-free structural scan when it is not, and fails if any job has no steps. It runs in
CI *and* inside `check-contract-parity.py`, so a workflow that cannot run fails the repo
rather than the other way round.

### 4 (P2) Two smaller ones

- **The access log never emitted.** `_log` had no level and no handler, so it inherited the
  root WARNING threshold and every `_log.info(...)` was discarded — the line the round-5
  report asked for was dead as configured. `configure_access_log()` now attaches a handler
  and an INFO level, only when the host has not already configured logging, so it works
  without hijacking an application's handlers.
- **Release sizes cannot be a gate.** A clean debug build is not byte-reproducible across
  toolchains: drift of -4, +8, -12, +16 bytes in both directions. `release-evidence.py`
  checks the two things that cannot drift silently — the recorded commit (HEAD or its
  parent) and the versionCode — and *reports* size and digest as provenance. A gate that
  fails on every honest build teaches everyone to ignore red.

## Round 9 — the first CI run, and what it caught

CI ran for the first time since `e1a9cf8` and immediately proved its own value, though not
on the thing it was built for. The only red step was "Release evidence matches the build",
and it was red for a structural reason: `actions/checkout@v4` defaults to a **depth-1
clone**, so there is no parent commit, so the "HEAD or its parent" rule could not be
evaluated and the check blamed the document.

The deeper lesson is the general one, and it is worth writing down: *a gate that fails for
a reason unrelated to what it checks is the same disease as a gate that never fails.*
Neither teaches anybody anything except that red is noise.

Three changes came out of it:

1. **Both history-reading gates now refuse to run rather than guess.**
   `check-version-bump.py` was the quieter failure and nobody had noticed: on a push to
   main, `origin/main` *is* HEAD, so its diff was empty and it reported success without
   checking anything — vacuously green, in a full history, on every run. It now selects
   `HEAD~1` for the push shape and the merge base for a pull request, and both gates fail
   loudly with "shallow clone" and the fix when the history they need is missing.

2. **The evidence rule was replaced, not patched.** "HEAD or its parent" was narrower
   than the truth and was only stable for exactly one push. The invariant it was reaching
   for is: the recorded commit must be in this history, and its versionCode must match the
   build file. Distance behind HEAD is now *reported* rather than failed on, because
   recording the numbers is itself a commit and further behind is already caught by the
   version-bump gate.

3. **`fetch-depth: 0` on every checkout**, so what the gates see in CI is what they see
   locally.

`test_release_gates.py` now exercises both scripts in temporary repositories, in both
clone shapes: that each one refuses to run when starved, that each one fires on the drift
it exists to catch, and that size and digest stay provenance. Writing it caught a bug in
the tests themselves — the first draft invoked the *original* scripts, which quietly
inspected this repository instead of the fixture, and a fixture commit that was failing
silently because a clone has no git identity. Both are the same mistake in different
clothes: not checking that the thing you meant to run actually ran.

## Round 10 — the access log, green in the harness and dead in production

The reviewer's measurement was exact: `grep -c "access " server.log` = 0 after hours of
real traffic, while the test printed happily. The cause was the guard I added the round
before:

```python
if not logger.handlers and not logging.getLogger().handlers:   # the defect
    logger.addHandler(handler)
```

That is true in a bare test process and false wherever the host has configured logging —
which is the only place the log matters. In production `hermes_widget` had no handler of
its own, the record propagated to the gateway's WARNING-level root handler, and it was
dropped. The instinct behind the guard was right ("a library must not hijack the host's
handlers") and it was misread: attaching to *our own* logger is not hijacking.

Now: a handler is attached whenever **this** logger has none, whatever the root has;
`propagate` is disabled then so a verbose host does not print every line twice; a host that
has already configured `hermes_widget` is left alone; and `HERMES_WIDGET_LOG=off` silences
it deliberately for an operator who does not want request lines.

**The destination was already right.** The startup hook spawns the server with
`stdout=log, stderr=STDOUT` into `<Hermes home>/widget/server.log`
(`gateway_hook.py::_spawn_server`), which is the file an operator greps — so a plain
`StreamHandler` lands exactly where it should, and adding a file handler would have
double-written every line. Verified the way the reviewer did it: a real spawned process
with a WARNING root handler, output redirected to a log file, `grep -c "access "` → 2.

`AccessLogContexts` now tests the *deployment* context rather than the bare one: a root
logger that already has a WARNING handler must still emit, a verbose root must not
double-print, a pre-configured `hermes_widget` must be respected, and `off` must silence.
The first draft of that test attached a handler to `hermes_widget` before calling
`configure_access_log`, which made the guard unreachable — it passed against the broken
code, and passing against the broken code is the same disease as the bug. The suite now
fails when the old guard is restored, which is the only property that matters.

Three gates in `check-contract-parity.py` have now matched a *comment* explaining the
defect they forbid, so source greps go through one `source_of()` helper that strips
comments first.

## Round 11 — a second tap that did not land, and what it did not tell us

14:07 the pill worked end to end. 15:23 it did not, with the device demonstrably alive:
fetched, downloaded and render-submitted revision 24 at 15:23:03, attention aggregates
written — and no `request_update` event, no `widget_update_requests` row, and a production
access log with zero lines. A fix that works once is not a fix, so this round does not
claim the second tap is explained. It removes three reasons for it to be *unexplainable*,
and asks for the one number that discriminates.

**What I could not establish.** The trail lives on the phone, and nobody has read it back.
The server log is empty, so the server has no view of the press either. I have three
hypotheses and no evidence to choose between them:

1. the composition on screen at 15:23 was laid out differently from the one at 14:07 —
   a different band, a different instance, or a different scroll height, which would put the
   action outside the cell again;
2. the press produced a broadcast that Glance's dispatcher dropped;
3. the press never became a broadcast at all, i.e. it landed on the surface.

The composed/fired/outcome line separates all three in one reading, and it is the line the
review asked for. This round makes obtaining it one tap instead of a photograph.

**What changed.**

- **`Copy widget trail`** in Diagnostics puts the whole trail on the clipboard: app
  identity, every recorded composition with its instance, band, both heights, scroll height
  and geometry source, the fire count, every outcome, and the current inventory. One paste
  ends the transcription step that has now cost two rounds. The on-screen trail also shows
  the last four compositions instead of only the most recent one, so "was it laid out the
  same way?" is answerable without a paste.
- **Every composition is recorded, not just the last.** `Config.recordComposition` keeps a
  bounded history of eight. With one value stored, two presses cannot be compared at all —
  which is precisely the comparison this round needed.
- **The fire detector no longer depends on one internal extra name.** Glance exposes no
  constant for `ActionCallbackBroadcastReceiver:callbackClass`, and the previous code
  matched that exact string. The extras are matched by suffix, and a broadcast carrying
  the parameters extra but an unrecognisable name is still counted — with its extra keys —
  rather than ignored. A Glance upgrade would otherwise silence the trail again, which is
  how it went quiet twice already.
- **The access line for `/v1/device/attention` names its widget** instead of a dash, so a
  rejection and a success are attributable without a second query.

Three structural gates cover the new behaviour and were each verified by reverting it. Two
tautological JVM tests were written for the same properties and then **deleted**: they
restated the logic instead of exercising it, and a test that cannot fail is the disease
this round is about. Four separate "prove the gate bites" mutations in this session were
no-ops because they changed one of several occurrences — the same mistake, four times, in
the verification harness rather than the product. The gates are right; the way I kept
proving it was not.

## Round 12 — the press that reached the surface instead of the button

Two presses, both answered by the same evidence: a `review` event and no
`request_update`. `review` is recorded when the publication detail view opens, so
**the press was handled by the outer target** — the widget surface, whose action is
"open the app". It was not swallowed; it was answered by the wrong control, and the
wrong control does something, so nothing looked broken.

Two causes, both ours:

1. **Nested click targets.** The root `Column` had a blanket `clickable` and the
   action lived inside it. A clickable nested inside another clickable has no
   guaranteed winner in RemoteViews, so which one handled the press depended on
   layout and timing — which is why 14:07 worked and 15:23 did not.
2. **The action was the last child, below the scroll region.** A Glance lazy
   collection is a RemoteViews collection view; the height we give it is a request,
   not a guarantee. When it measures past that height it grows over whatever sits
   below it, and the footer — the only control — leaves the cell. The user aims at
   where the button *should* be and presses the body, which opens the app.

The fix removes both, and changes one behaviour deliberately:

- **The action is pinned in the header**, the first child, so nothing the scroll
  region does can move it out of the cell.
- **The root is no longer a click target.** The drill-down is the hero line, the
  action is the header button, and neither is inside the other. A press can now
  mean exactly one thing, so "the action press opened the app instead" is
  structurally impossible rather than merely unlikely.
- **The footer keeps the delivery state** and nothing else. Losing that line is
  harmless where losing the action is not.
- The chrome arithmetic follows: the header is now 52dp when the band has an
  action, so a 270dp cell reserves 94dp of chrome and gets a 176dp list. Three
  tests pin that, and the fit invariant still holds at 4 widths x 9 heights.

**This is a behaviour change worth naming:** tapping the body no longer opens the
app — only the hero does. That is the cost of removing the ambiguity, and it is
the right trade: a control that sometimes opens a different app is worse than one
that does nothing. The next press is also unambiguous — if `review` appears again
when the button is pressed, the button is not receiving the press, and that is now
a fact rather than an inference.

Four gates cover it and each was verified by reverting it, including one that only
appeared after strengthening: the header check originally looked for
`requestUpdateAction()` in the header, and a `if (false)` around the action still
contained the call, so the gate stayed green on a widget with no button. A check
that cannot distinguish live code from dead code is a check that has not been
written yet.

## Material 3 adoption (round 14)
Read from m3.material.io, the Android Material 3 guidance, Google's Expressive research and
the M3 design kit. As with the earlier design document, the split matters: this is a Glance
widget plus four XML screens, and the parts of M3 that suit it are taken whole while the
parts that describe a different product are named and skipped.

### Adopted, with the source of each decision

| Principle | What we do | Source |
| --- | --- | --- |
| Baseline is the fallback, **dynamic colour is the point** | `values-v31` and `values-night-v31` alias the platform's own tonal roles (`system_neutral1_*`, `system_neutral2_*`, `system_accent1_*`), so the screens follow the wallpaper-derived scheme on Android 12+; `values*/` hold the static baseline below that | "Dynamic color is the key part of Material You" — M3 in Compose |
| Surfaces are **tonal containers, not elevation overlays** | The full `surfaceContainerLowest → Highest` ladder, plus `surfaceDim/Bright`, replacing three hand-picked steps | "Introducing tone-based surfaces in Material 3" |
| Roles, not colours, in the code | Layouts and drawables reference `app_surface_container*`, `app_on_surface_variant`, `app_outline`; no screen names a hex value | M3 colour roles |
| Canonical values, not a lookalike | Values are the M3 baseline system tokens with the reference tone noted beside each (neutral 98/100/96/94/92/90 light, 6/4/10/12/17/22 dark; primary 40/80; error 40/80) and a parity check pins them | m3.material.io/styles/color/static/baseline |
| Colour is accessible by construction | Every text pair clears 4.5:1 and every non-text pair clears 3:1, asserted per mode; the tonal ladder's direction is asserted too | "Tonal palettes are critical to making any colour scheme accessible by default" |
| Shape is a **named size-based scale** | `shape_xs/sm/md/lg/xl/full` (4/8/12/16/28/full) with component steps: buttons pill, fields `md`, cards `lg`, widget root `xl` | M3 corner radius scale |
| A state layer is the **control's own ink** | Four layers, each that control's foreground at the M3 opacities (8–10%): filled `onPrimary`, tonal `onPrimaryContainer`, outlined `primary`, field `onSurface` | M3 state layers |
| Type is a role scale with line height **and** tracking | `title-lg 22/28`, `body-lg 16/24`, `body-md 14/20`, `label-md 12/16`, each with its M3 tracking; expressed as size + `lineSpacingExtra` because `android:lineHeight` is API 28+ and minSdk is 26 | M3 type scale |
| The app follows the device's theme | `WidgetTheme` and the activity theme resolve day/night; nothing forces dark | "A dark theme… to fit your branding" |

### Deliberately not adopted

| Not taken | Why |
| --- | --- |
| `com.google.android.material` / Compose components | **Superseded in round 15**: the Material Components library is in, because a style plus a drawable can express a *token* but not a component (no label in a field's outline, no state layer drawn by the control, no MD3 dialog geometry). Compose is still out: the screens are Views, and a rewrite is a different decision. See round 15 for the measured cost. |
| Spring-physics motion, shape morphing, expressive springs | There is no animation surface to apply them to: RemoteViews layouts are static and the one transition in the app is deliberately suppressed. |
| Chips, switches, checkboxes, progress, FABs, bottom sheets, nav bars | No such feature exists. Adding them to a diagnostics screen would be decoration, not design. |
| Fixed / add-on colour roles (`primary-fixed` and friends) | The spec itself scopes them to a hero-CTA use case we do not have. |
| Roboto Flex | No font file, and the widget cannot set a family at all; the system sans is the platform equivalent. |
| 40dp buttons | M3's button is a 40dp *visual* height; the platform's minimum touch target is 48dp. We keep 48 and say so rather than shipping an unreachable control. |
| Forcing dark | Dark is the canonical *authored* state and the device default on dark; it is not imposed. |

### One thing the alignment exposed

The widget's chrome tokens were a lookalike palette — close enough to pass every contrast
test, not the M3 roles. They are now the baseline, and the host preview mirror
(`preview.py::WIDGET_SURFACE`) was realigned with them, which is why the parity check
compares the two. The publisher-facing contract (`Typo.kt`, `layout.schema.json`) is
deliberately untouched: it is a closed wire contract, and `ContrastTest` now also proves its
`SECONDARY` still clears 4.5:1 on every new light surface (worst case 4.89:1).

## Round 15 — Material 3 *components*, not just Material 3 *colours*

Round 14 made the palette Material 3. Every control was still a platform widget with a
hand-drawn background, which is the half that looks almost right and is not Material: a
`<Button>` cannot take a colour role, so it cannot dark-mode correctly from the theme, it
cannot draw the state layer the spec asks for, and a text field built on `bg_field.xml`
could only swap its whole background on focus — which is why that file needed a
hand-written `state_focused` item to have a focus cue at all.

### What changed

| Before | After |
|---|---|
| `Theme.AppCompat.DayNight.DarkActionBar` + hand-mapped roles | `Theme.Material3.DayNight.NoActionBar`, every role mapped, corner scale handed to the components, `elevationOverlayEnabled=false` |
| `<Button>` + a per-button `<ripple>` drawable | `MaterialButton` (filled / tonal / outlined / text); the state layer is the control's own ink over its own fill |
| `<EditText>` + `bg_field.xml` | `TextInputLayout` outlined box: label in the outline's cut-out, 1dp outline → 2dp primary on focus, `medium` corners |
| `<LinearLayout style="@style/SurfaceCard">` + `surface_card.xml` | `MaterialCardView` filled / outlined — tone and a hairline instead of a shadow |
| A `View` + `divider_hairline.xml` | `MaterialDivider` |
| No app bar of our own | `MaterialToolbar` as a small top app bar on all five screens, with `ic_arrow_back` / `ic_close` |
| `Toast` | `Snackbar`, anchored to the screen |
| `AlertDialog.Builder` | `MaterialAlertDialogBuilder` (28dp corners, tonal surface) |
| 13sp and 18sp text | `TextBodySmall` / `TextBody` — both were off the type scale |
| Missing `secondary` / `tertiary` / `errorContainer` / `inverse*` roles | all present in `values/`, `values-night/` and both `-v31` sets |

The dependency: **`com.google.android.material:material:1.12.0`**, and it is the only one
added. It brings no network, database or analytics surface — appcompat, recyclerview,
constraintlayout, coordinatorlayout, transition, dynamicanimation, vectordrawable,
drawerlayout, cardview. The cost, measured on a clean `assembleDebug` against `f5c0068`:

| Build | Bytes | Note |
| --- | --- | --- |
| `f5c0068` (before) | 7,279,075 | |
| versionCode 13 (this round) | 9,750,534 | +2,471,459 (+33.9%) |
| uncompressed | 16,991,803 → 21,524,094 | +4,532,291 (+26.7%) |

No build type here sets `isMinifyEnabled`, so the release APK is unminified too and this
is not a debug-only number — the library's unused components ship. Turning on R8 plus
resource shrinking is the obvious follow-up, and it is a *separate* decision with its own
risk: a shrunk build has to be installed and exercised on a device before it is trusted,
and this repo does not claim device verification it has not done.

`glance-material3` stays out. The widget has no chip, FAB or button component to use one
for, its action's 8dp corner is `LayoutDefaults.BUTTON_CORRIER` publisher parity rather
than an app-side shape decision, and its type scale is a closed wire contract.

### Two failures this round found that were not about Material

1. **The tree did not build.** `work/DwellWorker.kt` (untracked, left behind by a discarded
   experiment) called `BackoffPolicy.EXPENSIVE`, which does not exist in
   `work-runtime:2.9.1`. `EXPONENTIAL` is the value it meant, and the build had been red
   before any of this work started.
2. **The gates were not re-running.** `AppSurfaceTest` reads `src/main/res` and the
   activity sources straight off disk — a theme is not a runtime class, so Gradle saw no
   input change and reported the test task `UP-TO-DATE` after a token was edited. The
   res and java directories are now declared as test resources, so the dependency is real.
   A gate that does not re-run is not a gate; it was verified by mutation (edit a token,
   watch the task fail).

### Also removed

`gutter` (a second name for 16dp — two names for one step is how a layout ends up half on
one scale and half on the other), `divider_hairline.xml` (a colour where a component
belongs), and the five `android:background="@color/app_surface"` on layout roots, which
the theme's `windowBackground` already paints (five overdraw warnings).

## Gates

`AppSurfaceTest` (JVM, runs in CI):

- no screen layout hard-codes a colour;
- the programmatic publication view hard-codes none either (comments stripped first);
- every `@color/app_*` a layout uses is defined in **both** light and night;
- the tonal ladder rises and falls in the right direction per mode, and the dark set is the
  design system's values exactly;
- every ink token clears 4.5:1 on every surface it is painted on, in both modes;
- touch targets stay ≥ 48dp;
- **the app theme is a real `Theme.Material3`, and it is the NoActionBar variant**;
- **every MD3 colour role the theme uses is mapped to a Hermes token** — an unmapped role
  does not fall back to the app's palette, it falls back to the library's static purple;
- **the secondary, tertiary, error-container and inverse pairs clear AA in both modes**;
- **no screen uses a platform `<Button>`/`<EditText>`/`<Switch>`/`<CheckBox>`/`<SeekBar>`**;
- **every screen has an MD3 top app bar and applies the window insets**;
- **text fields are TextInputLayout outlined boxes with a focus stroke in the theme**;
- **the corner scale handed to components is the M3 scale, and each step is a shared dimen**;
- **`elevationOverlayEnabled=false`** — depth is tone, not a tinted overlay;
- **system-bar icon polarity follows the device mode** (a bool, because a night style
  replaces the light one rather than merging with it);
- **no type size off the M3 scale** (11/12/14/16/22/24/28/32/36/45/57);
- **transient messages are snackbars, and a surviving toast has to say why** — the two that
  do (a message for a screen that is closing, with nothing left to anchor to) are checked
  for that comment, so a decision cannot decay into looking like an oversight.

`scripts/check-contract-parity.py` also fails if a screen layout or `PublicationActivity`
reintroduces a literal, so the gate survives someone editing the XML without running tests.

`docs/MD3_COMPLIANCE.md` is the self-audit this round was measured against: what is
compliant, what is deliberately not, and — in as many words — what cannot be answered from
the repository at all.

