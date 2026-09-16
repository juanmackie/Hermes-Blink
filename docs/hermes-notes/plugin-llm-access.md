Plugin LLM Access | Hermes Agent

Skip to main content

On this page
Plugin LLM Access

ctx.llm
is the supported way for a plugin to make an LLM call.
Chat completion, structured extraction, sync, async, with or without
images — same surface, same trust gate, same host-owned credentials.

Plugins reach for this when they need to do something that involves
the model but isn't part of the agent's conversation. A hook that
rewrites a tool error into something a non-engineer can read. A
gateway adapter that translates an inbound message before queuing
it. A slash command that summarises a long paste. A scheduled job
that scores yesterday's activity and writes one line to a status
board. A pre-filter that decides whether a message is worth waking
the agent up for at all.

These are jobs the agent shouldn't be in the loop on. They want one
LLM call, a typed answer, and to be done.

The smallest possible call​

result = ctx.llm.complete(messages=[{"role": "user", "content": "ping"}])

return result.text

That's the whole API in one line. No keys, no provider config, no
SDK initialisation. The plugin runs against whatever provider and
model the user is currently using — when they switch providers, the
plugin follows them automatically.

A more complete chat example​

result = ctx.llm.complete(

messages=[

{"role": "system", "content": "Rewrite errors as one short sentence a non-engineer can act on."},

{"role": "user", "content": traceback_text},

],

max_tokens=64,

purpose="hooks.error-rewrite",

)

return result.text

purpose
is a free-form audit string — it shows up in agent.log

and in result.audit
so operators can see which plugin made which
call. Optional but recommended for anything that fires often.

Structured output​

When the plugin needs a typed answer, switch to the structured lane:

result = ctx.llm.complete_structured(

instructions="Score this support reply for urgency (0–1) and pick a category.",

input=[{"type": "text", "text": message_body}],

json_schema=TRIAGE_SCHEMA,

purpose="support.triage",

temperature=0.0,

max_tokens=128,

)

if result.parsed["urgency"] > 0.8:

await dispatch_to_oncall(result.parsed["category"], message_body)

The host requests JSON output from the provider, parses it locally
as a fallback, validates against your schema if jsonschema
is
installed, and hands back a Python object on result.parsed
. If the
model couldn't produce valid JSON, result.parsed
is None
and
result.text
carries the raw response.

What this lane gives you​

One call, four shapes. complete()
for chat,
complete_structured()
for typed JSON, acomplete()
and
acomplete_structured()
for asyncio. Same arguments, same result
objects.

Host-owned credentials. OAuth tokens, refresh flows, the
credential pool, per-task aux overrides — every credential
concept Hermes already has applies. The plugin never sees a
token; the host attributes the call back through result.audit
.

Bounded. Single sync or async call. No streaming, no tool
loops, no conversation state to manage. State the input, get the
result, return.

Fail-closed trust. A plugin you've never configured cannot
pick its own provider, model, agent, or stored credential. The
default posture is "use what the user is using." Operators opt in
to specific overrides, per plugin, in config.yaml
.

Quick start​

Two complete plugins below — one chat, one structured. Both ship
inside a single register(ctx)
function and need zero outside
configuration to run against whatever model the user has active.

Chat completion — /tldr
​

def register(ctx):

ctx.register_command(

name="tldr",

handler=lambda raw: _tldr(ctx, raw),

description="Summarise the supplied text in one paragraph.",

args_hint="<text>",

)

def _tldr(ctx, raw_args: str) -> str:

text = raw_args.strip()

if not text:

return "Usage: /tldr <text to summarise>"

result = ctx.llm.complete(

messages=[

{"role": "system",

"content": "Summarise the user's text in one tight paragraph. No preamble."},

{"role": "user", "content": text},

],

max_tokens=256,

temperature=0.3,

purpose="tldr",

)

return result.text

result.text
is the model's response; result.usage
carries token
counts; result.provider
and result.model
carry attribution.

Structured extraction — /paste-to-tasks
​

def register(ctx):

ctx.register_command(

name="paste-to-tasks",

handler=lambda raw: _paste_to_tasks(ctx, raw),

description="Turn freeform meeting notes into structured tasks.",

args_hint="<text>",

)

