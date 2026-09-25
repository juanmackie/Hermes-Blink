"""Background refresh plumbing for the hermes-widget plugin.

The widget only changes when the agent pushes a layout, so that the user does not
have to open a chat, this module installs the bundled skill into the user's skill
tree and registers a Hermes cron job whose prompt asks the agent to rebuild the
widget from what it already knows about the user.

Every cron import is deferred: importing this module must never fail outside a
running Hermes process (unit tests, the Android build, a bare Python shell).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
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
BUNDLED_HOOK = PLUGIN_DIR / "gateway_hook.py"
HOOK_NAME = "hermes-widget-startup"


def _atomic_text(path: Path, text: str, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
        with contextlib.suppress(OSError):
            os.chmod(path, mode)
    return path


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        configured_home = os.environ.get("HERMES_HOME")
        if configured_home:
            return Path(configured_home)
        return Path.home() / ".hermes"


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
    _atomic_text(target, BUNDLED_SKILL.read_text(encoding="utf-8"))
    logger.info("hermes-widget: installed skill at %s", target)
    return target


def install_startup_hook() -> Path:
    """Install one idempotent gateway:startup hook for the widget server."""
    if not BUNDLED_HOOK.is_file():
        raise FileNotFoundError(f"bundled gateway hook missing at {BUNDLED_HOOK}")
    target = _hermes_home() / "hooks" / HOOK_NAME
    manifest = (
        "name: hermes-widget-startup\n"
        "description: Start the personal Hermes widget server once on gateway startup.\n"
        "events: [gateway:startup]\n"
    )
    _atomic_text(target / "HOOK.yaml", manifest)
    _atomic_text(target / "handler.py", BUNDLED_HOOK.read_text(encoding="utf-8"))
    return target


def write_server_config(host: str = "127.0.0.1", port: int = 8788) -> Path:
    """Write the non-secret runtime config consumed by the startup hook."""
    home = _hermes_home()
    binary = os.environ.get("HERMES_BIN") or shutil.which("hermes") or "hermes"
    target = home / "widget" / "server.json"
    value = {"hermesBin": binary, "home": str(home), "host": host, "port": port}
    _atomic_text(target, json.dumps(value, indent=2, sort_keys=True) + "\n")
    return target


def routine_prompt(widget_id: str = store.DEFAULT_WIDGET_ID) -> str:
    """The unattended prompt that makes the agent rebuild the widget."""
    return (
        "Refresh the user's home-screen Hermes widget. "
        f"Load the widget skill with skill_view('{SKILL_NAME}') first, then decide whether "
        "the context already contains a genuinely useful update for the user right now: "
        "recent sessions, memory, or a connected calendar, email, or task source. "
        "If and only if there is a useful, supported update, call widget_publish with "
        f"widget_id='{widget_id}', a truthful title and summary, and exactly one of text, "
        "safe static SVG, or a bounded local PNG/JPEG/WebP path. Never invent data or filler, "
        "never republish an identical revision, and do nothing when nothing useful changed. "
        "A successful publish means the host stored a revision, not that the phone rendered "
        "or the user noticed it. This runs unattended, so do not ask the user a question."
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
    existing_jobs = [
        job for job in jobs.list_jobs() if job.get("name") == name
    ]
    existing = existing_jobs[0] if existing_jobs else None
    if existing is not None:
        updated = jobs.update_job(existing["id"], updates)
        for duplicate in existing_jobs[1:]:
            jobs.remove_job(duplicate["id"])
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
    existing_jobs = [job for job in jobs.list_jobs() if job.get("name") == name]
    removed = False
    for job in existing_jobs:
        removed = bool(jobs.remove_job(job["id"])) or removed
    return removed
