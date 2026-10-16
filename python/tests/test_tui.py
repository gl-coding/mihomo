import asyncio
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from textual.widgets import Button, DataTable, Input, Select, Switch, TabbedContent

from mihomo_mgr.tui import MihomoApp, SubscriptionScreen, ConfirmScreen, SubscriptionCard
from mihomo_mgr.tui_service import Manager, ManagerError, resolve_node


def fixture():
    groups = {
        "main": {"type": "Selector", "now": "node-a", "all": ["node-a", "node-b", "DIRECT"]},
        "stream": {"type": "Selector", "now": "node-c", "all": ["node-c", "node-d"]},
        "GLOBAL": {"type": "Selector", "now": "main", "all": ["main", "node-d", "DIRECT"]},
    }
    return {
        "running": True, "version": "test", "groups": groups, "main": "main",
        "proxies": {**groups, **{name: {"type": "Trojan", "udp": True} for name in ("node-a", "node-b", "node-c", "node-d")}},
        "core": {"mode": "rule", "tun": {"enable": False}},
        "network": {"supported": True, "desktop": "KDE Plasma", "mode": "off", "target": "127.0.0.1:7890", "addresses": {}, "matches_target": True},
        "subscriptions": {"active_id": "one", "items": [
            {"id": "one", "name": "Current", "active": True, "url": "https://example.invalid/private", "node_count": 4},
            {"id": "two", "name": "Backup", "active": False, "url": "https://example.invalid/other", "node_count": 2},
        ]}, "errors": [],
    }


class FakeManager:
    def __init__(self):
        self.state = fixture()
        self.calls = []
        self.fail_select = False
        self.started = threading.Event()
        self.gate = None

    def snapshot(self):
        return copy.deepcopy(self.state)

    def select(self, group, node):
        self.calls.append(("select", group, node))
        if self.fail_select:
            raise ManagerError("切换失败")
        self.state["groups"][group]["now"] = node

    def measure(self, name):
        self.calls.append(("measure", name))
        self.started.set()
        if self.gate:
            self.gate.wait(5)
        return {"name": name, "ok": True, "delay": 25 if name.endswith("a") else 80}

    def mode(self, mode):
        self.calls.append(("mode", mode))
        self.state["core"]["mode"] = mode

    def system_proxy(self, enabled):
        self.calls.append(("system", enabled))
        self.state["network"]["mode"] = "system" if enabled else "off"

    def subscription(self, action, profile_id="", payload=None):
        self.calls.append(("subscription", action, profile_id, payload))
        if action == "delete":
            self.state["subscriptions"]["items"] = [row for row in self.state["subscriptions"]["items"] if row["id"] != profile_id]
        return self.state["subscriptions"]

    def ip(self):
        return {"direct": {"ip": "192.0.2.1"}, "proxy": {"ip": "198.51.100.1"}}

    def connectivity(self, target):
        return {"name": target, "state": "success", "message": "访问正常", "http_status": 200, "elapsed_ms": 5}

    def start(self):
        self.calls.append(("start",))
        self.state["running"] = True


class TuiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._state_dir.cleanup)
        config_patch = patch(
            "mihomo_mgr.tui.load_config",
            return_value=SimpleNamespace(mihomo_dir=Path(self._state_dir.name)),
        )
        config_patch.start()
        self.addCleanup(config_patch.stop)

    async def mount(self, app, pilot):
        await app.workers.wait_for_complete()
        await pilot.pause()

    async def test_initial_render_and_refresh_never_mutate_proxy(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            self.assertEqual(app.query_one("#nodes-table", DataTable).row_count, 1)
            self.assertEqual(len(app.query_one("#nodes-table", DataTable).columns), 3)
            self.assertEqual(manager.calls, [])
            app.action_refresh()
            await self.mount(app, pilot)
            self.assertEqual(manager.calls, [])

    async def test_long_current_node_name_does_not_expand_overview_selector_row(self):
        manager = FakeManager()
        long_name = "A very long node name " * 5
        manager.state["groups"]["main"]["now"] = long_name
        manager.state["groups"]["main"]["all"] = [long_name, "node-b"]
        manager.state["proxies"][long_name] = {"type": "Trojan"}
        app = MihomoApp(manager)
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            self.assertEqual(app.query_one("#overview-node").region.height, 3)
            self.assertEqual(app.query_one("#overview-selectors").region.height, 3)
            self.assertEqual(app.query_one("#node-card").region.width, 53)

    async def test_emoji_names_are_preserved_in_display_and_node_switch(self):
        manager = FakeManager()
        group, first, second = "🦊 main", "🇯🇵 日本D1", "🇸🇬 新加坡D2"
        manager.state["main"] = group
        manager.state["groups"][group] = manager.state["groups"].pop("main")
        manager.state["groups"][group].update(now=first, all=[first, second])
        for name in (first, second):
            manager.state["proxies"][name] = {"type": "Trojan"}
        app = MihomoApp(manager)
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            summary = app.query_one("#node-summary").render().plain
            self.assertIn(first, summary)
            self.assertIn(group, summary)
            self.assertEqual(app.query_one("#overview-group", Select).value, group)
            app.query_one("#overview-node", Select).value = second
            await self.mount(app, pilot)
            await pilot.click("#overview-switch")
            await self.mount(app, pilot)
            self.assertEqual(manager.calls, [("select", group, second)])

    async def test_three_column_node_cells_show_latency_on_one_line(self):
        app = MihomoApp(FakeManager())
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            first_row = app.query_one("#nodes-table", DataTable).get_row_at(0)
            self.assertTrue(first_row[0].plain.startswith("当前: node-a · main"))
            await pilot.press("1")
            table = app.query_one("#nodes-table", DataTable)
            app.delays["node-a"] = {"ok": True, "delay": 42}
            app._render_nodes()
            await pilot.pause()
            self.assertEqual(table.get_row_height(table.ordered_rows[0].key), 1)
            self.assertIn("42 ms", table.get_row_at(0)[0].plain)
            self.assertNotIn("Trojan", table.get_row_at(0)[0].plain)

    async def test_keyboard_group_then_node_selection_targets_chosen_group(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            await pilot.press("g", "down")
            self.assertEqual(app.active_group, "stream")
            self.assertEqual(app.query_one("#groups-table", DataTable).get_row_at(1)[0].plain, "● stream")
            self.assertEqual(app.query_one("#overview-group", Select).value, "main")
            self.assertIn("node-a", app.query_one("#node-summary").render().plain)
            self.assertEqual(manager.calls, [])
            await pilot.press("enter")
            self.assertEqual(app.query_one("#groups-table", DataTable).get_row_at(1)[0].plain, "● stream")
            await pilot.press("right", "right", "enter")
            await self.mount(app, pilot)
            self.assertIn(("select", "stream", "node-d"), manager.calls)
            self.assertEqual(app.state["groups"]["main"]["now"], "node-a")
            self.assertEqual(app.state["groups"]["stream"]["now"], "node-d")

    async def test_last_proxy_group_is_restored_on_next_tui_launch(self):
        first = MihomoApp(FakeManager())
        async with first.run_test(size=(110, 38)) as pilot:
            await self.mount(first, pilot)
            await pilot.press("g", "down")
            self.assertEqual(first.active_group, "stream")

        second = MihomoApp(FakeManager())
        async with second.run_test(size=(110, 38)) as pilot:
            await self.mount(second, pilot)
            self.assertEqual(second.active_group, "stream")
            self.assertEqual(second.query_one("#overview-group", Select).value, "main")

    async def test_overview_group_browsing_does_not_persist_without_switch(self):
        first = MihomoApp(FakeManager())
        async with first.run_test(size=(110, 38)) as pilot:
            await self.mount(first, pilot)
            group_select = first.query_one("#overview-group", Select)
            first.choose_overview(Select.Changed(group_select, "stream"))
            self.assertEqual(first.overview_group, "stream")
            self.assertEqual(first.active_group, "stream")

        second = MihomoApp(FakeManager())
        async with second.run_test(size=(110, 38)) as pilot:
            await self.mount(second, pilot)
            self.assertEqual(second.overview_group, "main")
            self.assertEqual(second.query_one("#overview-group", Select).value, "main")

    async def test_clicking_proxy_group_does_not_change_current_node_card(self):
        app = MihomoApp(FakeManager())
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            await pilot.press("1")
            await pilot.click(app.query_one("#groups-table", DataTable), offset=(5, 2))
            self.assertEqual(app.active_group, "stream")
            self.assertEqual(app.query_one("#overview-group", Select).value, "main")
            self.assertEqual(app.query_one("#overview-node", Select).value, "node-a")
            self.assertIn("node-a", app.query_one("#node-summary").render().plain)
            first_row = app.query_one("#nodes-table", DataTable).get_row_at(0)
            self.assertIn("node-a · main", first_row[0].plain)
            self.assertTrue(first_row[1].plain.startswith("node-c ·"))
            self.assertTrue(first_row[0].plain.startswith("当前: node-a · main"))
            self.assertNotIn("●", first_row[0].plain)
            self.assertNotIn("●", first_row[1].plain)

    async def test_node_search_and_sort_controls_are_removed(self):
        app = MihomoApp(FakeManager())
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            self.assertEqual(len(app.query("#node-search")), 0)
            self.assertEqual(len(app.query("#node-sort")), 0)
            self.assertEqual(app.query_one("#nodes-table", DataTable).row_count, 1)

    async def test_failed_node_switch_keeps_actual_selection(self):
        manager = FakeManager()
        manager.fail_select = True
        app = MihomoApp(manager)
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            await pilot.press("n", "down", "enter")
            await self.mount(app, pilot)
            self.assertEqual(app.state["groups"]["main"]["now"], "node-a")
            self.assertFalse(app.busy)

    async def test_measurement_does_not_block_ui_or_allow_second_mutation(self):
        manager = FakeManager()
        manager.gate = threading.Event()
        app = MihomoApp(manager)
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            await pilot.press("1")
            await pilot.click("#measure-all")
            self.assertTrue(await asyncio.to_thread(manager.started.wait, 2))
            try:
                await pilot.press("1")
                self.assertTrue(app.busy)
                self.assertEqual(app.query_one("#nodes-table", DataTable).row_count, 1)
                self.assertTrue(app.query_one("#apply-node").disabled)
                app.action_quit()
                self.assertTrue(app.is_running)
            finally:
                manager.gate.set()
            await self.mount(app, pilot)
            self.assertEqual(set(app.delays), {"node-a", "node-b"})
            self.assertFalse(app.busy)

    async def test_mode_selection_applies_immediately_and_global_locks_group(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            await pilot.click("#home-mode-global")
            await pilot.pause()
            await self.mount(app, pilot)
            self.assertIn(("mode", "global"), manager.calls)
            self.assertEqual(app.active_group, "GLOBAL")
            self.assertEqual(app.query_one("#groups-table", DataTable).row_count, 1)

    async def test_system_switch_uses_actual_state_and_calls_backend_once(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            await pilot.click("#home-system-switch")
            await self.mount(app, pilot)
            self.assertEqual(manager.calls, [("system", True)])
            self.assertTrue(app.query_one("#home-system-switch", Switch).value)

    async def test_import_modal_masks_url_and_saves_without_auto_download(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            await pilot.press("2")
            await pilot.click("#sub-new")
            self.assertIsInstance(app.screen, SubscriptionScreen)
            self.assertTrue(app.screen.query_one("#profile-url", Input).password)
            app.screen.query_one("#profile-name", Input).value = "New"
            app.screen.query_one("#profile-url", Input).value = "https://example.invalid/test"
            await pilot.click("#profile-save")
            await self.mount(app, pilot)
            self.assertEqual(manager.calls[0][0:2], ("subscription", "save"))
            self.assertEqual(len(manager.calls), 1)

    async def test_subscription_cards_layout_and_scoped_actions(self):
        for width in (80, 110):
            with self.subTest(width=width):
                manager = FakeManager()
                app = MihomoApp(manager)
                async with app.run_test(size=(width, 38)) as pilot:
                    await self.mount(app, pilot)
                    await pilot.press("2")
                    cards = list(app.query(SubscriptionCard))
                    self.assertEqual(len(cards), 2)
                    if width == 80:
                        self.assertEqual(cards[0].region.x, cards[1].region.x)
                        self.assertGreater(cards[1].region.y, cards[0].region.y)
                    else:
                        self.assertEqual(cards[0].region.y, cards[1].region.y)
                    self.assertTrue(cards[0].has_class("active-profile"))
                    self.assertNotIn("/private", str(cards[0].query_one(".profile-meta").render()))
                    update = next(b for b in cards[1].query(Button) if b.name == "update")
                    app.query_one("#profiles-scroll").scroll_end(animate=False)
                    await pilot.pause()
                    await pilot.click(update)
                    await self.mount(app, pilot)
                    self.assertEqual(manager.calls[0], ("subscription", "update", "two", None))
                    cards[1].focus()
                    await pilot.press("enter")
                    await self.mount(app, pilot)
                    self.assertEqual(manager.calls[-1], ("subscription", "activate", "two", None))

    async def test_delete_requires_confirmation_and_active_subscription_is_protected(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            await pilot.press("2")
            cards = list(app.query(SubscriptionCard))
            self.assertTrue(next(button for button in cards[0].query(Button) if button.name == "delete").disabled)
            cards[0].focus()
            await pilot.press("down")
            self.assertTrue(cards[1].has_focus)
            await pilot.click(next(button for button in cards[1].query(Button) if button.name == "delete"))
            self.assertIsInstance(app.screen, ConfirmScreen)
            await pilot.click("#confirm-no")
            self.assertEqual(manager.calls, [])
            await pilot.click(next(button for button in cards[1].query(Button) if button.name == "delete"))
            await pilot.click("#confirm-yes")
            await self.mount(app, pilot)
            self.assertEqual(manager.calls[0][0:3], ("subscription", "delete", "two"))

    async def test_offline_narrow_terminal_keeps_ui_available(self):
        manager = FakeManager()
        manager.state.update(running=False, core={}, groups={}, proxies={})
        manager.state["network"] = {"supported": False, "message": "未识别桌面"}
        app = MihomoApp(manager)
        async with app.run_test(size=(80, 24)) as pilot:
            await self.mount(app, pilot)
            self.assertTrue(app.query_one("#apply-node").disabled)
            self.assertTrue(app.query_one("#home-system-switch").disabled)
            self.assertFalse(app.query_one("#start-core").disabled)
            await pilot.press("q")
            self.assertFalse(app.is_running)

    async def test_cancel_measurement_skips_requests_not_started(self):
        manager = FakeManager()
        manager.state["groups"]["main"]["all"] = [f"node-{i}" for i in range(8)]
        manager.gate = threading.Event()
        app = MihomoApp(manager)
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            await pilot.press("1")
            await pilot.click("#measure-all")
            self.assertTrue(await asyncio.to_thread(manager.started.wait, 2))
            try:
                await pilot.click("#measure-cancel")
                self.assertTrue(app.cancel_measure)
            finally:
                manager.gate.set()
            await self.mount(app, pilot)
            measured = [call for call in manager.calls if call[0] == "measure"]
            self.assertLessEqual(len(measured), 4)
            self.assertEqual(len(app.delays), len(measured))
            self.assertFalse(app.busy)

    async def test_direct_mode_disables_proxy_selection(self):
        manager = FakeManager()
        manager.state["core"]["mode"] = "direct"
        app = MihomoApp(manager)
        async with app.run_test(size=(80, 24)) as pilot:
            await self.mount(app, pilot)
            self.assertEqual(app.query_one("#nodes-table", DataTable).row_count, 0)
            self.assertTrue(app.query_one("#apply-node").disabled)
            self.assertTrue(app.query_one("#measure-all").disabled)
            self.assertEqual(manager.calls, [])

    async def test_failed_system_change_restores_actual_switch_value(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        with patch.object(manager, "system_proxy", side_effect=ManagerError("设置失败")):
            async with app.run_test(size=(110, 38)) as pilot:
                await self.mount(app, pilot)
                await pilot.click("#home-system-switch")
                await self.mount(app, pilot)
                self.assertFalse(app.query_one("#home-system-switch", Switch).value)
                self.assertFalse(app.busy)

    async def test_diagnostics_render_without_modifying_groups(self):
        manager = FakeManager()
        before = copy.deepcopy(manager.state["groups"])
        app = MihomoApp(manager)
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            app.query_one("#home-check-ip").scroll_visible(immediate=True)
            await pilot.pause()
            await pilot.click("#home-check-ip")
            await self.mount(app, pilot)
            await pilot.click("#home-check-sites")
            await self.mount(app, pilot)
            self.assertEqual(manager.state["groups"], before)
            self.assertEqual(manager.calls, [])
            self.assertFalse(app.busy)

    async def test_home_site_results_and_compact_layout(self):
        app = MihomoApp(FakeManager())
        async with app.run_test(size=(80, 24)) as pilot:
            await self.mount(app, pilot)
            self.assertEqual(len(app.query("TabPane")), 3)
            button = app.query_one("#home-check-sites")
            button.scroll_visible(immediate=True)
            await pilot.pause()
            await pilot.click("#home-check-sites")
            await self.mount(app, pilot)
            self.assertEqual(set(app.site_results), {"google", "chatgpt", "ipify"})
            self.assertTrue(all(result["state"] == "success" for result in app.site_results.values()))

    async def test_site_single_retest_preserves_other_results(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            app.site_results["google"] = {"state": "success", "http_status": 200, "elapsed_ms": 123}
            app._render_sites()
            app.query_one("#home-site-test-chatgpt").scroll_visible(immediate=True)
            await pilot.pause()
            with patch.object(manager, "connectivity", wraps=manager.connectivity) as check:
                await pilot.click("#home-site-test-chatgpt")
                await self.mount(app, pilot)
                check.assert_called_once_with("chatgpt")
            self.assertEqual(app.site_results["google"]["elapsed_ms"], 123)
            self.assertEqual(app.site_results["chatgpt"]["state"], "success")
            app.query_one("#home-clear-results").scroll_visible(immediate=True)
            await pilot.pause()
            await pilot.click("#home-clear-results")
            self.assertEqual(app.site_results, {})

    async def test_site_restriction_and_unexpected_failure_are_independent(self):
        manager = FakeManager()
        def check(target):
            if target == "google":
                raise RuntimeError("test-only")
            if target == "chatgpt":
                return {"state": "restricted", "http_status": 403, "elapsed_ms": 7, "message": "站点限制"}
            return {"state": "success", "http_status": 200, "elapsed_ms": 9, "ip": "198.51.100.1"}
        app = MihomoApp(manager)
        with patch.object(manager, "connectivity", side_effect=check):
            async with app.run_test(size=(110, 38)) as pilot:
                await self.mount(app, pilot)
                app.query_one("#home-check-sites").scroll_visible(immediate=True)
                await pilot.pause()
                await pilot.click("#home-check-sites")
                await self.mount(app, pilot)
                self.assertEqual(app.site_results["google"]["state"], "failed")
                self.assertEqual(app.site_results["chatgpt"]["state"], "restricted")
                self.assertEqual(app.site_results["ipify"]["state"], "success")
                app.query_one("#home-site-details-chatgpt").scroll_visible(immediate=True)
                await pilot.pause()
                await pilot.click("#home-site-details-chatgpt")
                self.assertIn("站点限制", app.screen.details)
                self.assertIn("403", app.screen.details)
                await pilot.press("escape")

    async def test_site_results_clear_when_group_changes_not_when_only_browsing(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(110, 38)) as pilot:
            await self.mount(app, pilot)
            app.site_results["google"] = {"state": "success"}
            await pilot.press("g", "down")
            self.assertIn("google", app.site_results)
            manager.state["groups"]["main"]["now"] = "node-b"
            app.action_refresh()
            await self.mount(app, pilot)
            self.assertEqual(app.site_results, {})

    async def test_site_tests_are_disabled_without_core(self):
        manager = FakeManager()
        manager.state.update(running=False, core={}, groups={}, proxies={})
        app = MihomoApp(manager)
        async with app.run_test(size=(80, 24)) as pilot:
            await self.mount(app, pilot)
            self.assertTrue(app.query_one("#home-check-sites").disabled)
            self.assertTrue(app.query_one("#home-site-test-google").disabled)
            with patch.object(manager, "connectivity") as check:
                app._start_site_test(True)
                check.assert_not_called()

    async def test_dashboard_is_default_and_controls_reflect_actual_core(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(120, 42)) as pilot:
            await self.mount(app, pilot)
            self.assertEqual(app.query_one("#pages", TabbedContent).active, "dashboard")
            self.assertEqual(app.theme, "mihomo-light")
            self.assertEqual(app.query_one("#home-mode-rule").variant, "primary")
            self.assertEqual(manager.calls, [])
            await pilot.click("#home-mode-global")
            await self.mount(app, pilot)
            self.assertIn(("mode", "global"), manager.calls)
            self.assertEqual(app.query_one("#home-mode-global").variant, "primary")
            self.assertEqual(app.active_group, "GLOBAL")

    async def test_dashboard_switch_updates_actual_state(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(120, 42)) as pilot:
            await self.mount(app, pilot)
            await pilot.click("#home-system-switch")
            await self.mount(app, pilot)
            self.assertEqual(manager.calls, [("system", True)])
            self.assertTrue(app.query_one("#home-system-switch", Switch).value)

    async def test_dashboard_site_test_stays_on_dashboard_and_updates_shared_results(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(120, 42)) as pilot:
            await self.mount(app, pilot)
            with patch.object(manager, "connectivity", wraps=manager.connectivity) as check:
                app.query_one("#home-site-test-google").scroll_visible(immediate=True)
                await pilot.pause()
                await pilot.click("#home-site-test-google")
                await self.mount(app, pilot)
                check.assert_called_once_with("google")
            self.assertEqual(app.query_one("#pages", TabbedContent).active, "dashboard")
            self.assertEqual(app.site_results["google"]["state"], "success")
            self.assertIn("正常", str(app.query_one("#home-site-value-google").content))
            await pilot.press("f2")
            self.assertEqual(app.theme, "textual-dark")
            await pilot.press("f2")
            self.assertEqual(app.theme, "mihomo-light")

    async def test_dashboard_layout_adapts_to_small_terminal_and_navigates(self):
        app = MihomoApp(FakeManager())
        async with app.run_test(size=(80, 24)) as pilot:
            await self.mount(app, pilot)
            self.assertLessEqual(app.query_one("#subscription-card").region.right, app.query_one("#node-card").region.x)
            self.assertLessEqual(app.query_one("#home-network-card").region.bottom, app.query_one("#home-mode-card").region.y)
            await pilot.click("#overview-nodes")
            self.assertEqual(app.query_one("#pages", TabbedContent).active, "nodes")
            await pilot.press("0")
            self.assertEqual(app.query_one("#pages", TabbedContent).active, "dashboard")

    async def test_network_details_opens_dialog_and_keeps_current_page(self):
        manager = FakeManager()
        manager.state["network"]["addresses"] = {"http": "127.0.0.1:7890", "https": "127.0.0.1:7890"}
        app = MihomoApp(manager)
        async with app.run_test(size=(100, 32)) as pilot:
            await self.mount(app, pilot)
            await pilot.click("#home-network-details")
            self.assertEqual(app.screen.title_text, "系统代理设置")
            for text in ("KDE Plasma", "已关闭", "HTTP：127.0.0.1:7890", "HTTPS：127.0.0.1:7890", "SOCKS：未设置"):
                self.assertIn(text, app.screen.details)
            await pilot.click("#result-close")
            self.assertEqual(app.query_one("#pages", TabbedContent).active, "dashboard")
            self.assertEqual(manager.calls, [])

    async def test_network_tabs_keep_actual_tun_state_read_only(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(120, 42)) as pilot:
            await self.mount(app, pilot)
            await pilot.click("#home-network-tun")
            self.assertEqual(app.network_tab, "tun")
            self.assertTrue(app.query_one("#home-tun-row").display)
            self.assertFalse(app.query_one("#home-system-row").display)
            self.assertTrue(app.query_one("#home-tun-switch", Switch).disabled)
            manager.state["core"]["tun"]["enable"] = True
            app.action_refresh()
            await self.mount(app, pilot)
            self.assertEqual(app.network_tab, "tun")
            self.assertTrue(app.query_one("#home-tun-switch", Switch).value)
            self.assertIn("TUN 已开启", str(app.query_one("#home-system-status").content))
            self.assertEqual(manager.calls, [])
            await pilot.click("#home-network-system")
            self.assertFalse(app.query_one("#home-tun-row").display)
            await pilot.click("#home-system-switch")
            await self.mount(app, pilot)
            self.assertEqual(manager.calls, [("system", True)])

    async def test_overview_group_and_node_selection_apply_only_after_switch(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(120, 42)) as pilot:
            await self.mount(app, pilot)
            app.query_one("#overview-group", Select).value = "stream"
            await pilot.pause()
            self.assertEqual(manager.calls, [])
            self.assertEqual(app.active_group, "stream")
            self.assertEqual(app.query_one("#overview-node", Select).value, "node-c")
            app.query_one("#overview-node", Select).value = "node-d"
            await self.mount(app, pilot)
            self.assertEqual(manager.calls, [])
            self.assertEqual(app.query_one("#overview-node", Select).value, "node-d")
            await pilot.click("#overview-switch")
            await self.mount(app, pilot)
            self.assertEqual(manager.calls, [("select", "stream", "node-d")])
            self.assertEqual(app.query_one("#overview-node", Select).value, "node-d")
            saved = json.loads(app._ui_state_path.read_text(encoding="utf-8"))
            self.assertEqual((saved["overview_group"], saved["overview_node"]), ("stream", "node-d"))
            reopened = MihomoApp(FakeManager())
            async with reopened.run_test(size=(120, 42)) as reopened_pilot:
                await self.mount(reopened, reopened_pilot)
                self.assertEqual(reopened.overview_group, "stream")
                self.assertEqual(reopened.query_one("#overview-node", Select).value, "node-d")
            manager.fail_select = True
            app.query_one("#overview-node", Select).value = "node-c"
            await self.mount(app, pilot)
            await pilot.click("#overview-switch")
            await self.mount(app, pilot)
            self.assertEqual(app.query_one("#overview-node", Select).value, "node-c")
            self.assertEqual(manager.calls[-1], ("select", "stream", "node-c"))

    async def test_global_outbound_and_direct_mode_controls(self):
        manager = FakeManager()
        manager.state["core"]["mode"] = "global"
        app = MihomoApp(manager)
        async with app.run_test(size=(120, 42)) as pilot:
            await self.mount(app, pilot)
            self.assertTrue(app.query_one("#overview-group", Select).disabled)
            self.assertTrue(app.query_one("#home-global-node").display)
            app.query_one("#home-global-node", Select).value = "node-d"
            await self.mount(app, pilot)
            self.assertEqual(manager.calls, [])
            await pilot.click("#overview-switch")
            await self.mount(app, pilot)
            self.assertEqual(manager.calls, [("select", "GLOBAL", "node-d")])
            self.assertEqual(app.query_one("#overview-node", Select).value, "node-d")
            await pilot.click("#home-mode-direct")
            await self.mount(app, pilot)
            self.assertTrue(app.query_one("#overview-node", Select).disabled)
            self.assertFalse(app.query_one("#home-global-node").display)

    async def test_overview_update_uses_active_subscription_and_measure_current(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(120, 42)) as pilot:
            await self.mount(app, pilot)
            app.selected_profile = "two"
            await pilot.click("#overview-update")
            await self.mount(app, pilot)
            self.assertEqual(manager.calls[0], ("subscription", "update", "one", None))
            await pilot.click("#overview-measure")
            await self.mount(app, pilot)
            self.assertIn(("measure", "node-a"), manager.calls)

    async def test_one_click_verifies_ip_and_all_sites_without_proxy_mutation(self):
        manager = FakeManager()
        app = MihomoApp(manager)
        async with app.run_test(size=(120, 44)) as pilot:
            await self.mount(app, pilot)
            app.query_one("#home-verify-all").scroll_visible(immediate=True)
            await pilot.pause()
            await pilot.click("#home-verify-all")
            await self.mount(app, pilot)
            self.assertEqual(app.ip_results["proxy"]["ip"], "198.51.100.1")
            self.assertEqual(set(app.site_results), {"google", "chatgpt", "ipify"})
            self.assertEqual(manager.calls, [])


class ServiceTests(unittest.TestCase):
    def test_node_resolution_handles_nested_groups_and_cycles(self):
        proxies = {"group": {"now": "auto"}, "auto": {"now": "node"}, "node": {}}
        self.assertEqual(resolve_node(proxies, "group"), ("node", ["group", "auto", "node"]))
        proxies["auto"]["now"] = "group"
        self.assertEqual(resolve_node(proxies, "group")[1], ["group", "auto"])

    def test_ip_diagnostics_never_synchronize_strategy_groups(self):
        with patch("mihomo_mgr.tui_service.perform_ip_check", return_value={}) as check:
            Manager().ip()
        check.assert_called_once_with(sync_current=False)

    def test_select_validates_membership_and_reads_back(self):
        group = {"type": "Selector", "all": ["a", "b"], "now": "a"}
        with patch("mihomo_mgr.tui_service._mihomo_request", side_effect=[(200, group), (204, {}), (200, {**group, "now": "b"})]) as request:
            Manager().select("流媒体", "b")
        self.assertEqual(request.call_args_list[1].args, ("PUT", "/proxies/%E6%B5%81%E5%AA%92%E4%BD%93", {"name": "b"}))

    def test_invalid_group_member_never_writes(self):
        with patch("mihomo_mgr.tui_service._mihomo_request", return_value=(200, {"type": "Selector", "all": ["a"]})) as request:
            with self.assertRaises(ManagerError):
                Manager().select("main", "invalid")
        self.assertEqual(request.call_count, 1)
