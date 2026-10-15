import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mihomo_mgr import cli
from mihomo_mgr.network import NetworkError
from mihomo_mgr.proxy_mode import ProxyModeError
from mihomo_mgr.tui_service import ManagerError


class CliTests(unittest.TestCase):
    def invoke(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_json_flag_supports_prefix_suffix_and_nested_commands(self):
        parser = cli.build_parser()
        for argv in (["--json", "status"], ["status", "--json"], ["--json", "sub", "list"], ["sub", "--json", "list"], ["sub", "list", "--json"]):
            with self.subTest(argv=argv):
                self.assertTrue(parser.parse_args(argv).json)
        self.assertFalse(parser.parse_args(["status"]).json)

    def test_groups_show_automatic_groups_as_readonly(self):
        proxies = {"manual": {"type": "Selector", "all": ["a"], "now": "a"}, "auto": {"type": "URLTest", "all": ["a"], "now": "a"}, "a": {"type": "Trojan", "password": "test"}}
        with patch.object(cli, "_mihomo_request", return_value=(200, {"proxies": proxies})):
            code, out, err = self.invoke("groups", "--json")
        self.assertEqual(code, 0)
        items = json.loads(out)["items"]
        self.assertEqual(len(items), 2)
        self.assertFalse(items[0]["selectable"])
        self.assertTrue(items[1]["selectable"])
        self.assertNotIn("password", out)
        self.assertEqual(err, "")

    def test_nodes_respect_requested_group_and_keep_builtin_targets(self):
        with patch.object(cli, "_mihomo_request", return_value=(200, {"now": "DIRECT", "all": ["DIRECT", "a"]})) as request:
            code, out, _ = self.invoke("nodes", "--group", "流媒体", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["nodes"], ["DIRECT", "a"])
        request.assert_called_once_with("GET", "/proxies/%E6%B5%81%E5%AA%92%E4%BD%93")

    def test_missing_group_returns_json_and_nonzero_exit(self):
        with patch.object(cli, "_mihomo_request", return_value=(404, {})):
            code, out, _ = self.invoke("nodes", "--group", "missing", "--json")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["ok"])

    def test_select_uses_actual_default_group_instead_of_hardcoded_name(self):
        with patch.object(cli, "main_selector_state", return_value=("Custom", "a", ["a", "b"], None)), patch("mihomo_mgr.tui_service.Manager.select") as select:
            code, out, _ = self.invoke("select", "b", "--json")
        self.assertEqual(code, 0)
        select.assert_called_once_with("Custom", "b")
        self.assertEqual(json.loads(out)["group"], "Custom")

    def test_select_failure_is_machine_readable(self):
        with patch("mihomo_mgr.tui_service.Manager.select", side_effect=ManagerError("选择失败")):
            code, out, _ = self.invoke("select", "b", "--group", "test", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out), {"ok": False, "message": "选择失败"})

    def test_query_mode_never_sets_mode(self):
        with patch("mihomo_mgr.proxy_mode.proxy_mode_status", return_value={"mode": "rule"}), patch("mihomo_mgr.proxy_mode.set_proxy_mode") as set_mode:
            code, out, _ = self.invoke("mode", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["mode"], "rule")
        set_mode.assert_not_called()

    def test_set_mode_only_uses_requested_mode(self):
        with patch("mihomo_mgr.proxy_mode.set_proxy_mode", return_value={"mode": "global"}) as set_mode:
            code, out, _ = self.invoke("mode", "global", "--json")
        self.assertEqual(code, 0)
        set_mode.assert_called_once_with("global")
        self.assertEqual(json.loads(out)["mode"], "global")

    def test_offline_mode_returns_structured_error(self):
        with patch("mihomo_mgr.proxy_mode.proxy_mode_status", side_effect=ProxyModeError("内核未连接")):
            code, out, _ = self.invoke("mode", "--json")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["ok"])

    def test_system_status_never_changes_settings(self):
        with patch("mihomo_mgr.network.network_status", return_value={"supported": True, "mode": "off"}), patch("mihomo_mgr.tui_service.Manager.system_proxy") as set_proxy:
            code, out, _ = self.invoke("system-proxy", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["mode"], "off")
        set_proxy.assert_not_called()

    def test_system_off_and_failure_are_structured(self):
        with patch("mihomo_mgr.tui_service.Manager.system_proxy", return_value={"supported": True, "mode": "off"}) as set_proxy:
            code, out, _ = self.invoke("system-proxy", "off", "--json")
        self.assertEqual(code, 0)
        set_proxy.assert_called_once_with(False)
        with patch("mihomo_mgr.tui_service.Manager.system_proxy", side_effect=NetworkError("未识别桌面")):
            code, out, _ = self.invoke("system-proxy", "on", "--json")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["ok"])

    def test_start_returns_running_status(self):
        with patch("mihomo_mgr.tui_service.Manager.start") as start, patch.object(cli, "_mihomo_request", return_value=(200, {"version": "test"})):
            code, out, _ = self.invoke("start", "--json")
        self.assertEqual(code, 0)
        start.assert_called_once()
        self.assertTrue(json.loads(out)["running"])


class InstallerTests(unittest.TestCase):
    def test_install_is_idempotent_and_entry_works_outside_repository(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix="mihomo-cli-test-") as directory:
            dest = Path(directory) / "bin with spaces"
            for _ in range(2):
                result = subprocess.run([str(root / "install-cli.sh"), "--bin-dir", str(dest)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            target = dest / "mihomo"
            self.assertTrue(target.is_symlink())
            (dest / "relative-link").symlink_to("mihomo")
            result = subprocess.run([str(dest / "relative-link"), "--help"], cwd="/tmp", capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("system-proxy", result.stdout)

    def test_installer_never_overwrites_existing_command(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix="mihomo-cli-test-") as directory:
            target = Path(directory) / "mihomo"
            target.write_text("keep me")
            result = subprocess.run([str(root / "install-cli.sh"), "--bin-dir", directory], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(target.read_text(), "keep me")
