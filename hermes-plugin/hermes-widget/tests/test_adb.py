"""Unit tests for the ADB-over-USB helper (no device needed; subprocess mocked)."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import adb  # noqa: E402


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=["adb"], returncode=returncode, stdout=stdout, stderr=stderr)


class ParseDevicesTest(unittest.TestCase):
    def test_parses_usb_device(self):
        out = (
            "List of devices attached\n"
            "58211FDCQ007VP\tdevice product:mustang model:Pixel_10_Pro_XL device:mustang transport_id:1\n"
        )
        devices = adb.parse_devices(out)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["serial"], "58211FDCQ007VP")
        self.assertEqual(devices[0]["state"], "device")
        self.assertIn("Pixel", devices[0].get("model", ""))

    def test_skips_header_and_blank(self):
        self.assertEqual(adb.parse_devices("List of devices attached\n\n"), [])


class EnsureDeviceTest(unittest.TestCase):
    @patch.object(adb, "list_devices", return_value=[{"serial": "ABC", "state": "device"}])
    def test_single_device_resolves(self, _mock):
        self.assertEqual(adb.ensure_device(None), "ABC")

    @patch.object(adb, "list_devices", return_value=[])
    def test_no_device_raises(self, _mock):
        with self.assertRaises(adb.AdbError):
            adb.ensure_device(None)

    @patch.object(
        adb,
        "list_devices",
        return_value=[{"serial": "A", "state": "device"}, {"serial": "B", "state": "device"}],
    )
    def test_multiple_requires_serial(self, _mock):
        with self.assertRaises(adb.AdbError):
            adb.ensure_device(None)
        self.assertEqual(adb.ensure_device("B"), "B")


class ReverseTest(unittest.TestCase):
    @patch.object(adb, "ensure_device", return_value="SER1")
    @patch.object(adb, "run_adb", return_value=_completed(""))
    def test_reverse_builds_specs(self, mock_run, _mock_ensure):
        result = adb.reverse(8788, None, serial=None)
        self.assertTrue(result["ok"])
        self.assertEqual(result["deviceUrl"], "http://127.0.0.1:8788")
        args = mock_run.call_args[0][0]
        self.assertEqual(args, ["-s", "SER1", "reverse", "tcp:8788", "tcp:8788"])

    @patch.object(adb, "ensure_device", return_value="SER1")
    @patch.object(adb, "run_adb", return_value=_completed(""))
    def test_reverse_remove(self, mock_run, _mock_ensure):
        result = adb.reverse(8788, serial="SER1", remove=True)
        self.assertTrue(result["removed"])
        self.assertIn("--remove", mock_run.call_args[0][0])


class InstallTest(unittest.TestCase):
    @patch.object(adb, "ensure_device", return_value="SER1")
    @patch.object(
        adb,
        "run_adb",
        return_value=_completed(
            "Performing Streamed Install\n"
            "adb.exe: failed to install x.apk: "
            "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match]",
            returncode=1,
        ),
    )
    def test_signature_mismatch_surfaces_failure(self, _mock_run, _mock_ensure):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".apk", delete=False) as tmp:
            tmp.write(b"fake")
            path = tmp.name
        try:
            with self.assertRaises(adb.AdbError) as ctx:
                adb.install_apk(path, serial="SER1")
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertIn("INSTALL_FAILED_UPDATE_INCOMPATIBLE", str(ctx.exception))
        self.assertIn("re-pair", str(ctx.exception))


class UsbStatusTest(unittest.TestCase):
    @patch.object(adb, "require_adb", return_value=Path("/usr/bin/adb"))
    @patch.object(
        adb,
        "list_devices",
        return_value=[{"serial": "SER1", "state": "device", "model": "Pixel"}],
    )
    @patch.object(adb, "reverse_list", return_value=["tcp:8788 local tcp:8788"])
    def test_usb_ready(self, _rl, _ld, _req):
        status = adb.usb_status(8788)
        self.assertTrue(status["ok"])
        self.assertTrue(status["usbReady"])
        self.assertEqual(status["onlineCount"], 1)


if __name__ == "__main__":
    unittest.main()
