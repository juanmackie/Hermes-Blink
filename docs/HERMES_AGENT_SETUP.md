# Connect the Hermes Android widget to your own Hermes agent

This guide walks a non-expert through running the **hermes-widget** plugin inside your own
Hermes installation and pointing the Android home-screen widget at it. Nothing talks to a
developer-hosted backend: the small HTTP server that serves the widget runs on **your** machine
(or VPS), reads the same database your agent writes, and is reached over Tailscale or your LAN.

Read `CONNECTION.md` for the frozen interface contract. This file is only the
operator walkthrough.

---

## 0. What you need before you start

- Hermes installed and working on your computer (the same one you run the agent on).
- The Hermes Widget app installed on your Android phone (Android 8.0 / API 26 or newer).
- Both devices on the same Tailscale network, or the same Wi-Fi LAN.
- A terminal on the computer. On Windows use PowerShell.

The pieces, in one picture:

    your computer / VPS
      Hermes agent  -> writes layouts -> ~/.hermes/widget/widget.db
      hermes widget serve  -> reads widget.db, answers HTTP on port 8788
              ^
              | Tailscale or LAN (HTTP is fine inside Tailscale; use HTTPS otherwise)
              v
      your phone: Hermes Widget app -> enters URL + agent token -> shows the widget

The default widget id everywhere is `hermes-brief` (the widget code and the plugin both use it),
so you normally do not have to choose an id.

---

## 1. Install the plugin

The plugin is the folder `hermes-plugin/hermes-widget` in this repository.

**Windows (PowerShell):**

    Copy-Item -Recurse hermes-plugin\hermes-widget "$env:USERPROFILE\.hermes\plugins\hermes-widget"
    hermes plugins enable hermes-widget

**macOS / Linux:**

    cp -r hermes-plugin/hermes-widget ~/.hermes/plugins/hermes-widget
    hermes plugins enable hermes-widget

Once the plugin is published to a git URL you will instead run:

    hermes plugins install <git-url-of-hermes-widget>
    hermes plugins enable hermes-widget

If you cloned this repository on Linux/macOS, `scripts/install-hermes-widget.ps1` (Windows) does
the copy for you.

Confirm the plugin loaded:

    hermes widget status

---

## 2. Create the agent token (one time)

    hermes widget setup

This creates the operator ("agent") token and the widget database. It prints where the token was
stored:

    <your Hermes home>/widget/agent_token

and, by default, the database at:

    <your Hermes home>/widget/widget.db

Keep `agent_token` secret. It is the credential that lets the agent push layouts. The phone gets
a different, per-device token during pairing, so you never type this one into a widget by hand
except in the app's **Settings** screen below.

You can also supply the token with the environment variable `HERMES_WIDGET_AGENT_TOKEN` if you
prefer not to keep a file.

---

## 3. Start the widget server

    hermes widget serve --host 0.0.0.0 --port 8788

- `--host 0.0.0.0` listens on every network interface so your phone can reach it. If you only
  ever use Tailscale, you can bind the Tailscale address instead (safer).
- `--port 8788` is the default; keep it unless it is taken.

Leave this process running. If you close the terminal, the widget stops updating. Use your OS
service manager, a scheduled task, or `tmux`/`screen` if you want it always on.

### TLS (optional)

Traffic inside Tailscale is already encrypted, so plain HTTP is acceptable there. If you expose
the port to a LAN or the internet, terminate TLS:

    hermes widget serve --host 0.0.0.0 --port 8788 --certfile cert.pem --keyfile key.pem

You can also put a reverse proxy (Caddy, nginx) in front and use `https://`.

---

## 4. Make sure the phone can reach the server

**Tailscale (recommended):**

    tailscale ip -4

This prints an address like `100.x.y.z`. Use that in the app URL. If you bound `--host 0.0.0.0`,
Tailscale reachability is usually automatic. Otherwise bind the `100.x.y.z` address explicitly.

