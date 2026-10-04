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
import importlib
import json
import logging
import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
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
_REFRESH_LOCK = threading.Lock()
_REFRESH_PROCESS: subprocess.Popen[Any] | None = None
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8788

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
    return store._hermes_home()


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


def remove_skill_file() -> bool:
    """Remove only the exact bundled skill copy installed by this plugin."""
    target = skill_target_path()
    if not target.exists():
        return False
    if not target.is_file() or target.read_text(encoding="utf-8") != BUNDLED_SKILL.read_text(encoding="utf-8"):
        raise RuntimeError(f"preserving modified skill file: {target}")
    target.unlink()
    with contextlib.suppress(OSError):
        target.parent.rmdir()
    return True


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


def remove_startup_hook() -> bool:
    """Remove the startup hook only while its installed files still match this plugin."""
    target = _hermes_home() / "hooks" / HOOK_NAME
    if not target.exists():
        return False
    manifest = (
        "name: hermes-widget-startup\n"
        "description: Start the personal Hermes widget server once on gateway startup.\n"
        "events: [gateway:startup]\n"
    )
    expected = {
        "HOOK.yaml": manifest,
        "handler.py": BUNDLED_HOOK.read_text(encoding="utf-8"),
    }
    for name, content in expected.items():
        path = target / name
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            raise RuntimeError(f"preserving modified startup hook file: {path}")
    extras = [path for path in target.iterdir() if path.name not in expected]
    if extras:
        raise RuntimeError(f"preserving startup hook directory with additional files: {target}")
    for name in expected:
        (target / name).unlink()
    target.rmdir()
    return True


class ServerConfigError(RuntimeError):
    """The saved non-secret server config exists but cannot be trusted."""


def server_config_path() -> Path:
    return _hermes_home() / "widget" / "server.json"


def read_server_config() -> dict[str, Any] | None:
    """Return the saved server config, or None when it has not been written.

    Raises ServerConfigError when the file exists but is not a JSON object, so a
    binding update fails loudly instead of silently replacing operator settings.
    """
    path = server_config_path()
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ServerConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ServerConfigError(f"{path} must contain a JSON object")
    return value


def _port_or_default(raw: Any, default: int = DEFAULT_PORT) -> int:
    if isinstance(raw, bool):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if 1 <= value <= 65535 else default


def resolve_server_binding(
    host: str | None = None, port: int | None = None
) -> tuple[str, int, dict[str, Any]]:
    """Resolve the effective bind from explicit flags and the saved config.

    Explicit values always win; an omitted field keeps the saved value
    independently; a first install falls back to 127.0.0.1:8788. The saved
    config is returned so callers can tell whether an explicit change needs a
    restart.
    """
    saved = read_server_config() or {}
    resolved_host = str(host) if host else str(saved.get("host") or DEFAULT_HOST)
    resolved_port = _port_or_default(port) if port else _port_or_default(saved.get("port"))
    return resolved_host, resolved_port, saved


def write_server_config(host: str | None = None, port: int | None = None) -> Path:
    """Write the non-secret runtime config consumed by the startup hook.

    Omitted host/port keep the saved values; the saved Hermes executable and
    home are preserved so a binding update never rewrites the operator's paths.
    """
    resolved_host, resolved_port, saved = resolve_server_binding(host, port)
    home = _hermes_home()
    binary = str(
        saved.get("hermesBin")
        or os.environ.get("HERMES_BIN")
        or shutil.which("hermes")
        or "hermes"
    )
    saved_home = str(saved.get("home") or home)
    target = server_config_path()
    value = {
        "hermesBin": binary,
        "home": saved_home,
        "host": resolved_host,
        "port": resolved_port,
    }
    _atomic_text(target, json.dumps(value, indent=2, sort_keys=True) + "\n")
    return target


