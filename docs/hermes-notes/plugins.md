Build a Hermes Plugin | Hermes Agent

Skip to main content

On this page
Build a Hermes Plugin

This guide walks through building a complete Hermes plugin from scratch. By the end you'll have a working plugin with multiple tools, lifecycle hooks, shipped data files, and a bundled skill — everything the plugin system supports.

Not sure which guide you need?
Hermes has several distinct pluggable interfaces — some use Python register_*
APIs, others are config-driven or drop-in directories. Use this map first:
If you want to add…Read
Custom tools, hooks, slash commands, skills, or CLI subcommandsThis guide (the general plugin surface)
A native desktop app extension (panes, pages, status bar, palette, themes)Desktop Plugin SDK
A web dashboard extension (tabs, shell slots, themes)Extending the Dashboard
An LLM / inference backend (new provider)Model Provider Plugins
A gateway channel (Discord/Telegram/IRC/Teams/etc.)Adding Platform Adapters
A memory backend (Honcho/Mem0/Supermemory/etc.)Memory Provider Plugins
A context-compression engineContext Engine Plugins
An image-generation backendImage Generation Provider Plugins
A video-generation backendVideo Generation Provider Plugins
A web-search / extract backendWeb Search Provider Plugins
A cloud browser backend (Browserbase-style CDP session provider)Browser Provider Plugins
A secret-manager backend (vault / password manager / OS keystore)Secret Source Plugins
A dashboard OIDC/auth providerWeb Dashboard — custom providers — ctx.register_dashboard_auth_provider()

A TTS backend (any CLI — Piper, VoxCPM, Kokoro, voice cloning, …)TTS custom command providers — config-driven, no Python needed
An STT backend (custom whisper / ASR CLI)Voice Message Transcription — set HERMES_LOCAL_STT_COMMAND
to an argv-tokenized template
External tools via MCP (filesystem, GitHub, Linear, any MCP server)MCP — declare mcp_servers.<name>
in config.yaml

Gateway event hooks (fire on startup, session events, commands)Event Hooks — drop HOOK.yaml
+ handler.py
into ~/.hermes/hooks/<name>/

Shell hooks (run a shell command on events)Shell Hooks — declare under hooks:
in config.yaml

Additional skill sources (custom GitHub repos, private skill indexes)Skills — hermes skills tap add <repo>
· Publishing a tap
A first-class core inference provider (not a plugin)Adding Providers
See the full Pluggable interfaces table for a consolidated view of every extension surface including config-driven (TTS, STT, MCP, shell hooks) and drop-in directory (gateway hooks) styles.