**Same Wi-Fi / LAN:**

    # Windows
    ipconfig

    # macOS / Linux
    ipconfig getifaddr en0     # macOS
    hostname -I                # Linux

Use the computer's LAN IP, e.g. `http://192.168.1.20:8788`.

**Firewall:** allow inbound TCP on port 8788 for the tailnet interface or your LAN. On Windows,
the first run of `hermes widget serve` normally raises a firewall prompt — click **Allow**.

Quick check from the phone's network (or any second machine):

    curl http://100.x.y.z:8788/v1/health

You should get `{"status":"ok",...}`. Health needs no token. If this hangs, it is a firewall or
wrong-IP problem, not the app.

---

## 5. Enter the URL and agent token on the phone

1. Open the **Hermes Widget** app and tap **Connect**.
2. **Hermes agent URL** — enter the full base URL including scheme and port, e.g.
   `http://100.x.y.z:8788`. It must start with `http://` or `https://`.
3. **Agent token** — paste the contents of the `agent_token` file from step 2.
4. Tap **Connect**.

The app calls `/v1/health`, then automatically mints a short-lived pairing code and pairs itself
with the server. On success it stores a **device token** (encrypted on the phone) and the base
URL. You do not need to type a pairing code during this automatic flow.

### Manual pairing (alternative)

If you would rather pair from the server side, or you are given a code:

    hermes widget code

This prints a code such as `ABCD-1234`. Open the widget's pairing screen and enter it. Pairing
returns a per-device token; only its hash is stored on the server.

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

If the widget shows *"Connect to your Hermes agent"*, the phone has no working URL/token yet —
redo step 5. The app falls back to a bundled sample layout only when nothing is cached.

---

## 7. Keep it fresh in the background

The widget refreshes when the app/worker polls and whenever the agent pushes a new layout. To have
the **agent** rebuild the widget on a schedule (news, calendar, tasks — whatever the skill authors),
install a Hermes cron job:

    hermes widget routine --schedule "every 6h" --widget-id hermes-brief

That runs the agent with the bundled hermes-widget skill and pushes an updated layout each time.
Common schedules: `"every 1h"`, `"every 6h"`, or a cron expression like `"0 7 * * *"` for 07:00 daily.

Remove it later with:

    hermes widget routine --remove

Check the overall picture any time with:

    hermes widget status

---

## 8. Troubleshooting

**The app says Failed (HTTP 401) or the widget shows nothing after working once.**
The device token is wrong, expired, or revoked. Check `hermes widget devices`; if the device is
missing or revoked, re-run the Connect flow in the app (step 5) or `hermes widget code` + manual
pairing. A 401 on the *agent* token means the token you pasted does not match
`agent_token` on the server — re-copy it (no quotes, no trailing spaces).

**Failed (HTTP 404) / "unknown widget".**
The server is reachable but has no layout stored under that id. Push one from the agent (ask it to
update the widget, or run `hermes widget routine`), and confirm the id is `hermes-brief`.
`hermes widget status` lists the widget ids the server knows.

**Cannot connect at all / health check hangs.**
- Wrong IP: re-check `tailscale ip -4` or your LAN IP; Tailscale addresses are `100.x.y.z`.
- Firewall: allow TCP 8788 inbound (Windows Defender Firewall → Inbound Rules).
- Server not running or bound to the wrong interface: restart with `--host 0.0.0.0`.
- Phone on a different network than the server and not on the tailnet.
- If you used `https://`, the certificate must be valid for the hostname you typed; a self-signed
  cert will fail. Use HTTP inside Tailscale, or a proxy with a real certificate.

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
| Widget database (layouts, devices, events) | `<Hermes home>/widget/widget.db` | Contains token hashes only |
| Device tokens | Phone secure storage; only sha256 on the server | Yes |
| Layout JSON pushed by the agent | `widget.db` | No |

Never share the agent token or paste it into a chat/bug report. To rotate it, stop the server,
delete `agent_token`, run `hermes widget setup` again, and update the app's token (re-pair the
phones).