def routine_prompt(widget_id: str = store.DEFAULT_WIDGET_ID) -> str:
    """The unattended prompt that makes the agent rebuild the widget."""
    return (
        f"Refresh the user's home-screen Hermes widget '{widget_id}'. "
        f"The '{SKILL_NAME}' skill is attached; use its rules without loading it twice. "
        "First call widget_status with consume_update_requests=true and the widget_id. "
        "Read workContext, its presentation, sources, session reference, finalResult, and "
        "recheck instructions before selecting checks. This is a fresh cron session; "
        "workContext is the deliberate handoff from recent work. If refreshLease is busy, "
        "stop: another run owns the requests. Otherwise retain its refreshId. Recheck "
        "the cited sources using enabled tools. Publish a meaningful finding, comparison, "
        "blocker, or milestone with structured presentation data and work_context when "
        "the evidence changes. Preview at inventory geometry first. Pass refresh_id to "
        "widget_publish to complete the refresh atomically. Use widget_finish_refresh "
        "with outcome=unchanged and a reason after checks find no useful change, or "
        "outcome=failed and a concrete reason if sources cannot be rechecked. Keep existing "
        "data and timestamps when sources are unavailable; never invent fresher data. "
        "A Request update tap requires an explicit outcome. Normal work selects useful "
        "updates autonomously; this six-hour run is a fallback. Custom SVG and local "
        "PNG/JPEG/WebP remain supported. Use high priority for time-sensitive content. "
        "widget_watch operation=tick can evaluate bounded source snapshots; resolve "
        "queued actions through widget_read_intents/widget_resolve_intent. Publishing "
        "does not prove delivery or visibility; inspect widget_status receipts."
    )


def _cron_jobs() -> Any:
    try:
        # Optional Hermes-host module; deferred so importing this module never fails
        # outside a running Hermes process (unit tests, the Android build, a bare shell).
        return importlib.import_module("cron").jobs
    except Exception as exc:  # pragma: no cover - only outside a Hermes install
        raise RuntimeError(
            "the Hermes cron module is unavailable; run this from a Hermes installation"
        ) from exc


def trigger_refresh() -> dict[str, Any]:
    """Ask the existing widget routine to run now; the agent chooses the content."""
    global _REFRESH_PROCESS
    binary = os.environ.get("HERMES_BIN") or shutil.which("hermes")
    if not binary:
        return {"triggered": False, "error": "hermes executable is not available"}
    with _REFRESH_LOCK:
        if _REFRESH_PROCESS is not None and _REFRESH_PROCESS.poll() is None:
            return {"triggered": True, "duplicate": True, "pid": _REFRESH_PROCESS.pid, "job": ROUTINE_NAME}
        try:
            started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            process = subprocess.Popen(
                [str(binary), "cron", "run", ROUTINE_NAME],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=(os.name != "nt"),
            )
            _REFRESH_PROCESS = process
            # Reap the child after it exits without blocking the HTTP request that
            # initiated the background refresh.
            threading.Thread(
                target=_reap_refresh, args=(process, started_at),
                name="hermes-widget-refresh-reaper", daemon=True,
            ).start()
            return {"triggered": True, "pid": process.pid, "job": ROUTINE_NAME}
        except OSError as exc:
            return {"triggered": False, "error": str(exc)}


def _reap_refresh(process: subprocess.Popen[Any], started_at: str) -> None:
    global _REFRESH_PROCESS
    try:
        return_code = process.wait()
    finally:
        with _REFRESH_LOCK:
            if _REFRESH_PROCESS is process:
                _REFRESH_PROCESS = None
        # Only queue work that arrived during this run. This avoids repeatedly
        # launching the same run when it exits without consuming its request.
        try:
            if return_code == 0 and store.has_unconsumed_update_requests(created_after=started_at):
                trigger_refresh()
        except Exception:
            logger.warning("could not schedule queued widget update request", exc_info=True)


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
        "enabled_toolsets": ["hermes-widget", "web", "file", "terminal"],
    }
    existing_jobs = [
        job for job in jobs.list_jobs() if job.get("name") == name
    ]
    existing = existing_jobs[0] if existing_jobs else None
    if existing is not None:
        updated = jobs.update_job(existing["id"], updates)
        for duplicate in existing_jobs[1:]:
            jobs.remove_job(duplicate["id"])
        result = updated or existing
        if "hermes-widget" not in result.get("enabled_toolsets", []):
            raise RuntimeError("refresh job must enable the hermes-widget toolset")
        return result
    created = jobs.create_job(
        prompt=prompt,
        schedule=schedule,
        name=name,
        skills=[SKILL_NAME],
        deliver="local",
        enabled_toolsets=updates["enabled_toolsets"],
    )
    if "hermes-widget" not in created.get("enabled_toolsets", []):
        raise RuntimeError("refresh job must enable the hermes-widget toolset")
    return created


def remove_routine(name: str = ROUTINE_NAME) -> bool:
    """Remove the refresh job. False when none was installed."""
    jobs = _cron_jobs()
    existing_jobs = [job for job in jobs.list_jobs() if job.get("name") == name]
    removed = False
    for job in existing_jobs:
        removed = bool(jobs.remove_job(job["id"])) or removed
    return removed
