"""Idempotent gateway:startup launcher for the Hermes widget server.

This file is copied into the detected Hermes home by scripts/bootstrap.py. It
intentionally depends only on the Python standard library and reads its runtime
configuration from <HERMES_HOME>/widget/server.json.
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO
from urllib.request import urlopen


def _home() -> Path:
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".hermes"


def _config() -> dict[str, object]:
    path = _home() / "widget" / "server.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _health_ok(host: str, port: int) -> bool:
    try:
        with urlopen(f"http://{host}:{port}/v1/health", timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def _claim_lock(lock_path: Path) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _attempt in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                return False
            if age < 30:
                return False
            try:
                lock_path.unlink()
            except OSError:
                return False
            continue
        else:
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                stream.write(str(os.getpid()))
            return True
    return False


def _release_lock(lock_path: Path) -> None:
    with contextlib.suppress(OSError):
        lock_path.unlink()


_WINDOWS_LAUNCHER_CODE = """
import pathlib
import subprocess
import sys

pid_path = pathlib.Path(sys.argv[1])
target = sys.argv[2:]
flags = (
    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    | getattr(subprocess, "DETACHED_PROCESS", 0)
    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
)
process = subprocess.Popen(
    target,
    stdin=subprocess.DEVNULL,
    stdout=sys.stdout,
    stderr=sys.stderr,
    close_fds=True,
    creationflags=flags,
)
try:
    pid_path.write_text(str(process.pid), encoding="ascii")
except OSError:
    pass
"""


def _popen_kwargs(*, breakaway: bool = True) -> dict[str, Any]:
    """Return platform-appropriate detachment flags for a long-lived child."""
    if os.name != "nt":
        return {"start_new_session": True}
    flags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    if breakaway:
        flags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    return {"creationflags": flags}


def _spawn_server(
    binary: str,
    host: str,
    port: int,
    home: Path,
    log: BinaryIO,
) -> tuple[subprocess.Popen[Any], int | None]:
    """Start the server outside the gateway's Windows process tree.

    Windows keeps a child linked to its creator even with DETACHED_PROCESS, and
    Hermes stops the gateway with `taskkill /T`. A short-lived launcher gives
    the server a parent that exits before the gateway does; the server therefore
    survives gateway restarts while the health check still prevents duplicates.
    """
    target = [binary, "widget", "serve", "--host", host, "--port", str(port)]
    env = {**os.environ, "HERMES_HOME": str(home)}
    if os.name != "nt":
        process = subprocess.Popen(
            target,
            cwd=home,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            **_popen_kwargs(),
        )
        return process, process.pid

    pid_path = home / "widget" / "server-launch.pid"
    with contextlib.suppress(OSError):
        pid_path.unlink()
    launcher_argv = [sys.executable, "-c", _WINDOWS_LAUNCHER_CODE, str(pid_path), *target]
    try:
        launcher = subprocess.Popen(
            launcher_argv,
            cwd=home,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            **_popen_kwargs(),
        )
    except OSError:
        # Some Windows job objects forbid breakaway. The intermediate launcher
        # still detaches the long-lived process from the gateway process tree.
        launcher = subprocess.Popen(
            launcher_argv,
            cwd=home,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            **_popen_kwargs(breakaway=False),
        )

    server_pid: int | None = None
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            value = pid_path.read_text(encoding="ascii").strip()
            if value.isdigit():
                server_pid = int(value)
                break
        except OSError:
            pass
        if launcher.poll() is not None:
            break
        time.sleep(0.05)
    with contextlib.suppress(OSError):
        pid_path.unlink()
    return launcher, server_pid


def handle(event_type: str, context: object | None = None) -> None:
    """Start one detached widget server when Hermes starts its gateway."""
    if event_type != "gateway:startup":
        return
    config = _config()
    host = str(config.get("host") or "127.0.0.1")
    raw_port = config.get("port")
    try:
        port = int(raw_port) if isinstance(raw_port, (int, str)) else 8788
    except (TypeError, ValueError):
        port = 8788
    if not 1 <= port <= 65535:
        port = 8788
    binary = str(config.get("hermesBin") or "hermes")
    home = Path(str(config.get("home") or _home()))
    if _port_open(host, port):
        return

    lock_path = home / "widget" / "startup.lock"
    if not _claim_lock(lock_path):
        return
    process = None
    try:
        if _port_open(host, port) or not _health_ok(host, port):
            log_path = home / "widget" / "server.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log = log_path.open("ab")
            try:
                process, server_pid = _spawn_server(binary, host, port, home, log)
            finally:
                log.close()
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not _health_ok(host, port):
                time.sleep(0.1)
            if not _health_ok(host, port):
                raise RuntimeError("widget server did not become healthy within 8 seconds")
            record = {
                "pid": server_pid if server_pid is not None else process.pid,
                "host": host,
                "port": port,
                "binary": binary,
                "startedAt": time.time(),
            }
            target = home / "widget" / "server-process.json"
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_text(json.dumps(record, separators=(",", ":")), encoding="utf-8")
            os.replace(temporary, target)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        try:
            with (home / "widget" / "server.log").open("a", encoding="utf-8") as stream:
                stream.write(f"widget startup hook failed: {exc}\n")
        except OSError:
            pass
    finally:
        _release_lock(lock_path)
