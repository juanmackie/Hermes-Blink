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
| 8dp grid, 16dp margin/gutter, 4dp sub-increments | `res/values/app_dimens.xml` | 24dp everywhere was not on the grid the system describes. |
| Shape: 24dp card radii, pill buttons, 12dp fields | `res/drawable/surface_card.xml`, `bg_button_*.xml`, `bg_field.xml` | Concentric harmony: 24dp cards inside the 28dp widget root. |
| Filled / tonal / outlined button hierarchy | `ButtonFilled`, `ButtonTonal`, `ButtonOutlined` | One primary action per screen, which is also the accessibility guidance. |
| Outline-variant hairline instead of shadow | `divider_hairline.xml`, card stroke | Reads on any wallpaper, which is the whole point for a launcher surface. |
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

## Gates

`AppSurfaceTest` (JVM, runs in CI):

- no screen layout hard-codes a colour;
- the programmatic publication view hard-codes none either (comments stripped first);
- every `@color/app_*` a layout uses is defined in **both** light and night;
- the tonal ladder rises and falls in the right direction per mode, and the dark set is the
  design system's values exactly;
- every ink token clears 4.5:1 on every surface it is painted on, in both modes;
- touch targets stay ≥ 48dp.

`scripts/check-contract-parity.py` also fails if a screen layout or `PublicationActivity`
reintroduces a literal, so the gate survives someone editing the XML without running tests.
