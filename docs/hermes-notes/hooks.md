Event Hooks | Hermes Agent

Skip to main content

On this page
Event Hooks

Hermes has four hook systems that run custom code at key lifecycle points:

SystemRegistered viaRuns inUse case
Gateway hooksHOOK.yaml
+ handler.py
in ~/.hermes/hooks/
Gateway onlyLogging, alerts, webhooks
Plugin hooksctx.register_hook()
in a pluginCLI + GatewayTool interception, metrics, guardrails
Shell hookshooks:
block in profile config.yaml
pointing at shell scriptsCLI + Gateway + Desktop/TUI/dashboard chatDrop-in scripts for blocking, auto-formatting, context injection
Outbound webhookshooks.outbound:
list in ~/.hermes/config.yaml
CLI + GatewayPush signed lifecycle events to external HTTP endpoints — CI, dashboards, other agents

Hook callback errors are isolated and logged rather than crashing the agent. Hooks are not all passive: directive/control hooks can change flow, transforms can replace content, and a shell pre_tool_call
hook can block or fail closed.

Gateway Event Hooks​

Gateway hooks fire automatically during gateway operation (Telegram, Discord, Slack, WhatsApp, Teams) without blocking the main agent pipeline.

Creating a Hook​

Each hook is a directory under ~/.hermes/hooks/
containing two files:

~/.hermes/hooks/

└── my-hook/

├── HOOK.yaml # Declares which events to listen for

└── handler.py # Python handler function

HOOK.yaml​

name: my-hook

description: Log all agent activity to a file

events:

- agent:start

- agent:end

- agent:step

The events
list determines which events trigger your handler. You can subscribe to any combination of events, including wildcards like command:*
.

handler.py​

import json

from datetime import datetime

from pathlib import Path

LOG_FILE = Path.home() / ".hermes" / "hooks" / "my-hook" / "activity.log"

async def handle(event_type: str, context: dict):

"""Called for each subscribed event. Must be named 'handle'."""

entry = {

"timestamp": datetime.now().isoformat(),

"event": event_type,

**context,

}

with open(LOG_FILE, "a") as f:

f.write(json.dumps(entry) + "\n")

Handler rules:

Must be named handle

Receives event_type
(string) and context
(dict)

Can be async def
or regular def
— both work

Errors are caught and logged, never crashing the agent

Available Events​

EventWhen it firesContext keys
gateway:startup
Gateway process startsplatforms
(list of active platform names)
session:start
New messaging session createdplatform
, user_id
, session_id
, session_key

session:end
Session ended (before reset)platform
, user_id
, session_key

session:reset
User ran /new
or /reset
platform
, user_id
, session_key

session:compress
Context compression completed for a sessionplatform
, session_id
, old_session_id
(empty when compacted in place), in_place
(bool — true
= transcript compacted on the same id, false
= rotated from old_session_id
), compression_count

agent:start
Agent begins processing a messageplatform
, user_id
, chat_id
, thread_id
(forum-topic / thread root id; empty when not in a thread), chat_type
("dm"
| "group"
| "forum"
; empty if unknown), session_id
, message
(truncated to 500 chars)
agent:step
Each iteration of the tool-calling loopplatform
, user_id
, session_id
, iteration
, tool_names

agent:end
Agent finishes processingsame keys as agent:start
, plus response
(truncated to 500 chars)
reaction:added
An emoji reaction was added to a message the bot can see (Slack adapter currently). Requires the reactions:read
scope + the reaction_added
bot event subscription; the bot must be a member of the channel.platform
, reaction
, user_id
, item_user_id
, item_type
, channel_id
, message_ts
, team_id
, event_ts
, raw_event

reaction:removed
An emoji reaction was removed from a message the bot can see. Requires the reaction_removed
bot event subscription.same shape as reaction:added

command:*
Any slash command executedplatform
, user_id
, command
, args

Wildcard Matching​

Handlers registered for command:*
fire for any command:
event (command:model
, command:reset
, etc.). Monitor all slash commands with a single subscription.

Threaded replies
A handler posting a follow-up message into the same Telegram forum topic should include message_thread_id=int(thread_id)
when chat_type == "forum"
and thread_id
is non-empty.

Examples​

Telegram Alert on Long Tasks​

Send yourself a message when the agent takes more than 10 steps:

# ~/.hermes/hooks/long-task-alert/HOOK.yaml

name: long-task-alert

description: Alert when agent is taking many steps

events:

- agent:step

# ~/.hermes/hooks/long-task-alert/handler.py

import os

import httpx

THRESHOLD = 10

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CHAT_ID = os.getenv("TELEGRAM_HOME_CHANNEL")

async def handle(event_type: str, context: dict):

iteration = context.get("iteration", 0)

if iteration == THRESHOLD and BOT_TOKEN and CHAT_ID:

tools = ", ".join(context.get("tool_names", []))

