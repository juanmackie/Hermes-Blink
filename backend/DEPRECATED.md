# DEPRECATED — removed from the v2 contract and release

`backend/` holds no code. The FastAPI service that used to live here (`backend/app/main.py`,
`backend/app/db.py`) is gone; the shipped transport is the Hermes plugin in
`hermes-plugin/hermes-widget/`.

Kept as a one-line redirect rather than deleted because older links pointed here. If you are
looking for the rules that used to be documented in `backend/SECURITY.md` or
`backend/TAILSCALE.md`, they now have exactly one owner each:

| Concern | Owner |
| --- | --- |
| Layout contract (nodes, typography, colours, caps) | `docs/SCHEMA.md` + `hermes-plugin/hermes-widget/layout.schema.json` |
| Push-time guardrails, auth model, REST transport | `docs/CONNECTION.md` |
| Private/Tailscale URLs | `docs/TAILSCALE_HTTPS.md` and `docs/HERMES_AGENT_SETUP.md` step 4 |
| Operator walkthrough | `docs/HERMES_AGENT_SETUP.md` |
