# Autoresearch: Hermes-Blink Android widget UI/UX quality

## Objective
Implement the supplied Android widget design/quality plan task-by-task on the existing Glance 1.1.0 + v2-layout architecture, improving size-aware layout, scrolling/chrome behavior, theming, contrast, picker/loading fidelity, thresholds, publisher budgets, and regression gates without changing the wire contract or adding dependencies.

## Metrics
- **Primary**: `widget_quality_score` (points, higher is better) — deterministic audit of the required UI/UX evidence in source, resources, tests, and docs.
- **Secondary**: `python_tests`, `android_unit_tests`, `contract_parity`, `lint_ok`, `apk_built` — independent correctness/build monitors.

## How to Run
`./.auto/measure.sh` — outputs `METRIC name=value` lines. The audit is intentionally conservative: it checks evidence, not screenshots; physical-device claims remain separate.

## Files in Scope
- `android/app/src/main/java/com/you/hermeswidget/widget/HermesWidget.kt` — responsive composition, bands, chrome, theme/radius, request action.
- `android/app/src/main/java/com/you/hermeswidget/widget/WidgetDimensions.kt` — per-instance geometry and size classification.
- `android/app/src/main/java/com/you/hermeswidget/widget/Typo.kt` and `HexColor.kt` — theme/contrast tokens.
- `android/app/src/main/java/com/you/hermeswidget/DiagnosticsActivity.kt` — device observability.
- `android/app/src/main/res/values*/`, `drawable*/`, `layout*/`, `xml/hermes_widget_info.xml` — theme, preview, loading, radius, picker metadata.
- `android/app/src/test/java/com/you/hermeswidget/` — JVM regression tests.
- `hermes-plugin/hermes-widget/validate.py`, skill/docs, parity/verification scripts — publisher budgets and contract evidence.
- `docs/WIDGET_DESIGN.md`, `docs/CONNECTION.md`, `RELEASE.md` — design/quality documentation.

## Off Limits
- No wire-format or node-type changes; existing `2x2`, `4x2`, `2x4`, `4x4` variant keys remain the contract.
- No new Android/Python runtime dependencies, HTML/CSS/JS surface, cloud relay, or engagement mechanics.
- No push content, no direct execution from widget taps, no destructive live-data repair.
- Do not claim physical-device verification from previews or static audits.

## Constraints
- Keep Glance 1.1.0 and compileSdk 35 compatibility.
- Every task must end with its stated verification command and a green result before the next task.
- Preserve private HTTPS/bearer auth, device revocation, additive DB migrations, and queue-not-authorise semantics.
- Keep prompt/caches stable and avoid adding per-turn context or bloat.

## Task Order
0. Re-base and re-read the current scroll implementation.
1. Per-instance size mode and geometry.
2. Band breakpoint ladder and variant selection.
3. Pin status/action outside the scroll region; cap short lines only.
4. Header/mark/status dot.
5. Real 48dp request-update action and band-driven image height.
6. Theme tokens and system light/dark surfaces.
7. Contrast fixes and parity tests.
8. System corner radius with fallback.
9. Preview fidelity and night variant.
10. Loading state.
11. Size thresholds and dead-code cleanup.
12. Publisher-side band budget warnings.
13. Regression gates, CI, and checklist evidence.
14. Optional in-app widget pinning promotion.

## What's Been Tried
- 2026-09-26: Baseline branch created at 7abc096. The repository already has scrollable `LazyColumn` content from that commit, but the audit found the action/footer inside the scroll region, fixed-height images, hard-coded theme/radius/contrast, static light-only preview, and a single compact boolean.
- No experiments logged yet.
