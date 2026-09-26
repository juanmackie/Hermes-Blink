"""Hermes plugin entrypoint for the home-screen widget agent surface.

Registered as ``hermes_plugins.hermes_widget`` by the plugin loader, so every
import here is relative. ``register()`` must never raise: a failure in the CLI
or skill layer is logged and skipped rather than taking tool registration down
with it (a plugin that raises during load is disabled wholesale).
"""
from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import schemas, tools

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).resolve().parent
SKILL_PATH = PLUGIN_DIR / "skills" / "widget" / "SKILL.md"

_TOOLSET = "hermes-widget"
_SKILL_DESCRIPTION = (
    "Proactive home-screen widget publishing, previews, delivery, and action intents."
)

_TOOLS: tuple[tuple[dict[str, Any], Callable[..., str]], ...] = (
    (schemas.WIDGET_UPDATE, tools.widget_update),
    (schemas.WIDGET_VALIDATE, tools.widget_validate),
    (schemas.WIDGET_LIST, tools.widget_list),
    (schemas.WIDGET_READ_EVENTS, tools.widget_read_events),
    (schemas.WIDGET_MINT_PAIRING_CODE, tools.widget_mint_pairing_code),
    (schemas.WIDGET_SETUP, tools.widget_setup),
    (schemas.WIDGET_PUBLISH, tools.widget_publish),
    (schemas.WIDGET_PREVIEW, tools.widget_preview),
    (schemas.WIDGET_READ_INTENTS, tools.widget_read_intents),
    (schemas.WIDGET_RESOLVE_INTENT, tools.widget_resolve_intent),
    (schemas.WIDGET_ASK, tools.widget_ask),
    (schemas.WIDGET_READ_QUESTIONS, tools.widget_read_questions),
    (schemas.WIDGET_WATCH_CREATE, tools.widget_watch_create),
    (schemas.WIDGET_WATCH_LIST, tools.widget_watch_list),
    (schemas.WIDGET_WATCH_PAUSE, tools.widget_watch_pause),
    (schemas.WIDGET_WATCH_TICK, tools.widget_watch_tick),
    (schemas.WIDGET_WAKE_TEST, tools.widget_wake_test),
    (schemas.WIDGET_SET_QUIET_HOURS, tools.widget_set_quiet_hours),
    (schemas.WIDGET_STATUS, tools.widget_status),
)


def register(ctx: Any) -> None:
    """Wire the widget tools, slash command, CLI command, and skill into Hermes."""
    _register_tools(ctx)
    _register_slash_command(ctx)
    _register_cli_command(ctx)
    _register_skill(ctx)
    _register_proactive_guidance(ctx)


_PROACTIVE_GUIDANCE = (
    "Hermes widget: use it when a useful user-facing update exists, the user asks for a "
    "brief/status/decision surface, or a scheduled run finds a real change. Prefer "
    "widget_publish with a truthful title, required summary, and exactly one supported "
    "source; do not publish filler or republish an identical revision. Check "
    "widget_status when delivery or freshness matters, widget_preview before committing "
    "a visual, and widget_read_intents/widget_resolve_intent for queued taps. High "
    "priority is only for genuinely time-sensitive content. Publishing stores a revision; "
    "it never proves the user saw it (publishing does not prove delivery or visibility). "
    "If setup is missing, use widget_setup rather than "
    "guessing private paths."
)


def _register_proactive_guidance(ctx: Any) -> None:
    """Add one bounded, cache-safe prompt section with a legacy hook fallback.

    Current Hermes renders plugin prompt sections once per session, after memory,
    instead of appending the same text to every turn.  The fallback keeps older
    hosts functional without making the guidance a new dependency.
    """
    register_section = getattr(ctx, "register_system_prompt_section", None)
    if callable(register_section):
        try:
            register_section(
                "hermes-widget.proactive-guidance",
                _system_prompt_section,
                position="after_memory",
                max_chars=1800,
            )
            return
        except Exception:  # noqa: BLE001 - fall back for older/partially upgraded hosts
            logger.debug("hermes-widget: prompt section unavailable; using hook fallback", exc_info=True)
    register_hook = getattr(ctx, "register_hook", None)
    if register_hook is None:
        return
    try:
        register_hook("pre_llm_call", _availability_reminder)
    except Exception:  # noqa: BLE001
        logger.warning("hermes-widget: failed to register availability reminder", exc_info=True)


