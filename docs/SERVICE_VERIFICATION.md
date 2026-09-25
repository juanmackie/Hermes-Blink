# Service verification

The supported installer is capability-based. It does not require Ubuntu, WSL, systemd, a
particular Hermes home, or a particular container runtime. This document records the checks
that matter on the actual Hermes host and the separate checks that require a real phone or
container recreation.

## Host install

From the repository checkout on the Hermes host:

```sh
bash scripts/bootstrap-linux.sh --json
hermes widget status
```

On Windows, use:

```powershell
python scripts/bootstrap.py --json
```

Run the bootstrap a second time and confirm the report remains successful and idempotent. Set
`HERMES_BIN` only when Hermes is not on `PATH`; set `HERMES_HOME` only when the detected home
is not the persistent volume you intend to use.

The bootstrap must report the detected Hermes executable, version, profile, persistent home,
plugin/toolset capability, and startup result. It copies the plugin, skill, gateway startup
hook, and six-hour routine into that home, then starts one server if the configured port is not
already healthy. It must not create duplicate devices, credentials, hooks, routines, or
servers.

## Server health

```sh
hermes widget status
curl http://127.0.0.1:8788/v1/health
```

A healthy host reports a listening server and an existing widget database. A status of
`degraded` or `Server: not listening` means the host is not ready for phone pairing; inspect
`<Hermes home>/widget/server.log`, rerun the bootstrap, or use the diagnostic command:

```sh
hermes widget serve --host 127.0.0.1 --port 8788
```

The server binds loopback by default. Do not bind it to `0.0.0.0` merely to reach a phone.
Expose it through Tailscale Serve or another private HTTPS proxy.

## Remote phone path

1. Install Tailscale on the host and Android phone and connect both to the same tailnet.
2. On the host, expose the loopback server:

   ```sh
   tailscale serve --bg --https=8788 tcp://127.0.0.1:8788
   ```

3. From another tailnet-connected machine, verify:

   ```sh
   curl https://<host-tailnet-name>:<serve-port>/v1/health
   ```

4. Create a short-lived code:

   ```sh
   hermes widget code
   ```

5. Enter the HTTPS URL and code in the Android app. The phone receives a device-scoped token;
   it never receives the operator token.

The phone may be on mobile data, a different Wi-Fi, or a different physical network. It needs
Tailscale connectivity, not LAN shared with the host. If Hermes is containerized, Tailscale
Serve must run in a host/network namespace that can reach the container's private published
port.

## Restart and persistence verification

On the real target environment:

1. Stop or restart the Hermes gateway/container.
2. Confirm the gateway startup hook restores one healthy server.
3. Recreate the container using the same persistent Hermes home.
4. Confirm the publication database, assets, device token hashes, routine, and hook survive.
5. Confirm the phone remains paired and can fetch the current publication after the restart.

Record exit status, server health, device count, and whether the server PID changed. A local
Windows gateway test does not establish TrueNAS container recreation.

## Automated checks

From the repository root:

```sh
python -m unittest discover -s hermes-plugin/hermes-widget/tests -v
python -m compileall -q hermes-plugin/hermes-widget scripts
python scripts/check-contract-parity.py
```

Use unittest discovery for this hyphenated plugin directory. Direct `pytest` collection is not
the supported command in this checkout.

Android:

```sh
cd android
# Set JAVA_HOME to your JDK 21 installation before running Gradle.
./gradlew testDebugUnitTest lintDebug assembleDebug
```

The debug APK is suitable for local testing. A release APK remains pending until it is built
with the owner's existing personal signing identity and verified with `apksigner`.
