# Widget design checklist

This checklist maps the Android widget design guidance to the Hermes widget implementation.

## Layout and sizing

- The provider declares a 4×2 default with horizontal and vertical resizing.
- Resize bounds cover the recommended handheld/tablet ranges.
- The surface fills the allocated bounds and uses a 24dp system-like corner radius.
- Geometry is reported from the launcher, so previews and rendering use the actual instance size.
- Compact instances show the hero; the ticker and supplemental regions are omitted when they would clip.
- Long publications use a Glance `LazyColumn`, so content is scrollable instead of silently cut off; stable item ordering preserves position across refreshes.

## Content

- The hero is the single focal point; title, summary, body, ticker, and delivery state have distinct hierarchy.
- A ticker can update without replacing the hero.
- Empty and expired states explain the next useful action.
- **Request update** is a 48dp-or-larger action that pokes the agent; it does not prescribe content.
- Publication history is bounded and reachable without unbounded storage.

## Color and typography

- Light and dark system themes are honored.
- The publication can request the dark palette explicitly.
- Ink/secondary colors are contrast-conscious and the accent is reserved for actions/status.
- The existing closed four-step type scale keeps the widget compact and legible.

## Discovery and picker

- The widget has a unique provider name and description.
- `previewLayout` shows representative Hermes content instead of the generic loading view.
- The default 4×2 size is the same conceptual layout the user receives.

## Quality gates

Before shipping a widget change:

1. Verify the minimum and maximum reported geometries.
2. Verify light and dark rendering.
3. Verify 48dp touch targets for every action.
4. Verify empty, stale, offline, and content-heavy states.
5. Verify the picker preview matches the real widget.
6. Run the Android unit tests, lint, and APK build.
