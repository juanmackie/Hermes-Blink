# Gate 8 — 72-hour real-device and remote-network soak

This gate verifies the personal visual channel against a real Android phone and a real Hermes
host. It cannot be completed by unit tests or by a preview build. The phone may be on mobile
data, a different Wi-Fi, or a different physical network; it must have Tailscale connectivity to
the same tailnet as the host.

## Preconditions

- Hermes is installed and its widget bootstrap completed successfully.
- The host's Hermes home, database, assets, plugin, skill, hook, and routine are persistent.
- The widget server is healthy through Tailscale Serve HTTPS.
- A debug APK is acceptable for pre-release testing. A personally signed release APK is required
  before calling the release gate complete.
- The phone is paired with a short-lived code and has a device-scoped token.
- The host operator token remains outside the phone.

## 72-hour exercise

1. Publish a text update, a safe static SVG diagram/chart, and a raster photograph.
2. Confirm the phone fetches each revision and that the host reports `published`, `downloaded`,
   and `render_submitted` as separate states.
3. Leave the widget running through Android battery restrictions and WorkManager's nominal
   15-minute polling. Confirm that a delayed poll is acceptable and does not claim delivery.
4. Move the phone between Wi-Fi and mobile data. Disconnect and reconnect Tailscale. Confirm
   the app shows the last successful publication offline and recovers without a blank sample
   state.
5. Restart the Hermes gateway and, separately, the TrueNAS container or equivalent host. Confirm
   the startup hook restores exactly one healthy server and that the device remains paired.
6. Tap the widget to open the larger view and zoom a visual. Return to the widget and manually
   refresh it.
7. Revoke a test device token and confirm the next request fails safely. Re-pair only when the
   test is complete.
8. Change the publication to an expired revision and confirm the app does not present it as a
   current publication.

## Evidence to collect

- Timestamped host health and startup-hook output showing one server after each restart.
- `hermes widget status` output showing revision, device fetch state, render acknowledgement, and
  usable widget dimensions without claiming that the user saw the update.
- Android screenshots or recordings for text, SVG/diagram, chart, and photograph rendering at
  more than one widget size.
- Android logcat output for network changes, Tailscale reconnects, offline cache recovery, and
  revoked-token handling.
- Tailscale Serve configuration and a health request from the phone's network path.
- A 72-hour timeline showing host restarts, phone refreshes, offline intervals, and recovery.

Do not use `systemctl --user` as a universal requirement. Record the actual service manager or
container runtime used by the target host. The acceptance condition is a surviving persistent
server and widget state, not a particular init system.
