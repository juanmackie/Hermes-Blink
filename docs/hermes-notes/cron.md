Scheduled Tasks (Cron) | Hermes Agent

Skip to main content

On this page
Scheduled Tasks (Cron)

Schedule tasks to run automatically with natural language or cron expressions. Hermes exposes cron management through a single cronjob
tool with action-style operations instead of separate schedule/list/remove tools.

What cron can do now​

Cron jobs can:

schedule one-shot or recurring tasks

pause, resume, edit, trigger, and remove jobs

attach zero, one, or multiple skills to a job

deliver results back to the origin chat, local files, or configured platform targets

run in fresh agent sessions with the normal static tool list

run in no-agent mode — a script on a schedule, its stdout delivered verbatim, zero LLM involvement (see the no-agent mode section below)

All of this is available to Hermes itself through the cronjob
tool, so you can create, pause, edit, and remove jobs by asking in plain language — no CLI required.

tip
Which model does a cron job run on? Resolution at fire time is: per-job pin → cron.model
in config.yaml
→ the global default from hermes model
.

Per-job pin — set by you via the dashboard, hermes cron create/edit --model … --provider …
, or by editing ~/.hermes/cron/jobs.json
. Once set, it sticks until you change it. The agent's cronjob
tool cannot set or change per-job models — inference pins are user-owned.

cron.model
/ cron.model_provider
— a cron-fleet default: every unpinned job runs on this model, independent of your chat model. Set it once (hermes config set cron.model <name>
) and switching your chat model with hermes model
or /model
never touches your cron fleet.

Global default — only when neither of the above is set does a job follow hermes model
. Hermes snapshots the provider and model at creation, and that snapshot is the job's effective pin: if you later switch the global default (hermes model
, /model
, hermes config set model.default …
), the job keeps running on the model and provider it was created under and logs one INFO line per run noting the difference. A global model change never stops a scheduled job, and an unattended job never silently inherits a switch to a paid provider/model (#44585). To move a job to the new default, resnap it (hermes cron resnap <job_id>
, or --all
for every unpinned job) so it adopts the current default while staying unpinned, pin it (hermes cron edit <job_id> --provider <provider> --model <model>
), or set cron.model
to move the whole fleet at once. Jobs created before snapshots existed keep following the live global default.

Whichever provider a job resolves to, its provider-specific request settings (e.g. request_overrides
such as extra_body
/extra_headers
for custom providers) carry into the scheduled run just like an interactive session.
hermes setup --portal
is the lowest-friction option for unattended runs since OAuth refresh is automatic. See Nous Portal.

tip
Per-job reasoning effort. A job can pin its own thinking level, independent of the model pin: one of none
, minimal
, low
, medium
, high
, xhigh
, max
, ultra
. When set, it overrides both the global agent.reasoning_effort
and per-model agent.reasoning_overrides
for that job's runs (none
disables thinking). Set it via hermes cron create/edit --reasoning-effort high
; pass an empty string on edit to clear the pin and follow config again. (It is deliberately not exposed on the agent's cronjob
tool — model configuration stays a user decision.) Levels a model doesn't support are clamped or omitted by the provider at request time — pinning xhigh
on a model that caps at high
runs at high
. The pin has no effect on no_agent
jobs (there is no LLM call to tune). Use it to run heavy scheduled analyses at high
while cheap recurring jobs run at minimal
, without touching your global default.

warning
Cron-run sessions cannot recursively create more cron jobs. Hermes disables cron management tools inside cron executions to prevent runaway scheduling loops.

Creating scheduled tasks​

In chat with /cron
​

/cron add "in 30m" "Remind me to check the build"

/cron add "every 2h" "Check server status"

/cron add "every 1h" "Summarize new feed items" --skill blogwatcher

/cron add "every 1h" "Use both skills and combine the result" --skill blogwatcher --skill maps

From the standalone CLI​

hermes cron create "every 2h" "Check server status"

hermes cron create "every 1h" "Summarize new feed items" --skill blogwatcher

hermes cron create "every 1h" "Use both skills and combine the result" \

--skill blogwatcher \

--skill maps \

--name "Skill combo"

Through natural conversation​

Ask Hermes normally:

Every morning at 9am, check Hacker News for AI news and send me a summary on Telegram.

Hermes will use the unified cronjob
tool internally.

Pre-dispatch configuration validation​

Before constructing any agent machinery for a scheduled run, the scheduler
validates that the job's configuration can actually produce a successful run:

the provider API key resolves (skipped when a fallback_providers
chain is
configured, since the fallback path may rescue a missing primary key),

attached skills are ready (no missing required environment variables,
commands, or credential files),

delivery platform targets are known and have gateway credentials configured
(local
/origin
targets are never checked).

When validation fails, the job's last_status
becomes blocked_config
, ONE
alert is delivered (it is not repeated every tick), and no LLM call is
made — a misconfigured job never spends tokens. The next healthy run clears
the blocked state so a future configuration break alerts again.

To disable the validation and restore the old behavior (the run proceeds and
fails during execution):

cron:

preflight: false

Or: hermes config set cron.preflight false

Moving unpinned jobs to a new global default​

An unpinned job stays on the provider/model it was created under, so changing your chat model
never changes (or stops) your cron fleet. When you do want scheduled jobs to move:

hermes cron edit <job_id> --provider <provider> --model <model> # one job

hermes config set cron.model <model> # every unpinned job

hermes config set model.default …
and the Desktop model picker list the unpinned jobs that will
keep their original model so you can decide deliberately. Stored snapshots are refreshed whenever
you edit a job's provider, model, or base URL.

Resnapping refreshes an unpinned job's stored snapshot to the current global resolution without
pinning it, so it keeps tracking future changes:

hermes cron resnap <job_id> # one job

hermes cron resnap --all # every unpinned agent job

The agent-facing cronjob
tool accepts the same action (action=resnap job_id=<id>
or
action=resnap all=true
). Pinned axes and no_agent
script jobs are left untouched.

Skill-backed cron jobs​

A cron job can load one or more skills before it runs the prompt.

Single skill​

cronjob(

action="create",

skill="blogwatcher",

prompt="Check the configured feeds and summarize anything new.",

schedule="0 9 * * *",

name="Morning feeds",

)

Multiple skills​

Skills are loaded in order. The prompt becomes the task instruction layered on top of those skills.

cronjob(

action="create",

skills=["blogwatcher", "maps"],

prompt="Look for new local events and interesting nearby places, then combine them into one short brief.",

schedule="every 6h",

name="Local brief",

)

This is useful when you want a scheduled agent to inherit reusable workflows without stuffing the full skill text into the cron prompt itself.

Running a job inside a project directory​

Cron jobs default to running detached from any repo — no AGENTS.md
, CLAUDE.md
, or .cursorrules
is loaded, and the terminal / file / code-exec tools run from whatever working directory the gateway started in. Pass --workdir
(CLI) or workdir=
(tool call) to change that:

# Standalone CLI (schedule and prompt are positional)

hermes cron create "every 1d at 09:00" \

"Audit open PRs, summarize CI health, and post to #eng" \

--workdir /home/me/projects/acme

# From a chat, via the cronjob tool

cronjob(

action="create",

schedule="every 1d at 09:00",

workdir="/home/me/projects/acme",

prompt="Audit open PRs, summarize CI health, and post to #eng",

)

When workdir
is set:

AGENTS.md
, CLAUDE.md
, and .cursorrules
from that directory are injected into the system prompt (same discovery order as the interactive CLI)

terminal
, read_file
, write_file
, patch
, search_files
, and execute_code
all use that directory as their working directory

The path must be an absolute directory that exists — relative paths and missing directories are rejected at create / update time

Pass --workdir ""
(or workdir=""
via the tool) on edit to clear it and restore the old behaviour

Isolation
Each agent run binds its workdir
to that run's unique task identity. Workdir jobs therefore use the normal parallel pool without mutating process-global terminal state or leaking paths between concurrent runs. Set cron.max_parallel_jobs
if you want to limit total cron concurrency.

Editing jobs​

You do not need to delete and recreate jobs just to change them.

Job reference
The <job_id>
placeholder below (and in Lifecycle actions) also accepts the job's name (case-insensitive) — handy when you remember morning-digest
but not the hex ID. An exact job ID takes precedence over name matches; if the reference is not an ID and a name matches more than one job, the command refuses and prints the candidate IDs so you can disambiguate.

Chat​

/cron edit <job_id> --schedule "every 4h"

/cron edit <job_id> --prompt "Use the revised task"

/cron edit <job_id> --skill blogwatcher --skill maps

/cron edit <job_id> --remove-skill blogwatcher

/cron edit <job_id> --clear-skills

Standalone CLI​

hermes cron edit <job_id> --schedule "every 4h"

hermes cron edit <job_id> --prompt "Use the revised task"

