#!/usr/bin/env python3
"""Capability-based persistent installer for the Hermes widget plugin."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = REPO / "hermes-plugin" / "hermes-widget"
PLUGIN_NAME = "hermes-widget"
HOOK_NAME = "hermes-widget-startup"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8788


class BootstrapError(RuntimeError):
    pass


def run_command(
    command: list[str],
    *,
    timeout: int = 30,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError(f"{command[0]} failed: {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise BootstrapError(f"{' '.join(command)} exited {result.returncode}: {detail}")
    return result


def discover_hermes(explicit: str | None) -> Path:
    candidate = explicit or os.environ.get("HERMES_BIN") or shutil.which("hermes")
    if not candidate:
        raise BootstrapError(
            "Hermes executable not found; set HERMES_BIN to the actual hermes command"
        )
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        resolved = shutil.which(str(path))
        if not resolved:
            raise BootstrapError(f"Hermes executable is not runnable: {path}")
        path = Path(resolved)
    if not path.is_file() and os.name == "nt":
        for suffix in (".exe", ".cmd", ".bat"):
            candidate = path.with_name(path.name + suffix)
            if candidate.is_file():
                path = candidate
                break
    if not path.is_file():
        raise BootstrapError(f"Hermes executable does not exist: {path}")
    return path.resolve()


def detect_profile(hermes: Path) -> str:
    env_profile = os.environ.get("HERMES_PROFILE")
    if env_profile:
        return env_profile
    result = run_command([str(hermes), "profile", "list"], check=False)
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            match = re.match(r"\s*[◆✓]?\s*([A-Za-z0-9._-]+)\s+", line)
            if match and ("◆" in line or match.group(1) == "default"):
                return match.group(1)
    return "default"


def detect_home(hermes: Path) -> Path:
    result = run_command([str(hermes), "config", "path"], check=False)
    if result.returncode == 0:
        raw = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        if raw:
            return Path(raw).expanduser().resolve().parent
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    raise BootstrapError("could not detect Hermes home from `hermes config path`")


def detect_capabilities(hermes: Path) -> dict[str, Any]:
    version = run_command([str(hermes), "--version"], check=False)
    tools = run_command([str(hermes), "tools", "list"], check=False)
    toolset_enabled = any(
        "hermes-widget" in line and "enabled" in line.lower()
        for line in tools.stdout.splitlines()
    )
    commands = {
        name: run_command([str(hermes), "plugins", name, "--help"], check=False).returncode == 0
        for name in ("enable", "install", "list")
    }
    commands["profile"] = run_command([str(hermes), "profile", "list", "--help"], check=False).returncode == 0
    commands["gateway"] = run_command([str(hermes), "gateway", "status"], check=False).returncode == 0
    commands["cron"] = run_command([str(hermes), "cron", "list"], check=False).returncode == 0
    return {
        "version": (version.stdout or version.stderr).strip(),
        "toolsetEnabled": toolset_enabled,
        "commands": commands,
        "toolsetOutput": tools.stdout if tools.returncode == 0 else "",
    }


def atomic_write(path: Path, text: str, mode: int = 0o600) -> bool:
    changed = not path.is_file() or path.read_text(encoding="utf-8") != text
    if not changed:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(temporary, mode)
    os.replace(temporary, path)
    return True


def remove_tree(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        return None


def restore_previous(target: Path, backup: Path) -> None:
    if not target.exists() and backup.exists():
        backup.replace(target)


def install_plugin(home: Path) -> Path:
    if not (PLUGIN_SOURCE / "plugin.yaml").is_file():
        raise BootstrapError(f"plugin source is missing: {PLUGIN_SOURCE}")
    target = home / "plugins" / PLUGIN_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + ".tmp")
    backup = target.with_name(target.name + ".previous")
    remove_tree(staging)
    remove_tree(backup)
    shutil.copytree(
        PLUGIN_SOURCE,
        staging,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "_testdata"),
    )
    try:
        if target.exists():
            target.replace(backup)
        staging.replace(target)
    except Exception:
        restore_previous(target, backup)
        raise
    finally:
        remove_tree(staging)
        remove_tree(backup)
    return target


def install_skill(home: Path) -> Path:
    source = PLUGIN_SOURCE / "skills" / "widget" / "SKILL.md"
    if not source.is_file():
        raise BootstrapError(f"bundled skill is missing: {source}")
    target = home / "skills" / PLUGIN_NAME / "SKILL.md"
    atomic_write(target, source.read_text(encoding="utf-8"))
    return target


def install_hook(home: Path) -> Path:
    source = PLUGIN_SOURCE / "gateway_hook.py"
    if not source.is_file():
        raise BootstrapError(f"gateway hook source is missing: {source}")
    target = home / "hooks" / HOOK_NAME
    manifest = (
        "name: hermes-widget-startup\n"
        "description: Start the personal Hermes widget server once on gateway startup.\n"
        "events: [gateway:startup]\n"
    )
    atomic_write(target / "HOOK.yaml", manifest)
    atomic_write(target / "handler.py", source.read_text(encoding="utf-8"))
    return target


def read_server_config(home: Path) -> dict[str, Any]:
    """Return the saved server config; raise when it exists but is unusable."""
    target = home / "widget" / "server.json"
    if not target.is_file():
        return {}
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BootstrapError(f"{target} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise BootstrapError(f"{target} must contain a JSON object")
    return value


def _port_or_default(raw: Any, default: int = DEFAULT_PORT) -> int:
    if isinstance(raw, bool):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if 1 <= value <= 65535 else default


def resolve_binding(
    home: Path, host: str | None, port: int | None
) -> tuple[str, int, dict[str, Any]]:
    """Explicit flags win; omitted fields keep saved values; first install is loopback."""
    saved = read_server_config(home)
    resolved_host = host or str(saved.get("host") or DEFAULT_HOST)
    resolved_port = _port_or_default(port) if port else _port_or_default(saved.get("port"))
    return str(resolved_host), resolved_port, saved


def write_server_config(
    home: Path, hermes: Path, host: str | None, port: int | None
) -> Path:
    resolved_host, resolved_port, saved = resolve_binding(home, host, port)
    target = home / "widget" / "server.json"
    saved_home = str(saved.get("home") or home)
    if Path(saved_home).resolve() != home.resolve():
        # This installer owns the home it detected; a saved path from another home
        # would point the startup hook at the wrong directory.
        saved_home = str(home)
    value = {
        "hermesBin": str(saved.get("hermesBin") or hermes),
        "home": saved_home,
        "host": resolved_host,
        "port": resolved_port,
    }
    atomic_write(target, json.dumps(value, indent=2, sort_keys=True) + "\n")
    return target


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def health_ok(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/v1/health", timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def load_gateway_hook() -> Any:
    """Import the gateway hook that this installer also copies into the Hermes home.

    The hook owns both duplicate-start protection and platform detachment. On Windows
    a plain DETACHED_PROCESS child still belongs to the installing shell's job object
    and dies with it, so starting the server here would produce a widget that stops
    working as soon as the installing session exits.
    """
    source = PLUGIN_SOURCE / "gateway_hook.py"
    if not source.is_file():
        raise BootstrapError(f"gateway hook source is missing: {source}")
    spec = importlib.util.spec_from_file_location("hermes_widget_gateway_hook", source)
    if spec is None or spec.loader is None:
        raise BootstrapError(f"could not load gateway hook: {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def start_server(home: Path, hermes: Path, host: str, port: int) -> dict[str, Any]:
    if port_open(host, port):
        if not health_ok(host, port):
            raise BootstrapError(f"{host}:{port} is occupied by a non-Hermes service")
        return {"state": "already-running", "host": host, "port": port}
    widget_dir = home / "widget"
    widget_dir.mkdir(parents=True, exist_ok=True)
    log_path = widget_dir / "server.log"

    previous_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(home)
    try:
        load_gateway_hook().handle("gateway:startup")
    finally:
        if previous_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = previous_home

    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if health_ok(host, port):
            pid: int | None = None
            try:
                record = json.loads(
                    (widget_dir / "server-process.json").read_text(encoding="utf-8")
                )
                value = record.get("pid") if isinstance(record, dict) else None
                pid = value if isinstance(value, int) else None
            except (OSError, ValueError, TypeError):
                pid = None
            return {"state": "started", "pid": pid, "host": host, "port": port}
        time.sleep(0.1)

    detail = ""
    with contextlib.suppress(OSError):
        detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    raise BootstrapError(f"widget server did not become healthy; log tail:\n{detail}")


def systemd_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def install_systemd(hermes: Path, home: Path, host: str, port: int) -> dict[str, Any]:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return {"available": False, "reason": "systemctl not found"}
    probe = run_command([systemctl, "--user", "show-environment"], timeout=5, check=False)
    if probe.returncode != 0:
        return {"available": False, "reason": "systemd user manager unavailable"}
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    unit = config_home / "systemd" / "user" / "hermes-widget.service"
    text = (
        "[Unit]\nDescription=Hermes personal widget server\nAfter=network-online.target\n\n"
        "[Service]\nType=simple\n"
        f"ExecStart={systemd_quote(str(hermes))} widget serve --host {host} --port {port}\n"
        f"Environment=HERMES_HOME={systemd_quote(str(home))}\n"
        f"WorkingDirectory={systemd_quote(str(home))}\n"
        "Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n"
    )
    changed = atomic_write(unit, text, mode=0o644)
    if port_open(host, port):
        return {"available": True, "installed": str(unit), "changed": changed, "state": "already-running"}
    run_command([systemctl, "--user", "daemon-reload"])
    run_command([systemctl, "--user", "enable", "--now", "hermes-widget.service"])
    return {"available": True, "installed": str(unit), "changed": changed, "state": "enabled"}


def enable_plugin(hermes: Path) -> None:
    run_command(
        [str(hermes), "plugins", "enable", PLUGIN_NAME, "--no-allow-tool-override"],
        timeout=30,
    )


def install_routine(hermes: Path, schedule: str) -> None:
    run_command(
        [str(hermes), "widget", "routine", "--schedule", schedule],
        timeout=30,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-bin")
    parser.add_argument("--host", default=None, help="Interface to bind (default: saved config or 127.0.0.1).")
    parser.add_argument("--port", type=int, default=None, help="Port to bind (default: saved config or 8788).")
    parser.add_argument("--schedule", default="every 6h")
    parser.add_argument("--skip-start", action="store_true")
    parser.add_argument("--skip-routine", action="store_true")
    parser.add_argument("--restart-gateway", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    report: dict[str, Any] = {
        "ok": False,
        "dryRun": args.dry_run,
    }
    try:
        hermes = discover_hermes(args.hermes_bin)
        profile = detect_profile(hermes)
        home = detect_home(hermes)
        host, port, _saved = resolve_binding(home, args.host, args.port)
        capabilities = detect_capabilities(hermes)
        report.update(
            {
                "hermesBin": str(hermes),
                "version": capabilities["version"],
                "profile": profile,
                "home": str(home),
                "host": host,
                "port": port,
                "toolsetEnabledBefore": capabilities["toolsetEnabled"],
                "capabilities": capabilities["commands"],
            }
        )
        if args.dry_run:
            report["ok"] = True
        else:
            plugin = install_plugin(home)
            enable_plugin(hermes)
            skill = install_skill(home)
            hook = install_hook(home)
            config = write_server_config(home, hermes, args.host, args.port)
            report["installed"] = {
                "plugin": str(plugin),
                "skill": str(skill),
                "gatewayHook": str(hook),
                "serverConfig": str(config),
            }
            if not args.skip_routine:
                install_routine(hermes, args.schedule)
                report["routine"] = args.schedule
            report["systemd"] = install_systemd(hermes, home, host, port)
            if not args.skip_start:
                report["server"] = start_server(home, hermes, host, port)
            if args.restart_gateway:
                run_command([str(hermes), "gateway", "restart"], timeout=60)
                report["gatewayRestarted"] = True
            report["toolsetEnabledAfter"] = detect_capabilities(hermes)["toolsetEnabled"]
            report["ok"] = True
    except (BootstrapError, OSError, ValueError, json.JSONDecodeError) as exc:
        report["error"] = str(exc)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Hermes widget bootstrap")
        for key in ("hermesBin", "version", "profile", "home", "toolsetEnabledBefore", "routine"):
            if key in report:
                print(f"{key}: {report[key]}")
        if "server" in report:
            print(f"server: {report['server']['state']} on {report.get('host')}:{report.get('port')}")
        if report.get("systemd", {}).get("available"):
            print(f"systemd: {report['systemd']['state']}")
        if report["ok"]:
            print("Bootstrap complete. Keep this Hermes home and the widget directory persistent.")
        else:
            print(f"Bootstrap failed: {report.get('error', 'unknown error')}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
