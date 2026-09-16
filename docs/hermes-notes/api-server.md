API Server | Hermes Agent

Skip to main content

On this page
API Server

The API server exposes hermes-agent as an OpenAI-compatible HTTP endpoint. Any frontend that speaks the OpenAI format — Open WebUI, LobeChat, LibreChat, NextChat, ChatBox, and hundreds more — can connect to hermes-agent and use it as a backend.

Your agent handles requests with its full toolset (terminal, file operations, web search, memory, skills) and returns the final response. When streaming, tool progress indicators appear inline so frontends can show what the agent is doing.

One backend covers models + tools
Hermes itself needs a configured provider and tool backends for the API server to be useful. A Nous Portal subscription handles both — 300+ models plus web/image/TTS/browser via the Tool Gateway. Run hermes setup --portal
once before starting the API server and frontends like Open WebUI or LobeChat get a fully tool-equipped backend.

Quick Start​

1. Enable the API server​

Add to ~/.hermes/.env
:

API_SERVER_ENABLED=true

API_SERVER_KEY=change-me-local-dev

# Optional: only if a browser must call Hermes directly

# API_SERVER_CORS_ORIGINS=http://localhost:3000

2. Start the gateway​

hermes gateway

You'll see:

[API Server] API server listening on http://127.0.0.1:8642

3. Connect a frontend​

Point any OpenAI-compatible client at http://localhost:8642/v1
:

# Test with curl

curl http://localhost:8642/v1/chat/completions \

-H "Authorization: Bearer change-me-local-dev" \

-H "Content-Type: application/json" \

-d '{"model": "hermes-agent", "messages": [{"role": "user", "content": "Hello!"}]}'

Or connect Open WebUI, LobeChat, or any other frontend — see the Open WebUI integration guide for step-by-step instructions.

Endpoints​

POST /v1/chat/completions​

Standard OpenAI Chat Completions format. Stateless — the full conversation is included in each request via the messages
array.

Request:

{

"model": "hermes-agent",

"messages": [

{"role": "system", "content": "You are a Python expert."},

{"role": "user", "content": "Write a fibonacci function"}

],

"stream": false

}

Response:

{

"id": "chatcmpl-abc123",

"object": "chat.completion",

"created": 1710000000,

"model": "hermes-agent",

"choices": [{

"index": 0,

"message": {"role": "assistant", "content": "Here's a fibonacci function..."},

"finish_reason": "stop"

}],

"usage": {"prompt_tokens": 50, "completion_tokens": 200, "total_tokens": 250}

}

Inline image input: user messages may send content
as an array of text
and image_url
parts. Both remote http(s)
URLs and data:image/...
URLs are supported:

{

"model": "hermes-agent",

"messages": [

{

"role": "user",

"content": [

{"type": "text", "text": "What is in this image?"},

{"type": "image_url", "image_url": {"url": "https://example.com/cat.png", "detail": "high"}}

]

}

]

}

Uploaded files (file
/ input_file
/ file_id
) and non-image data:
URLs return 400 unsupported_content_type
.

Streaming ("stream": true
): Returns Server-Sent Events (SSE) with token-by-token response chunks. For Chat Completions, the stream uses standard chat.completion.chunk
events plus Hermes' custom hermes.tool.progress
event for tool-start UX. For Responses, the stream uses OpenAI Responses event types such as response.created
, response.output_text.delta
, response.output_item.added
, response.output_item.done
, and response.completed
.

Tool progress in streams:

Chat Completions: Hermes emits event: hermes.tool.progress
for tool-start visibility without polluting persisted assistant text.

Responses: Hermes emits spec-native function_call
and function_call_output
output items during the SSE stream, so clients can render structured tool UI in real time.

POST /v1/responses​

OpenAI Responses API format. Supports server-side conversation state via previous_response_id
— the server stores full conversation history (including tool calls and results) so multi-turn context is preserved without the client managing it.

Request:

{

"model": "hermes-agent",

"input": "What files are in my project?",

"instructions": "You are a helpful coding assistant.",

"store": true

}

Response:

{

"id": "resp_abc123",

"object": "response",

"status": "completed",

"model": "hermes-agent",

"output": [

{"type": "function_call", "status": "completed", "name": "terminal", "arguments": "{\"command\": \"ls\"}", "call_id": "call_1"},

{"type": "function_call_output", "status": "completed", "call_id": "call_1", "output": "README.md src/ tests/"},

{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Your project has..."}]}

],

"usage": {"input_tokens": 50, "output_tokens": 200, "total_tokens": 250}

}

Tool calls in the output
array were already executed server-side by the Hermes agent — they are replayed with "status": "completed"
for structured tool UI, never as pending calls for the client to execute.

Inline image input: input[].content
can contain input_text
and input_image
parts. Both remote URLs and data:image/...
URLs are supported:

{

"model": "hermes-agent",

"input": [

{

"role": "user",

"content": [

{"type": "input_text", "text": "Describe this screenshot."},

{"type": "input_image", "image_url": "data:image/png;base64,iVBORw0K..."}

]

}

]

}

Uploaded files (input_file
/ file_id
) and non-image data:
URLs return 400 unsupported_content_type
.

Multi-turn with previous_response_id​

Chain responses to maintain full context (including tool calls) across turns:

{

"input": "Now show me the README",

"previous_response_id": "resp_abc123"

}

The server reconstructs the full conversation from the stored response chain — all previous tool calls and results are preserved. Chained requests also share the same session, so multi-turn conversations appear as a single entry in the dashboard and session history.

Named conversations​

Use the conversation
parameter instead of tracking response IDs:

{"input": "Hello", "conversation": "my-project"}

{"input": "What's in src/?", "conversation": "my-project"}

{"input": "Run the tests", "conversation": "my-project"}

The server automatically chains to the latest response in that conversation. Like the /title
command for gateway sessions.

GET /v1/responses/{id}​

Retrieve a previously stored response by ID.

DELETE /v1/responses/{id}​

Delete a stored response.

GET /v1/models​

Lists the agent as an available model. The advertised model name defaults to the profile name (or hermes-agent
for the default profile). Required by most frontends for model discovery.

/v1/models
is intentionally the cheap OpenAI-compat surface. It does not
enumerate every authenticated provider/model combination Hermes can route to,
and it does not do pricing or capability enrichment.

GET /api/model/options​

Hermes-aware clients can request the same curated provider/model inventory used
by the dashboard and TUI. This route uses the API server's normal bearer
authentication and returns provider rows, model capability hints, and pricing
metadata that do not belong in the OpenAI-compatible /v1/models
response:

curl \

-H "Authorization: Bearer $API_SERVER_KEY" \

"http://127.0.0.1:8642/api/model/options"

That payload is the same substrate the dashboard Models page and the TUI
model.options
RPC use. It returns authenticated providers, curated model
lists, per-model pricing, and model capability hints.

Normal opens are intentionally conservative for custom providers: Hermes probes
only the currently selected custom endpoint so a stale or offline saved
endpoint does not block the picker. An explicit refresh flips to full probing
and busts the provider model cache:

curl \

-H "Authorization: Bearer $API_SERVER_KEY" \

"http://127.0.0.1:8642/api/model/options?refresh=1"

Use /v1/models
when an OpenAI-compatible client only needs a model name to
send back in chat/responses requests. Use /api/model/options
when an
authenticated UI needs the richer Hermes-specific picker metadata.

GET /v1/capabilities​

Returns a machine-readable description of the API server's stable surface for external UIs, orchestrators, and plugin bridges.

{

"object": "hermes.api_server.capabilities",

"platform": "hermes-agent",

"model": "hermes-agent",

"auth": {"type": "bearer", "required": true},

"features": {

"chat_completions": true,

"responses_api": true,

"run_submission": true,

"run_status": true,

"run_events_sse": true,

"run_stop": true

}

}

Use this endpoint when integrating dashboards, browser UIs, or control planes so they can discover whether the running Hermes version supports runs, streaming, cancellation, and session continuity without depending on private Python internals.

Browser-extension control​

Hermes can route browser tools through an authenticated extension that controls
the browser session associated with the current Hermes session. The feature is
disabled by default; set browser.extension_control.enabled
to true
to opt in:

browser:

extension_control:

enabled: true

The local API path also requires the API server bearer key. A controller may
register only for an existing server session. Hermes derives the controller
principal from authenticated server state; a client-supplied principal_id
is
ignored.

Discover the live contract through GET /v1/capabilities
. The
browser_extension_control
object reports whether the feature is enabled, the
protocol version, transport names, and the exact capability allowlist:

controller.noop

browser_back

browser_click

browser_navigate

browser_press

browser_screenshot

browser_scroll

browser_snapshot

browser_tab_activate

browser_tabs

browser_type

Requested capabilities outside that list are filtered out. Raw CDP, arbitrary
script evaluation, console access, uploads, image extraction, and vision are not
part of the controller protocol.

When a request has no bound controller identity, or when the feature is disabled,
Hermes preserves the existing browser backend. Once the gateway binds a
controller principal and transport family to the request, that extension lane
is authoritative: missing, ambiguous, disconnected, or incapable controllers
fail closed instead of silently switching to a different local/cloud browser.
After an exact controller is selected, its result or error is authoritative and
Hermes never retries the same action through another backend.

Local API registration​

Send an authenticated POST /v1/browser-control/register
with
protocol_version
, session_id
, controller_id
, browser_profile_id
, and
the requested capabilities
.

Hermes returns a single-use ticket with a 30-second TTL and the filtered,
server-bound controller scope.

Open GET /v1/browser-control/ws
with both WebSocket subprotocols:
hermes-browser-control-v1
and
hermes-browser-control-ticket.<ticket>
.

The ticket is never accepted in the query string. Unknown, expired, reused, or
malformed tickets fail before WebSocket upgrade.

Controller frames​

Hermes sends browser.controller.command
frames containing command_id
,
action
, immutable arguments
, browser/controller ids, and the originating
tool_call_id
. The controller replies with browser.controller.result
, the
same command_id
, an exact boolean ok
, and either result
or error
.
Cancellation and timeout emit browser.controller.cancel
; late results are
ignored.

An unexpected socket loss marks the controller offline and preserves work
already in flight until each command's original deadline. A reconnect with the
same principal, profile, session, controller id, browser profile, and transport
identity refreshes the transport without admitting new work before any deferred
cancels are flushed. Negotiated capabilities may change on that reconnect; they
are not an identity field. A different controller id or browser profile in the
same authenticated session lane is a hard replacement: old pending work is
cancelled before the successor becomes routable. Send
browser.controller.detach
on the authenticated controller transport for an
intentional hard detach — that immediately cancels pending work. Merely closing
the socket is treated as a recoverable disconnect.

The authenticated dashboard transport exposes the same registration, result,
heartbeat, capability, and ownership semantics over its Gateway RPC/event
channel. In both transports, selection requires one unambiguous exact match on
principal, profile, session, controller, browser profile, transport family, and
capability. Once selected, a controller failure is authoritative and is never
retried through a different browser backend.

Per-request model selection​

Authenticated clients can override Hermes' default model selection per request
by sending:

model
— the target model id for this turn

provider
— the Hermes provider slug to resolve credentials/runtime for this turn

model_options
— request-scoped reasoning / service-tier controls

The same request fields are accepted on:

POST /v1/chat/completions

POST /v1/responses

POST /v1/runs

POST /api/sessions/{session_id}/chat

POST /api/sessions/{session_id}/chat/stream

Precedence is deterministic:

Session /model
override, if that session already has one

A static gateway.platforms.api_server.model_routes
mapping selected when
the request's model
is a configured route alias

Direct request model
/ provider
when no route alias matches

Global gateway config / environment defaults

model_options
stays request-scoped regardless of which model/provider wins.
If a request sends a provider
that conflicts with a configured model_routes

alias, Hermes rejects the request with 400
instead of silently remixing route
credentials with another provider.

Bare model
values on the OpenAI-compatible endpoints are opt-in. Generic
OpenAI clients routinely hardcode model names (gpt-4o
, ...), and existing
deployments rely on those falling back to the gateway default. On
POST /v1/chat/completions
and POST /v1/responses
, a model
value sent
WITHOUT a provider
is therefore ignored unless you enable:

gateway:

platforms:

api_server:

direct_model_requests: true

Requests that include an explicit provider
— and the Hermes-native
/v1/runs
and session-chat endpoints — always honor the requested model
regardless of this flag.

Example:

{

"model": "MiniMax-M3",

"provider": "minimax",

"model_options": {

"reasoning_effort": "high",

"service_tier": "priority"

},

"messages": [

{"role": "user", "content": "Summarize the repo status."}

]

}

GET /health​

Health check. Returns {"status": "ok"}
. Also available at GET /v1/health for OpenAI-compatible clients that expect the /v1/
prefix.

GET /health/detailed​

Authenticated readiness check for monitoring and control planes. It reports
bounded status for the active profile's config, state database, configured
model, disk space, gateway/platform state, active API runs, pending process
completions, and active delegations. The response exposes status and counts,
not config values, credentials, paths, commands, queue payloads, or raw errors.

The public /health
route remains a cheap liveness probe and does not run
readiness checks. A degraded readiness result still uses HTTP 200; inspect the
top-level status
and readiness.checks
fields.

Runs API (streaming-friendly alternative)​

In addition to /v1/chat/completions
and /v1/responses
, the server exposes a runs API for long-form sessions where the client wants to subscribe to progress events instead of managing streaming themselves.

POST /v1/runs​

Create a new agent run. Returns a run_id
that can be used to subscribe to progress events.

{

"run_id": "run_abc123",

"status": "started"

}

Runs accept a simple input
string and optional session_id
, instructions
, conversation_history
, or previous_response_id
. When session_id
is provided, Hermes surfaces it in the run status so external UIs can correlate runs with their own conversation IDs.

For safely retryable creation, send an Idempotency-Key
header (1–255 visible ASCII characters). Hermes durably reserves the key before starting work. An identical retry returns the original run_id
with HTTP 202 and Idempotency-Replayed: true
, including after a gateway restart and after the run has completed, failed, or been cancelled. Reusing the same key with a different JSON payload returns HTTP 409 with code idempotency_key_conflict
. Keys are isolated by authenticated API profile/credential and retained for 24 hours after their last status update; clients should use unique, unguessable keys and must not reuse them for unrelated operations. Requests without the header retain the legacy behavior and always create a new run.

When session_id
identifies an existing Hermes session and no explicit
conversation_history
or previous_response_id
is supplied, the run loads
that session's active transcript. Session turn leases serialize concurrent
writers and refresh the transcript after a contended wait.

GET /v1/runs/{run_id}<a href="#get-v1runsrun_id" class="hash-link" aria-label="Direct link to GET /v1/runs/{run_id}" title="Direc