Third-party-product plugins ship standalone — not into the core tree
Plugins that integrate someone else's product or project — observability/metrics backends, vendor SaaS connectors, analytics dashboards, paid-service tie-ins — are built and distributed as standalone plugin repos, not merged into NousResearch/hermes-agent
. Users install them into ~/.hermes/plugins/
or via a pip entry point; everything in this guide works the same way from a standalone repo. This is a coupling-and-maintenance decision (the core moves fast and we don't own your backend), not a quality bar — a plugin can be excellent and still belong in its own repo. Promote it in the Nous Research Discord #plugins-skills-and-skins
channel. See CONTRIBUTING.md for the policy.

Portable Agent Plugins v1 packages​

Hermes can also install and load directory packages that target the Agent
Plugins v1.0.0 format. This is a compatibility adapter for the portable
components Hermes already owns. It does not replace native plugin.yaml
plus
register(ctx)
plugins.

my-portable-plugin/

├── plugin.json

├── skills/

│ └── summarize/

│ ├── SKILL.md

│ └── references/

└── mcp.json

Install and activate a portable package through the normal workflow:

hermes plugins install owner/repository --no-enable

hermes plugins list

hermes plugins enable <plugin-name>

Portable packages are disabled after installation unless you explicitly enable
them. An enabled package may provide immediate skills/*/SKILL.md
directories
and stdio MCP servers from root mcp.json
. Skills are read-only, namespaced,
and loaded through skills_list
plus skill_view
. MCP commands are passed as
one executable token with a separate argument list, never through a shell.
Use skills_list
to discover the full qualified skill name. Portable skill
namespaces have the deterministic form agent-plugin-<slug>-<hash>
, derived
from the discovered plugin key so sanitized names cannot collide.

Hermes validates plugin.json
, Agent Skills frontmatter, fixed component
locations, mcp.json
, resolved paths, and symlink containment locally. It does
not fetch JSON schemas while loading a package. A bad skill or MCP entry is
skipped at its own boundary when valid sibling components can still load.
PLUGIN_ROOT
points to the resolved package root. PLUGIN_DATA
points to a
profile-scoped writable directory managed by Hermes.
Values declared in portable MCP env
are visible package data, not a secret
storage mechanism. Do not place credentials in mcp.json
.

The current portable subset supports stdio and Streamable HTTP MCP entries.
Portable streamable-http
entries are routed through Hermes' existing native
remote MCP client (the same runtime that powers URL-based mcp_servers

config), with the v1 boundary rules enforced: the URL must be absolute
http(s) with no user information or fragment, plain HTTP is accepted only
for localhost
/loopback hosts, and configured headers are never forwarded
across a cross-origin redirect. Legacy sse
entries are reported and
skipped. Agent Plugins v1 does not define trust, permissions, provenance, or a
sandbox. Enabling a package grants its instructions and local executable the
same full-trust posture as other installed Hermes plugins.

The rendered specification currently
labels v1.0.0 a Working Draft, while the
versioned specification repository
records it as Published. Hermes keys behavior on the canonical v1.0.0 schema
identifiers and normative text, not either mutable status label. This is an
explicit supported subset, not a claim of full Agent Plugins conformance.

Native plugin compatibility contract​

Native plugin.yaml
plus register(ctx)
plugins are protected by behavior,
not by one global plugin API number. Hermes does not expose a
PLUGIN_API_VERSION
, require a manifest-wide api:
match, or attach an API
version to unrelated values. A plugin that uses a documented behavior should
continue to work after a normal Hermes upgrade.

The compatibility rules are:

Evolve additively. Documented PluginContext
methods are not removed or
renamed. New parameters are optional, have defaults, and should be
keyword-only. Existing return fields are not removed or silently retyped.

Hook payloads are keyword payloads. New hook data is added as keyword
fields, never by changing the meaning or position of an existing field.
Hermes inspects callback signatures: a legacy callback receives the fields it
declares, while a callback with **kwargs
receives the complete current
payload. New plugins should accept **kwargs
so they can opt into additive
data without another signature change.

Manifests are open to additions. Unknown plugin.yaml
fields are ignored.
Older Hermes releases can therefore load a plugin whose manifest contains
metadata introduced by a newer release, provided the plugin code itself uses
supported runtime behavior.

Provider interfaces grow through defaults. New provider methods have a
default implementation. New callback context is optional and forwarded only
when signature inspection shows that a provider accepts it. Adding an
abstract method or an unconditionally forwarded argument requires a
migration window rather than a flag-day signature change.

Version the contract that crosses a boundary. A capability may carry its
own schema version when it defines a wire payload or persisted format (for
example, observer payloads or secret-source state). Keep fields additive
within that local schema. Persisted plugin state and config must remain
readable, or ship an explicit migration; resumed sessions written by the old
format must still replay. Do not add version literals to unrelated callback
or context values.

Deprecation policy​

A documented native plugin behavior may be deprecated only with all of the
following:

a replacement and migration instructions in the plugin guide and release
notes;

a warning emitted at most once per process, naming the replacement and the
earliest removal release;

support for the old behavior through at least two subsequent minor
releases; and

behavior-based compatibility coverage for both the legacy path and the
replacement throughout that window.

Removal after the window must include any migration needed for persisted data
or resumable sessions. In practice, additive aliases and adapters are preferred
to removal.

Hermes enforces this contract with frozen external-plugin fixtures discovered
from an isolated HERMES_HOME
. Those tests load and invoke the plugin through
PluginManager
; they assert real registration and callback outcomes rather
than internal symbol lists or source-code shape.

Sep 2026 module decomposition: old import paths end 2026-09-14​

Hermes's internals were split into <stem>_<topic>
sibling modules in Sep 2026 (PR #102117). Internal
import paths were never part of the plugin contract above, but many plugins used them. Every moved name
still resolves from its old module until 2026-09-14, then the compatibility layer is removed.

Check your plugin: hermes plugins compat /path/to/your/plugin
lists every file:line
with the
old path and the new one, and exits 1 while any remain. COMPAT_MANIFEST.md
in the repo is the full map.

What users see: a notice under the CLI banner, in hermes doctor
and after hermes update
, and a
one-time Desktop dialog naming the plugin. Each resolution through an old path also emits a
HermesPluginCompatWarning
once per process.

From 2026-09-14: plugins that still import old paths are not loaded (the reason shows in
hermes plugins list
). Users can force-load with plugins.allow_deprecated_imports: true
until the
layer is actually removed, at which point the old paths raise ImportError
.

What you're building​

A calculator plugin with two tools:

calculate
— evaluate math expressions (2**16
, sqrt(144)
, pi * 5**2
)

unit_convert
— convert between units (100 F → 37.78 C
, 5 km → 3.11 mi
)

Plus a hook that logs every tool call, and a bundled skill file.

Step 1: Create the plugin directory​

Create a directory and continue with Step 2:

mkdir -p ~/.hermes/plugins/calculator

cd ~/.hermes/plugins/calculator

Validate with Plugin Doctor​

hermes plugins doctor [path-or-id]
runs the same directory discovery,
manifest parser, namespaced import, register(ctx)
, hook registry, and tool
registry used by Hermes itself. It reports invalid hook names, callbacks that do
not accept **kwargs
, registration failures, and drift between declared and
registered tools/hooks. Pass --ci
to exit non-zero on an error:

hermes plugins doctor . --ci

Doctor uses a temporary HERMES_HOME
, restores plugin registration state after
the check, and blocks direct Python socket connections to catch accidental
network access while registration runs. This is not a sandbox: plugin code still
executes in-process with the current user's permissions and can spawn subprocesses,
so only run Doctor on code you trust enough to import.

Step 2: Write the manifest​

Create plugin.yaml
:

name: calculator

version: 1.0.0

description: Math calculator — evaluate expressions and convert units

provides_tools:

- calculate

- unit_convert

provides_hooks:

- post_tool_call

This tells Hermes: "I'm a plugin called calculator, I provide tools and hooks." The provides_tools
and provides_hooks
fields are lists of what the plugin registers.

Optional fields you could add:

author: Your Name

requires_env: # gate loading on env vars; prompted during install

- SOME_API_KEY # simple format — plugin disabled if missing

- name: OTHER_KEY # rich format — shows description/url during install

description: "Key for the Other service"

url: "https://other.com/keys"

secret: true

capabilities: # privileged host surfaces you request (consent flow)

- tools.override # replace built-in tools (needs user consent)

- llm.model_override # choose the model for host-owned LLM calls

Declaring capabilities​

If your plugin needs a privileged host surface — overriding a built-in tool,
picking the model for ctx.llm
calls, etc. — declare it in capabilities:
.
At install/enable time the user sees the list and consents once; if a later
version adds a capability, the update flow asks again for just the addition.
Undeclared or unconsented capabilities are simply off (fail closed), so
probe before using them and degrade gracefully:

def register(ctx):

if ctx.has_capability("tools.override"):

ctx.register_tool(..., override=True)

else:

ctx.register_tool(...) # register under a non-conflicting name

Known capability ids: tools.override
, llm.provider_override
,
llm.model_override
, llm.agent_id_override
, llm.profile_override
,
llm.task_override
(see hermes_cli/plugin_capabilities.py
for the
canonical registry). Unknown ids are ignored. The older per-capability
config keys (plugins.entries.<id>.allow_tool_override
, …) still work but
are deprecated — declare capabilities instead so users get a single,
auditable consent screen. Capabilities are consent + audit, not a
sandbox: they gate host API surfaces, nothing more.

Pip-distributed plugins have no plugin.yaml
directory once installed,
so declare capabilities in distribution metadata instead, via the companion
hermes_agent.plugin_capabilities
entry-point group. Each declaration is
named <plugin-id>.<capability-id>
and points at the same object as your
hermes_agent.plugins
entry point:

[project.entry-points."hermes_agent.plugins"]

calculator = "my_pkg:register"

[project.entry-points."hermes_agent.plugin_capabilities"]

"calculator.tools.override" = "my_pkg:register"

Hermes reads these from installed metadata without importing your code, so
hermes plugins capabilities
and the consent flow stay accurate for pip
installs.

Manifest v2 reference​

plugin.yaml
also supports an additive v2 schema (#64165). Every field is
optional; a manifest without manifest_version
is a v1 manifest and stays
fully supported forever. Unknown fields never break loading — they are ignored
with a warning (forward compatibility), and a manifest_version
newer than
this Hermes understands still loads with a warning.

FieldTypeMeaning
manifest_version
intManifest file-format version. Absent = 1
. Current max: 2
. Independent from api_version
.
api_version
intRuntime plugin API generation the plugin targets (ctx surface / hook signatures). Deliberately a separate axis from manifest_version
— an api_version: 1
plugin can use a v2 manifest.
requires_plugins
listInter-plugin dependencies: - id: other-plugin
with optional version_range: ">=1.0,<2"
. Advisory: a missing dependency logs a clear warning but the plugin still loads — probe at runtime with ctx.has_plugin("other-plugin")
. Load order honors these edges: when A requires B, B's register()
runs before A's (topological sort, alphabetical tiebreak; cycles warn and fall back to alphabetical order).
python_dependencies
list of strDeclared pip requirements (e.g. "requests>=2.0,<3"
). Declaration seam only — Hermes validates them, and hermes plugins install
/ hermes plugins doctor
surface missing ones with a pip install
hint, but Hermes never auto-installs them. Pin upper bounds.
config_schema
mappingJSON-schema-ish description of keys under plugins.entries.<id>.settings
: api_url: {type: str, default: "", description: "...", required: false}
. Validated at load; mismatches log actionable warnings naming the key and expected type — never load failures. Types: str
, int
, float
, bool
, list
, dict
(plus JSON-schema aliases).
license
strSPDX-style license id (e.g. MIT
).
homepage
strProject URL.
tags
list of strFree-form discovery tags (e.g. [gateway, telegram]
).

# plugin.yaml — manifest v2 example

name: my-plugin

version: 1.2.0

manifest_version: 2

api_version: 1

license: MIT

homepage: https://github.com/owner/my-plugin

tags: [gateway, demo]

requires_plugins:

- id: other-plugin

version_range: ">=1.0,<2"

python_dependencies:

- "somepkg>=1.0,<2" # surfaced, never auto-installed

config_schema:

api_url: {type: str, default: "", description: "Service endpoint"}

pip-dependency isolation is deferred
python_dependencies
is intentionally declare-and-surface only. Installing
arbitrary packages into Hermes' shared venv is a conflict and supply-chain
surface, so the install seam's isolation design (constraints-file installs
against the host lock vs. per-plugin vendored dirs vs. conflict detection
with refusal) is an explicitly deferred follow-up — see the round-2 review on
#64165 and
#15220. Plugin
packs (#64166) build on these v2 fields.

Step 3: Write the tool schemas​

Create schemas.py
— this is what the LLM reads to decide when to call your tools:

"""Tool schemas — what the LLM sees."""

CALCULATE = {

"name": "calculate",

"description": (

"Evaluate a mathematical expression and return the result. "

"Supports arithmetic (+, -, *, /, **), functions (sqrt, sin, cos, "

"log, abs, round, floor, ceil), and constants (pi, e). "

"Use this for any math the user asks about."

),

"parameters": {

"type": "object",

"properties": {

"expression": {

"type": "string",

"description": "Math expression to evaluate (e.g., '2**10', 'sqrt(144)')",

},

},

"required": ["expression"],

},

}

UNIT_CONVERT = {

"name": "unit_convert",

"description": (

"Convert a value between units. Supports length (m, km, mi, ft, in), "

"weight (kg, lb, oz, g), temperature (C, F, K), data (B, KB, MB, GB, TB), "

"and time (s, min, hr, day)."

),

"parameters": {

"type": "object",

"properties": {

"value": {

"type": "number",

"description": "The numeric value to convert",

},

"from_unit": {

"type": "string",

"description": "Source unit (e.g., 'km', 'lb', 'F', 'GB')",

},

"to_unit": {

"type": "string",

"description": "Target unit (e.g., 'mi', 'kg', 'C', 'MB')",

},

},

"required": ["value", "from_unit", "to_unit"],

},

}

Why schemas matter: The description
field is how the LLM decides when to use your tool. Be specific about what it does and when to use it. The parameters
define what arguments the LLM passes.

Step 4: Write the tool handlers​

Create tools.py
— this is the code that actually executes when the LLM calls your tools:

"""Tool handlers — the code that runs when the LLM calls each tool."""

import json

import math

# Safe globals for expression evaluation — no file/network access

_SAFE_MATH = {

"abs": abs, "round": round, "min": min, "max": max,

"pow": pow, "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,

"tan": math.tan, "log": math.log, "log2": math.log2, "log10": math.log10,

"floor": math.floor, "ceil": math.ceil,

"pi": math.pi, "e": math.e,

"factorial": math.factorial,

}

def calculate(args: dict, **kwargs) -> str</spa