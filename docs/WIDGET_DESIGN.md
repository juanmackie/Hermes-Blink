# Widget design checklist

This checklist maps the Android widget design guidance to the Hermes widget implementation.
Every row names the file that satisfies it and the command that proves it, so a reviewer
never has to trust a claim about a phone they cannot see.

## The band ladder (the single source of truth)

`android/.../widget/Breakpoints.kt` is the only place that decides how much content fits.
Height selects the band; width applies the single-column guard.

| Band | Height | Canonical shapes | Renders | Variant key | Action |
| --- | --- | --- | --- | --- | --- |
| `XS` | < 130dp | 2×1, 4×1 | mark + status dot, provenance + 1 hero line | `2x2` | tap opens the app |
| `S` | 130–184dp | 2×2, 4×2 floor | `XS` + 1 summary line | `2x2` | tap opens the app |
| `M` | 185–299dp | 4×2, 2×3 | `S` + scrollable body (≈3 lines visible) + pinned status + **Request update** | `4x2` | 48dp button |
| `L` | ≥ 300dp | 4×3, 4×4 | `M` + ticker, ≈8 body lines visible | `4x4` (falls back to `4x2`) | 48dp button |

`widthDp < 245` is single column: no ticker, no question, no split row, one hero line.

The host mirrors the same thresholds in `hermes-plugin/hermes-widget/bands.py`
(`size_band`, `BAND_BODY_LINES`, `chars_per_line`) so a publisher warning names the band the
user will actually see. The offline preview mirrors the same plan in `preview.py`
(`BAND_PLAN`, `WIDGET_SURFACE`).

## Acceptance table

| ID | Requirement | Where | Proof |
| --- | --- | --- | --- |
| WL-1 | Content fills the allocated bounds | `HermesWidget.kt` (outer `Column.fillMaxSize`) | `WidgetBreakpointsTest` + device pass list |
| WL-2 | Resizable to at least 2×2 / 4×1 / 4×2 | `res/xml/hermes_widget_info.xml` (`minResize*`, `maxResize*`) | `lintDebug` |
| WL-3 | Header: icon always, title when space allows | `HermesWidget.kt:258` `HeaderRow`, `res/drawable/ic_hermes_mark.xml` | device pass list (2×2, 4×4) |
| WL-4.2 | The minimum size still offers value | `Breakpoints.kt` `WidgetBand`, `HermesWidget.kt` `HeroBlock` | `WidgetBreakpointsTest.band edges …` |
| Breakpoints | Conditional content per size | `Breakpoints.kt`, `SizeGate` (`WidgetTheme.kt:140`) | `WidgetBreakpointsTest` (19 cases) |
| WC-1 | Device/app colour theming | `WidgetTheme.kt:24`, `res/values/colors.xml`, `values-night/`, `values-v31/` | `ContrastTest.the shipped resources are present and legible` |
| WC-2 | Light **and** dark palettes | same, plus `dark_palette` → `widget_*_dark` tokens | `ContrastTest.the forced dark palette …` |
| WC-3 | WCAG AA contrast | `Typo.kt` (`SECONDARY #5F5F66`, `SUCCESS #1E7D3C`, `DANGER #C5221A`) | `ContrastTest` (9 cases), `check-contract-parity.py` |
| WD-1 | Preview includes user content, matches the composition | `res/layout/widget_preview.xml`, `layout-night/widget_preview.xml` | `AppSurfaceTest`, device pass list |
| WD-4 | Preview accurate to size and theme | same files + `preview.py` `BAND_PLAN` | `check-contract-parity.py`, preview unit checks |
| WS-2 | System corner radius | `WidgetTheme.kt` `WidgetRadius`, `res/values/dimens.xml` | device pass list |
| WS-3 | Loading state matching the shape | `res/layout/widget_loading.xml`, `initialLayout` | `AppSurfaceTest`, device pass list |
| WT-3.1 | Widget updates after an action | `ActionCallbacks` `request_update` | host delivery tests |
| WT-4 | Actions reachable without hunting | `HermesWidget.kt:300` `FooterRow` **outside** the `LazyColumn` | `check-contract-parity.py` (`widget-surface`), device pass list |
| D15 | Band-driven image height | `Breakpoints.kt` `BandSpec.imageHeightDp`, `HermesWidget.kt:336` | `WidgetBreakpointsTest.image height is band driven …` |
| — | Truncation designed, not assumed | caps in `BandSpec` (hero 1–2, summary 1, status 1, ticker 1, question 1) | `WidgetBreakpointsTest.caps grow with the band …` |
| D14 | Discovery / promotion | `WidgetPinning.kt` (offered once after pairing; Diagnostics has a manual button) | device pass list |

