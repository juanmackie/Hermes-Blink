"""Background refresh plumbing for the hermes-widget plugin.

The widget only changes when the agent pushes a layout, so that the user does not
have to open a chat, this module installs the bundled skill into the user's skill
tree and registers a Hermes cron job whose prompt asks the agent to rebuild the
widget from what it already knows about the user.

Every cron import is deferred: importing this module must never fail outside a
running Hermes process (unit tests, the Android build, a bare Python shell).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

try:  # normal path: imported as part of the hermes-widget plugin package
    from . import store
except ImportError:  # pragma: no cover - direct import from tests/scripts
    import store  # type: ignore

logger = logging.getLogger(__name__)

DEFAULT_SCHEDULE = "every 6h"
ROUTINE_NAME = "hermes-widget-refresh"
SKILL_NAME = "hermes-widget"

PLUGIN_DIR = Path(__file__).resolve().parent
BUNDLED_SKILL = PLUGIN_DIR / "skills" / "widget" / "SKILL.md"


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def skill_target_path() -> Path:
    """Where the routine-visible copy of the skill lives."""
    return _hermes_home() / "skills" / SKILL_NAME / "SKILL.md"


def install_skill_file() -> Path:
    """Copy the bundled skill into the ordinary skill tree for cron resolution.

    The plugin also registers its skill as hermes-widget:widget, but that copy is
    only reachable through an explicit skill_view. A cron job resolves the names
    in its skills list from the user skill tree, so the routine needs a real file
    at <hermes home>/skills/hermes-widget/SKILL.md.
    """
    if not BUNDLED_SKILL.is_file():
        raise FileNotFoundError(f"bundled skill missing at {BUNDLED_SKILL}")
    target = skill_target_path()
    text = BUNDLED_SKILL.read_text(encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.read_text(encoding="utf-8") != text:
        target.write_text(text, encoding="utf-8")
        logger.info("hermes-widget: installed skill at %s", target)
    return target


def routine_prompt(widget_id: str = store.DEFAULT_WIDGET_ID) -> str:
    """The unattended prompt that makes the agent rebuild the widget."""
    return (
        "Refresh the user's home-screen Hermes widget. "
        f"Load the widget skill with skill_view('{SKILL_NAME}') first, then decide what the "
        "user would genuinely want to see right now from the context you already have: recent "
        "sessions, memory, and any connected calendar, email, or task sources. "
        f"Call widget_update with widget_id='{widget_id}' and a complete, schema-valid v1 "
        "layout: a short meaningful header, then two to four focused items. Keep it under 100 "
        "nodes, use https:// for any image, and never invent data or filler - a short accurate "
        "summary beats a padded one. This runs unattended, so do not ask the user a question."
    )


def _cron_jobs() -> Any:
    try:
        from cron import jobs
    except Exception as exc:  # pragma: no cover - only outside a Hermes install
        raise RuntimeError(
            "the Hermes cron module is unavailable; run this from a Hermes installation"
        ) from exc
    return jobs


def find_routine(name: str = ROUTINE_NAME) -> dict[str, Any] | None:
    """Return the installed refresh job, or None when it is not present."""
    for job in _cron_jobs().list_jobs():
        if job.get("name") == name:
            return job
    return None


def install_routine(
    schedule: str = DEFAULT_SCHEDULE,
    widget_id: str = store.DEFAULT_WIDGET_ID,
    name: str = ROUTINE_NAME,
) -> dict[str, Any]:
    """Create or update the background refresh job and return the job record.

    Idempotent: re-running updates the existing job in place rather than
    accumulating duplicates.
    """
    install_skill_file()
    jobs = _cron_jobs()
    prompt = routine_prompt(widget_id)
    updates = {
        "prompt": prompt,
        "schedule": schedule,
        "skills": [SKILL_NAME],
        "deliver": "local",
    }
    existing = find_routine(name)
    if existing is not None:
        updated = jobs.update_job(existing["id"], updates)
        return updated or existing
    return jobs.create_job(
        prompt=prompt,
        schedule=schedule,
        name=name,
        skills=[SKILL_NAME],
        deliver="local",
    )


def remove_routine(name: str = ROUTINE_NAME) -> bool:
    """Remove the refresh job. False when none was installed."""
    jobs = _cron_jobs()
    existing = find_routine(name)
    if existing is None:
        return False
    return bool(jobs.remove_job(existing["id"]))
