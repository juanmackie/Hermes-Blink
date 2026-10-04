"""ADB-over-USB helpers for Hermes Blink local development.

Stdlib-only wrapper around the platform-tools ``adb`` binary. All functions
raise :class:`AdbError` with a human-readable message instead of leaking
subprocess details, so CLI and agent surfaces can print the message directly.

USB workflow this enables (Pixel on USB, no Tailscale needed for dev):

* ``adb devices`` -> confirm the phone is on USB (``transport: usb``)
* ``adb -s <serial> reverse tcp:8788 tcp:8788`` -> the phone reaches the host
  loopback server at ``http://127.0.0.1:8788`` over the USB cable.
  Debug builds allow cleartext (``usesCleartextTraffic`` is debug-only);
  release builds still require private HTTPS.
* ``adb install -r app-debug.apk`` -> install, then ``am start`` the pair screen.
* ``adb logcat`` -> streamed or dumped diagnostics, redacted by the caller.

Only ``reverse`` (host -> device loopback) is the recommended dev transport.
``forward`` (device -> host) is exposed for completeness (emulator cases).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


class AdbError(RuntimeError):
    """Raised when adb is missing or a command fails."""


def find_adb() -> Path | None:
    """Locate the adb binary, honouring ADB / ANDROID_ADB env overrides."""
    for env_key in ("ADB", "ANDROID_ADB"):
        configured = os.environ.get(env_key, "").strip()
        if configured:
            candidate = Path(configured).expanduser()
            if candidate.is_file():
                return candidate
    which = shutil.which("adb")
    if which:
        return Path(which)
    # Common SDK layouts when adb is not on PATH (Windows WinGet + SDK cases).
    candidates: list[Path] = []
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if sdk:
        candidates.append(Path(sdk).expanduser() / "platform-tools" / "adb.exe")
        candidates.append(Path(sdk).expanduser() / "platform-tools" / "adb")
    home = Path.home()
    candidates.extend(
        [
            home / "android-tools" / "sdk" / "platform-tools" / "adb.exe",
            home / "android-tools" / "sdk" / "platform-tools" / "adb",
            home / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe",
            Path(r"C:\Users\juanm\android-tools\sdk\platform-tools\adb.exe"),
        ]
    )
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def require_adb() -> Path:
    """Return the adb path or raise with install guidance."""
    found = find_adb()
    if found is None:
        raise AdbError(
            "adb not found on PATH. Install Android platform-tools "
            "(https://developer.android.com/tools/adb) and retry. "
            "Set ADB=/path/to/adb to override."
        )
    return found


def run_adb(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run ``adb <args>`` and return the completed process or raise AdbError."""
    binary = str(require_adb())
    try:
        return subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdbError(f"adb {' '.join(args)} failed: {exc}") from exc


def _check(result: subprocess.CompletedProcess[str], what: str) -> str:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        first = detail[0] if detail else f"exit {result.returncode}"
        raise AdbError(f"{what} failed: {first}")
    return result.stdout


_DEVICE_LINE = re.compile(r"^(\S+)\s+(\S+)(.*)$")


