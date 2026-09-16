# Security audit note (Phase 7)
- Rate limit: max 30 pushes/widget/hour (suggested in docs)
- Payload cap: >64 KB rejected
- Node cap: >100 nodes rejected
- Image allowlist: https:// only
- Action allowlist: event/url/refresh/open_app only; url=https://, deeplink=hermes://
- Schema validation enforced before every push
- Token revocation endpoint: POST /v1/devices/{id}/revoke
- Malicious layout (wrong version, bad node type, oversized) rejected server-side