## Caps, and why they are caps

Glance 1.1.0 has no `TextOverflow`: `maxLines` clips hard and never appends an ellipsis. So
every capped string is also *written* short — the hero is a headline, the summary is a
clause, the ticker is a fragment. The body is the one deliberately uncapped node: it lives
in a `LazyColumn`, so long text scrolls instead of disappearing, and the pinned footer means
the status line and the action never scroll away from it.

## Content

- The hero is the single focal point; title, summary, body, ticker, and delivery state have distinct hierarchy.
- A ticker can update without replacing the hero.
- Empty and expired states explain the next useful action, and offer the same 48dp action once the band has room.
- **Request update** is a 48dp-or-larger action that pokes the agent; it does not prescribe content.
- Publication history is bounded and reachable without unbounded storage.

## Publisher budgets (host side)

`widget_publish` returns advisory warnings keyed to the
smallest band a device registered:

| Warning | Meaning |
| --- | --- |
| `BODY_HIDDEN_IN_XS` / `_S` | The body does not render at that band at all — put it in the title. |
| `BODY_MAY_SCROLL_M` / `_L` | The body is longer than the visible budget; the reader scrolls. |
| `TITLE_MAY_CLIP` | More than ~2 lines of hero at that width (no ellipsis, so it clips). |
| `SUMMARY_HIDDEN_IN_XS` | The summary is not shown in a 4×1. |

Line counts are an estimate (~34 characters per line at 245dp, `body` 14sp) and every
message says so. `hermes-plugin/hermes-widget/skills/widget/SKILL.md` documents the ladder
so publishers write per band deliberately.

## Quality gates

Before shipping a widget change:

1. `./gradlew --no-daemon --max-workers=1 assembleDebug lintDebug` — builds and resource checks.
2. `./gradlew --no-daemon --max-workers=1 :app:testDebugUnitTest` — bands, caps, contrast, contract.
3. `python3 scripts/check-contract-parity.py` — Android vs preview colors and size-band thresholds.
4. `python3 -m unittest discover -s hermes-plugin/hermes-widget/tests -v` — publisher budgets and delivery.
5. `hermes widget preview --widget-id hermes-brief --sizes 2x2,4x2,4x4 --out /tmp/preview` — the same
   composition the phone composes (header, hero, summary, body, pinned footer with the action).
6. The device pass list below, with screenshots. Static checks cannot prove any of it.

## Device pass list (Pixel-class phone, Android 12+)

| # | Case | Pass condition | Screenshot |
| --- | --- | --- | --- |
| 1 | 2×1 (4×1) | header + one hero line, no clipped text, no action | `4x1.png` |
| 2 | 2×2 minimum (110×115dp) | title legible, no overflow | `2x2-min.png` |
| 3 | 4×2 (407×250dp) | body scrolls, status + action pinned | `4x2.png` |
| 4 | 4×4 (407×412dp) | ticker visible, ~8 body lines, footer pinned | `4x4.png` |
| 5 | Long body (500+ chars) | status line and action stay visible while the body scrolls | `long-body.png` |
| 6 | Image publication at 2×2 and 4×4 | band-driven height, no overflow, no dead band | `image-2x2.png`, `image-4x4.png` |
| 7 | Light theme | surface, ink, secondary and action all readable; no scrim | `light-4x4.png` |
| 8 | Dark theme + `dark_palette` | dark surface/ink pair; no washed-out text | `dark-4x4.png` |
| 9 | Android 12+ dynamic theme | surface/accent follow the wallpaper-derived palette | `dynamic-4x4.png` |
| 10 | Empty / expired / offline | each state explains the next action and offers it | `empty.png`, `expired.png` |
| 11 | TalkBack on the header | announces "Hermes, fresh, updated 6 min ago" | — |
| 12 | Tap targets | action ≥48dp; surface tap opens the app; action does not open the app | — |
| 13 | Picker preview | light and dark previews match cases 3/4 | `picker-light.png`, `picker-dark.png` |
| 14 | Cold add | loading wireframe does not jump to a different shape | `loading.png` |
| 15 | Corner radius | matches a stock Google widget on the same launcher | `radius.png` |
| 16 | Diagnostics → instances | per-instance dp/px, size class, band and variant key | — |

Cases 1–16 are **not** covered by CI. Everything in the "Proof" column above is; this table
is the remainder and must be run on hardware before a release.