hermes cron edit <job_id> --skill blogwatcher --skill maps

hermes cron edit <job_id> --add-skill maps

hermes cron edit <job_id> --remove-skill blogwatcher

hermes cron edit <job_id> --clear-skills

Notes:

repeated --skill
replaces the job's attached skill list

--add-skill
appends to the existing list without replacing it

--remove-skill
removes specific attached skills

--clear-skills
removes all attached skills

Lifecycle actions​

Cron jobs now have a fuller lifecycle than just create/remove.

Chat​

/cron list

/cron pause <job_id>

/cron resume <job_id>

/cron run <job_id>

/cron remove <job_id>

Standalone CLI​

hermes cron list

hermes cron pause <job_id_or_name>

hermes cron resume <job_id_or_name>

hermes cron run <job_id_or_name>

hermes cron remove <job_id_or_name>

hermes cron edit <job_id_or_name> [...flags]

hermes cron status

hermes cron tick

What they do:

pause
— keep the job but stop scheduling it

resume
— re-enable the job and compute the next future run

run
— trigger the job on the next scheduler tick

remove
— delete it entirely

edit
— modify schedule, prompt, delivery, etc.

Name-based lookup. All four mutating verbs (pause
, resume
, run
, remove
, edit
) plus the agent's cronjob
tool now accept a job name (case-insensitive) in place of the hex ID. The agent and CLI both prefer an exact ID match if one exists; ambiguous name matches (multiple jobs sharing the same name) are refused with the full list of candidate IDs so you can pick one explicitly. Names are not unique, so this guard is load-bearing — it prevents silently mutating the wrong job when two share a name.

Creating a job paused (safe canary)​

Create a canary without a create-then-pause scheduling race:

hermes cron create "every 1h" "Post the digest" --paused --paused-reason "Awaiting review"

hermes cron resume <job_id>

--paused
stores enabled: false
, state: paused
, next_run_at: null
, a pause
timestamp and an auditable reason in the first locked write, without registering a
trigger. Omit the reason to store "Created paused; awaiting operator approval."
Omit --paused
to retain normal enabled creation. --paused-reason
requires
--paused
; invalid values are rejected before persistence.

The same paused
boolean and optional paused_reason
string are accepted by
cron.jobs.create_job
, the cron management tool's create
action, the gateway
POST /api/jobs
, and the dashboard POST /api/cron/jobs
. Resume schedules the next
future run. Pausing prevents automatic fires, not operator overrides: existing
explicit Run now / force-run behavior remains available and can resume and run
the job. It is not a security boundary against an operator who can run jobs.

Agent-managed scheduling (cron jobs that manage cron jobs)​

By default, agents launched by the scheduler cannot use the cronjob
tool —
a scheduled job cannot create, edit, or remove other jobs. Opt in via
config.yaml
:

cron:

allow_agent_scheduling: true # default: false

When enabled, a scheduled agent can manage the cron table like any chat
session: schedule follow-up one-shots from within scheduled work, tune its own
cadence, or run a "cron librarian" job that reconciles the whole table
(list, then update/remove/create as needed). Two properties keep this sane:

One flat, user-owned table. Jobs created from a cron run land in the
same jobs.json
as every other job with no special ownership — you can
list, edit, or remove them exactly as if you had created them yourself.

No dangling delivery. A cron run is ephemeral, so deliver: origin

from inside one is resolved at create time to the creating job's own
concrete target (platform:chat_id[:thread_id]
, or local
if the creating
job delivers nowhere). A job created by a scheduled agent can never point
its output at a session that no longer exists. Explicit targets
(local
, all
, telegram:<chat_id>
) are honored verbatim.

Prefer prompts that update existing jobs (list first, then update by ID)
over ones that create new jobs each run.

How it works​

Cron execution is handled by the gateway daemon. The gateway ticks the scheduler every 60 seconds, running any due jobs in isolated agent sessions.

hermes gateway install # Install as a user service

sudo hermes gateway install --system # Linux: boot-time system service for servers

hermes gateway # Or run in foreground

hermes cron list

hermes cron status

Gateway scheduler behavior​

On each tick Hermes:

loads jobs from ~/.hermes/cron/jobs.json

checks next_run_at
against the current time

starts a fresh AIAgent
session for each due job

optionally injects one or more attached skills into that fresh session

runs the prompt to completion

delivers the final response

updates run metadata and the next scheduled time

A file lock at ~/.hermes/cron/.tick.lock
prevents overlapping scheduler ticks from double-running the same job batch.

