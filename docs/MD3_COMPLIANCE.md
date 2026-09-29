# MD3 compliance audit

The self-audit the `material-3` skill asks for, run against this repository rather than a
mockup. Scored from source, resources and tests — **no screenshot, no device, no claim of
physical verification**. Anything that would need a device to confirm is marked as such
instead of being scored as passed.

- **Target:** `android/app` (5 activities + the Glance home-screen widget)
- **Date:** 2026-09-29
- **Reference:** m3.material.io, the AndroidX Material 3 views guidance, and Google's
  Expressive research. Not the Compose API — this app's screens are Views, and the skill
  names Views/web as the secondary path, so the audit is written against the platform the
  code actually uses.
- **Change this audit describes:** versionCode 13 / 0.4.9 (round 15). Round 14 made the
  *palette* M3; this round made the *components* M3.

## Overall: 82/100

| # | Category | Score | Status |
|---|----------|-------|--------|
| 1 | Colour roles and tokens | 10/10 | pass |
| 2 | Dynamic colour (Material You) | 9/10 | pass |
| 3 | Typography | 9/10 | pass |
| 4 | Shape | 10/10 | pass |
| 5 | Elevation / depth | 9/10 | pass |
| 6 | Components | 9/10 | pass |
| 7 | Layout and insets | 8/10 | warn |
| 8 | Navigation | 7/10 | warn |
| 9 | Motion | 4/10 | warn |
| 10 | Accessibility | 8/10 | warn |

---

## 1. Colour roles and tokens — 10/10, pass