_TASKS_SCHEMA = {

"type": "object",

"properties": {

"tasks": {

"type": "array",

"items": {

"type": "object",

"properties": {

"owner": {"type": "string"},

"action": {"type": "string"},

"due": {"type": "string", "description": "ISO date or empty"},

},

"required": ["action"],

},

},

},

"required": ["tasks"],

}

def _paste_to_tasks(ctx, raw_args: str) -> str:

if not raw_args.strip():

return "Usage: /paste-to-tasks <meeting notes>"

result = ctx.llm.complete_structured(

instructions=(

"Extract concrete action items from these meeting notes. "

"One task per actionable line. If no owner is named, leave 'owner' blank."

),

input=[{"type": "text", "text": raw_args}],

json_schema=_TASKS_SCHEMA,

schema_name="meeting.tasks",

purpose="paste-to-tasks",

temperature=0.0,

max_tokens=512,

)

if result.parsed is None:

return f"Couldn't parse a response. Raw output:\n{result.text}"

lines = [f"- [{t.get('owner') or '?'}] {t['action']}" for t in result.parsed["tasks"]]

return "\n".join(lines) or "(no tasks found)"

A third worked example, this time with image input, lives in the
hermes-example-plugins

repo (companion repo for reference plugins — not bundled with
hermes-agent itself). For the async surface (acomplete()
/
acomplete_structured()
with asyncio.gather()
), see
plugin-llm-async-example

in the same repo.

When to use which​

You want…Reach for
A free-form text response (translation, summary, rewrite, generation)complete()

A multi-turn prompt (system + few-shot examples + user)complete()

A typed dict back, validated against a schemacomplete_structured()

Image-or-text input with a typed dict backcomplete_structured()

The same call from async code (gateway adapters, async hooks)acomplete()
/ acomplete_structured()

Everything else — provider selection, model resolution, auth, fallback,
timeout, vision routing — is the same across all four.

API surface​

ctx.llm
is an instance of agent.plugin_llm.PluginLlm
.

complete()
​

result = ctx.llm.complete(

messages=[{"role": "user", "content": "Hi"}],

provider=None, # optional, gated — Hermes provider id (e.g. "openrouter")

model=None, # optional, gated — whatever string that provider expects

temperature=None,

max_tokens=None,

timeout=None, # seconds

agent_id=None, # optional, gated

profile=None, # optional, gated — explicit auth-profile name

purpose="optional-audit-string",

task=None, # optional — a plugin-registered auxiliary slot

)

# → PluginLlmCompleteResult(text, provider, model, agent_id, usage, audit)

Plain chat completion. messages
is the standard OpenAI shape — a
list of {"role": "...", "content": "..."}
dicts. Multi-turn
prompts (system + few-shot user/assistant pairs + final user) work
exactly as they would with the OpenAI SDK.

provider=
and model=
are independent and follow the same shape
as the host's main config (model.provider
+ model.model
). Set
just model=
to use the user's active provider with a different
model on it. Set both to switch providers entirely. Either argument
without operator opt-in raises PluginLlmTrustError
.

complete_structured()
​

result = ctx.llm.complete_structured(

instructions="What you want extracted.",

input=[

{"type": "text", "text": "..."},

{"type": "image", "data": b"...", "mime_type": "image/png"},

{"type": "image", "url": "https://..."},

],

json_schema={...}, # optional — triggers parsed result + validation

json_mode=False, # set True without a schema to ask for JSON anyway

schema_name=None, # optional human-readable schema name

system_prompt=None,

provider=None, # optional, gated

model=None, # optional, gated

temperature=None,

max_tokens=None,

timeout=None,

agent_id=None,

profile=None,

purpose=None,

task=None, # optional — a plugin-registered auxiliary slot

)

# → PluginLlmStructuredResult(text, provider, model, agent_id,

# usage, parsed, content_type, audit)

Inputs are typed text or image blocks (raw bytes get base64 encoded
as a data:
URL automatically). When json_schema
or
json_mode=True
is supplied, the host requests JSON output via
response_format
, parses it locally as a fallback, and validates
against your schema if jsonschema
is installed.

result.content_type == "json"
— result.parsed
is a Python
object that matches your schema.

result.content_type == "text"
— parsing or validation failed;
inspect result.text
for the raw model response.

Async​

result = await ctx.llm.acomplete(messages=..., task="classifier")

result = await ctx.llm.acomplete_structured(

instructions=..., input=...,<span class="token pla