Restart-safe workers under systemd​

When the gateway runs as a systemd service, each due job is handed to an external worker process launched in a transient user scope (systemd-run --user --scope
), so restarting the gateway mid-job does not kill the job. Creating that scope needs a user systemd session; hosts without one (containers, minimal LXCs, a service user without linger) cannot provide it.

By default cron then degrades: the job still runs as a separate external process with the same execution handoff, but without cgroup isolation, so a gateway restart during the job kills it (the execution ledger records that). One warning is logged per gateway process. To fail closed instead — skip the job and record the error on the job row — set:

cron:

require_restart_safe_scope: true

The lasting fix is a user session for the gateway user: sudo loginctl enable-linger <gateway-user>
(and XDG_RUNTIME_DIR
/ DBUS_SESSION_BUS_ADDRESS
in the unit for system-level installs), then restart the gateway. Kanban workers always require a scope and fail closed regardless of this key.

Execution history​

Hermes records each claimed cron attempt in the profile-local
~/.hermes/cron/executions.db
before executor or provider dispatch. Attempts
move through claimed
, running
, and one immutable terminal state:
completed
, failed
, or unknown
. After restart, Hermes marks an abandoned
attempt unknown
only when the original PID and process-start fingerprint prove
that its owner is gone. Unknown attempts are audit records and are never
automatically rerun.

Inspect recent attempts with hermes cron runs [job-id] --limit 20
(alias:
history
). Terminal history is bounded; active attempts are never pruned. The
ledger is included in quick backups.

Scheduled attempts also record their exact scheduled instant, separately from
the time they were claimed. If an old jobs.json
snapshot re-arms an occurrence
that the retained ledger records as completed, Hermes skips that replay and
re-anchors recurring jobs. This works even when the snapshot predates the
dispatch stamp or the original run started late. Explicit manual runs do not
consume a scheduled occurrence's identity.

This is not an exactly-once side-effect guarantee: legacy rows without an
identity, pruned history, unavailable ledgers, and interrupted attempts cannot
prove completion. Restoring the ledger itself to an older backup also removes
that evidence. External fire callbacks identify the currently accepted store
claim, not an upstream scheduled slot absent from the callback.

Repeated-failure review nudge​

