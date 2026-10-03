# Android app and widget UI

This is the current UI contract for the Android app and Glance widget. The app uses Material 3
Views; the widget keeps its own compact rendering and typography contract because published
content must render consistently on launchers.

## Design source of truth

- App color roles and day/night tokens live in `android/app/src/main/res/values*/app_colors.xml`
  and the Material theme files. Android 12+ follows dynamic system colors; earlier versions use
  the authored light and dark palettes.
- Type and spacing roles live in the app resources. The widget's band and size rules remain
  in `bands.py` and `docs/WIDGET_DESIGN.md`; its type steps (`Typo.kt`: title 16, body 14,
  label 12, caption 11) are the MD3 title-md / body-md / label-md / label-sm steps, and the
  request action is the 48dp MD3 M-size pill.
- Screens use Material 3 controls, one primary action, named surface/shape roles, and visible
  pressed/focus states. The widget reports delivery state in words and gives its action a separate
  accessible target.
- UI decisions are checked by `AppSurfaceTest` and `ContrastTest`; these parse source/resources,
  including light/dark contrast, role mappings, touch targets, and component use.

## Accessibility contract

- Text contrast is at least 4.5:1 and non-text contrast at least 3:1 in authored light and dark
  palettes.
- Interactive targets remain at least 48dp. Color never carries status by itself; state is also
  named in text or a content description.
- The publication view exposes a description and supports keyboard and pinch zoom. The widget
  action is not nested inside the surface's open-app target.
- TalkBack, large font scales, Android 15 edge-to-edge insets, and launcher rendering still need a
  device pass. The zoom control does not announce scale changes, and transient message focus has
  not been checked with a screen reader.

## Deliberate limits and remaining checks

- The app screens are phone-width forms and diagnostics, not adaptive list/detail destinations.
  Tablet/foldable layouts and Material expressive motion are not implemented.
- Credential storage remains on `androidx.security:security-crypto` 1.1.0. AndroidX deprecated
  its APIs in favor of platform APIs and direct Android Keystore use. The app keeps the current
  encrypted-preferences path to preserve already-paired credentials; a direct-Keystore change
  needs an on-device migration test before removing that dependency. [AndroidX Security release
  notes](https://developer.android.com/jetpack/androidx/releases/security).
- Release builds enable R8 and resource shrinking. A non-debuggable local review variant was
  3,297,454 bytes; the unminified debug APK was 10,006,428 bytes. These are different build
  variants, so the comparison is indicative rather than a release-size guarantee. The review
  APK passed R8 and lint vital; the device behavior pass is still required. See
  [APK release evidence](APK_RELEASE.md).
- The source-based gates do not replace the device pass in [WIDGET_DESIGN.md](WIDGET_DESIGN.md).
  The launcher, Android versions, TalkBack, font scaling, and resize cases must be observed on a
  phone before claiming device verification.
