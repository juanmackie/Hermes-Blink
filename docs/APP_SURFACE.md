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