text = f"⚠️ Agent has been running for {iteration} steps. Last tools: {tools}"

async with httpx.AsyncClient() as client:

await client.post(

f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",

json={"chat_id": CHAT_ID, "text": text},

)

Command Usage Logger​

Track which slash commands are used:

# ~/.hermes/hooks/command-logger/HOOK.yaml

name: command-logger

description: Log slash command usage

events:

- command:*

# ~/.hermes/hooks/command-logger/handler.py

import json

from datetime import datetime

from pathlib import Path

LOG = Path.home() / ".hermes" / "logs" / "command_usage.jsonl"

def handle(event_type: str, context: dict):

LOG.parent.mkdir(parents=True, exist_ok=True)

entry = {

"ts": datetime.now().isoformat(),

"command": context.get("command"),

"args": context.get("args"),

"platform": context.get("platform"),

"user": context.get("user_id"),

}

with open(LOG, "a") as f:

f.write(json.dumps(entry) + "\n")

Session Start Webhook​

POST to an external service on new sessions:

# ~/.hermes/hooks/session-webhook/HOOK.yaml

name: session-webhook

description: Notify external service on new sessions

events:

- session:start

- session:reset

# ~/.hermes/hooks/session-webhook/handler.py

import httpx

WEBHOOK_URL = "https://your-service.example.com/hermes-events"

async def handle(event_type: str, context: dict):

async with httpx.AsyncClient() as client:

await client.post(WEBHOOK_URL, json={

"event": event_type,

**context,

}, timeout=5)

Tutorial: BOOT.md — Run a Startup Checklist on Every Gateway Boot​

A popular pattern from the community: drop a Markdown checklist at ~/.hermes/BOOT.md
, and have the agent run it once every time the gateway starts. Useful for "on every boot, check overnight cron failures and ping me on Discord if anything failed," or "summarize the last 24h of deploy.log and post it to Slack #ops."

This tutorial shows how to build it yourself as a user-defined hook. Hermes does not ship a built-in BOOT.md hook — you wire up exactly the behavior you want.

What we're building​

A file at ~/.hermes/BOOT.md
with natural-language startup instructions.

A gateway hook that fires on gateway:startup
, spawns a one-shot agent with your gateway's resolved model/credentials, and runs the BOOT.md instructions.

A [SILENT]
convention so the agent can opt out of sending a message when there's nothing to report.

Step 1: Write your checklist​

Create ~/.hermes/BOOT.md
. Write it as if you were giving instructions to a human assistant:

# Startup Checklist

1. Run `hermes cron list` and check if any scheduled jobs failed overnight.

2. If any failed, summarize them for Discord #ops (the hook delivers your final response to its configured target).

3. Check if `/opt/app/deploy.log` has any ERROR lines from the last 24 hours. If yes, summarize them and include in the same report.

4. If nothing went wrong, reply with only `[SILENT]` so no message is sent.

The agent sees this as part of its prompt, so anything you can describe in plain language works — tool calls, shell commands, sending messages, summarizing files.

Step 2: Create the hook​

~/.hermes/hooks/boot-md/

├── HOOK.yaml

└── handler.py

~/.hermes/hooks/boot-md/HOOK.yaml

name: boot-md

description: Run ~/.hermes/BOOT.md on gateway startup

events:

- gateway:startup

~/.hermes/hooks/boot-md/handler.py

"""Run ~/.hermes/BOOT.md on every gateway startup."""

import logging

import threading

from pathlib import Path

logger = logging.getLogger("hooks.boot-md")

BOOT_FILE = Path.home() / ".hermes" / "BOOT.md"

def _build_prompt(content: str) -> str:

return (

"You are running a startup boot checklist. Follow the instructions "

"below exactly.\n\n"

"---\n"

f"{content}\n"

"---\n\n"

"Execute each instruction. Put any user-facing summary in your "

"final response — the hook delivers it to the configured channel "

"(e.g. Discord or Slack); you do not send messages yourself.\n"

"If nothing needs attention and there is nothing to report, reply "

"with ONLY: [SILENT]"

)

def _run_boot_agent(content: str) -> None:

"""Spawn a one-shot agent and execute the checklist.

Uses the gateway's resolved model and runtime credentials so this works

against custom endpoints, aggregators, and OAuth-based providers alike.

"""

try:

from gateway.run import _resolve_gateway_model, _resolve_runtime_agent_kwargs

from run_agent import AIAgent

agent = AIAgent(

model=_resolve_gateway_model(),

**_resolve_runtime_agent_kwargs(),

platform="gateway",

quiet_mode=True,

skip_context_files=True,

skip_memory=True,

max_iterations=20,

)

result = agent.run_conversation(_build_prompt(content))

response = (result.get("final_response", "") or "").strip()

if response.upper() not in {"[SILENT]", "SILENT", "NO_REPLY", "NO REPLY"}:

logger.info(<span class="token string" style="co