# Connect the Hermes Android widget to your own Hermes agent

This guide walks a non-expert through running the **hermes-widget** plugin inside your own
Hermes installation and pointing the Android home-screen widget at it. Nothing talks to a
developer-hosted backend: the small HTTP server that serves the widget runs on the Hermes host
or VPS, reads the same database your agent writes, and is reached over private HTTPS.

The phone and host do **not** need to be on the same Wi-Fi or LAN. Install Tailscale on both,
join them to the same tailnet, and let the phone pull from the host through Tailscale Serve.

Read `CONNECTION.md` for the frozen interface contract. This file is only the
operator walkthrough.

---

## 0. What you need before you start

- Hermes installed and working on the computer, VPS, or TrueNAS container that runs the agent.
- The Hermes Widget app installed on your Android phone (Android 8.0 / API 26 or newer).
- Tailscale installed and connected on both the host and the phone, in the same tailnet.
- A terminal on the Hermes host. On Windows use PowerShell.

The pieces, in one picture:

    your computer / VPS / TrueNAS container
      Hermes agent -> writes publication/layout revisions
                    -> <detected Hermes home>/widget/widget.db + assets/
      hermes widget serve -> authenticated HTTP on loopback port 8788
              ^
              | Tailscale Serve HTTPS (or a private HTTPS reverse proxy)
              v
      phone app on any network -> Tailscale tailnet URL -> device token

The default widget id everywhere is `hermes-brief` (the widget code and the plugin both use it),
so you normally do not have to choose an id.

---

## 1. Install the plugin and persistent integration

From the repository checkout, use the capability-based bootstrap:

    bash scripts/bootstrap-linux.sh --json
    bash scripts/bootstrap-linux.sh --json       # safe second run

It detects the actual Hermes executable, version, profile, persistent home, and available
plugin/gateway/cron capabilities. It installs the plugin and skill, one `gateway:startup`
launcher, and the idempotent six-hour publication routine. It uses a generated systemd user
unit only when a systemd user manager exists; otherwise it starts one detached server and the
gateway hook restores it after a container or host restart. Set `HERMES_BIN` only if `hermes` is
not on `PATH`. The bootstrap does not install the Android APK; it prepares the host-side agent,
storage, server, and proactive publication integration.

Confirm the plugin loaded and create the host database/token:

    hermes widget status
    hermes widget setup

---

## 2. Create the agent token (one time)

    hermes widget setup

This creates the operator ("agent") token and the widget database. It prints where the token was
stored:

    <your Hermes home>/widget/agent_token

and, by default, the database at:

    <your Hermes home>/widget/widget.db

Keep `agent_token` secret. It is the operator credential that lets Hermes publish. The phone
uses only a short-lived pairing code and a separate per-device token; never enter the agent
token in the Android app.

You can also supply the token with the environment variable `HERMES_WIDGET_AGENT_TOKEN` if you
prefer not to keep a file.

---

## 3. Start and persist the widget server

The bootstrap starts one server only when the port is not already healthy. For
manual diagnostics:

    hermes widget serve --host 127.0.0.1 --port 8788

Omitted `--host`/`--port` keep whatever is saved in `widget/server.json`, so a
rerun never clobbers a deliberate binding; a first install defaults to
`127.0.0.1:8788`. A malformed `server.json` is reported instead of replaced.

A direct host install should stay on loopback and expose it through Tailscale
Serve or a private HTTPS reverse proxy rather than binding every interface.

A TrueNAS/container install is the exception: a runtime-published port cannot
reach a loopback-only listener, so bind `0.0.0.0` *inside the container* and let
the host publish that port to loopback only (see step 4). A systemd user unit is
generated only when `systemctl --user` is available; Docker and TrueNAS SCALE
installations use the generated `gateway:startup` hook instead. Both paths
preserve the same `<Hermes home>/widget/` data and do not duplicate the process.

---

## 4. Make sure the phone can reach the server

**Tailscale Serve HTTPS (recommended):**

    tailscale serve --bg --https=8788 tcp://127.0.0.1:8788

This terminates private HTTPS on the tailnet and forwards unchanged bearer
headers to the loopback server. Preserve any existing Serve paths. Do not add a
second `/v1` path prefix: the server routes already include `/v1`.

**If Hermes runs in a TrueNAS container:** bind the widget server to `0.0.0.0`
*inside the container* and publish that port to the host's loopback only; persist
the Hermes home on the container dataset. Do not bind the host to `0.0.0.0`.
Tailscale Serve must run in a namespace that can reach that published port.

    bash scripts/bootstrap-linux.sh --host 0.0.0.0 --port 8788 --json

