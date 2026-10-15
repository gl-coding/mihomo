"""Mihomo 全屏终端管理：所有阻塞业务在后台执行。"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlsplit

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.message import Message
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.theme import Theme
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, ProgressBar, Select, Static, Switch, TabbedContent, TabPane

from .service import CONNECTIVITY_TARGETS
from .subscription import SubscriptionError
from .tui_service import EXPECTED_ERRORS, Manager, expiry, resolve_node, traffic

BUILTINS = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE"}


class SubscriptionCard(Vertical, can_focus=True):
    """A subscription summary with actions scoped to this profile."""
    class Action(Message):
        def __init__(self, card, action):
            super().__init__()
            self.card, self.action = card, action

    def __init__(self, profile: dict, blocked: bool = False):
        super().__init__(classes="profile-card")
        self.profile = profile
        self.blocked = blocked

    def compose(self) -> ComposeResult:
        yield Static("", classes="profile-name", markup=False)
        yield Static("", classes="profile-meta", markup=False)
        yield Static("", classes="profile-quota", markup=False)
        yield ProgressBar(total=100, show_eta=False, show_percentage=False, classes="profile-progress")
        yield Static("", classes="profile-foot", markup=False)
        with Horizontal(classes="profile-actions"):
            for action, label in (("update", "更新 ↻"), ("edit", "编辑"), ("activate", "启用"), ("delete", "删除")):
                yield Button(label, name=action, variant="error" if action == "delete" else "default")

    def on_mount(self) -> None:
        self.update_profile(self.profile, self.app.busy or self.app.refreshing)

    def update_profile(self, profile: dict, blocked: bool) -> None:
        self.profile, self.blocked = profile, blocked
        active = bool(profile.get("active"))
        self.set_class(active, "active-profile")
        self.query_one(".profile-name", Static).update(("● " if active else "") + profile.get("name", ""))
        host = urlsplit(profile.get("url") or "").hostname or "本地订阅"
        self.query_one(".profile-meta", Static).update(f"{host}  ·  更新 {(profile.get('updated_at') or '未更新')[:16]}")
        used = None if profile.get("traffic_upload") is None and profile.get("traffic_download") is None else (profile.get("traffic_upload") or 0) + (profile.get("traffic_download") or 0)
        total = profile.get("traffic_total")
        self.query_one(".profile-quota", Static).update(f"{traffic(used)} / {traffic(total)}  ·  到期 {expiry(profile.get('expire_at'))}")
        self.query_one(ProgressBar).update(progress=min(100, 100 * (used or 0) / total) if total and total > 0 else 0)
        foot = "当前使用" if active else "已下载" if profile.get("has_cache") else "尚未下载 · 点更新"
        self.query_one(".profile-foot", Static).update(f"{foot}  ·  {profile.get('node_count') if profile.get('node_count') is not None else '—'} 节点  ·  {'代理下载' if profile.get('via_proxy') else '直连'}")
        for button in self.query(Button):
            button.disabled = blocked or (active and button.name in ("activate", "delete"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(self.Action(self, event.button.name))

    def on_key(self, event) -> None:
        if event.key in ("up", "down"):
            cards = list(self.parent.query(SubscriptionCard))
            index = cards.index(self) + (-1 if event.key == "up" else 1)
            cards[max(0, min(len(cards) - 1, index))].focus()
        elif event.key in ("enter", "space") and self.has_focus:
            if not self.blocked and not self.profile.get("active"):
                self.post_message(self.Action(self, "activate"))
        else:
            return
        event.stop()
        event.prevent_default()


class ConfirmScreen(ModalScreen[bool]):
    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.message, markup=False)
            with Horizontal(classes="toolbar"):
                yield Button("取消", id="confirm-no")
                yield Button("确认删除", variant="error", id="confirm-yes")

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(False)
            event.stop()


class SubscriptionScreen(ModalScreen[dict | None]):
    def __init__(self, profile: dict | None = None):
        super().__init__()
        self.profile = profile or {}

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("编辑订阅" if self.profile else "导入订阅", classes="dialog-title")
            yield Label("名称")
            yield Input(value=self.profile.get("name", ""), placeholder="订阅名称（最多 80 字）", id="profile-name", max_length=80)
            yield Label("订阅链接（编辑时留空保留原链接）")
            yield Input(password=True, placeholder="https://…", id="profile-url")
            with Horizontal(classes="switch-row"):
                yield Label("通过当前代理下载")
                yield Switch(value=bool(self.profile.get("via_proxy")), id="profile-via")
            yield Static("保存后在订阅列表中更新或启用。", classes="muted")
            with Horizontal(classes="toolbar"):
                yield Button("取消", id="profile-cancel")
                yield Button("保存", variant="primary", id="profile-save")

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        if event.button.id == "profile-cancel":
            self.dismiss(None)
        elif event.button.id == "profile-save":
            name = self.query_one("#profile-name", Input).value.strip()
            url = self.query_one("#profile-url", Input).value.strip()
            if not name or (not self.profile and not url):
                self.notify("请填写名称和订阅链接", severity="warning")
                return
            payload = {"name": name, "via_proxy": self.query_one("#profile-via", Switch).value}
            if self.profile:
                payload["id"] = self.profile["id"]
            if url:
                payload["url"] = url
            self.dismiss(payload)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()


class ResultScreen(ModalScreen):
    def __init__(self, title: str, details: str):
        super().__init__()
        self.title_text, self.details = title, details

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            yield Static(self.details, markup=False)
            yield Button("关闭", variant="primary", id="result-close")

    @on(Button.Pressed, "#result-close")
    def close_result(self) -> None:
        self.dismiss()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()
            event.stop()


class MihomoApp(App):
    TITLE = "Mihomo"
    SUB_TITLE = "终端代理管理"
    CSS_PATH = "tui.tcss"
    BINDINGS = [
        Binding("0", "show_tab('dashboard')", "概览"),
        Binding("1", "show_tab('nodes')", "节点"), Binding("2", "show_tab('subscriptions')", "订阅"),
        Binding("r", "refresh", "刷新"), Binding("/", "search", "搜索"),
        Binding("g", "focus_groups", "代理组", show=False), Binding("n", "focus_nodes", "节点表", show=False),
        Binding("f2", "toggle_theme", "主题", show=False),
        Binding("q", "request_quit", "退出"), Binding("ctrl+q", "request_quit", show=False, priority=True),
        Binding("ctrl+c", "request_quit", show=False, priority=True),
    ]

    def __init__(self, manager: Manager | None = None):
        super().__init__()
        self.register_theme(Theme(name="mihomo-light", primary="#3b82f6", secondary="#64748b", accent="#2563eb",
                                  background="#f1f5f9", foreground="#334155", surface="#ffffff", panel="#f8fafc",
                                  success="#16a34a", warning="#d97706", error="#dc2626", dark=False,
                                  variables={"text-muted": "#64748b", "border": "#e2e8f0", "footer-background": "#ffffff"}))
        self.theme = "mihomo-light"
        self.manager = manager or Manager()
        self.state: dict = {}
        self.active_group = ""
        self.delays: dict[str, dict] = {}
        self.busy = False
        self.refreshing = False
        self.measuring = False
        self.cancel_measure = False
        self.ready = False
        self.site_results: dict[str, dict] = {}
        self.site_context = None
        self.ip_results: dict = {}
        self.network_tab = "system"
        self.selected_profile = ""
        self.last_active_profile = ""

    def compose(self) -> ComposeResult:
        with Horizontal(id="top-header"):
            yield Static(Text.assemble(("Mihomo", "bold"), ("  /  代理管理", "dim")), id="brand")
            yield Static("● 正在连接", id="header-status", markup=False)
            yield Button("刷新 ↻", id="header-refresh", compact=True)
        with Horizontal(id="overview"):
            with Vertical(classes="overview-card", id="subscription-card"):
                with Horizontal(classes="card-heading"):
                    yield Label("当前订阅")
                    yield Button("更新 ↻", id="overview-update", compact=True, classes="text-button")
                    yield Button("订阅管理 ›", id="overview-subscriptions", compact=True, classes="text-button")
                yield Static("正在读取订阅…", id="subscription-summary", markup=False)
                yield ProgressBar(total=100, show_eta=False, show_percentage=False, id="quota")
                yield Static("", id="subscription-footer", classes="card-footnote", markup=False)
            with Vertical(classes="overview-card", id="node-card"):
                with Horizontal(classes="card-heading"):
                    yield Label("当前节点")
                    yield Button("测速", id="overview-measure", compact=True, classes="text-button")
                    yield Button("节点管理 ›", id="overview-nodes", compact=True, classes="text-button")
                yield Static("正在连接 Mihomo…", id="node-summary", markup=False)
                with Horizontal(id="overview-selectors"):
                    yield Select([], prompt="代理组", id="overview-group")
                    yield Select([], prompt="节点", id="overview-node")
                yield Static("", id="node-footer", classes="card-footnote", markup=False)
        with TabbedContent(initial="dashboard", id="pages"):
            with TabPane("概览", id="dashboard"):
                with VerticalScroll(id="dashboard-scroll"):
                    with Horizontal(id="dashboard-controls"):
                        with Vertical(classes="dashboard-card", id="home-network-card"):
                            with Horizontal(id="home-network-heading"):
                                yield Label("网络设置", classes="card-title")
                                yield Static("", id="home-network-desktop", markup=False)
                                yield Button("详情 ›", id="home-network-details", compact=True, classes="text-button")
                            with Horizontal(id="home-network-tabs"):
                                yield Button("系统代理", id="home-network-system", compact=True)
                                yield Button("虚拟网卡", id="home-network-tun", compact=True)
                            yield Static("正在读取桌面状态…", id="home-system-status", markup=False)
                            with Horizontal(classes="home-switch-row", id="home-system-row"):
                                yield Label("系统代理")
                                yield Switch(id="home-system-switch", disabled=True)
                            with Horizontal(classes="home-switch-row", id="home-tun-row"):
                                yield Label("虚拟网卡模式")
                                yield Switch(id="home-tun-switch", disabled=True)
                            yield Static("", id="home-proxy-target", classes="card-footnote", markup=False)
                            yield Button("修复代理地址", id="home-repair-system", compact=True, classes="text-button")
                            yield Static("当前管理页暂未提供 TUN 启停功能", id="home-tun-status", classes="card-footnote", markup=False)
                        with Vertical(classes="dashboard-card", id="home-mode-card"):
                            yield Label("代理模式", classes="card-title")
                            with Horizontal(id="home-mode-buttons"):
                                yield Button("规则", id="home-mode-rule", compact=True)
                                yield Button("全局", id="home-mode-global", compact=True)
                                yield Button("直连", id="home-mode-direct", compact=True)
                            yield Static("等待内核连接…", id="home-mode-description", markup=False)
                            yield Static("", id="home-global-status", classes="card-footnote", markup=False)
                            yield Select([], prompt="选择 GLOBAL 出口", id="home-global-node")
                    with Horizontal(classes="dashboard-section-heading"):
                        yield Label("出口 IP 与网站连通性")
                        yield Button("一键验证", id="home-verify-all", variant="primary", classes="action-button")
                        yield Button("验证 IP", id="home-check-ip", classes="action-button")
                        yield Button("站点验证", id="home-check-sites", classes="action-button")
                        yield Button("清空", id="home-clear-results", classes="action-button")
                    with Grid(id="home-check-grid"):
                        for key, title in (("direct", "本机直连"), ("proxy", "代理出口")):
                            with Vertical(classes="check-card", id="home-ip-" + key):
                                yield Label(title, classes="check-card-label")
                                yield Static("未查询", id="home-ip-value-" + key, markup=False)
                                yield Static("出口 IP 与归属", id="home-ip-meta-" + key, classes="card-footnote", markup=False)
                        for target, (name, _url) in CONNECTIVITY_TARGETS.items():
                            with Vertical(classes="check-card", id="home-site-card-" + target):
                                yield Label(name, classes="check-card-label")
                                yield Static("未测试", id="home-site-value-" + target, markup=False)
                                yield Static("HTTP / 耗时", id="home-site-meta-" + target, classes="card-footnote", markup=False)
                                with Horizontal(classes="site-actions"):
                                    yield Button("重测", id="home-site-test-" + target, compact=True, classes="text-button")
                                    yield Button("详情", id="home-site-details-" + target, compact=True, classes="text-button")
                    yield Static("站点测试使用当前代理；IP 查询保留策略组选择。点击「详情」查看错误、时间和出口信息。", classes="card-footnote")
            with TabPane("节点", id="nodes"):
                with Horizontal(id="node-browser"):
                    with Vertical(id="group-panel"):
                        yield Label("代理组 · ↑↓ 选择", classes="section-label")
                        yield DataTable(id="groups-table", cursor_type="row", zebra_stripes=True)
                    with Vertical(id="node-panel"):
                        with Horizontal(classes="toolbar"):
                            yield Input(placeholder="搜索节点名称 / 协议（/）", id="node-search")
                            yield Select([("原始顺序", "original"), ("延迟排序", "delay"), ("名称排序", "name")], value="original", allow_blank=False, id="node-sort")
                        yield DataTable(id="nodes-table", cursor_type="row", zebra_stripes=True)
                        yield Static("请选择代理组", id="group-status", markup=False)
                with Horizontal(classes="toolbar"):
                    yield Button("应用节点 ↵", variant="primary", id="apply-node")
                    yield Button("测选中节点", id="measure-one")
                    yield Button("全组测速", id="measure-all")
                    yield Button("停止测速", id="measure-cancel", disabled=True)
            with TabPane("订阅", id="subscriptions"):
                with Horizontal(classes="toolbar"):
                    yield Button("导入订阅 +", variant="primary", id="sub-new")
                yield Static("Tab / ↑↓ 浏览卡片；Enter 启用。更新仅下载该订阅。", classes="muted")
                with VerticalScroll(id="profiles-scroll"):
                    yield Grid(id="profiles-grid")
                    yield Static("尚未添加订阅，点击「导入订阅」开始。", id="profiles-empty", markup=False)
        with Horizontal(id="bottom-bar"):
            yield Static("准备连接…", id="status-line", markup=False)
            yield Button("启动 Mihomo", id="start-core", variant="success")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#groups-table", DataTable).add_columns("代理组", "数量")
        self.query_one("#nodes-table", DataTable).add_columns("当前", "节点 / 策略", "协议", "延迟")
        self.ready = True
        self.action_refresh()
        self.set_interval(15, self.auto_refresh)

    def on_resize(self, event) -> None:
        self.set_class(event.size.width < 100, "narrow")
        self.set_class(event.size.height < 32, "compact")

    def action_toggle_theme(self) -> None:
        self.theme = "textual-dark" if self.theme == "mihomo-light" else "mihomo-light"

    def action_show_tab(self, tab: str) -> None:
        self.query_one("#pages", TabbedContent).active = tab

    def action_search(self) -> None:
        self.action_show_tab("nodes")
        self.query_one("#node-search", Input).focus()

    def action_focus_groups(self) -> None:
        self.action_show_tab("nodes")
        self.query_one("#groups-table", DataTable).focus()

    def action_focus_nodes(self) -> None:
        self.action_show_tab("nodes")
        self.query_one("#nodes-table", DataTable).focus()

    def action_request_quit(self) -> None:
        if self.busy:
            if self.measuring:
                self.cancel_measure = True
            self.notify("操作仍在进行，请等待完成后退出；测速已请求停止。" if self.measuring else "操作仍在进行，请等待完成后退出。", severity="warning")
            return
        self.exit()

    def action_quit(self) -> None:
        # Textual 的命令面板和默认退出动作也遵循同一操作保护。
        self.action_request_quit()

    def auto_refresh(self) -> None:
        if len(self.screen_stack) == 1 and not self.busy and not self.refreshing:
            self.action_refresh()

    def action_refresh(self) -> None:
        if not self.ready or self.busy or self.refreshing or len(self.screen_stack) != 1:
            return
        self.refreshing = True
        self._sync_controls()
        self._refresh()

    @work(group="refresh", exit_on_error=False)
    async def _refresh(self) -> None:
        try:
            await self._read_state()
            self._status("已刷新 · 每 15 秒自动刷新")
        except Exception as exc:
            self._failure(exc)
        finally:
            self.refreshing = False
            self._sync_controls()

    async def _read_state(self) -> None:
        state = await asyncio.to_thread(self.manager.snapshot)
        active_id = state.get("subscriptions", {}).get("active_id", "")
        if active_id != self.last_active_profile:
            self.delays.clear()
        self.last_active_profile = active_id
        context = (state.get("running"), state.get("core", {}).get("mode"), active_id,
                   tuple((name, item.get("now")) for name, item in sorted(state.get("groups", {}).items())),
                   tuple((item.get("id"), item.get("updated_at")) for item in state.get("subscriptions", {}).get("items", []) if item.get("active")),
                   state.get("network", {}).get("target"))
        if self.site_context is not None and context != self.site_context:
            self.site_results.clear()
            self.ip_results.clear()
        self.site_context = context
        self.state = state
        groups = state.get("groups", {})
        if state.get("core", {}).get("mode") == "global":
            self.active_group = "GLOBAL" if "GLOBAL" in groups else ""
        elif self.active_group not in groups or self.active_group == "GLOBAL":
            self.active_group = state.get("main", "")
        self._render_all()

    def _status(self, text: str) -> None:
        self.query_one("#status-line", Static).update(text)

    def _log(self, text: str) -> None:
        logging.getLogger(__name__).info(text)

    def _failure(self, exc: Exception) -> None:
        if isinstance(exc, SubscriptionError):
            message = "订阅操作失败，请检查链接、下载方式或配置；未展示可能包含凭证的原始错误。"
        elif isinstance(exc, EXPECTED_ERRORS):
            message = str(exc)
        else:
            message = f"操作失败（{type(exc).__name__}），请检查配置后重试"
        self._status(message)
        self._log(message)
        self.notify(message, severity="error", timeout=7)

    def _start_job(self, label: str, operation: Callable, *, asynchronous=False, refresh=True, callback: Callable | None = None) -> None:
        if self.busy or self.refreshing:
            self.notify("正在执行或刷新，请稍后重试", severity="warning")
            return
        self.busy = True
        self._status(label + "…")
        self._log(label)
        self._sync_controls()
        self._job(label, operation, asynchronous, refresh, callback)

    @work(group="operation", exit_on_error=False)
    async def _job(self, label: str, operation: Callable, asynchronous: bool, refresh: bool, callback: Callable | None) -> None:
        try:
            result = await operation() if asynchronous else await asyncio.to_thread(operation)
            if callback:
                callback(result)
            if refresh:
                await self._read_state()
            self._status(label + "完成")
            self._log(label + "完成")
        except Exception as exc:
            self._failure(exc)
            try:
                await self._read_state()
            except Exception:
                pass
        finally:
            self.busy = self.measuring = False
            self._sync_controls()

    def _sync_controls(self) -> None:
        if not self.ready:
            return
        blocked = self.busy or self.refreshing
        running = self.state.get("running", False)
        direct = self.state.get("core", {}).get("mode") == "direct"
        has_node = bool(self._highlighted_node())
        has_group = bool(self.active_group) and not direct
        profile = self._profile()
        for button_id in ("apply-node", "measure-one", "measure-all", "home-check-ip", "home-check-sites", "home-verify-all"):
            enabled = running
            if button_id in ("apply-node", "measure-one"):
                enabled = enabled and has_node and has_group
            if button_id == "measure-all":
                enabled = enabled and has_group
            self.query_one("#" + button_id, Button).disabled = blocked or not enabled
        self.query_one("#sub-new", Button).disabled = blocked
        for card in self.query(SubscriptionCard):
            if card.is_mounted:
                card.update_profile(card.profile, blocked)
        self.query_one("#home-clear-results", Button).disabled = blocked or not (self.site_results or self.ip_results)
        self.query_one("#measure-cancel", Button).disabled = not self.measuring or self.cancel_measure
        self.query_one("#start-core", Button).disabled = blocked or running
        self.query_one("#start-core", Button).display = not running
        self.set_class(bool(running), "core-running")
        net = self.state.get("network", {})
        enabled = net.get("mode") == "system"
        for switch_id in ("home-system-switch",):
            self.query_one("#" + switch_id, Switch).disabled = blocked or not net.get("supported") or (not running and not enabled)
        for target in CONNECTIVITY_TARGETS:
            self.query_one("#home-site-test-" + target, Button).disabled = blocked or not running
        for mode in ("rule", "global", "direct"):
            self.query_one("#home-mode-" + mode, Button).disabled = blocked or not running or self.state.get("core", {}).get("mode") not in ("rule", "global", "direct")
        for select_id in ("overview-group", "overview-node", "home-global-node"):
            self.query_one("#" + select_id, Select).disabled = blocked or not running or direct or (select_id == "overview-group" and self.state.get("core", {}).get("mode") == "global")
        self.query_one("#overview-update", Button).disabled = blocked
        selected = self.state.get("groups", {}).get(self.active_group, {}).get("now", "")
        current, _chain = resolve_node(self.state.get("proxies", {}), selected)
        self.query_one("#overview-measure", Button).disabled = blocked or not running or direct or not current or current in BUILTINS
        self.query_one("#header-refresh", Button).disabled = blocked
        self.query_one("#home-repair-system").display = self.network_tab == "system" and enabled and not net.get("matches_target")
        self.query_one("#home-repair-system", Button).disabled = blocked or not running or not net.get("supported")
        self.query_one("#groups-table", DataTable).disabled = direct or blocked

    @staticmethod
    def _cursor_key(table: DataTable) -> str:
        if not table.row_count:
            return ""
        return str(table.ordered_rows[min(table.cursor_row, table.row_count - 1)].key.value)

    def _highlighted_node(self) -> str:
        return self._cursor_key(self.query_one("#nodes-table", DataTable))

    def _profile(self) -> dict | None:
        return next((row for row in self.state.get("subscriptions", {}).get("items", []) if row["id"] == self.selected_profile), None)

    def _render_all(self) -> None:
        self._render_groups()
        self._render_nodes()
        self._render_profiles()
        self._render_network()
        self._render_summary()
        self._render_sites()
        for message in self.state.get("errors", []):
            self._log(message)

    def _render_groups(self) -> None:
        table = self.query_one("#groups-table", DataTable)
        groups = self.state.get("groups", {})
        mode = self.state.get("core", {}).get("mode")
        names = sorted(groups)
        if mode == "global":
            names = ["GLOBAL"] if "GLOBAL" in groups else []
        elif mode == "direct":
            names = []
        else:
            names = [name for name in names if name != "GLOBAL"]
        with table.prevent(DataTable.RowHighlighted, DataTable.RowSelected):
            table.clear()
            for name in names:
                table.add_row(Text(name), str(len(groups[name].get("all") or [])), key=name)
            if self.active_group in names:
                table.move_cursor(row=names.index(self.active_group))

    def _render_nodes(self) -> None:
        table = self.query_one("#nodes-table", DataTable)
        previous = self._cursor_key(table)
        group = self.state.get("groups", {}).get(self.active_group, {})
        query = self.query_one("#node-search", Input).value.casefold()
        proxies = self.state.get("proxies", {})
        names = [name for name in (group.get("all") or []) if isinstance(name, str)]
        if self.state.get("core", {}).get("mode") == "direct":
            names = []
        names = [name for name in names if query in (name + " " + str(proxies.get(name, {}).get("type", ""))).casefold()]
        order = self.query_one("#node-sort", Select).value
        if order == "name":
            names.sort(key=str.casefold)
        elif order == "delay":
            names.sort(key=lambda name: (not self.delays.get(name, {}).get("ok", False), self.delays.get(name, {}).get("delay") or 999999, name))
        with table.prevent(DataTable.RowHighlighted, DataTable.RowSelected):
            table.clear()
            for name in names:
                delay = self.delays.get(name)
                text, style = "—", "dim"
                if delay:
                    if delay.get("pending"):
                        text = "测速中…"
                    elif delay.get("ok"):
                        ms = delay["delay"]
                        text, style = f"{ms} ms", "green" if ms < 150 else "yellow" if ms < 400 else "red"
                    else:
                        text, style = "超时 / 失败", "red"
                table.add_row("●" if name == group.get("now") else "", Text(name),
                              Text(proxies.get(name, {}).get("type", "—")), Text(text, style=style), key=name)
            focus = previous if previous in names else group.get("now")
            if focus in names:
                table.move_cursor(row=names.index(focus))
        actual = group.get("now", "—")
        status = "直连模式不使用代理组" if self.state.get("core", {}).get("mode") == "direct" else f"{self.active_group or '无代理组'} → {actual} · 显示 {len(names)} 项"
        self.query_one("#group-status", Static).update(status)
        self._sync_controls()

    def _render_profiles(self) -> None:
        rows = self.state.get("subscriptions", {}).get("items", [])
        grid = self.query_one("#profiles-grid", Grid)
        cards = list(grid.query(SubscriptionCard))
        blocked = self.busy or self.refreshing
        if [card.profile["id"] for card in cards] == [row["id"] for row in rows]:
            for card, row in zip(cards, rows):
                card.update_profile(row, blocked)
        else:
            grid.remove_children()
            grid.mount(*(SubscriptionCard(row, blocked) for row in rows))
        self.query_one("#profiles-empty").display = not rows

    @on(SubscriptionCard.Action)
    def profile_action(self, event: SubscriptionCard.Action) -> None:
        if self.busy or self.refreshing:
            return
        self.selected_profile = event.card.profile["id"]
        if event.action == "edit":
            self.push_screen(SubscriptionScreen(self._profile()), self._save_profile)
        else:
            self._subscription_action(event.action)

    def _render_summary(self) -> None:
        subs = self.state.get("subscriptions", {})
        active = next((row for row in subs.get("items", []) if row.get("id") == subs.get("active_id")), None)
        self.query_one("#overview-update", Button).label = "更新 ↻" if active else "导入 +"
        if active:
            used = None if active.get("traffic_upload") is None and active.get("traffic_download") is None else (active.get("traffic_upload") or 0) + (active.get("traffic_download") or 0)
            total = active.get("traffic_total")
            source = urlsplit(active.get("url") or "").hostname or "本地订阅"
            count = active.get("node_count")
            summary = Text()
            summary.append(active.get("name", ""), style="bold")
            summary.append(f"\n{source}" + (f" · {count} 个节点" if count is not None else ""), style="dim")
            summary.append(f"\n已用 {traffic(used)} / {traffic(total)}")
            self.query_one("#subscription-summary", Static).update(summary)
            self.query_one("#quota", ProgressBar).update(progress=min(100, used / total * 100) if used is not None and total else 0)
            updated = (active.get("updated_at") or "未更新")[:16].replace("T", " ")
            self.query_one("#subscription-footer", Static).update(f"更新 {updated}  ·  到期 {expiry(active.get('expire_at'))}")
        else:
            self.query_one("#subscription-summary", Static).update(Text.assemble(("尚未启用订阅", "bold"), "\n导入订阅后，流量和到期信息将在这里显示。"))
            self.query_one("#quota", ProgressBar).update(progress=0)
            self.query_one("#subscription-footer", Static).update("按 2 进入订阅管理")
        mode = self.state.get("core", {}).get("mode", "unknown")
        mode_label = {"rule": "规则", "global": "全局", "direct": "直连"}.get(mode, "未知")
        if not self.state.get("running"):
            node = Text.assemble(("Mihomo 未连接", "bold"), "\n启动内核后显示当前节点及延迟。")
            footer = "可使用底部按钮启动 Mihomo"
        elif mode == "direct":
            node = Text.assemble(("直连 · DIRECT", "bold"), "\n流量不经过代理节点", ("\n直连模式", "dim"))
            footer = "当前模式已从内核读取"
        else:
            selected = self.state.get("groups", {}).get(self.active_group, {}).get("now", "")
            name, chain = resolve_node(self.state.get("proxies", {}), selected)
            delay = self.delays.get(name, {})
            item = self.state.get("proxies", {}).get(name, {})
            node = Text()
            node.append(name or "暂无节点", style="bold")
            if delay.get("ok"):
                node.append(f"  {delay['delay']} ms", style="#16a34a" if delay["delay"] < 150 else "#d97706")
            else:
                node.append("  未测速", style="dim")
            node.append(f"\n{item.get('type') or '策略组'}" + (" · UDP" if item.get("udp") else "") + f" · {mode_label}模式", style="dim")
            node.append("\n" + " → ".join([self.active_group, *chain[:-1]]))
            footer = "● 内核运行中 · " + (self.state.get("version") or "Mihomo")
        self._render_overview_selectors()
        self.query_one("#node-summary", Static).update(node)
        self.query_one("#node-footer", Static).update(footer)
        running = self.state.get("running")
        status = Text("● " + ("运行中" if running else "未连接"), style="#16a34a" if running else "#dc2626")
        if running:
            status.append(" · " + self.state.get("version", ""), style="dim")
        self.query_one("#header-status", Static).update(status)

    def _render_overview_selectors(self) -> None:
        groups = self.state.get("groups", {})
        mode = self.state.get("core", {}).get("mode")
        names = ["GLOBAL"] if mode == "global" and "GLOBAL" in groups else sorted(name for name in groups if name != "GLOBAL")
        if mode == "direct":
            names = []
        selections = {
            "overview-group": (names, self.active_group),
            "overview-node": (groups.get(self.active_group, {}).get("all", []) if names else [], groups.get(self.active_group, {}).get("now")),
            "home-global-node": (groups.get("GLOBAL", {}).get("all", []), groups.get("GLOBAL", {}).get("now")),
        }
        for widget_id, (options, actual) in selections.items():
            select = self.query_one("#" + widget_id, Select)
            with select.prevent(Select.Changed):
                select.set_options([(name, name) for name in options])
                select.value = actual if actual in options else Select.NULL
        self.query_one("#home-global-node").display = mode == "global"

    @on(Select.Changed, "#overview-group")
    @on(Select.Changed, "#overview-node")
    @on(Select.Changed, "#home-global-node")
    def choose_overview(self, event: Select.Changed) -> None:
        if not self.ready or self.busy or self.refreshing or event.value == Select.NULL:
            return
        if event.select.id == "overview-group":
            self.active_group = str(event.value)
            self._render_groups()
            self._render_nodes()
            self._render_summary()
            self._sync_controls()
        else:
            group = "GLOBAL" if event.select.id == "home-global-node" else self.active_group
            name = str(event.value)
            self._render_overview_selectors()
            if self.state.get("core", {}).get("mode") != "direct" and self.state.get("groups", {}).get(group, {}).get("now") != name:
                self._start_job("切换节点", lambda: self.manager.select(group, name))

    async def _verify_all(self) -> None:
        try:
            self._show_ip(await asyncio.to_thread(self.manager.ip))
        except Exception as exc:
            self._failure(exc)
        await self._check_sites(list(CONNECTIVITY_TARGETS))

    def _render_sites(self) -> None:
        self._render_home_checks()

    def _render_home_checks(self) -> None:
        labels = {"success": ("正常", "#16a34a"), "restricted": ("受限", "#d97706"),
                  "failed": ("失败", "#dc2626"), "testing": ("测试中…", "#2563eb")}
        for target in CONNECTIVITY_TARGETS:
            result = self.site_results.get(target, {})
            label, color = labels.get(result.get("state"), ("未测试", "#64748b"))
            self.query_one("#home-site-value-" + target, Static).update(Text(label, style=color))
            status = result.get("http_status")
            elapsed = result.get("elapsed_ms")
            meta = f"HTTP {status or '—'} · {elapsed} ms" if elapsed is not None else "HTTP / 耗时"
            self.query_one("#home-site-meta-" + target, Static).update(meta)
            self.query_one("#home-site-card-" + target).tooltip = result.get("message") or CONNECTIVITY_TARGETS[target][1]
        for key in ("direct", "proxy"):
            result = self.ip_results.get(key, {})
            value = result.get("ip") or ("获取失败" if result else "未查询")
            widget = self.query_one("#home-ip-value-" + key, Static)
            widget.update(Text(value, style="bold"))
            widget.tooltip = result.get("ip") or result.get("message") or "点击「验证 IP」查询"
            self.query_one("#home-ip-meta-" + key, Static).update(result.get("label") or result.get("message") or "出口 IP 与归属")

    def _render_network(self) -> None:
        core, net = self.state.get("core", {}), self.state.get("network", {})
        switch = self.query_one("#home-system-switch", Switch)
        with switch.prevent(Switch.Changed):
            switch.value = net.get("mode") == "system"
        self._render_network_tab()
        mode = core.get("mode")
        self.set_class(mode == "global", "global-mode")
        descriptions = {"rule": "按订阅规则分流，匹配对应的代理节点或直连。", "global": "进入 Mihomo 的流量统一使用 GLOBAL 出口。", "direct": "进入 Mihomo 的流量直接连接目标，不经过代理节点。"}
        for value in ("rule", "global", "direct"):
            self.query_one("#home-mode-" + value, Button).variant = "primary" if value == mode else "default"
        self.query_one("#home-mode-description", Static).update(descriptions.get(mode, "启动 Mihomo 后选择流量转发方式。"))
        current = self.state.get("groups", {}).get("GLOBAL", {}).get("now", "—")
        self.query_one("#home-global-status", Static).update(f"GLOBAL → {current}" if mode == "global" else "● 当前模式已从内核读取")

    def _render_network_tab(self) -> None:
        net, core = self.state.get("network", {}), self.state.get("core", {})
        system = self.network_tab == "system"
        enabled = net.get("mode") == "system"
        tun_enabled = bool((core.get("tun") or {}).get("enable"))
        self.query_one("#home-network-desktop", Static).update(net.get("desktop") or "未识别桌面")
        for tab in ("system", "tun"):
            self.query_one("#home-network-" + tab, Button).variant = "primary" if self.network_tab == tab else "default"
        self.query_one("#home-system-row").display = system
        self.query_one("#home-tun-row").display = not system
        self.query_one("#home-proxy-target").display = system
        self.query_one("#home-tun-status").display = not system
        self.query_one("#home-repair-system").display = system and enabled and not net.get("matches_target")
        tun_switch = self.query_one("#home-tun-switch", Switch)
        with tun_switch.prevent(Switch.Changed):
            tun_switch.value = tun_enabled
        if not system:
            message = ("TUN 已开启，当前由 Mihomo 虚拟网卡接管流量。" if tun_enabled else "TUN 未开启，可接管不遵循系统代理设置的应用流量。") if self.state.get("running") and core else "内核未连接，暂时无法读取虚拟网卡状态。"
        elif not net.get("supported"):
            message = net.get("message") or "系统代理状态暂不可用。"
        elif net.get("mode") == "other":
            message = "当前使用 PAC 或其他代理模式，开启后将切换为手动代理。"
        elif enabled and not net.get("matches_target"):
            message = "系统代理已开启，但地址与当前 Mihomo 不一致。"
        else:
            message = "系统代理已开启，遵循系统设置的应用将通过 Mihomo 访问网络。" if enabled else "系统代理已关闭，可开启以供 Chrome 等应用使用。"
        self.query_one("#home-system-status", Static).update(message)
        self.query_one("#home-proxy-target", Static).update(net.get("target") or "支持 KDE Plasma / GNOME")

    @on(DataTable.RowHighlighted)
    def highlight(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "groups-table":
            self.active_group = str(event.row_key.value)
            self._render_nodes()
            self._render_summary()
        elif event.data_table.id == "nodes-table":
            self._sync_controls()

    @on(DataTable.RowSelected)
    def select_row(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "nodes-table":
            self._apply_node()
        elif event.data_table.id == "groups-table":
            self.query_one("#nodes-table", DataTable).focus()

    @on(Input.Changed, "#node-search")
    @on(Select.Changed, "#node-sort")
    def filter_nodes(self) -> None:
        if self.ready:
            self._render_nodes()

    @on(Switch.Changed, "#home-system-switch")
    def toggle_system(self, event: Switch.Changed) -> None:
        actual = self.state.get("network", {}).get("mode") == "system"
        with event.switch.prevent(Switch.Changed):
            event.switch.value = actual
        if event.value == actual:
            return
        self._start_job("开启系统代理" if event.value else "关闭系统代理", lambda: self.manager.system_proxy(event.value))

    def _apply_node(self) -> None:
        name, group = self._highlighted_node(), self.active_group
        if not name or not group or self.state.get("core", {}).get("mode") == "direct":
            return
        if self.state.get("groups", {}).get(group, {}).get("now") == name:
            self.notify("该节点已在使用")
            return
        self._start_job("切换节点", lambda: self.manager.select(group, name))

    def _subscription_action(self, action: str) -> None:
        profile = self._profile()
        if not profile or self.busy or self.refreshing:
            return
        if profile.get("active") and action in ("activate", "delete"):
            self.notify("当前订阅已在使用，请先切换其他订阅", severity="warning")
            return
        profile_id = profile["id"]
        if action == "delete":
            self.push_screen(ConfirmScreen(f"确定删除订阅「{profile.get('name', '')}」及其本地缓存？"),
                             lambda yes: self._start_job("删除订阅", lambda: self.manager.subscription("delete", profile_id)) if yes else None)
        else:
            self._start_job("启用订阅" if action == "activate" else "更新订阅", lambda: self.manager.subscription(action, profile_id), callback=lambda _result: self.delays.clear())

    def _save_profile(self, payload: dict | None) -> None:
        if payload:
            self._start_job("保存订阅", lambda: self.manager.subscription("save", payload=payload))

    async def _measure(self, names: list[str]) -> None:
        self.measuring = True
        self.cancel_measure = False
        self._sync_controls()
        semaphore = asyncio.Semaphore(4)
        async def measure(name: str):
            async with semaphore:
                if self.cancel_measure:
                    return None
                self.delays[name] = {"pending": True}
                self._render_nodes()
                return await asyncio.to_thread(self.manager.measure, name)
        tasks = [asyncio.create_task(measure(name)) for name in names]
        completed = 0
        try:
            for task in asyncio.as_completed(tasks):
                result = await task
                if result is None:
                    continue
                self.delays[result["name"]] = result
                completed += 1
                self._status(f"测速 {completed}/{len(names)}" + (" · 正在停止" if self.cancel_measure else ""))
                self._render_nodes()
                self._render_summary()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for name in names:
                if self.delays.get(name, {}).get("pending"):
                    self.delays.pop(name, None)
        self._log(f"测速结束：完成 {completed}/{len(names)}" + ("（已停止）" if self.cancel_measure else ""))

    def _start_measure(self, all_nodes: bool) -> None:
        if self.state.get("core", {}).get("mode") == "direct":
            return
        names = list(self.state.get("groups", {}).get(self.active_group, {}).get("all") or []) if all_nodes else [self._highlighted_node()]
        names = list(dict.fromkeys(name for name in names if name and name not in BUILTINS))
        if not names:
            self.notify("没有可测速的代理节点", severity="warning")
            return
        self._start_job("节点测速", lambda: self._measure(names), asynchronous=True, refresh=False)

    def _start_site_test(self, all_sites: bool, *, target: str | None = None) -> None:
        if not self.state.get("running"):
            self.notify("请先启动 Mihomo", severity="warning")
            return
        targets = list(CONNECTIVITY_TARGETS) if all_sites else [target]
        targets = [name for name in targets if name in CONNECTIVITY_TARGETS]
        if not targets:
            return
        self._start_job("站点测试", lambda: self._check_sites(targets), asynchronous=True, refresh=False)

    async def _check_sites(self, targets: list[str]) -> None:
        for target in targets:
            self.site_results[target] = {"state": "testing", "message": "正在连接…"}
        self._render_sites()

        async def check(target):
            try:
                result = await asyncio.to_thread(self.manager.connectivity, target)
            except Exception:
                result = {"state": "failed", "message": "测试异常，请重试"}
            return target, {**result, "checked_at": result.get("checked_at") or datetime.now().astimezone().isoformat()}

        tasks = [asyncio.create_task(check(target)) for target in targets]
        completed = 0
        try:
            for task in asyncio.as_completed(tasks):
                target, result = await task
                self.site_results[target] = result
                completed += 1
                self._render_sites()
                self._status(f"站点测试 {completed}/{len(targets)}")
                self._log(f"{CONNECTIVITY_TARGETS[target][0]}：{result.get('message', '')} · HTTP {result.get('http_status') or '—'} · {result.get('elapsed_ms', 0)} ms")
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _show_ip(self, result: dict) -> None:
        self.ip_results = result
        self._render_home_checks()
        self._sync_controls()
        for key, label in (("direct", "本机直连"), ("proxy", "代理出口")):
            item = result.get(key, {})
            self._log(f"{label}：{item.get('ip') or item.get('message', '获取失败')}  {item.get('label', '')}")

    @on(Button.Pressed)
    def button(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "header-refresh":
            self.action_refresh()
        elif button_id == "overview-subscriptions":
            self.action_show_tab("subscriptions")
        elif button_id == "overview-update":
            profile_id = self.state.get("subscriptions", {}).get("active_id")
            if profile_id:
                self._start_job("更新订阅", lambda: self.manager.subscription("update", profile_id), callback=lambda _result: self.delays.clear())
            else:
                self.push_screen(SubscriptionScreen(), self._save_profile)
        elif button_id == "overview-measure":
            selected = self.state.get("groups", {}).get(self.active_group, {}).get("now", "")
            name, _chain = resolve_node(self.state.get("proxies", {}), selected)
            if name and name not in BUILTINS:
                self._start_job("节点测速", lambda: self._measure([name]), asynchronous=True, refresh=False)
        elif button_id == "home-verify-all":
            self._start_job("一键验证", self._verify_all, asynchronous=True, refresh=False)
        elif button_id == "overview-nodes":
            self.action_focus_nodes()
        elif button_id in ("home-network-system", "home-network-tun"):
            self.network_tab = button_id.removeprefix("home-network-")
            self._render_network_tab()
        elif button_id == "home-network-details":
            net = self.state.get("network", {})
            status = {"system": "已开启", "off": "已关闭", "other": "其他模式"}.get(net.get("mode"), "未知") if net.get("supported") else "不可用"
            addresses = net.get("addresses") or {}
            details = f"桌面：{net.get('desktop') or '未识别'}\n代理状态：{status}\nMihomo 地址：{net.get('target') or '—'}\n"
            details += "\n".join(f"{protocol.upper()}：{addresses.get(protocol) or '未设置'}" for protocol in ("http", "https", "socks"))
            if not net.get("supported") and net.get("message"):
                details += "\n\n" + net["message"]
            if net.get("mode") == "system" and not net.get("matches_target"):
                details += "\n\n系统代理目标与 Mihomo 不一致，请修复代理地址。"
            details += "\n\n系统代理只影响遵循该设置的应用。关闭不会停止 Mihomo，也不会清除终端的代理环境变量。Chrome 未生效时，请完全退出后重新打开。"
            self.push_screen(ResultScreen("系统代理设置", details))
        elif button_id and button_id.startswith("home-mode-"):
            mode = button_id.removeprefix("home-mode-")
            if mode != self.state.get("core", {}).get("mode"):
                self._start_job("切换代理模式", lambda: self.manager.mode(mode))
        elif button_id == "home-check-sites":
            self._start_site_test(True)
        elif button_id and button_id.startswith("home-site-details-"):
            target = button_id.removeprefix("home-site-details-")
            name, url = CONNECTIVITY_TARGETS[target]
            result = self.site_results.get(target, {})
            details = f"{url}\n\n{result.get('message') or '尚未测试，请点击重测。'}\nHTTP：{result.get('http_status') or '—'}\n耗时：{result.get('elapsed_ms', '—')} ms\n测试时间：{result.get('checked_at') or '—'}\n出口 IP：{result.get('ip') or '—'}"
            self.push_screen(ResultScreen(name + " · 测试详情", details))
        elif button_id and button_id.startswith("home-site-test-"):
            self._start_site_test(False, target=button_id.removeprefix("home-site-test-"))
        elif button_id == "apply-node":
            self._apply_node()
        elif button_id in ("measure-one", "measure-all"):
            self._start_measure(button_id == "measure-all")
        elif button_id == "measure-cancel":
            self.cancel_measure = True
            self._status("正在停止测速，等待进行中的请求返回…")
            self._sync_controls()
        elif button_id == "sub-new":
            self.push_screen(SubscriptionScreen(), self._save_profile)
        elif button_id == "home-repair-system":
            self._start_job("修复系统代理地址", lambda: self.manager.system_proxy(True))
        elif button_id == "home-check-ip":
            self._start_job("验证出口 IP", self.manager.ip, refresh=False, callback=self._show_ip)
        elif button_id == "home-clear-results":
            self.site_results.clear()
            self.ip_results.clear()
            self._render_sites()
            self._sync_controls()
        elif button_id == "start-core":
            self._start_job("启动 Mihomo", self.manager.start)


def run_tui() -> None:
    import sys
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Textual 界面需要交互终端；脚本请使用 status / nodes / sub 等命令及 --json。", file=sys.stderr)
        raise SystemExit(2)
    MihomoApp().run()
