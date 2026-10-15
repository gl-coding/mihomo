import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "admin"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mihomo_site.settings")
import django
django.setup()
from rest_framework.test import APIClient
from mihomo_mgr import views


class NetworkApiTests(unittest.TestCase):
    def setUp(self):
        self.client = APIClient(HTTP_HOST="127.0.0.1")
        self.url = "/api/mihomo/network/"

    def test_read_returns_actual_backend_status(self):
        with patch("mihomo_mgr.network.network_status", return_value={"supported": True, "mode": "off"}):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "off")

    def test_form_post_without_custom_header_is_rejected(self):
        with patch("mihomo_mgr.network.set_network_mode") as write:
            response = self.client.post(self.url, {"mode": "off"})
        self.assertEqual(response.status_code, 403)
        write.assert_not_called()

    def test_remote_request_is_rejected(self):
        response = self.client.get(self.url, REMOTE_ADDR="192.168.1.3")
        self.assertEqual(response.status_code, 403)

    def test_embedded_mode_is_rejected(self):
        with patch.object(views, "load_config") as cfg:
            cfg.return_value.standalone = False
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_enable_requires_running_core(self):
        with patch.object(views, "_mihomo_running", return_value=False), patch("mihomo_mgr.network.set_network_mode") as write:
            response = self.client.post(self.url, {"mode": "system"}, format="json", HTTP_X_MIHOMO_NETWORK="1")
        self.assertEqual(response.status_code, 409)
        write.assert_not_called()

    def test_disable_works_even_when_core_is_stopped(self):
        with patch.object(views, "_mihomo_running", return_value=False), patch("mihomo_mgr.network.set_network_mode", return_value={"mode": "off"}) as write:
            response = self.client.post(self.url, {"mode": "off"}, format="json", HTTP_X_MIHOMO_NETWORK="1")
        self.assertEqual(response.status_code, 200)
        write.assert_called_once_with("off")


class ProxyModeApiTests(unittest.TestCase):
    def setUp(self):
        self.client = APIClient(HTTP_HOST="127.0.0.1")
        self.url = "/api/mihomo/proxy-mode/"

    def test_read_returns_mode_without_changing_system_proxy(self):
        with patch("mihomo_mgr.proxy_mode.proxy_mode_status", return_value={"mode": "rule"}), patch("mihomo_mgr.network.set_network_mode") as network:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "rule")
        network.assert_not_called()

    def test_post_validates_mode_before_calling_core(self):
        with patch("mihomo_mgr.proxy_mode.set_proxy_mode") as write:
            response = self.client.post(self.url, {"mode": "tun"}, format="json", HTTP_X_MIHOMO_NETWORK="1")
        self.assertEqual(response.status_code, 400)
        write.assert_not_called()

    def test_post_switches_runtime_mode(self):
        with patch("mihomo_mgr.proxy_mode.set_proxy_mode", return_value={"mode": "global"}) as write:
            response = self.client.post(self.url, {"mode": "global"}, format="json", HTTP_X_MIHOMO_NETWORK="1")
        self.assertEqual(response.status_code, 200)
        write.assert_called_once_with("global")

    def test_core_failure_is_reported(self):
        from mihomo_mgr.proxy_mode import ProxyModeError
        with patch("mihomo_mgr.proxy_mode.proxy_mode_status", side_effect=ProxyModeError("offline")):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 503)

    def test_remote_and_cross_site_writes_are_rejected(self):
        for options in ({}, {"HTTP_X_MIHOMO_NETWORK": "1", "REMOTE_ADDR": "192.168.1.3"}):
            with self.subTest(options=options), patch("mihomo_mgr.proxy_mode.set_proxy_mode") as write:
                response = self.client.post(self.url, {"mode": "global"}, format="json", **options)
                self.assertEqual(response.status_code, 403)
                write.assert_not_called()


class ProxyMetadataTests(unittest.TestCase):
    def test_metadata_includes_nested_groups_for_current_node_resolution(self):
        proxies = {
            views.MAIN_SELECTOR: {"type": "Selector", "now": "auto", "all": ["auto", "node-a"]},
            "auto": {"type": "URLTest", "now": "node-a", "all": ["node-a"]},
            "node-a": {"type": "Trojan", "udp": True, "server": "example.invalid", "password": "test-only"},
        }
        client = APIClient(HTTP_HOST="127.0.0.1")
        with patch.object(views, "_mihomo_request", return_value=(200, {"proxies": proxies})):
            response = client.get("/api/mihomo/proxies/")
        self.assertEqual(response.status_code, 200)
        info = response.json()["node_info"]
        self.assertEqual(info[views.MAIN_SELECTOR]["now"], "auto")
        self.assertEqual(info["auto"]["now"], "node-a")
        self.assertEqual(info["node-a"], {"type": "Trojan", "udp": True, "now": ""})


    def test_group_selection_keeps_builtin_targets_but_speed_nodes_exclude_them(self):
        proxies = {
            views.MAIN_SELECTOR: {"type": "Selector", "now": "DIRECT", "all": ["DIRECT", "REJECT", "node-a"]},
            "DIRECT": {"type": "Direct"},
            "REJECT": {"type": "Reject"},
            "node-a": {"type": "Trojan", "udp": True},
        }
        client = APIClient(HTTP_HOST="127.0.0.1")
        with patch.object(views, "_mihomo_request", return_value=(200, {"proxies": proxies})):
            response = client.get("/api/mihomo/proxies/")
        data = response.json()
        self.assertEqual(data["nodes"], ["node-a"])
        self.assertEqual(data["groups"][0]["options"], ["DIRECT", "REJECT", "node-a"])
        self.assertEqual(data["groups"][0]["now"], "DIRECT")
