"""CLI / Textual 的业务适配层：复用管理逻辑，不依赖 Django 或 Web 服务。"""
from __future__ import annotations

import subprocess
import time
import urllib.parse
from datetime import datetime

from .config import load_config
from .network import NetworkError, network_status, set_network_mode
from .proxy_mode import ProxyModeError, set_proxy_mode
from .service import MAIN_SELECTOR, _check_connectivity, _measure_delay, _mihomo_request, perform_ip_check
from .subscription import SubscriptionError, delete_subscription, save_subscription, subscription_status, update_subscription


class ManagerError(Exception):
    pass


def traffic(value: int | float | None) -> str:
    if value is None:
        return "—"
    return f"{value / 1024 ** 3:.2f} GB"


def expiry(value: int | None) -> str:
    if value is None:
        return "未提供"
    if value == 0:
        return "不限期"
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return "无效日期"


def resolve_node(proxies: dict, name: str) -> tuple[str, list[str]]:
    chain: list[str] = []
    while name and name not in chain:
        chain.append(name)
        item = proxies.get(name, {})
        next_name = item.get("now") if isinstance(item, dict) else None
        if not isinstance(next_name, str) or not next_name or next_name in chain:
            break
        name = next_name
    return name, chain


class Manager:
    def snapshot(self) -> dict:
        errors = []
        code, version = _mihomo_request("GET", "/version", timeout=2)
        running = code == 200 and isinstance(version, dict)
        proxies, core = {}, {}
        if running:
            code, data = _mihomo_request("GET", "/proxies", timeout=5)
            if code == 200 and isinstance(data, dict) and isinstance(data.get("proxies"), dict):
                proxies = data["proxies"]
            else:
                errors.append("无法读取节点，请刷新重试")
            code, data = _mihomo_request("GET", "/configs", timeout=5)
            if code == 200 and isinstance(data, dict):
                core = data
            else:
                errors.append("无法读取内核运行模式")
        try:
            subscriptions = subscription_status()
        except Exception:
            subscriptions = {"items": [], "active_id": ""}
            errors.append("读取订阅失败，请检查配置文件和权限")
        groups = {name: item for name, item in proxies.items()
                  if isinstance(item, dict) and item.get("type") == "Selector"}
        main = MAIN_SELECTOR if MAIN_SELECTOR in groups else next((name for name in groups if name != "GLOBAL"), next(iter(groups), ""))
        return {"running": running, "version": version.get("version", "") if running else "",
                "proxies": proxies, "groups": groups, "main": main, "core": core,
                "subscriptions": subscriptions, "network": network_status(), "errors": errors}

    def select(self, group: str, node: str) -> None:
        path = "/proxies/" + urllib.parse.quote(group, safe="")
        code, data = _mihomo_request("GET", path, timeout=5)
        if code != 200 or not isinstance(data, dict) or data.get("type") != "Selector" or node not in (data.get("all") or []):
            raise ManagerError("代理组或节点已变化，请刷新后重试")
        code, _ = _mihomo_request("PUT", path, {"name": node}, timeout=5)
        if code not in (200, 204):
            raise ManagerError("节点切换失败，请刷新后重试")
        code, data = _mihomo_request("GET", path, timeout=5)
        if code != 200 or not isinstance(data, dict) or data.get("now") != node:
            raise ManagerError("节点切换后状态不一致，请检查实际选择")

    def measure(self, name: str) -> dict:
        return _measure_delay(name, timeout_ms=5000, test_url="http://www.gstatic.com/generate_204")

    def subscription(self, action: str, profile_id: str = "", payload: dict | None = None) -> dict:
        if action == "save":
            return save_subscription(payload or {})
        if action == "delete":
            return delete_subscription(profile_id)
        if action in ("activate", "update"):
            return update_subscription(_mihomo_request, profile_id, activate=action == "activate")
        raise ManagerError("不支持的订阅操作")

    def mode(self, mode: str) -> dict:
        return set_proxy_mode(mode)

    def system_proxy(self, enabled: bool) -> dict:
        if enabled:
            code, _ = _mihomo_request("GET", "/version", timeout=2)
            if code != 200:
                raise ManagerError("请先启动 Mihomo，再开启系统代理")
        return set_network_mode("system" if enabled else "off")

    def connectivity(self, target: str) -> dict:
        return _check_connectivity(target)

    def ip(self) -> dict:
        # 纯查询：诊断不能偷偷改动 GLOBAL 或其他策略组。
        return perform_ip_check(sync_current=False)

    def start(self) -> None:
        if _mihomo_request("GET", "/version", timeout=2)[0] == 200:
            return
        root = load_config().mihomo_dir
        try:
            result = subprocess.run(["bash", str(root / "run.sh"), "--background"], cwd=root,
                                    capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ManagerError("启动失败，请检查 cache/mihomo.log") from exc
        if result.returncode:
            raise ManagerError("启动失败，请检查内核、配置及 cache/mihomo.log")
        for _ in range(10):
            if _mihomo_request("GET", "/version", timeout=1)[0] == 200:
                return
            time.sleep(.3)
        raise ManagerError("启动后仍无法连接内核，请检查 cache/mihomo.log")


EXPECTED_ERRORS = (ManagerError, NetworkError, ProxyModeError, SubscriptionError)
