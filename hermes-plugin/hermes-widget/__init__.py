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
    "How to publish accessible text, safe static SVG, or validated local raster "
    "visuals to the personal Hermes widget and inspect delivery status."
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
    (schemas.WIDGET_SET_QUIET_HOURS, tools.widget_set_quiet_hours),
    (schemas.WIDGET_STATUS, tools.widget_status),
)


def register(ctx: Any) -> None:
    """Wire the widget tools, slash command, CLI command, and skill into Hermes."""
    _register_tools(ctx)
    _register_slash_command(ctx)
    _register_cli_command(ctx)
    _register_skill(ctx)
    _register_context_reminder(ctx)


def _register_context_reminder(ctx: Any) -> None:
    """Make the one-way publishing surface discoverable during ordinary turns."""
    register_hook = getattr(ctx, "register_hook", None)
    if register_hook is None:
        return
    try:
        register_hook("pre_llm_call", _availability_reminder)
    except Exception:  # noqa: BLE001
        logger.warning("hermes-widget: failed to register availability reminder", exc_info=True)


def _availability_reminder(**_kwargs: Any) -> dict[str, str]:
    return {
        "context": (
            "Hermes widget publishing is available. When a useful user-facing update exists, "
            "call widget_publish with a title, required summary, and exactly one text, safe "
            "static SVG, or bounded local raster source; otherwise do nothing. Publishing "
            "stores a revision and does not prove phone delivery or user visibility. High "
            "priority asks for a content-free wake; queued actions are not executions."
        )
    }


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