Every MD3 role the components read is mapped in `values/themes.xml`, and every mapping
points at an `app_*` token (or, on API 31+, at the platform's own `system_*` role). The
secondary and tertiary groups did not exist before this round: they arrived with the
components, and leaving them out would have silently fallen back to the Material library's
static purple.

The tonal ladder (`surfaceContainerLowest → Highest`, plus `surfaceDim`/`surfaceBright`),
`surfaceInverse`/`onSurfaceInverse`/`primaryInverse`, `outline` vs `outlineVariant`, and
the four error roles are all present in both `values/` and `values-night/`.

Measured, not assumed: `AppSurfaceTest` computes the contrast of every text pair (4.5:1)
and every non-text pair (3:1) per mode, and asserts the ladder's direction — the light
scheme darkens as it rises, the dark scheme lightens.

*Gate:* `every MD3 colour role the theme uses is mapped to a Hermes token`,
`the secondary and tertiary pairs are defined in both modes and clear AA`,
`every ink token clears AA on every surface it is painted on`.

## 2. Dynamic colour — 9/10, pass

`values-v31/` and `values-night-v31/` map the same roles to `@android:color/system_*`, so
on Android 12+ the screens follow the wallpaper-derived scheme. The baseline in `values/`
is the fallback below that. Surfaces come from `neutral1`, secondary ink and outlines from
`neutral2`, and accent1/2/3 take primary/secondary/tertiary — the M3 dynamic mapping.

One role deliberately does not follow the device: `error`. The platform's error ramp does
not form a legible on-error pair in both modes, so it stays on the baseline value rather
than guessing a tone. That is the one point of the 10.

*Gate:* the v31 files are read by `AppSurfaceTest`'s light/night colour parsing, so a role
removed from the baseline fails there; the dynamic overrides themselves are reviewed here.

## 3. Typography — 9/10, pass

`TextTitle` / `TextBody` / `TextBodySmall` / `TextLabel` carry the M3 steps with the spec's
line height and tracking (22/28, 16/24, 14/20, 12/16), expressed as size +
`lineSpacingExtra` because `android:lineHeight` is API 28+ and minSdk is 26. Buttons and
the app bar take text *appearances* (the M3 `label-large` / `title-large` forms), which is
the shape the library's components expect.

The ad hoc sizes are gone: 13sp (the pairing expiry line) and 18sp (the publication body)
are both off the M3 scale, and both are now a named role. `no screen uses a type size that
is not on the M3 scale` fails the build if one comes back.

Not a 10 because the widget's own type scale (`Typo.kt`) is a *publisher-facing wire
contract*, not an MD3 type scale, and deliberately stays as it is.

## 4. Shape — 10/10, pass

`shape_xs/sm/md/lg/xl/full` (4/8/12/16/28/full) is exposed to components as the MD3
corner scale (`shapeAppearanceCornerExtraSmall → …CornerExtraLarge`), so a component takes
a step from the scale instead of naming a radius: pill for anything pressed, `medium` for
fields, `large` for cards, `extra-large` for the dialogs the library draws.

*Gate:* `the corner scale the theme hands to components is the M3 scale` asserts both the
role → scale mapping and that each scale step comes from the shared `app_dimens` steps.

## 5. Elevation and depth — 9/10, pass

MD3 replaced elevation overlays with tone, and this app has all five container steps, so
`elevationOverlayEnabled=false` and depth is carried by the ladder. A raised card is
`Widget.Material3.CardView.Elevated` (a real shadow, no tint); the cards actually used are
filled (surface container, no elevation) or outlined (hairline in `outlineVariant`).

Not a 10 because there is no elevated card on screen yet — the diagnostic did not find a
place where one earns its keep.

*Gate:* `depth is tone, so the MD2 elevation overlay is off`.

## 6. Components — 9/10, pass

| Before | Now |
|---|---|
| `<Button>` + a hand-drawn `<ripple>` | `MaterialButton` (filled / tonal / outlined / text), state layer from the theme |
| `<EditText>` + `bg_field.xml` | `TextInputLayout` outlined box, label in the cut-out, 2dp primary focus stroke |
| `<LinearLayout style="@style/SurfaceCard">` | `MaterialCardView` filled / outlined |
| A `View` + `divider_hairline.xml` | `MaterialDivider` |
| No app bar (platform `DarkActionBar`) | `MaterialToolbar` as the top app bar, on every screen |
| `Toast` | `Snackbar`, anchored |
| `AlertDialog.Builder` | `MaterialAlertDialogBuilder` (28dp corners, tonal surface) |
| `LinearLayout` built in Kotlin | MD3 shell layout + MD3 action item layouts |

*Gate:* `every screen is built from MD3 components, not platform widgets` fails the build
if a platform `<Button>`, `<EditText>`, `<Switch>`, `<CheckBox>`, `<SeekBar>` or
`<Spinner>` reappears in a layout.

Not a 10: no MD3 input beyond the text field exists on these screens — see *Deliberately
not adopted*.

## 7. Layout and insets — 8/10, warn

Passing: the app bar is a small top app bar (64dp, no elevation) on every screen; content
is one scroll region; spacing is the MD3 scale (4/8/16/24) with one name per step (the
duplicate `gutter` name for 16dp is gone); the action hierarchy is one filled primary per
screen with the rest stepping down (tonal → outlined → text).

Warning: **no adaptive or canonical layouts.** Every screen is a single phone-width column.
MD3's canonical layouts (list-detail at 600dp+, supporting pane, foldable hinge avoidance)
are not implemented, because these four screens are a form, a diagnostics panel and a
publication view, and none of them has a list to pair a detail with. The
`ScrollView`/`NestedScrollView` reflow is honest, but on a tablet the pairing form is a
column stretched to full width, which the M3 guidance itself calls out as unreadable at
840dp+. Not fixed here: it needs a decision about what these screens do on a tablet, not
another dimension value.

Needs a device: window insets are applied with `fitsSystemWindows` on each root, which is
correct for targetSdk 35's enforced edge-to-edge, but has not been looked at on an Android
15 device.

*Gate:* `every screen has an MD3 top app bar that insets its content`,
`no screen uses a type size that is not on the M3 scale`.

## 8. Navigation — 7/10, warn

Passing: every screen has a top app bar with a way back (`ic_arrow_back`, or `ic_close` on
diagnostics, which is a panel rather than a step in a flow), and `onSupportNavigateUp` is
implemented in every activity.

Warning: **no navigation bar, rail or drawer, and no navigation destinations.** MD3's
navigation is for moving between destinations; this app has a launcher screen and three
things you can open from it, which is a screen-and-a-dialog shape, not a navigation shape.
The screen it *does* have (a top app bar with a back affordance) is the correct MD3
treatment for that shape. The 7 is for not having faced the question at all: a fifth
destination would force one.

Needs a device: predictive back (`OnBackPressedCallback`) is not implemented; the system
gesture handles these screens, but the animation between them is the platform's.

## 9. Motion — 4/10, warn

The MD3 transition tokens (emphasized 500/400/200ms, standard 300/250/200ms, and their
`cubic-bezier` equivalents) are **not** used anywhere. The screens switch instantly, which
is what they did before.

Deliberate in part: the one transition this app had — the diagnostics screen close — is
suppressed on purpose (it was suppressed because "the system animation on a diagnostics
panel read as the button not having worked"). The rest is simply not done.

Not implemented: spring-physics motion or M3 Expressive shape morphing. Both need a
Compose/Glance surface, and the widget is RemoteViews/Glance where there is no animation
to drive.

This is the one category where the honest score is low, and the reason it is *low* rather
than *absent* is worth stating: M3 motion is a real accessibility-sensitive feature
(reduced-motion preferences, spring stiffness), and adding it without a device to check it
on would be adding motion nobody has watched move.

## 10. Accessibility — 8/10, warn

Passing: touch targets are 48dp everywhere (MD3's button is a 40dp *visual* height inside
a 48dp target, and that is written down in the style); contrast is measured, not assumed;
the widget's action is a 48dp target and the root is not a click target (round 12: a press
that reaches the surface instead of the button is now structurally impossible); the
publication zoom view is keyboard/pinch zoomable with a `contentDescription`; status colours
are never the only signal (the status dot carries a content description, the footer spells
the state out in words).

Warning: **no formal accessibility audit, and TalkBack has not been run on these screens.**
Two known gaps: the publication screen's zoom view does not announce its scale changes, and
the snackbars do not move focus (MD3 guidance recommends that for a message the user has to
act on). Both need a device and a screen reader to verify honestly.

---

## Deliberately not adopted

| Not taken | Why |
|---|---|
| Chips, switches, checkboxes, radio buttons, FABs, bottom sheets, navigation bar/rail, dialogs with a sheet | No screen has a feature they would serve. Adding a switch to a screen with one setting would be decoration, and M3 components are not free (APK weight, see round 15 in `docs/APP_SURFACE.md`). |
| `glance-material3` for the widget | Removed in the v2.1.0 bloat audit because nothing imported it. The widget's own composition has no chip, FAB, or button component to use one for, and its type scale is a publisher-facing wire contract (`Typo.kt`). |
| MD3 Expressive (spring motion, shape morphing, larger shape range) | Needs a Compose/Glance animation surface and a device to evaluate. See *Motion*. |
| Material Web / Flutter guidance | Neither platform is in this app. |
| Roboto Flex | No font file to ship, and the widget cannot set a family at all; the platform sans is the equivalent. |
| 40dp buttons | Below the platform's 48dp touch-target floor. The style says so. |
| Forcing dark | Dark is the canonical *authored* scheme and the device's default in dark mode; it is not imposed. |
| A user-visible "follow the wallpaper / fixed palette" switch | Would be a real MD3-adjacent feature, but it adds a preference, a settings row and its own tests. Not part of "apply MD3"; a separate decision. |

## What the audit cannot tell you

Everything in *Motion* and the last paragraph of *Accessibility*, plus the insets
paragraph in *Layout*: this audit reads the repository. It has not seen these screens on a
device, in light and dark, at 48dp font scale, in TalkBack, or on a foldable. The numbers
it does report (contrast ratios, the tonal ladder, the scale steps, the presence of a
component) are computed from the files and are as good as the files.
