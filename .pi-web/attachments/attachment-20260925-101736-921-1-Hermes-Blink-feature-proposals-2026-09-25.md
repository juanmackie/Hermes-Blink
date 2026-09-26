# Hermes-Blink — three high-value feature proposals

Date: 2026-09-25 · Posture: written from a live deployment (TrueNAS container host, Tailscale-private phone path, real publications in all three kinds: text, PNG, SVG). Version under discussion: `e1451a6`.

Every claim below is anchored to something measured today on live hardware. Where a number is a projection rather than a measurement it is labelled **est**.

The five original bugs and the full field-feedback list from this deployment are now fixed — delivery truth, render surface, SVG allowlist, event vocabulary, pairing line, device labels, green CI. These three proposals are what I would build *next*, in this order of reasoning: Feature 3 is the highest leverage, Feature 2 is the cheapest and makes everything else look right, Feature 1 removes the ceiling on the whole product.

---

## Feature 1 — Wake-on-publish: priority revisions with a real push path

### The problem, measured
- Advertised poll: `pollIntervalSeconds: 900` (15 min). **Observed gaps: ~4 min and ~17 min** on an awake, in-use phone (fetch records: 07:21:50 → 07:26:02 → 07:43:00).
- Revision 4 (a PNG the user explicitly asked for) was published 07:30:34 and superseded 3 minutes later. The phone **never fetched it**. Before this week's fix there was no record of that skip; now there is a trace, but the content is still gone.
- Consequence: any time-anchored publication — a pre-open market brief, a "job finished" receipt, a deal alert — can arrive 15 minutes late or not at all. That makes the *only* honest use of the widget asynchronous, low-frequency content.

### The proposal
A priority lane plus a push wake, with the receipt chain closed:

1. **`priority: "normal" | "high"`** on publication. `high` means "the phone should be woken"; `normal` keeps today's passive polling. Rate-limit `high` (suggest: 6/hour, 30/day; over-limit degrades to normal and is recorded).
2. **Push transport, privacy-preserving by default.** Use **UnifiedPush** with a self-hostable distributor (an `ntfy` instance fits the existing stack) — no Google dependency, no content in the payload. The push body says only *"fetch"*; the phone then pulls over the existing Tailscale HTTPS path. FCM may be offered as an alternative backend for users who prefer it, never as the only option.
3. **App-side reliability work** (this is half the feature and the part that is easy to skip):
   - request the battery-optimisation exemption with a clear in-app explanation (Doze is the likely cause of the 17-minute gap),
   - expedited WorkManager on push receipt, with an `AlarmManager.setExactAndAllowWhileIdle` fallback when the app is not running,
   - a diagnostics screen showing last poll, last fetch, last render, and whether the exemption is held.
4. **Close the receipt loop.** Extend delivery records past `render_submitted`: `nudge_sent → fetched → rendered`, each with a timestamp, surfaced in `hermes widget status` and `widget_status`. The publisher should be able to see "woken at T, fetched at T+38s".

### Why it is high value
It removes the product's latency ceiling. Today the widget cannot legitimately carry anything with a shelf life under ~30 minutes; with this it can carry market-open content, completion receipts, and alerts. It also makes the existing truthfulness machinery pay off — the nudge/receipt chain is only credible because the download vs render distinction already exists.

### Honest limits and guardrails
- Deep Doze with no network cannot be defeated by any client-side technique. **Time-critical alerts must still go to the messaging surface**; the widget is best-effort by design, and the proposal should say so in the API docs rather than implying guaranteed delivery.
- Push payload must never contain publication content (lock-screen leakage, and it would put content through the distributor).
- Rate limits and a per-widget "quiet hours" setting, or this becomes a notification firehose with a home-screen face.

### Success criteria
- p95 publish → fetched latency under 60 seconds for `priority: high` with the app installed and the phone online. (**est** — depends on distributor; that is the point of measuring it.)
- Zero silent skips: every superseded revision carries `superseded_reason`, and a `high` revision that is skipped is recorded as an anomaly rather than a normal event.

---

## Feature 2 — Pre-publish preview and multi-size fidelity

### The problem, measured
- Until this week the render surface was unknowable before publication; I learned it was **966×387** only from a post-hoc render acknowledgement, sized an image at 1280×512 to match, and today the same device reports **1221×1236** because the widget was resized. One publication must survive both.
- The new `render` block (fit `contain`, letterboxed, never cropped, `lastRendered`, recommended aspect) closes the *retrospective* half. What is still missing is the *prospective* half: a publisher cannot see what their revision will look like at the sizes actually on the launcher, and cannot express "degrade gracefully when it is 2×2".
- Cost today: revisions spent discovering limits, and content designed for one size landing in another.

