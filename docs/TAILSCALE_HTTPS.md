# Tailscale Serve HTTPS for the Hermes widget

Keep the widget server bound to loopback and let Tailscale terminate private
HTTPS. The phone and host may be on different physical networks; they only need
to be connected to the same Tailscale tailnet.

```sh
tailscale serve --bg --https=8788 tcp://127.0.0.1:8788
```

The server already serves routes under `/v1`; do not add a second `/v1` path
prefix. The phone uses the resulting `https://<tailnet-hostname>:<port>/` base
URL and a short-lived code from `hermes widget code`.

If an existing Serve configuration is in use, add only the widget listener and
preserve unrelated paths. The proxy must forward the `Authorization: Bearer`
header unchanged. No public hostname, cloud service, or internet port is needed.

For a container, publish the port only to the private host/tailnet and keep the
Hermes home on persistent storage. Run Tailscale Serve in the host or network
namespace that can reach that private published port. After a container
recreation, run the capability-based bootstrap or restart the Hermes gateway;
the installed `gateway:startup` hook starts one widget server only when the
port is not already healthy.

Verify from a second tailnet machine:

```sh
curl https://<tailnet-hostname>:<serve-port>/v1/health
```

The health route is public, but publication, asset, pairing-code, and
acknowledgement routes still require the appropriate agent or paired-device
token. The phone uses a short-lived pairing code and a device-scoped token; do
not paste the operator token into the phone. Release builds require HTTPS, so
do not fall back to cleartext `http://` inside the tailnet.