Paired Compose settings keep the host side on loopback:

    services:
      hermes:
        ports:
          - "127.0.0.1:8788:8788"   # host loopback -> container 0.0.0.0:8788

Verify the host-side binding, because the container's `--host` is not the host's
binding:

    docker port <container> | grep 8788   # must show 127.0.0.1:8788
    ss -ltnp | grep 8788                  # no 0.0.0.0 or :: listener on the host

Do not publish the raw widget port publicly.

The Android release app requires HTTPS. Do not use the old `http://`/LAN workaround: cleartext
traffic is disabled in release builds. Tailscale provides the private network path; Tailscale
Serve provides the valid HTTPS endpoint.

Quick check from another machine on the tailnet:

    curl https://<your-tailscale-hostname>:<serve-port>/v1/health

You should get `{"status":"ok",...}`. Health needs no token. If this hangs, it is a firewall or
wrong-IP problem, not the app.

---

## 5. Pair the phone without the operator token

1. On the Hermes host, create a short-lived code:

       hermes widget code

2. Open the **Hermes Widget** app and tap **Pair or update**.
3. Enter the private HTTPS widget-server URL.
4. Enter the short-lived code and tap **Pair phone**.

The app exchanges the code for a per-device token, stores that token in
encrypted Android storage, and never asks for `agent_token`. Pairing codes
expire and can be used only once. Re-pair after revoking or replacing a phone.

The app sends a default device label (manufacturer, model, Android release) at
pair time and shows the code's expiry countdown. You can rename the device in
the app's Settings screen (a device may rename only itself). `hermes widget
pair` also prints a copy-paste one-liner (`URL  code`) for messaging surfaces;
a QR code can encode the same `hermeswidget://pair?url=...&code=...` deep link,
which the app opens directly. The app has no in-app scanner.

You can see paired phones, their labels and last-seen times with:

    hermes widget devices

If a phone is lost or you want to cut it off:

    hermes widget devices --revoke <deviceId>

A revoked device gets `401 unauthorized` on its next request; re-pair it to restore access.

---

## 6. Pin the widget to the home screen

1. Long-press an empty spot on the home screen.
2. Tap **Widgets**.
3. Find **Hermes** and drag the **Hermes Widget** onto the home screen.
4. When the configuration screen appears it reads the widget list from your server. The default
   id `hermes-brief` is used automatically; finish the screen to place the widget.

If the widget shows **Pair this phone**, the phone has no device credential yet—repeat
step 5. The app never substitutes a sample publication for a missing or failed
server response. A previously successful publication remains available
offline with an explicit offline state; an expired publication is not shown.

---

## 7. Keep it fresh in the background

The phone polls on a 15-minute WorkManager task (nominal), on app open, and after
a manual refresh. WorkManager batches and defers background work, so the observed
gap between revisions is often longer than 15 minutes; `hermes widget status`
(and `widget_status`) report `lastPollAt`, `lastFetchedRevision`, and any
`skippedRevisions` so "not polled yet" is distinguishable from "superseded
before it was fetched". A publication created with `maxAgeSeconds` is dropped
(`410 publication_stale`) once it is too old to be useful rather than rendered
late. The bootstrap installs one idempotent Hermes routine every six hours:

    hermes widget routine --schedule "every 6h" --widget-id hermes-brief

The routine uses `widget_publish` only when context contains a genuinely useful
supported update. The widget skill is attached to the job, so the unattended run does not spend
a second turn loading it. If nothing useful changed it does nothing, leaving the
current publication untouched. `widget_status` distinguishes host storage,
device download, and render submission; none claims user visibility.

`widget_setup` is the agent-facing, idempotent setup path: it installs the skill, startup hook,
server configuration, and this proactive routine. On current Hermes hosts the plugin adds one
bounded proactive-guidance section after memory, rendered once per session rather than injecting
a reminder into every turn.

Standing watches are evaluated by the routine before an ordinary publish. The agent creates
them with `widget_watch_create`, supplies bounded source snapshots to `widget_watch_tick`, and
uses `widget_watch_pause`/`widget_watch_list` for control. A low-stakes `ticker` publication
retains the hero; `widget_ask` creates a bounded user question answered into the widget without
executing work. `widget_status` reports aggregate-only attention numbers and bounded history.

For a genuinely time-sensitive update, publish with `priority: "high"`. To validate the wake
lane without inventing content or a publication revision, run:

    hermes widget wake-test --json