Each job tracks a failure_streak
— consecutive failed runs (delivery
failures don't count). A run that fails before the agent is reached at all —
a bad import after a half-applied update, a provider client that cannot be
constructed — counts and alerts the same as one the agent itself failed. When
a recurring job's streak reaches the threshold, the failure message
delivered to chat gains a review nudge telling you the job has failed N runs
in a row and suggesting you fix, pause (hermes cron pause <job>
), or remove
it. Any successful run resets the streak, and hermes cron list
shows the
streak alongside a failing job's last run. One-shot jobs never nudge.

cron:

failure_nudge_threshold: 3 # default; 0 disables the nudge

Automatic re-runs when the model was unreachable​

A recurring job whose run fails with a transient network or DNS error before
a single model call was made — the classic case is a fire right after the
computer wakes, while the VPN or Wi-Fi is still reconnecting — does not sit
out a whole period. The scheduler re-runs it automatically after 5, 15, and
30 minutes (inspired by Claude Cowork's scheduled-task re-runs), then falls
back to the normal schedule. Because zero API calls were made, the re-run is
spend-neutral and cannot duplicate any side effect.

While a re-run is pending, the interim failure notice is suppressed — you get
the real result when a re-run succeeds, or a normal failure alert once the
ladder is exhausted. Any run that reaches the model (success or failure)
resets the ladder. One-shot jobs are excluded: their dispatch accounting is
at-most-times and a consumed dispatch is never resurrected. Retries never
fire past the schedule's own next occurrence when that comes sooner.

cron:

retry_unreachable: false # default true; disables the automatic re-runs

Failure incidents: acknowledge a known failure​

A recurring job that keeps failing with the same error pings you on every
run. Each failure is also recorded as a durable incident, keyed by the
job plus a normalized signature of the error text, in the same per-profile
ledger database as the execution history.

hermes cron incidents # list incidents (newest activity first)

hermes cron incidents --state alerted # filter: detected | alerted | closed

hermes cron incidents ack <id> # acknowledge — stop re-pinging

Acknowledging an incident silences the per-run failure ping for that exact
signature only. Nothing else changes: the run history still records every
failure, the failure streak keeps counting, and the moment the job starts
failing with a different error a new incident is minted and alerts fire
again. A successful run doesn't touch incidents — they are per-signature, not
per-job.

Incident lifecycle: detected
(failure recorded) → alerted
(at least one
failure ping reached delivery) → closed
(acknowledged; terminal for that
signature). Stored error text is secret-redacted and truncated before it is
written.

Recording is always on and costs nothing to ignore — no ping is ever
suppressed until you explicitly ack
.

Fleet health check: hermes cron doctor
​

hermes cron doctor
is a read-only health check over every active job. It
prints grouped, per-job issues and exits 1
when anything actionable is
found (0
when healthy), so it works from a terminal, a watchdog script, or
a CI-style smoke check:

hermes cron doctor

Checks per active job:

last run failed (last_status
not ok, with the recorded error),

last delivery failed (the output was produced but never reached you),

next_run_at
missing, or parked in the past beyond a 15-minute ticker
grace window — the "job is silently not firing" signal (scheduler dead,
gateway down, or a wedged fire-claim),

script missing, not a file, or resolving outside HERMES_HOME/scripts
,

no_agent
job with no script,

configured workdir
that no longer exists.

Doctor never mutates jobs or state — it only reports. Pair it with
hermes cron incidents
(durable failure records) and hermes cron runs

(attempt ledger) when digging into a flagged job.

Delivery options​

When scheduling jobs, you specify where the output goes:

OptionDescriptionExample
"origin"
Back to where the job was createdDefault on messaging platforms
"local"
Save to local files only (~/.hermes/cron/output/
)Default on CLI
"telegram"
Telegram home channelUses TELEGRAM_HOME_CHANNEL

"telegram:123456"
Specific Telegram chat by IDDirect delivery
"telegram:-100123:17585"
Specific Telegram topicchat_id:thread_id
format
"discord"
Discord home channelUses DISCORD_HOME_CHANNEL

"discord:#engineering"
Specific Discord channelBy channel name
"slack"
Slack home channel
"whatsapp"
WhatsApp home
"signal"
Signal
"matrix"
Matrix home room
"mattermost"
Mattermost home channel
"email"
Email
"sms"
SMS via Twilio
"homeassistant"
Home Assistant
"dingtalk"
DingTalk
"feishu"
Feishu/Lark
"wecom"
WeCom
"weixin"
Weixin (WeChat)
"bluebubbles"
BlueBubbles (iMessage)
"qqbot"
QQ Bot (Tencent QQ)
"bot-chat"
This profile's canonical Bot Chat — the bot reads the output and respondsMachine-local
"bot-chat:research"
Another local profile's Bot ChatValidated at create time
"all"
Fan out to every connected home channelResolved at fire time
"telegram,discord"
Fan out to a specific set of channelsComma-separated list
"origin,all"
Deliver to the origin plus every other connected channelCombine any tokens

The agent's final response is automatically delivered to the configured deliver:
target — the agent does not send messages itself, so there is nothing to call in the cron prompt.

Delivery failures are a distinct status​

Execution and delivery are tracked separately. When the agent run succeeds but
the output never reaches the target (platform 5xx, rate limit, stale session,
adapter returned no positive evidence of a send), the job records
last_status: delivery_failed
— never a plain ok
— with the reason in
last_delivery_error
. hermes cron list
shows it in yellow as
delivery_failed: <reason>
, hermes cron doctor
reports it as a delivery
issue, and a manual cronjob run
reports success: false
with the delivery
error. A delivery failure does not count toward the job's failure_streak

(the agent did its job); the next fully successful run returns the status to
ok
.

Bot Chat delivery (bot-chat
)​

bot-chat
delivers the output into a profile's canonical "Bot Chat" session as a real message. Unlike every other target — where the recipient is a human reading a channel — the recipient here is the bot itself: it receives the output as an incoming message, acts on anything that needs action, and responds in its chat. Use it when scheduled output should be processed, not just posted.

bot-chat
(bare) targets the job's own profile.

bot-chat:<profile>
targets another profile on the same machine. Names are validated against hermes profile list
when the job is created; profiles on other gateways or machines can never be targeted, so same-named profiles across machines are unambiguous.

Each delivery costs the target bot one full agent turn — mind the schedule frequency.

Composes with other targets (bot-chat,telegram
) but is never included in all
.

If the canonical chat is open in a mailbox-capable Desktop/TUI backend, delivery is durably queued immediately, whether the bot is idle or busy. Only that live owner runs the incoming turn; cron does not start a com