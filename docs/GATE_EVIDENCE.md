# Hermes Widget v1 - Gate evidence checklist (Phase F)

Each gate must pass before release. Evidence required per gate:

## Gate 1 - Clean setup
- Fresh supported host (Ubuntu 24.04/WSL2) with working Hermes
- One agent instruction reaches pairing
- No source edits, no user-run server commands
- Evidence: `hermes widget up --json` output showing state=awaiting_pairing

## Gate 2 - Phone onboarding
- Signed APK install → QR pairing → pin → real initial brief
- Expired-code flow fails gracefully
- Same-phone pairing flow works
- Evidence: paired device shows real brief, not sample content

## Gate 3 - Persistence
- Closing terminals, restarting services, rebooting Linux, logging back into Windows restores operation
- No duplicate jobs, services, devices, credentials
- Evidence: status after restart shows same devices/widgets, no duplicates

## Gate 4 - Security
- Tokenless requests fail through HTTPS proxy (verified: loopback requires bearer)
- Device credentials cannot perform operator actions
- Consumed/expired codes fail
- Revoked devices lose access
- Secrets stay out of logs and ordinary preferences (verified: SecureStore only)
- Evidence: 401 on tokenless loopback, device token on operator route rejected, consumed code rejected, revoked device 401

## Gate 5 - Interaction
- Refresh, dismiss, review survive duplicate requests, network interruption, process restart, slow model responses
- Evidence: duplicate event deduped, refresh completes after newer valid layout stored

## Gate 6 - Rendering
- Every retained component renders correctly across widget sizes, large text, supported Android versions
- Removed types cannot be authored as supported content
- Evidence: layout validation rejects chart/image/icon/toggle/calendar-month

## Gate 7 - Recovery
- Offline phones retain visibly stale content
- Invalid layouts cannot poison the cache
- Upgrade, migration, backup restoration, rollback preserve pairing
- Evidence: stale content shown, invalid layout rejected, pairing preserved after rollback

## Gate 8 - Real-device soak
- 72-hour run exercises scheduled briefs, Android battery restrictions, network changes, host restarts, repeated taps without silent stalls
- Evidence: 72-hour log showing no stalls, scheduled briefs generated, taps processed
