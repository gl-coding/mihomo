import unittest
from unittest.mock import patch

from mihomo_mgr import proxy_mode


class ProxyModeTests(unittest.TestCase):
    def test_read_normalizes_mode_and_reports_tun_and_global(self):
        with patch.object(proxy_mode, "_mihomo_request", side_effect=[
            (200, {"mode": "Global", "tun": {"enable": True}}),
            (200, {"now": "node-a", "all": ["DIRECT", "node-a"]}),
        ]):
            state = proxy_mode.proxy_mode_status()
        self.assertEqual(state["mode"], "global")
        self.assertTrue(state["tun_enabled"])
        self.assertEqual(state["global_current"], "node-a")
        self.assertEqual(state["global_options"], ["DIRECT", "node-a"])

    def test_core_unavailable_is_not_defaulted_to_rule(self):
        with patch.object(proxy_mode, "_mihomo_request", return_value=(0, {})):
            with self.assertRaises(proxy_mode.ProxyModeError):
                proxy_mode.proxy_mode_status()

    def test_unknown_mode_is_not_defaulted_to_rule(self):
        with patch.object(proxy_mode, "_mihomo_request", return_value=(200, {"mode": "unknown"})):
            with self.assertRaises(proxy_mode.ProxyModeError):
                proxy_mode.proxy_mode_status()

    def test_missing_global_group_does_not_block_reading_mode(self):
        with patch.object(proxy_mode, "_mihomo_request", side_effect=[(200, {"mode": "rule"}), (404, {})]):
            state = proxy_mode.proxy_mode_status()
        self.assertEqual(state["mode"], "rule")
        self.assertFalse(state["tun_enabled"])
        self.assertEqual(state["global_options"], [])

    def test_mode_patch_only_updates_mode_and_verifies_result(self):
        with patch.object(proxy_mode, "_mihomo_request", return_value=(204, {})) as api, patch.object(proxy_mode, "proxy_mode_status", return_value={"mode": "direct"}):
            self.assertEqual(proxy_mode.set_proxy_mode("direct"), {"mode": "direct"})
        api.assert_called_once_with("PATCH", "/configs", {"mode": "direct"}, timeout=5)

    def test_invalid_mode_never_reaches_core(self):
        with patch.object(proxy_mode, "_mihomo_request") as api:
            with self.assertRaises(proxy_mode.ProxyModeError):
                proxy_mode.set_proxy_mode("system")
        api.assert_not_called()

    def test_patch_failure_is_reported(self):
        with patch.object(proxy_mode, "_mihomo_request", return_value=(400, {})):
            with self.assertRaises(proxy_mode.ProxyModeError):
                proxy_mode.set_proxy_mode("global")

    def test_unchanged_core_mode_is_not_reported_as_success(self):
        with patch.object(proxy_mode, "_mihomo_request", return_value=(204, {})), patch.object(proxy_mode, "proxy_mode_status", return_value={"mode": "rule"}):
            with self.assertRaises(proxy_mode.ProxyModeError):
                proxy_mode.set_proxy_mode("global")