def _system_prompt_section(_session_info: Any = None) -> str:
    return _PROACTIVE_GUIDANCE


def _availability_reminder(**_kwargs: Any) -> dict[str, str]:
    """Legacy per-turn context shape used only when prompt sections are unavailable."""
    return {"context": _PROACTIVE_GUIDANCE}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _register_tools(ctx: Any) -> None:
    register_tool = getattr(ctx, "register_tool", None)
    if register_tool is None:
        logger.warning("hermes-widget: context has no register_tool; tools not registered")
        return
    for schema, handler in _TOOLS:
        name = schema.get("name", "?")
        try:
            register_tool(
                name=name,
                toolset=_TOOLSET,
                schema=schema,
                handler=handler,
                description=schema.get("description", ""),
            )
        except Exception:  # noqa: BLE001 - one bad tool must not block the others
            logger.warning("hermes-widget: failed to register tool %s", name, exc_info=True)


# ---------------------------------------------------------------------------
# Slash command
# ---------------------------------------------------------------------------

def _register_slash_command(ctx: Any) -> None:
    register_command = getattr(ctx, "register_command", None)
    if register_command is None:
        return
    try:
        register_command(
            "widget",
            _slash_command,
            description="Show Hermes home-screen widget status (stored widgets and paired devices).",
        )
    except Exception:  # noqa: BLE001
        logger.warning("hermes-widget: failed to register slash command", exc_info=True)


def _slash_command(raw_args: str = "") -> str:
    """Slash-command entrypoint; args are ignored, the handler reports status."""
    return tools.widget_status()


# ---------------------------------------------------------------------------
# CLI command (hermes widget ...)
# ---------------------------------------------------------------------------

def _register_cli_command(ctx: Any) -> None:
    register_cli_command = getattr(ctx, "register_cli_command", None)
    if register_cli_command is None:
        return
    setup_fn, handler_fn = _resolve_cli_hooks()
    try:
        register_cli_command(
            name="widget",
            help="Hermes home-screen widget (serve, setup, code, status, devices)",
            setup_fn=setup_fn,
            handler_fn=handler_fn,
            description="Manage the on-device Hermes widget: pair devices, serve layouts, inspect status.",
        )
    except Exception:  # noqa: BLE001
        logger.warning("hermes-widget: failed to register CLI command", exc_info=True)


def _resolve_cli_hooks() -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Return the real cli.add_parser/dispatch, or inert fallbacks.

    The import is deferred so a missing or broken ``cli`` module (for example a
    partial install) degrades to a no-op subcommand instead of disabling the
    agent tools.
    """
    try:
        from . import cli
        setup_fn = getattr(cli, "add_parser", None)
        handler_fn = getattr(cli, "dispatch", None)
        if setup_fn is None or handler_fn is None:
            raise AttributeError("cli module is missing add_parser/dispatch")
        return setup_fn, handler_fn
    except Exception:  # noqa: BLE001
        logger.warning(
            "hermes-widget: cli module unavailable; registering inert CLI hooks",
            exc_info=True,
        )
        return _cli_add_parser_fallback, _cli_dispatch_fallback


def _cli_add_parser_fallback(parser: Any, *_args: Any, **_kwargs: Any) -> None:
    """No-op parser setup so ``hermes widget`` still resolves."""
    with contextlib.suppress(Exception):
        parser.add_argument(
            "--status",
            action="store_true",
            help="Print widget status (fallback when the widget CLI module is unavailable).",
        )


def _cli_dispatch_fallback(args: Any = None, *_rest: Any, **_kwargs: Any) -> int:
    """Explain the degraded state and return a non-zero exit code."""
    print(
        "hermes-widget: the widget CLI module could not be imported from this install.\n"
        "The agent tools are still registered; run 'hermes plugin list' to confirm status."
    )
    return 1


# ---------------------------------------------------------------------------
# Bundled skill
# ---------------------------------------------------------------------------

def _register_skill(ctx: Any) -> None:
    register_skill = getattr(ctx, "register_skill", None)
    if register_skill is None:
        return
    try:
        register_skill("widget", SKILL_PATH, description=_SKILL_DESCRIPTION)
    except FileNotFoundError:
        logger.warning("hermes-widget: bundled skill missing at %s; skipping", SKILL_PATH)
    except Exception:  # noqa: BLE001
        logger.warning("hermes-widget: failed to register bundled skill", exc_info=True)
