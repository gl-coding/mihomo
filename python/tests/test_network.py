import subprocess
import unittest
from unittest.mock import patch

from mihomo_mgr import network


class NetworkTests(unittest.TestCase):
    def test_unsupported_desktop_is_reported(self):
        with patch.dict(network.os.environ, {"XDG_CURRENT_DESKTOP": "", "DESKTOP_SESSION": ""}):
            state = network.network_status()
        self.assertFalse(state["supported"])
        self.assertEqual(state["mode"], "unknown")

    def test_kde_detection_does_not_use_gnome(self):
        with patch.dict(network.os.environ, {"XDG_CURRENT_DESKTOP": "KDE", "DESKTOP_SESSION": "plasma"}), patch.object(network, "_tool", return_value="tool"):
            self.assertEqual(network._backend(), "KDE Plasma")

    def test_kde_status_detects_old_port(self):
        with patch.object(network, "_backend", return_value="KDE Plasma"), patch.object(network, "_target", return_value=("127.0.0.1", 7890)), patch.object(network, "_read", return_value={
            "ProxyType": "1", "httpProxy": "http://127.0.0.1:7897",
            "httpsProxy": "http://127.0.0.1:7897", "socksProxy": "socks://127.0.0.1:7897",
        }):
            state = network.network_status()
        self.assertTrue(state["supported"])
        self.assertEqual(state["mode"], "system")
        self.assertFalse(state["matches_target"])

    def test_gnome_auto_mode_is_not_shown_as_disabled(self):
        values = {"mode": "'auto'"}
        for p in ("http", "https", "socks"):
            values.update({p + ".host": "'127.0.0.1'", p + ".port": "7890"})
        with patch.object(network, "_backend", return_value="GNOME"), patch.object(network, "_target", return_value=("127.0.0.1", 7890)), patch.object(network, "_read", return_value=values):
            state = network.network_status()
        self.assertEqual(state["mode"], "other")
        self.assertTrue(state["matches_target"])

    def test_failed_write_restores_previous_values_and_mode_last(self):
        before = {"ProxyType": "2", "httpProxy": "http://old:8080"}
        with patch.object(network, "_backend", return_value="KDE Plasma"), patch.object(network, "_target", return_value=("127.0.0.1", 7890)), patch.object(network, "_read", return_value=before), patch.object(network, "_write", side_effect=[network.NetworkError("failed"), None]) as write:
            with self.assertRaisesRegex(network.NetworkError, "已恢复"):
                network.set_network_mode("system")
        restored = write.call_args_list[1].args[1]
        self.assertEqual(restored, before)
        self.assertEqual(list(restored)[-1], "ProxyType")

    def test_failed_verification_rolls_back(self):
        with patch.object(network, "_backend", return_value="GNOME"), patch.object(network, "_target", return_value=("127.0.0.1", 7890)), patch.object(network, "_read", return_value={"mode": "'none'"}), patch.object(network, "_write") as write, patch.object(network, "network_status", return_value={"mode": "off"}):
            with self.assertRaisesRegex(network.NetworkError, "已恢复"):
                network.set_network_mode("system")
        self.assertEqual(write.call_count, 2)

    def test_disable_preserves_proxy_addresses(self):
        with patch.object(network, "_backend", return_value="KDE Plasma"), patch.object(network, "_target", return_value=("127.0.0.1", 7890)), patch.object(network, "_read", return_value={"ProxyType": "1"}), patch.object(network, "_write") as write, patch.object(network, "network_status", return_value={"mode": "off"}):
            network.set_network_mode("off")
        write.assert_called_once_with("KDE Plasma", {"ProxyType": "0"})

    def test_dconf_warning_is_a_failure_even_with_exit_zero(self):
        result = subprocess.CompletedProcess([], 0, "", "dconf-WARNING: failed to commit changes")
        with patch.object(network.subprocess, "run", return_value=result):
            with self.assertRaises(network.NetworkError):
                network._run("gsettings", "set")

    def test_invalid_mode_never_writes(self):
        with patch.object(network, "_write") as write:
            with self.assertRaises(network.NetworkError):
                network.set_network_mode("pac")
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
