# Hermes Widget - Tailscale Serve HTTPS (v2 production)

Bind the widget server to loopback (127.0.0.1) and expose through Tailscale Serve HTTPS:

    tailscale serve --bg --set-path=/v1 --https=8788 --tls-terminated tcp://localhost:8788

Or with an existing certificate:

    tailscale serve --bg --set-path=/v1 --https=8788 --tls-terminated tcp://localhost:8788

This provides private tailnet access and handles certificates. Preserve existing Serve config.
The server binds loopback only; Tailscale handles the HTTPS termination.

Proxy integration: if using a reverse proxy (Caddy/nginx) in front, set the proxy to forward Authorization bearer headers unchanged. The server requires bearer auth on every protected route including localhost (gate 4 verified).

References: Tailscale docs (serve), MS WSL systemd docs.