def parse_devices(output: str) -> list[dict[str, str]]:
    """Parse ``adb devices -l`` output into serial/state/detail rows."""
    devices: list[dict[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        match = _DEVICE_LINE.match(line)
        if not match:
            continue
        serial, state, rest = match.groups()
        if serial == "*":
            continue
        info: dict[str, str] = {"serial": serial, "state": state}
        for key in ("product", "model", "device", "transport_id", "transport"):
            found = re.search(rf"{key}:(\S+)", rest)
            if found:
                info[key] = found.group(1).replace("_", " ")
        devices.append(info)
    return devices


def list_devices() -> list[dict[str, str]]:
    """Return USB/network devices visible to adb (offline rows included)."""
    result = run_adb(["devices", "-l"])
    _check(result, "adb devices")
    return parse_devices(result.stdout)


def online_devices() -> list[dict[str, str]]:
    """Return devices in ``device`` state (usable for install/reverse)."""
    return [d for d in list_devices() if d.get("state") == "device"]


def ensure_device(serial: str | None = None) -> str:
    """Resolve one usable serial or raise with actionable guidance."""
    devices = online_devices()
    if serial:
        if any(d["serial"] == serial for d in devices):
            return serial
        known = ", ".join(d["serial"] for d in devices) or "none"
        raise AdbError(
            f"device {serial!r} is not online (online: {known}). "
            "Check USB debugging + cable, then `adb devices`."
        )
    if not devices:
        raise AdbError(
            "no online adb device. Enable USB debugging on the phone, "
            "connect via USB, accept the RSA prompt, then retry."
        )
    if len(devices) > 1:
        serials = ", ".join(d["serial"] for d in devices)
        raise AdbError(
            f"multiple online devices ({serials}); pass --device <serial>."
        )
    return devices[0]["serial"]


def reverse(
    host_port: int = 8788,
    device_port: int | None = None,
    *,
    serial: str | None = None,
    remove: bool = False,
) -> dict[str, object]:
    """Expose host loopback to the phone over USB (or remove it)."""
    target = ensure_device(serial)
    device_port = device_port or host_port
    spec = f"tcp:{device_port}"
    host_spec = f"tcp:{host_port}"
    if remove:
        result = run_adb(["-s", target, "reverse", "--remove", spec])
        _check(result, "adb reverse --remove")
        return {"ok": True, "removed": True, "serial": target, "spec": spec}
    result = run_adb(["-s", target, "reverse", spec, host_spec])
    _check(result, "adb reverse")
    return {
        "ok": True,
        "serial": target,
        "deviceUrl": f"http://127.0.0.1:{device_port}",
        "hostPort": host_port,
        "devicePort": device_port,
        "note": "Debug builds only: release forbids cleartext; use Tailscale HTTPS for release.",
    }


def reverse_list(*, serial: str | None = None) -> list[str]:
    """Return active ``adb reverse`` specs for one device."""
    target = ensure_device(serial)
    result = run_adb(["-s", target, "reverse", "--list"])
    _check(result, "adb reverse --list")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def install_apk(
    apk: str | Path, *, serial: str | None = None, reinstall: bool = True
) -> dict[str, object]:
    """Install (or reinstall) an APK over USB."""
    target = ensure_device(serial)
    path = Path(apk).expanduser()
    if not path.is_file():
        raise AdbError(f"APK not found: {path}")
    if path.suffix.lower() != ".apk":
        raise AdbError(f"not an APK: {path}")
    args = ["-s", target, "install"]
    if reinstall:
        args.append("-r")
    args.append(str(path))
    result = run_adb(args, timeout=180)
    combined = (result.stdout + "\n" + result.stderr).strip()
    lines = [ln.strip() for ln in combined.splitlines() if ln.strip()]
    failure = next((ln for ln in lines if "Failure" in ln), None)
    if result.returncode != 0 or failure or "Failure" in combined:
        detail = failure or (lines[-1] if lines else "install failed")
        hint = ""
        if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in combined:
            hint = " Signatures differ (release vs debug): `adb uninstall com.you.hermeswidget`, then reinstall and re-pair."
        raise AdbError(f"adb install failed on {target}: {detail}.{hint}")
    ok_line = next((ln for ln in reversed(lines) if "Success" in ln), lines[-1] if lines else "Success")
    return {"ok": True, "serial": target, "apk": str(path), "output": [ok_line]}


PACKAGE = "com.you.hermeswidget"


def start_app(
    *, serial: str | None = None, activity: str = ".MainActivity"
) -> dict[str, object]:
    """Launch the Hermes app on the USB device."""
    target = ensure_device(serial)
    component = f"{PACKAGE}/{activity}" if "/" not in activity else activity
    if component.startswith("."):
        component = PACKAGE + "/" + component
    elif "/" not in component:
        component = f"{PACKAGE}/{component}"
    result = run_adb(["-s", target, "shell", "am", "start", "-n", component])
    _check(result, "am start")
    return {"ok": True, "serial": target, "component": component}


def app_version(
    *, serial: str | None = None, package: str = PACKAGE
) -> dict[str, str | None]:
    """Read installed versionName/versionCode via dumpsys (None when absent)."""
    target = ensure_device(serial)
    result = run_adb(["-s", target, "shell", "dumpsys", "package", package])
    _check(result, "dumpsys package")
    name = re.search(r"versionName=([^\s]+)", result.stdout)
    code = re.search(r"versionCode=(\d+)", result.stdout)
    return {
        "serial": target,
        "package": package,
        "versionName": name.group(1) if name else None,
        "versionCode": code.group(1) if code else None,
        "installed": bool(name or code),
    }


def shell(args: list[str], *, serial: str | None = None) -> str:
    """Run ``adb shell <args>`` and return stdout."""
    target = ensure_device(serial)
    result = run_adb(["-s", target, "shell", *args])
    return _check(result, "adb shell")


def usb_status(host_port: int = 8788) -> dict[str, object]:
    """One snapshot for doctor/usb-up: adb binary, devices, reverse specs."""
    try:
        binary = str(require_adb())
    except AdbError as exc:
        return {"adb": None, "ok": False, "error": str(exc)}
    try:
        devices = list_devices()
    except AdbError as exc:
        return {"adb": binary, "ok": False, "error": str(exc)}
    online = [d for d in devices if d.get("state") == "device"]
    reverses: dict[str, object] = {}
    for device in online:
        try:
            reverses[device["serial"]] = reverse_list(serial=device["serial"])
        except AdbError as exc:
            reverses[device["serial"]] = f"error: {exc}"
    host_spec = f"tcp:{host_port}"
    usb_ready = any(
        isinstance(specs, list) and any(host_spec in str(s) for s in specs)
        for specs in reverses.values()
    )
    return {
        "adb": binary,
        "ok": True,
        "devices": devices,
        "onlineCount": len(online),
        "reverses": reverses,
        "hostPort": host_port,
        "usbReady": usb_ready,
        "deviceUrl": f"http://127.0.0.1:{host_port}",
    }