### The proposal
1. **Instance inventory.** The app reports every widget instance it hosts with its current size class (2×2, 4×2, 2×4, 4×4) — not just the last render ack. Server exposes it per device.
2. **Preview endpoint.** `POST /v1/widgets/{id}/preview` (plus `hermes widget preview --sizes 2x2,4x2,4x4 --out DIR`) rasterises the *exact* publication at each registered size and returns local PNGs. This is pure server-side — the plugin already has the SVG→raster toolchain in use today.
3. **Capacity validation at publish time.** The v2 validator already knows node types and lengths; extend it to warn when a publication cannot fit the smallest registered instance (e.g. "hero row + 5 list items will not render above two lines at 2×2"), rather than accepting it and letting the device truncate.
4. **Optional per-size variants** with an explicit fallback chain (large → standard → compact), so one publication degrades deliberately instead of accidentally.

### Why it is high value
It is the cheapest of the three (no new infrastructure, no new app permissions) and it raises the quality of every other feature: once a publisher can see the render, they stop guessing, stop wasting revisions, and start designing for the surface that exists. It also directly protects the user experience — the failure mode it prevents is "my widget looks broken", which is the fastest way to lose trust in the product.

### Success criteria
- Every publication can be previewed at every instance size registered by the device before it is published.
- Publishing content that cannot fit the smallest instance produces a warning, not a silent truncation.
- Reduction in superseded revisions per accepted publication (measurable from the new `publication_revisions` table).

---

## Feature 3 — The action round-trip: turn the widget into a decision surface

### The problem, measured
- The single most valuable publication this deployment has produced was not a chart: it was the routine's own brief, *"3 waiting on you"* — three prepared changes that had been pending **24, 27 and 29 days** because they were buried in chat scrollback. That is a real, repeated failure mode: work complete, approval unread, nothing moves.
- The event surface exists and is now documented (`refresh`, `dismiss`, `review`, `event`), but the events table is empty and there is no affordance in the app. A tap currently cannot change anything server-side, so the widget is structurally read-only.

### The proposal
An item-scoped action that round-trips to the agent, executed under an explicit policy:

1. **Item identity.** Publications (and v2 `list_item`/`button` nodes) carry stable `itemId`s.
2. **Tap → intent, not execution.** The app posts the tap as an event; the server records it and enqueues an **intent** (`approve:<itemId>`, `snooze:<itemId>`, `open:<itemId>`). The agent consumes intents on its next cycle (or via the existing cron/hook surface) and republishes with the outcome — "applied", "held", "needs you at a desktop".
3. **Policy layer, because this taps a lock screen.**
   - Execution is **allowlisted by action class**: only pre-staged, reversible, already-validated operations may run unattended (apply a staged patch, flip a flag, dismiss a reminder, re-run a check).
   - Anything destructive, external, or irreversible **cannot** be executed from a tap. It may only be *queued* and must be confirmed in an authenticated session — the widget is a queue, never an authoriser. (This mirrors the trust boundary the agent already operates under: read and reversible actions are free; destructive ones need explicit human approval in conversation.)
   - Rate-limit and audit: every tap is logged with device, revision, `itemId`, and outcome; a tap from a revoked device is rejected.
4. **Optional confirm-on-device** for the sensitive end of the allowlist: tap opens the app (or requires biometric unlock) before the intent is queued.

### Why it is high value
It attacks the actual bottleneck rather than the symptom. Everything else in this product moves information *to* the user; this is the only feature that lets the user move a decision back with one thumb, from the surface they already glance at. It also makes the widget categorically different from every read-only dashboard app — and it converts the agent's long-running work into something the user can close out without opening a chat.

### Honest limits and guardrails
- A home-screen widget is a **shared surface**: anyone holding the phone can tap. Hence queue-not-authorise, class allowlists, and confirmation for anything that matters.
- Requires the agent to be running to consume intents; a tap must therefore be durable and idempotent (enqueue once, survive restarts, expire if unactioned for N days).
- Do not expose this as "remote control". Scoped, audited, reversible — or it becomes a security liability that undoes the product's privacy story.

### Success criteria
- A tapped item reaches a terminal state (applied / declined / expired) and is reported back on the widget within one poll cycle.
- Zero unattended executions outside the allowlisted classes — verifiable from the audit log.
- Measurable: median age of "waiting on you" items falls from **29 days** to under 48 hours.

---

## What I would not build next, and why

- **HTML/CSS/JS rendering on the device.** The SVG subset plus raster is sufficient for charts, tables, diagrams and maps; a web runtime adds a large attack surface and a versioning problem for a surface whose value is glanceability.
- **More content kinds before delivery is reliable.** Adding media types to a channel with a 15-minute best-effort latency multiplies staleness rather than value.
- **Cloud relay infrastructure.** The private-Tailscale path is a feature, not a limitation. The push work in Feature 1 should be solved with a self-hostable distributor, not by routing content through a vendor.
- **Widget-side analytics beyond the receipts above.** `render_submitted` is already the honest limit of what the platform can tell you; anything further is inference dressed as measurement.

## Suggested sequencing

1. **Feature 2 first** — cheapest, no new infrastructure, and it makes every subsequent publication better.
2. **Feature 3 second** — no new infrastructure either, but it needs care: the policy layer is the whole feature, and the queue-not-authorise rule should be designed in from the first commit.
3. **Feature 1 third** — highest ceiling, highest cost: it needs app-side work, a distributor decision, and a rate-limit policy. Worth doing, but not before the other two have made publications worth waking a phone for.