It sends one content-free `fetch` wake and reports `nudge_sent`/`failed` per registered device.
The Android app uses a
user-selected UnifiedPush distributor (a self-hosted ntfy instance is suitable); the host sends
only a `fetch` wake and the phone pulls the content over the private HTTPS path. The app's
**Delivery diagnostics** screen shows the last poll/fetch/render timestamps and battery
optimisation exemption. If UnifiedPush or WorkManager is deferred, periodic polling remains
the fallback; no client technique can guarantee delivery through a deep Doze/network outage.

Before publishing a visual, preview the exact proposal at the sizes the phone reported:

    hermes widget preview --publication-file proposal.json --sizes 2x2,4x2,4x4 --out ./widget-previews

For a host without libcairo, Pillow still renders raster previews and the built-in Pillow path
renders text; only SVG rasterisation needs CairoSVG.

Capacity findings are warnings, not silent truncation. If a publication carries a stable
`itemId` and `approve`/`snooze`/`open` actions, a tap is recorded as a durable intent. The
agent consumes it with `widget_read_intents` and records the result with
`widget_resolve_intent`; the widget server never executes agent work.

Remove it later with:

    hermes widget routine --remove

Check the overall picture any time with:

    hermes widget status

### Author a layout yourself, without a phone

The agent authors layouts from the bundled **hermes-widget** skill, which carries the type scale,
the colour rule, the spacing rhythm, and the widget sizes, plus four reference layouts in
`fixtures/golden/`. Two commands make that loop safe and visible:

    hermes widget preview fixtures/golden/large-brief.json
    # Relative fixture paths resolve from the checkout root or the installed plugin copy.
    # -> fixtures/golden/large-brief.preview.html — every widget shape, with the widget
    #    bounds outlined so you can see where Android would clip it

The agent tool `widget_validate` runs the same checks without drawing anything. It reports the
node count, byte size, text styles used, and any design warnings — and **stores nothing**, so
iterating never replaces a working widget or spends a push from the 30-per-hour limit. Ask the
agent to validate before it pushes.

The preview is an approximation of Android, not a simulator — the phone stays authoritative. The
full rules are in `SCHEMA.md`; the design guidance the agent follows is
`hermes-plugin/hermes-widget/skills/widget/SKILL.md`.

---

## 8. Troubleshooting

**The app says pairing expired or HTTP 401.**
The device token was revoked or the persistent Hermes home changed. Run
`hermes widget code`, pair again, and revoke the old device. The phone never
needs the operator token; a host-only 401 on publishing is diagnosed with
`hermes widget doctor`, not by sending `agent_token` to the phone.

**No publication yet / HTTP 404.**
The server is reachable but has no publication under `hermes-brief`. Ask the
agent to `widget_publish`, or run the routine when useful context exists.
`hermes widget status` distinguishes an empty server from a device download or
render gap.

**Cannot connect at all / health check hangs.**
- Wrong IP: re-check `tailscale ip -4` or your LAN IP; Tailscale addresses are `100.x.y.z`.
- Firewall: no inbound rule is needed for a direct loopback install. For a
  container, publish only to the host loopback as above; never open the widget
  port to the internet.
- Server not running or the gateway hook did not restore it: run
  `bash scripts/bootstrap-linux.sh --json`, then inspect
  `<Hermes home>/widget/server.log`; keep loopback and proxy with Tailscale Serve.
- Phone on a different network than the server and not on the tailnet.
- If you used `https://`, the certificate must be valid for the hostname you typed; a self-signed
  certificate will fail. Use Tailscale Serve's HTTPS certificate or another proxy with a
  publicly trusted certificate; release builds do not allow cleartext HTTP.

**It worked, then stopped updating.**
The `hermes widget serve` process probably exited (closed terminal, sleep, reboot). Run it as a
service, or re-run it; the database and tokens persist in `<Hermes home>/widget/`.

**Reset everything.**
Stop the server, delete `<Hermes home>/widget/widget.db`, then repeat `hermes widget setup` and
`hermes widget serve`. All devices must re-pair afterwards.

---

## 9. Where things live and what is secret

| Item | Location | Secret? |
| --- | --- | --- |
| Agent token | `<Hermes home>/widget/agent_token` | Yes |
| Widget database (publications/layouts, device hashes, events) | `<Hermes home>/widget/widget.db` | Contains token hashes only |
| Immutable publication assets | `<Hermes home>/widget/assets/` | App-private content; treat as private |
| Device tokens | Phone encrypted storage; only sha256 on the server | Yes |
| Publication/layout JSON | `widget.db` | Treat as private content |
| Gateway startup hook | `<Hermes home>/hooks/hermes-widget-startup/` | No secret; persist for restart recovery |

Never share the agent token or paste it into a chat/bug report. To rotate it, stop the server,
delete `agent_token`, run `hermes widget setup` again, and update the app's token (re-pair the
phones).
